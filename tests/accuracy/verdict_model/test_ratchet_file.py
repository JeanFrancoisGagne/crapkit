"""The marks file: how crapkit reads crapkit-ratchet.tsv and writes it back.

`ratchet move OLD NEW` is the probe. It reads the whole file and rewrites it
with one path changed, so every other row must come back as the docs define a
written mark: the stamp comments, the header, one row per mark sorted by
(path, key name), CRAP to four decimals, and a row holding a delimiter or
opening with `#` as an encoded record (docs/ratchet.md, What a mark is;
docs/portable-records.md). Expected lines come from model_verdict.dump_marks
and, for the first test, from lines written out by hand from those two pages.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import drive, rulings
from accuracy.kit.settings import process, pure
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw
from accuracy.verdict_model.conftest import BARE_CONFIG

HERE = Path(__file__).resolve().parent
STAMP = "crapkit-analysis=11 lizard=1.24.0"
RECORD = "@crapkit-record-v1\t"


def lines_of(data: bytes) -> list[str]:
    """Physical rows split at LF with one trailing CR removed (portable-records.md)."""
    rows = data.decode("utf-8").split("\n")
    return [row.removesuffix("\r") for row in rows[:-1 if rows[-1] == "" else None]]


def decoded(line: str):
    """An encoded record as its field list; any other line as itself."""
    return json.loads(line[len(RECORD):]) if line.startswith(RECORD) else line


def write_marks(root: Path, lines: list[str], newline: str = "\n") -> Path:
    path = root / "crapkit-ratchet.tsv"
    path.write_bytes((newline.join(lines) + newline).encode("utf-8"))
    return path


def move(root: Path, old: str, new: str) -> drive.Result:
    return drive.Driver(root).run("ratchet", "move", old, new)


# --- hand: one file, written out from the docs -------------------------------------------

HAND_IN = [f"# {STAMP}", "# crapkit-keys=1", "path\tlong_name\tcrap",
           "src/b.py\tz( )\t3.1",
           "src/a.py\tf( x )\t30.0000",
           RECORD + json.dumps(["src/a\nb.py", "g( )", "12.0000"]),
           "src/a.py\t#h( )\t7",
           "src/\u00e9.py\tq( )\t9.87654",
           "src/a.py\tu\u2028v( )\t1.00004"]
# docs/ratchet.md#what-a-mark-is: rows sorted by (path, key name), CRAP to four
# decimals; docs/portable-records.md: a field holding LF or U+2028 is encoded, a
# `#` opening the second field is not. src/a.py moves to src/c.py; the encoded
# path "src/a\nb.py" is another file and stays.
HAND_OUT = [f"# {STAMP}", "# crapkit-keys=1", "path\tlong_name\tcrap",
            ["src/a\nb.py", "g( )", "12.0000"],
            "src/b.py\tz( )\t3.1000",
            "src/c.py\t#h( )\t7.0000",
            "src/c.py\tf( x )\t30.0000",
            ["src/c.py", "u\u2028v( )", "1.0000"],
            "src/\u00e9.py\tq( )\t9.8765"]


@pytest.mark.process
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_move_rewrites_every_other_row_as_the_docs_write_a_mark(bare_repo, newline):
    path = write_marks(bare_repo, HAND_IN, newline)

    result = move(bare_repo, "src/a.py", "src/c.py")

    assert (result.code, result.stdout.strip()) == (
        0, "crapkit-ratchet.tsv: moved 3 mark(s) from src/a.py to src/c.py")
    assert [decoded(line) for line in lines_of(path.read_bytes())] == HAND_OUT
    assert model.parse_marks(path.read_bytes().decode("utf-8")) == model.MarksFile(
        STAMP, "1", {**_repathed(HAND_OUT)})


def _repathed(expected: list) -> dict:
    marks = {}
    for line in expected[3:]:
        fields = line if isinstance(line, list) else line.split("\t")
        marks[(fields[0], fields[1])] = model.Decimal(fields[2])
    return marks


# --- model: any file, against the clean-room writer ----------------------------------------

AWKWARD = st.sampled_from(["\t", "\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029",
                           "#", "\\", "\u00e9", " ", "\"", "\u00a0"])
FIELD = st.lists(st.one_of(st.sampled_from("abz/._()"), AWKWARD), min_size=1, max_size=6).map("".join)
VALUE = st.integers(10_000, 5_000_000_000).map(lambda n: model.Decimal(n).scaleb(-4))
MARKS = st.dictionaries(st.tuples(FIELD, FIELD), VALUE, max_size=6)


@pytest.mark.process
@process
@given(marks=MARKS, moved=st.dictionaries(FIELD, VALUE, min_size=1, max_size=3),
       keys=st.sampled_from([None, "1"]))
def test_move_leaves_the_file_the_model_writes(tmp_path_factory, marks, moved, keys):
    root = tmp_path_factory.mktemp("move")
    (root / "crapkit.toml").write_bytes(BARE_CONFIG.encode("utf-8"))
    before = {**marks, **{("src/old.py", name): value for name, value in moved.items()}}
    path = write_marks(root, _as_lines(model.MarksFile(STAMP, keys, before)))

    result = move(root, "src/old.py", "src/new.py")

    after = {**marks, **{("src/new.py", name): value for name, value in moved.items()}}
    assert result.code == 0, result.stderr
    assert [decoded(line) for line in lines_of(path.read_bytes())] == \
        model.dump_marks(model.MarksFile(STAMP, keys, after))


def _as_lines(marks: model.MarksFile) -> list[str]:
    return [RECORD + json.dumps(line) if isinstance(line, list) else line
            for line in model.dump_marks(marks)]


# --- values the writer never writes ---------------------------------------------------------

@pytest.mark.process
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "abc", ""])
def test_nonfinite_mark_refuses(bare_repo, value):
    """A mark that is no finite number is refused, and the file is left alone:
    rewriting it would drop a mark the repo signed for (ruling V2)."""
    path = write_marks(bare_repo, [f"# {STAMP}", "path\tlong_name\tcrap",
                                   f"src/a.py\tf( x )\t{value}", "src/b.py\tg( )\t9.0000"])
    before = path.read_bytes()

    result = move(bare_repo, "src/b.py", "src/c.py")

    assert result.code == 3, result.stdout + result.stderr
    assert "src/a.py\tf( x )" in result.stderr.replace("\\t", "\t")
    assert path.read_bytes() == before
    rulings.pin_ruling("V2", crapkit="refused", oracle="undocumented")


@pytest.mark.process
def test_a_written_tie_rounds_on_its_double(bare_repo):
    """A hand-typed mark at a 4 dp tie: crapkit reads 1.00005 as a double,
    1.0000500000000001, and rounds that double as Python's round() does; the
    decimal text's own half-even is 1.0000 (ruling V3)."""
    path = write_marks(bare_repo, [f"# {STAMP}", "path\tlong_name\tcrap", "src/a.py\tf( )\t1.00005"])

    assert move(bare_repo, "src/a.py", "src/c.py").code == 0

    written = lines_of(path.read_bytes())[-1].split("\t")[2]
    assert written == f"{round(1.00005, 4):.4f}"
    rulings.pin_ruling("V3", crapkit=written, oracle=model.four(model.Decimal("1.00005")))


