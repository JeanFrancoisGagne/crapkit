"""tools/accuracy/wheel_diff.py: the moved-row map, its sides and the xplat rule.

- property: the map equals a diff of the two tables written here with the
  csv module, over Hypothesis tables with changed, dropped and added rows.
- metamorphic: swapping base and candidate mirrors every move.
- hand: two wheels zipped from this checkout's crapkit, the candidate with
  one planted change (cognitive complexity gains 1 at ccn 3), run on a
  two-function repo: the planted value is the only move, in both exports.
- hand: a side that handed off failure.json skips with one line and exit 0;
  a PyPI wheel whose bytes miss PyPI's sha256 refuses; the xplat rule's
  ulp cases, worked from math.ulp.
"""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import zipfile

from hypothesis import given, strategies as st
import pytest

from accuracy.corpus_goldens import releases, wheels
from accuracy.kit import repos, runlog
from accuracy.kit.settings import pure

# A two-scope repo: src has no lane, so its rows read no-lane and score
# crap(ccn, 0); lib's lane copies a hand-written coverage.py artifact.
_SUMMARY = {"covered_lines": 2, "num_statements": 2, "missing_lines": 0, "excluded_lines": 0,
            "covered_branches": 0, "num_branches": 0, "missing_branches": 0,
            "num_partial_branches": 0, "percent_covered": 100.0}
_REGION = {"executed_lines": [1, 2], "missing_lines": [], "excluded_lines": [],
           "executed_branches": [], "missing_branches": [], "summary": _SUMMARY}
ARTIFACT = {"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True,
                     "show_contexts": False, "timestamp": "2026-09-24T00:00:00"},
            "files": {"lib/b.py": {**_REGION, "functions": {"h": {**_REGION, "start_line": 1}},
                                   "classes": {}}},
            "totals": _SUMMARY}
TINY = {
    "crapkit.toml": ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
                     'languages = ["python"]\n\n[[scope]]\nname = "lib"\npaths = ["lib"]\n'
                     'languages = ["python"]\n\n'
                     + repos.lane_toml("lib", ".crapkit/cov/b.json", "coveragepy", ["lib"],
                                       "recorded/b.json")),
    "src/a.py": ("def f(x):\n    return x\n\n\ndef g(x):\n    if x > 1:\n        return 1\n"
                 "    if x > 2:\n        return 2\n    return 0\n"),
    "lib/b.py": "def h(x):\n    return x\n",
    "recorded/b.json": json.dumps(ARTIFACT),
}


wheel_diff = releases.wheel_diff()
COLUMNS = ("path", "long_name", "occurrence", "ccn", "crap", "remedy")


def test_the_tool_loads_under_the_name_its_spec_carries(monkeypatch):
    """The mutation tools stage names a mutated file by its path (see LAUNCHER in
    tools/accuracy/mutation.py: tools.accuracy.wheel_diff), and a dataclass finds its
    module in sys.modules by that name, so the loader must register the name the
    spec carries. Here the spec is renamed as the stage renames it."""
    real = releases.importlib.util.spec_from_file_location
    monkeypatch.setattr(releases.importlib.util, "spec_from_file_location",
                        lambda name, location: real("renamed_wheel_diff", location))
    for name in (releases.MODULE, "renamed_wheel_diff", "tools.accuracy.wheel_diff"):
        monkeypatch.setitem(sys.modules, name, None)
        del sys.modules[name]

    tool = releases.wheel_diff()

    assert sys.modules[tool.__name__] is tool is sys.modules[releases.MODULE]


# --- the map against a csv diff ------------------------------------------------------