@pytest.mark.process
@pytest.mark.parametrize("value, ruling", [("0", "V4.0"), ("-1", "V4")])
def test_a_mark_no_crap_can_reach_is_kept(bare_repo, value, ruling):
    """CRAP is at least ccn, so at least 1; a mark of 0 or below is kept as
    written (ruling V4 records the gap)."""
    path = write_marks(bare_repo, [f"# {STAMP}", "path\tlong_name\tcrap", f"src/a.py\tf( )\t{value}"])

    result = move(bare_repo, "src/a.py", "src/c.py")

    kept = lines_of(path.read_bytes())[-1] if result.code == 0 else "refused"
    rulings.pin_ruling(ruling, crapkit=kept.split("\t")[-1], oracle="refused")


# --- CRLF: a green verify leaves the file alone -----------------------------------------------

CRLF_WORLD = (vw.World().with_fn("app", vw.Fn("held", 6, 6)).with_fn("app", vw.Fn("fine", 1, 2))
              .with_test(vw.Test("t1")).with_test(vw.Test("t2", lane="b")))


@pytest.mark.process
def test_crlf_marks_parse_without_restamp(seeded_world):
    """docs/ratchet.md#how-verify-uses-the-ratchet: a stamped file whose marks
    all held stays byte-identical, and ratchet_changes is null. A checkout
    that wrote the file with CRLF endings is the same file."""
    scenario = seeded_world(CRLF_WORLD)
    path = scenario.root / "crapkit-ratchet.tsv"
    crlf = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(crlf)
    scenario.commit("the marks file as a CRLF checkout writes it")

    result = scenario.run("verify", "--json")

    assert result.code == 0, result.stdout + result.stderr
    assert result.json()["ratchet_changes"] is None
    assert "restamped" not in result.stdout + result.stderr
    assert path.read_bytes() == crlf