def _tsv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, COLUMNS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _csv_rows(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return {(row["path"], row["long_name"], row["occurrence"]): row for row in reader}


def _presence(key: tuple, old: dict, new: dict) -> tuple:
    return (*key, "row", "present" if key in old else "absent",
            "present" if key in new else "absent")


def _csv_cells(key: tuple, old: dict, new: dict) -> set[tuple]:
    if key not in old or key not in new:
        return {_presence(key, old, new)}
    return {(*key, column, old[key][column], new[key][column]) for column in COLUMNS[3:]
            if old[key][column] != new[key][column]}


def csv_diff(base: str, candidate: str) -> set[tuple]:
    """(path, long_name, occurrence, column, old, new) for every differing cell,
    with a row one side lacks as a `row` present/absent pair."""
    old, new = _csv_rows(base), _csv_rows(candidate)
    return {cell for key in set(old) | set(new) for cell in _csv_cells(key, old, new)}


_ROW = st.fixed_dictionaries({
    "path": st.sampled_from(["src/a.py", "src/b é.py", "lib/c.ts"]),
    "long_name": st.sampled_from(["f( x )", "g( a , b )", "(anonymous)"]),
    "occurrence": st.sampled_from(["1", "2"]),
    "ccn": st.integers(1, 40).map(str),
    "crap": st.floats(1, 2000, allow_nan=False).map(repr),
    "remedy": st.sampled_from(["ok", "add-tests", "decompose", "split-lines"]),
})


def _unique(rows: list[dict]) -> list[dict]:
    return list({(row["path"], row["long_name"], row["occurrence"]): row for row in rows}.values())


def _as_tuples(moves) -> set[tuple]:
    return {(row.path, row.long_name, row.occurrence, row.column, row.old, row.new)
            for row in moves}


@pure
@given(st.lists(_ROW, max_size=12).map(_unique), st.lists(_ROW, max_size=12).map(_unique))
def test_the_map_equals_a_csv_diff(base_rows, candidate_rows):
    base, candidate = _tsv(base_rows), _tsv(candidate_rows)

    assert _as_tuples(wheel_diff.moved("scored.tsv", base, candidate)) == csv_diff(base, candidate)


def _mirror(row) -> tuple:
    return (row.path, row.long_name, row.occurrence, row.column, row.new, row.old)


@pure
@given(st.lists(_ROW, max_size=12).map(_unique), st.lists(_ROW, max_size=12).map(_unique))
def test_swapping_the_sides_mirrors_every_move(base_rows, candidate_rows):
    base, candidate = _tsv(base_rows), _tsv(candidate_rows)
    forward = wheel_diff.moved("scored.tsv", base, candidate)
    backward = wheel_diff.moved("scored.tsv", candidate, base)

    assert {_mirror(row) for row in forward} == _as_tuples(backward)


def test_every_moved_column_names_a_calc_of_the_plan():
    names = set(wheel_diff.CALCS.values()) | {wheel_diff.DISCOVERY}

    assert names == {"File universe and scope ownership", "Function discovery and spans",
                     "ccn_std, ccn_mod and gated ccn", "nloc",
                     "Parameter list (params, packet.params)", "Nesting depth",
                     "Cognitive complexity", "Function coverage ratio", "Coverage flag",
                     "CRAP score", "Remedy label"}


def test_the_declared_calcs_must_equal_the_moved_ones():
    moves = [wheel_diff.Moved("scored.tsv", "src/a.py", "g( x )", "1", "crap", "12.0", "12.5")]

    assert wheel_diff.expectation_problem(moves, "CRAP score") is None
    assert wheel_diff.expectation_problem(moves, None) is None
    assert "declares ['Remedy label']" in wheel_diff.expectation_problem(moves, "Remedy label")
    assert "moved nothing" in wheel_diff.expectation_problem([], "CRAP score")
    assert wheel_diff.expectation_problem([], "") is None


# --- sides ------------------------------------------------------------------------------

def _wheel(dest: Path, planted: bool) -> Path:
    return wheels.zipped(dest, wheels.COGNITIVE_PLANT if planted else None)


def _tiny(tmp_path: Path) -> Path:
    tree = tmp_path / "tiny"
    for name, text in TINY.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(text.encode("utf-8"))
    return tree


@pytest.mark.process
def test_a_planted_change_is_the_only_move(tmp_path):
    tree, exports = _tiny(tmp_path), []
    for side, planted in (("base", False), ("candidate", True)):
        site = wheel_diff.unpack(_wheel(tmp_path / f"{side}.whl", planted), tmp_path / side)
        exports.append(wheel_diff.measure_tree(tree, site, tmp_path / f"{side}-run",
                                               wheel_diff.SMALL_COMMANDS, repos.EPOCH + 86_400))

    moves = wheel_diff.diff_exports(*exports)

    assert [(m.export, m.path, m.long_name, m.column, m.old, m.new, m.calc) for m in moves] == [
        (export, "src/a.py", "g( x )", "cognitive", "2", "3", "Cognitive complexity")
        for export in ("inventory.tsv", "scored.tsv")]


@pytest.mark.process
def test_a_wheel_without_crapkit_refuses(tmp_path):
    empty = tmp_path / "empty.whl"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("other/__init__.py", "")

    with pytest.raises(wheel_diff.WheelDiffError, match="resolved to"):
        wheel_diff.unpack(empty, tmp_path / "site")


def test_a_side_that_handed_off_failure_json_skips_with_one_line(tmp_path, capsys):
    base = tmp_path / "base"
    base.mkdir()
    (base / "failure.json").write_text(json.dumps({"phase": "install", "error": "no wheel"}))
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "crapkit-0.0.0-py3-none-any.whl").write_bytes(b"")

    code = wheel_diff.main(["diff", "--base-wheel", str(base), "--candidate-wheel",
                            str(candidate)])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "wheel diff skipped: a side handed off failure.json (stopped at install: no wheel)"]


def test_a_hand_off_without_one_wheel_is_an_infra_failure(tmp_path):
    with pytest.raises(wheel_diff.WheelDiffError, match="0 wheels"):
        wheel_diff.hand_off(tmp_path)