# --- two writers at once ------------------------------------------------------------------

def _worker(root: Path, name: str, old: str, new: str) -> subprocess.Popen:
    argv = [sys.executable, str(HERE / "race_worker.py"), str(root), name, old, new]
    return subprocess.Popen(argv, env=drive.child_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def _wait_ready(root: Path, name: str, worker: subprocess.Popen) -> None:
    import time
    deadline = time.monotonic() + 60
    while not (root / f"{name}-ready").exists():
        assert worker.poll() is None and time.monotonic() < deadline, worker.communicate()
        time.sleep(0.02)


@pytest.mark.process
def test_concurrent_writers_keep_every_mark(bare_repo):
    """Two moves that both read the file before either writes: the first
    write lands, and the second either lands on top of it or refuses. Neither
    may erase what the other admitted."""
    path = write_marks(bare_repo, [f"# {STAMP}", "path\tlong_name\tcrap",
                                   "src/a.py\tf( )\t50.0000", "src/b.py\tg( )\t40.0000"])
    first = _worker(bare_repo, "first", "src/a.py", "src/x.py")
    second = _worker(bare_repo, "second", "src/b.py", "src/y.py")
    for name, worker in (("first", first), ("second", second)):
        _wait_ready(bare_repo, name, worker)
    (bare_repo / "first-go").touch()
    assert first.wait(60) == 0, first.communicate()
    (bare_repo / "second-go").touch()
    second.wait(60)

    marks = model.parse_marks(path.read_bytes().decode("utf-8")).marks
    expected = {("src/x.py", "f( )"): 50, ("src/b.py", "g( )"): 40}
    if second.returncode == 0:
        expected = {("src/x.py", "f( )"): 50, ("src/y.py", "g( )"): 40}
    assert marks == expected, second.communicate()


# --- property, not an independent method: crapkit's own reader and writer round trip ------

@pure
@given(marks=MARKS)
def test_round_trip_through_crapkit_s_reader_and_writer(marks):
    from crapkit.ratchet import RatchetEntry, dump_ratchet, load_ratchet
    entries = [RatchetEntry(path, key, float(value)) for (path, key), value in marks.items()]

    text = dump_ratchet(entries, stamp=STAMP, key_version=1)

    assert sorted(load_ratchet(text)) == sorted(entries)
    assert [decoded(line) for line in lines_of(text.encode("utf-8"))] == \
        model.dump_marks(model.MarksFile(STAMP, "1", marks))