def test_a_pypi_wheel_must_hash_to_the_published_sha256(tmp_path, monkeypatch):
    release = {"urls": [{"packagetype": "bdist_wheel", "filename": "crapkit-9.9.9-py3-none-any.whl",
                         "url": "wheel", "digests": {"sha256": "0" * 64}}]}
    answers = {"wheel": b"not the published bytes"}
    monkeypatch.setattr(wheel_diff, "_fetch",
                        lambda url: answers.get(url, json.dumps(release).encode()))

    with pytest.raises(wheel_diff.WheelDiffError, match="does not hash"):
        wheel_diff.download("9.9.9", tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- the xplat rule ---------------------------------------------------------------------

def _next(value: float, steps: int) -> str:
    for _ in range(steps):
        value = math.nextafter(value, math.inf)
    return repr(value)


@pytest.mark.parametrize("left, right, agree", [
    ("13.125", "13.125", True),
    ("13.125", _next(13.125, 1), True),
    ("13.125", _next(13.125, 2), True),
    ("13.125", _next(13.125, 3), False),
    ("0.2500", "0.2501", False),
    ("7", "8", False),
    ("ok", "add-tests", False),
    ("nan", "inf", False),
    ("12", "12.0", False),
])
def test_the_xplat_rule(left, right, agree):
    assert (left == right or wheel_diff.within_ulps(left, right)) is agree


def _receipt(os_name: str, text: str) -> dict:
    return {"os": os_name, "python": "3.12",
            "exports": {"small/scored.tsv": {"sha256": "x", "text": text}}}


def test_xplat_names_the_first_row_and_column_that_differ():
    base = _tsv([{"path": "src/a.py", "long_name": "g( x )", "occurrence": "1", "ccn": "3",
                  "crap": "12.0", "remedy": "add-tests"}])
    near = base.replace("12.0", _next(12.0, 1))
    far = base.replace("\t3\t", "\t4\t")

    assert wheel_diff.xplat([_receipt("linux", base), _receipt("windows", near)]) == []
    assert wheel_diff.xplat([_receipt("linux", base), _receipt("windows", far)]) == [
        "linux-3.12 vs windows-3.12: small/scored.tsv src/a.py 'g( x )' #1 ccn: '3' against '4'"]
    assert wheel_diff.xplat([{"exports": {}}]) == ["no receipt noted a corpus export"]


def test_xplat_reads_the_receipts_its_command_line_names(tmp_path, capsys):
    paths = []
    for os_name in ("linux", "windows"):
        paths.append(tmp_path / f"{os_name}.json")
        paths[-1].write_text(json.dumps(_receipt(os_name, _tsv([_BASE_ROW]))), encoding="utf-8")

    assert wheel_diff.main(["xplat", *map(str, paths)]) == 0
    assert capsys.readouterr().out == "2 receipts agree on 1 export(s)\n"


# --- whole runs -------------------------------------------------------------------------

@pytest.mark.nightly
@pytest.mark.process
def test_one_wheel_against_itself_moves_nothing_on_the_small_corpus(tmp_path, capsys):
    wheel = _wheel(tmp_path / "crapkit-0.0.0-py3-none-any.whl", planted=False)
    started = time.monotonic()

    code = wheel_diff.main(["diff", "--base-wheel", str(wheel), "--candidate-wheel", str(wheel),
                            "--out", str(tmp_path / "out")])

    assert (code, capsys.readouterr().out.splitlines()[0]) == (0, "0 value(s) moved")
    assert time.monotonic() - started <= 90
    assert (tmp_path / "out" / "moved.tsv").read_text(encoding="utf-8").count("\n") == 1
    assert json.loads((tmp_path / "out" / "summary.json").read_text()) == {"moved_rows": 0,
                                                                           "calcs": {}}


@pytest.mark.nightly
@pytest.mark.process
def test_the_full_corpus_diff_reads_every_member(tmp_path):
    corpus = tmp_path / "corpus"
    for member in ("one", "two"):
        (corpus / member / "src").mkdir(parents=True)
        (corpus / member / "crapkit.toml").write_bytes(
            b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
            b'languages = ["python"]\ncoverage_optional = true\n')
        (corpus / member / "src" / "a.py").write_bytes(TINY["src/a.py"].encode("utf-8"))
    site = wheel_diff.unpack(_wheel(tmp_path / "w.whl", planted=False), tmp_path / "site")

    exports = wheel_diff.measure_full(corpus, site, tmp_path / "work", repos.EPOCH + 86_400)

    assert sorted(exports) == ["one/inventory.tsv", "two/inventory.tsv"]
    assert wheel_diff.diff_exports(exports, exports) == []


def _files(*kinds: str, yanked: bool = False) -> list[dict]:
    return [{"packagetype": kind, "yanked": yanked} for kind in kinds]


def test_the_releases_are_pypi_s_newest_final_wheels(monkeypatch):
    """0.10.0 sorts above 0.9.1 as a version, not as text; a pre-release, a
    release with only an sdist and a yanked wheel are left out."""
    listing = {"0.9.0": _files("bdist_wheel", "sdist"), "0.9.1": _files("bdist_wheel"),
               "0.10.0": _files("bdist_wheel"), "0.11.0rc1": _files("bdist_wheel"),
               "0.10.1": _files("sdist"), "0.10.2": _files("bdist_wheel", yanked=True),
               "0.8.0": _files("bdist_wheel")}
    asked = []

    def fetch(url):
        asked.append(url)
        return json.dumps({"releases": listing}).encode()
    monkeypatch.setattr(wheel_diff, "_fetch", fetch)

    assert wheel_diff.releases(3) == ["0.10.0", "0.9.1", "0.9.0"]
    assert wheel_diff.releases(2, at_most="0.9.1") == ["0.9.1", "0.9.0"]
    assert asked == ["https://pypi.org/pypi/crapkit/json"] * 2


def test_the_upload_date_is_the_wheel_s(monkeypatch):
    release = {"urls": [{"packagetype": "sdist", "upload_time_iso_8601": "2026-09-22T23:59:00Z"},
                        {"packagetype": "bdist_wheel", "filename": "crapkit-0.9.0-py3-none-any.whl",
                         "upload_time_iso_8601": "2026-09-23T20:04:49.119305Z"}]}
    monkeypatch.setattr(wheel_diff, "_fetch", lambda url: json.dumps(release).encode())

    assert wheel_diff.upload_date("0.9.0") == "2026-09-23"


def test_the_wheelhouse_is_the_one_the_environment_names(monkeypatch, tmp_path):
    monkeypatch.setenv(wheel_diff.WHEELHOUSE_ENV, str(tmp_path))

    assert wheel_diff.default_wheelhouse() == tmp_path


def test_a_failed_release_fetch_is_noted_as_an_infra_miss(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("CRAPKIT_ACCURACY_LOG", str(log))

    def fetch(url):
        raise wheel_diff.WheelDiffError(f"fetching {url} failed: offline")
    monkeypatch.setattr(wheel_diff, "_fetch", fetch)

    with pytest.raises(wheel_diff.WheelDiffError):
        releases.last(5)
    notes = runlog.read(log)
    assert [(note["kind"], note["message"]) for note in notes] == [
        ("infra", "fetching https://pypi.org/pypi/crapkit/json failed: offline")]


# --- the command line -------------------------------------------------------------------
# The usage lines are the module docstring's, as argparse spells them.

USAGE = {
    ("--help",): ("usage: wheel_diff.py [-h] {diff,xplat} ... Run two crapkit wheels on one corpus "
                  "and map every value that moved."),
    ("diff", "--help"): (
        "usage: wheel_diff.py diff [-h] --base-wheel BASE_WHEEL --candidate-wheel CANDIDATE_WHEEL "
        "[--corpus {small,full}] [--corpus-dir CORPUS_DIR] [--out OUT] [--wheelhouse WHEELHOUSE] "
        "[--expect-calcs EXPECT_CALCS] [--declared-since DECLARED_SINCE]"),
    ("xplat", "--help"): "usage: wheel_diff.py xplat [-h] receipts [receipts ...]",
}


@pytest.mark.parametrize("argv", sorted(USAGE))
def test_each_command_shows_the_usage_the_docstring_gives(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        wheel_diff.main(list(argv))

    assert stopped.value.code == 0
    assert " ".join(capsys.readouterr().out.split()).startswith(USAGE[argv])


@pytest.mark.parametrize("argv", [
    [],
    ["diff", "--candidate-wheel", "c.whl"],
    ["diff", "--base-wheel", "b.whl"],
    ["diff", "--base-wheel", "b.whl", "--candidate-wheel", "c.whl", "--corpus", "medium"],
    ["xplat"],
])
def test_a_missing_or_unknown_argument_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        wheel_diff.main(argv)

    assert stopped.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_each_command_reads_its_defaults_and_paths(monkeypatch, tmp_path):
    monkeypatch.setenv(wheel_diff.WHEELHOUSE_ENV, str(tmp_path))
    parse = wheel_diff._parser().parse_args
    sides = ["diff", "--base-wheel", "b.whl", "--candidate-wheel", "c.whl"]
    named = ["--corpus", "full", "--corpus-dir", "members", "--out", "out", "--wheelhouse",
             "house", "--expect-calcs", "nloc", "--declared-since", "v0.9.0"]

    assert vars(parse(sides)) == {
        "command": "diff", "base_wheel": "b.whl", "candidate_wheel": "c.whl", "corpus": "small",
        "corpus_dir": None, "out": None, "wheelhouse": tmp_path, "expect_calcs": None,
        "declared_since": None}
    assert vars(parse(sides + named)) == {
        "command": "diff", "base_wheel": "b.whl", "candidate_wheel": "c.whl", "corpus": "full",
        "corpus_dir": Path("members"), "out": Path("out"), "wheelhouse": Path("house"),
        "expect_calcs": "nloc", "declared_since": "v0.9.0"}
    assert vars(parse(["xplat", "one.json", "two.json"])) == {
        "command": "xplat", "receipts": [Path("one.json"), Path("two.json")]}


def test_the_default_wheelhouse_is_a_per_user_cache(monkeypatch, tmp_path):
    """LOCALAPPDATA on Windows, ~/.cache elsewhere (XDG's default cache home)."""
    monkeypatch.delenv(wheel_diff.WHEELHOUSE_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    local = wheel_diff.default_wheelhouse()
    monkeypatch.delenv("LOCALAPPDATA")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))

    assert local == tmp_path / "local" / "crapkit-accuracy" / "wheelhouse"
    assert wheel_diff.default_wheelhouse() == (tmp_path / "home" / ".cache" / "crapkit-accuracy"
                                               / "wheelhouse")


# --- what diff hands each piece -----------------------------------------------------------

_BASE_ROW = {"path": "src/a.py", "long_name": "g( x )", "occurrence": "1", "ccn": "3",
             "crap": "12.0", "remedy": "add-tests"}
SIDES = {"base": {"one/inventory.tsv": _tsv([_BASE_ROW])},
         "candidate": {"one/inventory.tsv": _tsv([{**_BASE_ROW, "crap": "12.5"}])}}
MOVED_LINES = ["1 value(s) moved", "  CRAP score: 1", "  src/a.py g( x ) crap: 12.0 -> 12.5"]


def _stand_ins(monkeypatch, declared: set) -> tuple[list, list]:
    """resolve, unpack, measure and declared_since replaced by recorders, and the
    scratch directory's arguments recorded: (the calls in order, the scratch kwargs)."""
    calls, made = [], []
    real = wheel_diff.tempfile.TemporaryDirectory

    def scratch(**kwargs):
        made.append(kwargs)
        return real(**kwargs)

    def resolve(spec, wheelhouse):
        calls.append(("resolve", spec, wheelhouse))
        return Path(spec)

    def unpack(wheel, dest):
        calls.append(("unpack", wheel, dest.name))
        return dest

    def measure(site, work, corpus, corpus_dir):
        calls.append(("measure", site.name, work.name, corpus, corpus_dir))
        return SIDES[work.name]

    def since(ref):
        calls.append(("declared_since", ref))
        return declared

    monkeypatch.setattr(wheel_diff.tempfile, "TemporaryDirectory", scratch)
    for name, stand_in in (("resolve", resolve), ("unpack", unpack), ("measure", measure),
                           ("declared_since", since)):
        monkeypatch.setattr(wheel_diff, name, stand_in)
    return calls, made


def _sides_measured(corpus: str, corpus_dir) -> list:
    return [("resolve", "b.whl", Path("house")), ("resolve", "c.whl", Path("house")),
            ("unpack", Path("b.whl"), "base-site"),
            ("measure", "base-site", "base", corpus, corpus_dir),
            ("unpack", Path("c.whl"), "candidate-site"),
            ("measure", "candidate-site", "candidate", corpus, corpus_dir)]


DIFF = ["diff", "--base-wheel", "b.whl", "--candidate-wheel", "c.whl", "--wheelhouse", "house"]


def test_diff_hands_each_piece_what_the_command_line_names(monkeypatch, tmp_path, capsys):
    """The scratch directory ignores cleanup errors: a file an unpacked wheel left
    open on Windows must not turn a finished comparison into a failure."""
    calls, made = _stand_ins(monkeypatch, {"CRAP score"})
    out = tmp_path / "out"

    code = wheel_diff.main([*DIFF, "--corpus", "full", "--corpus-dir", "members", "--out",
                            str(out), "--expect-calcs", "CRAP score", "--declared-since",
                            "v0.9.0"])

    assert (code, capsys.readouterr().out.splitlines()) == (0, MOVED_LINES)
    assert calls == _sides_measured("full", Path("members")) + [("declared_since", "v0.9.0")]
    assert made == [{"prefix": "crapkit-wheel-diff-", "ignore_cleanup_errors": True}]


def test_diff_names_an_undeclared_and_an_unexpected_move(monkeypatch, capsys):
    _stand_ins(monkeypatch, set())

    code = wheel_diff.main([*DIFF, "--expect-calcs", "nloc", "--declared-since", "v0.9.0"])

    assert (code, capsys.readouterr().out.splitlines()) == (1, MOVED_LINES + [
        "the wheels moved ['CRAP score'], the change declares ['nloc']",
        "no CHANGES row since the previous tag names ['CRAP score']"])


def test_diff_asks_no_ref_it_was_not_given(monkeypatch, tmp_path, capsys):
    calls, _ = _stand_ins(monkeypatch, set())
    monkeypatch.chdir(tmp_path)

    code = wheel_diff.main(DIFF)

    assert (code, capsys.readouterr().out.splitlines()) == (0, MOVED_LINES)
    assert calls == _sides_measured("small", None)
    assert list(tmp_path.iterdir()) == []


def test_out_holds_both_sides_the_map_and_the_summary(tmp_path):
    rows = wheel_diff.diff_exports(SIDES["base"], SIDES["candidate"])

    wheel_diff.write_out(tmp_path, (SIDES["base"], SIDES["candidate"]), rows)

    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.*")) == [
        "base/one/inventory.tsv", "candidate/one/inventory.tsv", "moved.tsv", "summary.json"]
    assert (tmp_path / "candidate" / "one" / "inventory.tsv").read_bytes() == (
        SIDES["candidate"]["one/inventory.tsv"].encode("utf-8"))
    assert (tmp_path / "moved.tsv").read_bytes() == (
        b"export\tpath\tlong_name\toccurrence\tcolumn\told\tnew\tcalc\n"
        b"one/inventory.tsv\tsrc/a.py\tg( x )\t1\tcrap\t12.0\t12.5\tCRAP score\n")
    assert (tmp_path / "summary.json").read_text(encoding="utf-8") == (
        '{\n "moved_rows": 1,\n "calcs": {\n  "CRAP score": 1\n }\n}\n')


def test_a_side_that_cannot_run_exits_3_with_one_line(capsys):
    code = wheel_diff.main(["diff", "--base-wheel", "no-such.whl", "--candidate-wheel",
                            "no-such.whl"])

    assert code == wheel_diff.EXIT_INFRA == 3
    assert capsys.readouterr() == ("", "wheel_diff.py: no wheel or hand-off at no-such.whl\n")


# --- measuring --------------------------------------------------------------------------

def _recorder(calls: list, kind: str, answer):
    def record(*args):
        calls.append((kind, *args))
        return answer
    return record


def test_measure_runs_the_small_corpus_or_each_full_member(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(wheel_diff, "measure_tree", _recorder(calls, "tree", {"t": "1"}))
    monkeypatch.setattr(wheel_diff, "measure_full", _recorder(calls, "full", {"f": "2"}))
    monkeypatch.setattr(wheel_diff.corpus_run, "date_now", lambda: 1234)
    site, work, members = tmp_path / "site", tmp_path / "work", tmp_path / "members"

    assert wheel_diff.measure(site, work, "small", None) == {"t": "1"}
    assert wheel_diff.measure(site, work, "full", members) == {"f": "2"}
    assert calls == [("tree", wheel_diff.corpus_run.SMALL, site, work, wheel_diff.SMALL_COMMANDS,
                      1234), ("full", members, site, work, 1234)]
    with pytest.raises(wheel_diff.WheelDiffError,
                       match=r"^--corpus full needs --corpus-dir \(corpus\.py build or fetch\)$"):
        wheel_diff.measure(site, work, "full", None)


def test_the_full_corpus_measures_each_member_in_its_own_directory(monkeypatch, tmp_path):
    calls = []
    for member in ("one", "two"):
        (tmp_path / "corpus" / member).mkdir(parents=True)
        (tmp_path / "corpus" / member / "crapkit.toml").write_bytes(b"")
    (tmp_path / "corpus" / "loose").mkdir()
    monkeypatch.setattr(wheel_diff, "measure_tree", _recorder(calls, "tree", {"inventory.tsv": "x"}))
    site, work = tmp_path / "site", tmp_path / "work"

    found = wheel_diff.measure_full(tmp_path / "corpus", site, work, 99)

    assert found == {"one/inventory.tsv": "x", "two/inventory.tsv": "x"}
    assert calls == [("tree", tmp_path / "corpus" / member, site, work / member,
                      wheel_diff.FULL_COMMANDS, 99) for member in ("one", "two")]


def test_a_tree_is_committed_under_repo_and_exported_under_out(monkeypatch, tmp_path):
    calls = []
    built = repos.Built(tmp_path / "top", tmp_path / "top")
    monkeypatch.setattr(wheel_diff.repos, "build", _recorder(calls, "build", built))
    monkeypatch.setattr(wheel_diff.repos, "tree_spec", _recorder(calls, "spec", "the spec"))
    monkeypatch.setattr(wheel_diff, "run_commands", _recorder(calls, "run", {"a": "b"}))
    work = tmp_path / "two" / "deep"

    assert wheel_diff.measure_tree(tmp_path / "tree", tmp_path / "site", work, "cmds", 7) == {
        "a": "b"}
    assert calls == [("spec", tmp_path / "tree"), ("build", "the spec", work / "repo"),
                     ("run", built.root, tmp_path / "site", work / "out", "cmds", 7)]
    assert (work / "out").is_dir()


class _Result:
    def __init__(self, code: int, stderr: str = ""):
        self.code, self.stderr = code, stderr


def _driver(made: list, code: int, stderr: str = ""):
    class Driver:
        def __init__(self, root, **kwargs):
            made.append((root, kwargs))

        def run(self, *argv):
            made.append(argv)
            Path(argv[-1]).write_bytes(b"row\n")
            return _Result(code, stderr)
    return Driver


def test_each_command_runs_on_the_side_s_site_at_the_frozen_clock(monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(wheel_diff.drive, "Driver", _driver(made, 0))
    site = tmp_path / "site"

    found = wheel_diff.run_commands(tmp_path, site, tmp_path, wheel_diff.FULL_COMMANDS, 42)

    assert found == {"inventory.tsv": "row\n"}
    assert made == [(tmp_path, {"date_now": 42, "spawn": True, "env": {"PYTHONPATH": str(site)}}),
                    ("inventory", "--export", f"{tmp_path.as_posix()}/inventory.tsv")]


def test_a_command_that_fails_names_itself_the_site_and_its_stderr_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(wheel_diff.drive, "Driver", _driver([], 2, "a" * 100 + "b" * 800))

    with pytest.raises(wheel_diff.WheelDiffError) as refused:
        wheel_diff.run_commands(tmp_path, tmp_path / "site", tmp_path, wheel_diff.FULL_COMMANDS, 1)

    assert str(refused.value) == ("crapkit inventory --export {out}/inventory.tsv exited 2 under "
                                  "site: " + "b" * 800)


def test_a_wheel_whose_python_prints_nothing_resolves_to_a_question_mark(monkeypatch, tmp_path):
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("crapkit/__init__.py", "")
    monkeypatch.setattr(wheel_diff.subprocess, "run",
                        lambda *args, **kwargs: wheel_diff.subprocess.CompletedProcess(args, 0, "", ""))

    with pytest.raises(wheel_diff.WheelDiffError) as refused:
        wheel_diff.unpack(wheel, tmp_path / "site")

    assert str(refused.value) == f"w.whl: import crapkit resolved to ?, not {tmp_path / 'site'}"


# --- sides --------------------------------------------------------------------------------

def test_a_side_is_a_cached_release_a_fetched_one_a_hand_off_or_a_wheel(monkeypatch, tmp_path):
    house = tmp_path / "house"
    house.mkdir()
    kept = house / "crapkit-0.9.0-py3-none-any.whl"
    kept.write_bytes(b"")
    fetched = []
    monkeypatch.setattr(wheel_diff, "download",
                        lambda version, wheelhouse: fetched.append((version, wheelhouse))
                        or wheelhouse / "fetched.whl")
    hand = tmp_path / "hand"
    hand.mkdir()
    (hand / "crapkit-1.0.0-py3-none-any.whl").write_bytes(b"")

    assert wheel_diff.resolve("crapkit==0.9.0", house) == kept
    assert wheel_diff.resolve("crapkit==0.9.1", house) == house / "fetched.whl"
    assert fetched == [("0.9.1", house)]
    assert wheel_diff.resolve(str(hand), house) == hand / "crapkit-1.0.0-py3-none-any.whl"
    assert wheel_diff.resolve(str(kept), house) == kept
    with pytest.raises(wheel_diff.WheelDiffError, match=r"^no wheel or hand-off at gone\.whl$"):
        wheel_diff.resolve("gone.whl", house)


def test_the_cache_answers_with_the_first_wheel_of_that_version(tmp_path):
    for name in ("crapkit-0.9.0-py3-none-any.whl", "crapkit-0.9.0-py2.py3-none-any.whl",
                 "crapkit-0.9.01-py3-none-any.whl"):
        (tmp_path / name).write_bytes(b"")

    assert wheel_diff.cached("0.9.0", tmp_path) == tmp_path / "crapkit-0.9.0-py2.py3-none-any.whl"
    assert wheel_diff.cached("0.8.0", tmp_path) is None


def test_a_hand_off_s_skip_names_its_side(tmp_path):
    side = tmp_path / "candidate"
    side.mkdir()
    (side / "failure.json").write_text(json.dumps({"phase": "build", "error": "no sdist"}),
                                       encoding="utf-8")

    assert wheel_diff.hand_off(side) == wheel_diff.Skip("candidate",
                                                        "stopped at build: no sdist")


def _release(version: str, *kinds: str) -> dict:
    return {"urls": [{"packagetype": kind, "filename": f"crapkit-{version}-py3-none-any.whl",
                      "url": f"https://files.invalid/{version}.whl",
                      "digests": {"sha256": hashlib.sha256(b"wheel " + version.encode()).hexdigest()},
                      "upload_time_iso_8601": "2026-09-23T20:04:49Z"} for kind in kinds]}


def _pypi(monkeypatch, *versions: str) -> list:
    """PyPI's JSON and each wheel's bytes, served from memory; the URLs asked, in order."""
    asked = []
    answers = {wheel_diff.PYPI.format(version=v): json.dumps(_release(v, "bdist_wheel")).encode()
               for v in versions}
    answers.update({f"https://files.invalid/{v}.whl": b"wheel " + v.encode() for v in versions})
    monkeypatch.setattr(wheel_diff, "_fetch", lambda url: asked.append(url) or answers[url])
    return asked


def test_a_release_is_fetched_once_into_a_wheelhouse_made_on_demand(monkeypatch, tmp_path):
    asked = _pypi(monkeypatch, "9.9.8", "9.9.9")
    house = tmp_path / "cache" / "wheelhouse"

    first = wheel_diff.download("9.9.9", house)
    again = wheel_diff.download("9.9.9", house)
    other = wheel_diff.download("9.9.8", house)

    assert first == again == house / "crapkit-9.9.9-py3-none-any.whl"
    assert first.read_bytes() == b"wheel 9.9.9" and other.read_bytes() == b"wheel 9.9.8"
    assert asked == ["https://pypi.org/pypi/crapkit/9.9.9/json", "https://files.invalid/9.9.9.whl",
                     "https://pypi.org/pypi/crapkit/9.9.9/json",
                     "https://pypi.org/pypi/crapkit/9.9.8/json", "https://files.invalid/9.9.8.whl"]


def test_a_release_without_a_wheel_is_named(monkeypatch):
    monkeypatch.setattr(wheel_diff, "_fetch",
                        lambda url: json.dumps(_release("0.1.0", "sdist")).encode())

    with pytest.raises(wheel_diff.WheelDiffError, match=r"^PyPI lists no wheel for crapkit 0\.1\.0$"):
        wheel_diff.download("0.1.0", Path("unused"))
    with pytest.raises(wheel_diff.WheelDiffError, match=r"^PyPI lists no wheel for crapkit 0\.1\.0$"):
        wheel_diff.upload_date("0.1.0")
    with pytest.raises(wheel_diff.WheelDiffError, match=r"^PyPI lists no wheel for crapkit 0\.2\.0$"):
        wheel_diff._wheel_entry({}, "0.2.0")


def test_the_upload_date_asks_for_that_release(monkeypatch):
    asked = _pypi(monkeypatch, "0.9.0")

    assert wheel_diff.upload_date("0.9.0") == "2026-09-23"
    assert asked == ["https://pypi.org/pypi/crapkit/0.9.0/json"]


class _Answer:
    def __enter__(self):
        return self

    def __exit__(self, *failure):
        return False

    def read(self) -> bytes:
        return b"body"


def test_a_fetch_waits_a_minute_and_a_failed_one_is_an_infra_failure(monkeypatch):
    asked = []
    monkeypatch.setattr(wheel_diff.urllib.request, "urlopen",
                        lambda url, timeout: asked.append((url, timeout)) or _Answer())

    assert wheel_diff._fetch("https://pypi.invalid/x") == b"body"
    assert asked == [("https://pypi.invalid/x", 60)]

    def offline(url, timeout):
        raise OSError("no route")
    monkeypatch.setattr(wheel_diff.urllib.request, "urlopen", offline)
    with pytest.raises(wheel_diff.WheelDiffError,
                       match=r"^fetching https://pypi\.invalid/x failed: no route$"):
        wheel_diff._fetch("https://pypi.invalid/x")


# --- declared since a ref -------------------------------------------------------------------

CHANGES_HEADER = "id\tdate\tkind\tcalcs\tanalysis_version\tlizard_version\tchangelog\treason\n"


@pytest.mark.process
def test_the_calcs_declared_since_a_ref_are_the_rows_it_lacks(tmp_path):
    old = CHANGES_HEADER + "C1\t2026-09-01\tfix\tnloc\t11\t1.24.0\t\twhy\n"
    root = repos.build(repos.Spec(steps=(repos.Commit(files={wheel_diff.CHANGES: old}),)),
                       tmp_path / "repo").root
    repos.git(root, "tag", "v1")
    (root / wheel_diff.CHANGES).write_bytes((
        old + "C2\t2026-09-02\tfix\tCRAP score; nloc ;\t11\t1.24.0\t\twhy\n"
        "C3\t2026-09-03\tnone\t\t11\t1.24.0\t\twhy\n"
        "C4\t2026-09-04\tfix\tRemedy label\nC5\tthree\tcells\n").encode("utf-8"))

    assert wheel_diff.declared_since("v1", root) == {"CRAP score", "nloc", "Remedy label"}
    assert wheel_diff.changes_calcs(wheel_diff.changes_text(root, "v1")) == {"C1": {"nloc"}}
    assert wheel_diff.changes_calcs(wheel_diff.changes_text(root, None)) == {
        "C1": {"nloc"}, "C2": {"CRAP score", "nloc"}, "C3": set(), "C4": {"Remedy label"}}
    assert wheel_diff.changes_text(root, "no-such-ref") == ""
    assert wheel_diff.changes_text(tmp_path, None) == ""


# --- the map's pieces ---------------------------------------------------------------------

def test_a_row_without_an_occurrence_keys_as_the_empty_occurrence():
    assert wheel_diff.keyed("path\tlong_name\toccurrence\tccn\nsrc/a.py\tf\n") == {
        ("src/a.py", "f", ""): {"path": "src/a.py", "long_name": "f"}}


def test_only_the_columns_both_sides_hold_are_compared():
    base = "path\tlong_name\toccurrence\tccn\textra\nsrc/a.py\tf\t1\t3\tx\n"
    candidate = "path\tlong_name\toccurrence\tccn\nsrc/a.py\tf\t1\t4\n"

    assert wheel_diff.moved("e.tsv", base, candidate) == [
        wheel_diff.Moved("e.tsv", "src/a.py", "f", "1", "ccn", "3", "4")]


def test_an_export_one_side_lacks_moves_every_row_it_holds():
    rows = wheel_diff.diff_exports({"a.tsv": _tsv([_BASE_ROW])}, {})

    assert rows == [wheel_diff.Moved("a.tsv", "src/a.py", "g( x )", "1", "row", "present",
                                     "absent")]


def test_an_export_only_the_candidate_holds_moves_every_row_it_holds():
    rows = wheel_diff.diff_exports({}, {"a.tsv": _tsv([_BASE_ROW])})

    assert rows == [wheel_diff.Moved("a.tsv", "src/a.py", "g( x )", "1", "row", "absent",
                                     "present")]


def test_the_rows_at_a_ref_are_read_from_the_repo_asked_about(tmp_path, monkeypatch):
    """git show runs in `repo`: run anywhere else, it reads another tree's rows."""
    changes = tmp_path / wheel_diff.CHANGES
    changes.parent.mkdir(parents=True)
    old = CHANGES_HEADER + "C1\t2026-09-01\tfix\tnloc\t11\t1.24.0\t\twhy\n"
    changes.write_bytes((old + "C2\t2026-09-02\tfix\tCRAP score\t11\t1.24.0\t\twhy\n").encode())
    asked = []

    def show(argv, cwd, capture_output):
        asked.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 0, old.encode(), b"")

    monkeypatch.setattr(wheel_diff, "subprocess", SimpleNamespace(run=show))

    assert wheel_diff.declared_since("v1", tmp_path) == {"CRAP score"}
    assert asked == [(["git", "show", f"v1:{wheel_diff.CHANGES}"], tmp_path)]


def test_the_parser_describes_the_tool_with_its_docstring_s_first_paragraph():
    description = wheel_diff._parser().description

    assert "\n\n" not in description
    assert wheel_diff.__doc__.startswith(description + "\n\n")


def _moves(count: int) -> list:
    return [wheel_diff.Moved("e.tsv", f"src/{n}.py", "f", "1", column, "1", "2")
            for n, column in zip(range(count), ["crap", "nloc", "crap"] * count)]


def test_the_counts_the_map_and_the_summary_lines():
    rows = _moves(11)

    assert wheel_diff.moved_calcs(_moves(3)) == {"CRAP score": 2, "nloc": 1}
    assert wheel_diff.moved_tsv(_moves(1)) == (
        "export\tpath\tlong_name\toccurrence\tcolumn\told\tnew\tcalc\n"
        "e.tsv\tsrc/0.py\tf\t1\tcrap\t1\t2\tCRAP score\n")
    lines = wheel_diff.summary_lines(rows)
    assert lines[:3] == ["11 value(s) moved", "  CRAP score: 7", "  nloc: 4"]
    assert lines[3:] == [f"  src/{n}.py f {rows[n].column}: 1 -> 2" for n in range(10)]
    assert wheel_diff.verdict(rows[:1], [None, "late"], " in x") == (
        1, ["1 value(s) moved in x", "  CRAP score: 1", "  src/0.py f crap: 1 -> 2", "late"])


def test_what_the_declared_calcs_are_checked_against():
    both = _moves(2)

    assert wheel_diff.expectation_problem(both, " CRAP score , nloc,") is None
    assert wheel_diff.expectation_problem(both, "") == (
        "the wheels moved ['CRAP score', 'nloc'], the change declares nothing")
    assert wheel_diff.undeclared_problem(both, {"nloc"}) == (
        "no CHANGES row since the previous tag names ['CRAP score']")
    assert wheel_diff.undeclared_problem(both, {"nloc", "CRAP score"}) is None
    assert wheel_diff.undeclared_problem(both, None) is None


# --- xplat on the command line ------------------------------------------------------------

def test_xplat_reads_its_receipts_from_the_command_line(tmp_path, capsys):
    text = _tsv([_BASE_ROW])
    paths = []
    for name, receipt in (("a", _receipt("linux", text)), ("b", _receipt("windows", text)),
                          ("c", {"os": "macos", "python": "3.13", "exports": {}})):
        paths.append(tmp_path / f"{name}.json")
        paths[-1].write_text(json.dumps(receipt), encoding="utf-8")

    agreed = wheel_diff.main(["xplat", str(paths[0]), str(paths[1])])
    said = capsys.readouterr().out
    apart = wheel_diff.main(["xplat", str(paths[0]), str(paths[2])])

    assert (agreed, said) == (0, "2 receipts agree on 1 export(s)\n")
    assert (apart, capsys.readouterr().out) == (
        1, "linux-3.12 and macos-3.13 noted different exports: ['small/scored.tsv']\n")


def test_an_export_note_without_text_is_not_an_export():
    noted = {"exports": {"a": {"sha256": "x"}, "b": {"sha256": "y", "text": "t"}, "c": "text"}}

    assert wheel_diff._exports(noted) == {"b": "t"}
