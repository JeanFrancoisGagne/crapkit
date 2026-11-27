"""The marks file owns its stamps on every publish.

The metric stamp says which rules produced the numbers; the key stamp says how
twin ordinals are counted. Every writer used to pick its own stamp, and most
took the running metric by omission, so a write that added no numbers, or added
numbers under one metric to marks another metric recorded, relabeled the whole
file and verify's stamp refusal went quiet. The rules now live in RatchetFile:

- a write that adds measured numbers signs the running per-scope stamp, and is
  refused when a marked scope's rules moved;
- a write that adds no measured number keeps every recorded stamp line, and a
  file it creates takes the running per-scope stamp;
- seed is the one write that takes the stamps of the run it read.
"""
import argparse
import re
import sys

from stamps import LIZARD, scoped, single_number

import pytest

from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.errors import ConfigError
from crapkit.override import record_override
from crapkit.ratchet import MetricStamp, RatchetEntry, dump_ratchet, load_ratchet, read_key_version, read_stamp
from crapkit.ratchetfile import RatchetFile
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation

NAME = "marks.tsv"
# What this crapkit measures: api is cc-only python, web is typescript under istanbul.
RUNNING = scoped(api=({"python": 13}, "cc-only"), web=({"typescript": 13}, {"istanbul": 1}))
# The run a seed reads, which a newer crapkit measured.
RUN = scoped(api=({"python": 14}, "cc-only"), web=({"typescript": 14}, {"istanbul": 1}))
# The lines 0.8.1 and an unreleased step before it wrote.
EQUAL = single_number(13, LIZARD)
OLDER = single_number(12, LIZARD)
# A per-scope file whose web scope a typescript 12 run signed.
SCOPED = scoped(api=({"python": 13}, "cc-only"), web=({"typescript": 12}, {"istanbul": 1}))
MARK = RatchetEntry("api/a.py", "f( )", 50.0)
WEB_MARK = RatchetEntry("web/c.ts", "h( )", 30.0)
GRANTED = RatchetEntry("api/b.py", "g( )", 8.0)
VIOLATION = GateViolation("api/b.py", "g( )", 3, 8, 0.0, 8.0, "decompose")
API = frozenset({"api"})

STATES = {"unstamped": MetricStamp.NONE, "single-number equal": EQUAL,
          "single-number older": OLDER, "per-scope": SCOPED}


def marks_file(tmp_path, stamp: MetricStamp, entries=(MARK,), keys: int = 1):
    path = tmp_path / NAME
    path.write_text(dump_ratchet(list(entries), stamp=stamp, key_version=keys, file_name=NAME),
                    encoding="utf-8")
    return path


def read(tmp_path, state: str) -> RatchetFile:
    """The marks file in `state`; `none` is a file that does not exist yet."""
    if state == "none":
        return RatchetFile.read(tmp_path / NAME)
    return RatchetFile.read(marks_file(tmp_path, STATES[state]))


def stamp_lines(text: str) -> list[str]:
    """The comment lines above the header: the stamp, the key stamp, the scope lines."""
    return [line for line in text.splitlines() if line.startswith("#")]


# --- the writer table, at RatchetFile ------------------------------------------------
# Each writer against each file state gives the stamp docs/ratchet.md's writer table
# names: the running per-scope stamp, the recorded lines kept verbatim, the run's
# stamp, or a refusal.

WRITERS = {
    # verify's tighten and `verify --override`: numbers this crapkit measured.
    "measured": lambda saved: saved.measured([MARK, GRANTED], RUNNING, API),
    # hook-precommit's grant and prune: no measured number.
    "kept": lambda saved: saved.kept([MARK, GRANTED], new_file_stamp=RUNNING),
    # move: no number at all, and the command refuses a file that is not there.
    "move": lambda saved: saved.kept([MARK._replace(path="api/z.py")]),
    "seed": lambda saved: saved.reseeded([MARK], RUN),
}

SIGNED, KEPT, REFUSED = "the running per-scope stamp", "kept verbatim", "refused"
TABLE = {
    ("measured", "none"): SIGNED, ("measured", "unstamped"): SIGNED,
    ("measured", "single-number equal"): SIGNED, ("measured", "single-number older"): REFUSED,
    ("measured", "per-scope"): SIGNED,
    ("kept", "none"): SIGNED, ("kept", "unstamped"): KEPT, ("kept", "single-number equal"): KEPT,
    ("kept", "single-number older"): KEPT, ("kept", "per-scope"): KEPT,
    ("move", "unstamped"): KEPT, ("move", "single-number equal"): KEPT,
    ("move", "single-number older"): KEPT, ("move", "per-scope"): KEPT,
    ("seed", "none"): RUN, ("seed", "unstamped"): RUN, ("seed", "single-number equal"): RUN,
    ("seed", "single-number older"): RUN, ("seed", "per-scope"): RUN,
}


@pytest.mark.parametrize(("writer", "state"), list(TABLE), ids=[f"{w}-{s}" for w, s in TABLE])
def test_each_writer_signs_the_stamp_the_table_names(tmp_path, writer, state):
    saved = read(tmp_path, state)
    expected = TABLE[writer, state]

    if expected == REFUSED:
        with pytest.raises(ConfigError, match=r"recorded under \[crapkit-analysis=12 lizard=1.24.0\]"):
            WRITERS[writer](saved)
        return
    text = WRITERS[writer](saved)

    if expected == KEPT:
        assert stamp_lines(text) == stamp_lines(saved.text), text
        return
    assert read_stamp(text) == (RUNNING if expected == SIGNED else expected), text
    assert text.splitlines()[0] == f"# crapkit-stamp=2 lizard={LIZARD}: {NAME} holds one stamp per " \
                                   "scope, which crapkit 0.9.0 or later reads"


# --- no writer writes a new single-number line -------------------------------------
# verify's tighten and `verify --override` write `measured`, hook-precommit's grant
# and prune `kept`, move a `kept` write with no new file stamp, seed `reseeded`, and
# the merge driver its own merged stamp. On a single-number file each leaves the
# per-scope stamp, a refusal, or the line it read written back as it was.

_SINGLE_NUMBER_LINE = re.compile(r"^# crapkit-analysis=[^\r\n]*", re.M)


def _single_number_lines(*texts: str) -> set[str]:
    return {line for text in texts for line in _SINGLE_NUMBER_LINE.findall(text)}


@pytest.mark.parametrize("state", ["single-number equal", "single-number older"])
@pytest.mark.parametrize("writer", list(WRITERS))
def test_no_writer_writes_a_new_single_number_line(tmp_path, writer, state):
    saved = read(tmp_path, state)

    try:
        text = WRITERS[writer](saved)
    except ConfigError as refused:
        assert "recorded under" in str(refused)
        return

    assert _single_number_lines(text) <= _single_number_lines(saved.text), text
    assert _single_number_lines(text) or read_stamp(text) in (RUNNING, RUN), text


def _merged(tmp_path, base: MetricStamp, ours: MetricStamp, theirs: MetricStamp) -> tuple[int | str, str]:
    """The merge driver's exit and OURS after it, or the refusal it raised."""
    sides = {"base": (base, 50.0), "ours": (ours, 40.0), "theirs": (theirs, 30.0)}
    files = []
    for side, (stamp, crap) in sides.items():
        path = tmp_path / f"{side}.tsv"
        path.write_text(dump_ratchet([RatchetEntry("api/a.py", "f( )", crap)], stamp=stamp, key_version=1,
                                     file_name=NAME), encoding="utf-8", newline="\n")
        files.append(str(path))
    try:
        code = cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))
    except ConfigError as refused:
        return str(refused), ""
    return code, (tmp_path / "ours.tsv").read_text(encoding="utf-8")


@pytest.mark.parametrize(("base", "ours", "theirs"), [
    pytest.param(OLDER, OLDER, EQUAL, id="theirs-moved-the-single-number"),
    pytest.param(OLDER, EQUAL, OLDER, id="ours-moved-the-single-number"),
    pytest.param(EQUAL, EQUAL, RUNNING, id="theirs-converted-to-per-scope"),
    pytest.param(EQUAL, OLDER, RUNNING, id="both-moved"),
])
def test_the_merge_driver_writes_no_new_single_number_line(tmp_path, base, ours, theirs):
    code, text = _merged(tmp_path, base, ours, theirs)

    if text == "":
        assert str(code).startswith("ratchet merge refused"), code
        return
    assert code == 0
    assert _single_number_lines(text) <= _single_number_lines(f"# {ours}\n", f"# {theirs}\n"), text
    assert read_stamp(text) in (ours, theirs) or read_stamp(text).scopes, text


@pytest.mark.parametrize("cut", ["ours", "theirs"])
def test_the_merge_driver_names_a_sides_cut_scope_stamp_line_and_keeps_ours(tmp_path, cut):
    """Compared as a stamp, the cut line refused the merge as two stamps that do
    not compare and sent the user to a reseed that refuses the same line. The
    driver now names the line, as seed and prune do."""
    files = {}
    for side, crap in (("base", 50.0), ("ours", 40.0), ("theirs", 30.0)):
        text = dump_ratchet([RatchetEntry("api/a.py", "f( )", crap)], stamp=RUNNING, key_version=1,
                            file_name=NAME)
        if side == cut:
            text = text[:text.index("#scope\tapi\t") + len("#scope\tapi\t")]
        files[side] = tmp_path / f"{side}.tsv"
        files[side].write_text(text, encoding="utf-8", newline="\n")
    before = files["ours"].read_bytes()

    with pytest.raises(ConfigError) as refused:
        cmd_ratchet(argparse.Namespace(action="merge", files=[str(path) for path in files.values()],
                                       repo=None, baseline=None))

    assert str(refused.value) == (f"unreadable ratchet file {files[cut]}: ratchet line 3 has an "
                                  "unreadable scope stamp: '#scope\\tapi\\t'")
    assert files["ours"].read_bytes() == before


def test_a_measured_write_over_a_marked_scope_whose_rules_moved_is_refused(tmp_path):
    """web's marks were signed under typescript 12 and this crapkit runs 13."""
    saved = RatchetFile.read(marks_file(tmp_path, SCOPED, entries=(MARK, WEB_MARK)))

    with pytest.raises(ConfigError, match=r"1 scope\(s\): web \(typescript analysis 12 to 13\)"):
        saved.measured([MARK, WEB_MARK], RUNNING, frozenset({"api", "web"}))


def test_a_single_number_file_whose_inferred_mode_flips_is_not_converted(tmp_path):
    """0.8.x read no `lcov` parser, so a single-number file leaves a scope an
    lcov lane measures inferred as cc-only: the flip refuses the conversion."""
    saved = RatchetFile.read(marks_file(tmp_path, EQUAL, entries=(WEB_MARK,)))
    flipped = scoped(web=({"typescript": 13}, {"lcov": 1}))

    with pytest.raises(ConfigError, match=r"web \(coverage cc-only to lcov=1\)"):
        saved.measured([WEB_MARK], flipped, frozenset({"web"}))


def test_a_measured_write_keeps_the_key_format_it_read(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, EQUAL, keys=0))

    assert read_key_version(saved.measured([MARK], RUNNING, API)) == 0
    assert read_key_version(saved.measured([MARK], RUNNING, API, keys=1)) == 1


def test_a_kept_write_on_a_single_number_file_writes_back_the_line_it_read(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, OLDER))

    text = saved.kept([MARK], new_file_stamp=RUNNING)

    assert text.splitlines()[0] == f"# {OLDER}"
    assert "#scope" not in text


def test_a_measured_or_kept_write_that_names_no_stamp_writes_nothing(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, EQUAL))

    with pytest.raises(ConfigError, match="names no metric"):
        saved.measured([MARK], MetricStamp.NONE, API)
    with pytest.raises(ConfigError, match="names no metric"):
        saved.reseeded([MARK], MetricStamp.NONE)


def test_the_stamp_conflict_is_the_one_verify_refuses_with(tmp_path):
    saved = RatchetFile.read(marks_file(tmp_path, OLDER))

    assert saved.stamp_conflict(OLDER) is None
    assert "re-baseline with" in saved.stamp_conflict(EQUAL).refusal(saved.path.name)
    assert RatchetFile.read(tmp_path / "absent.tsv").stamp_conflict(RUNNING) is None


# --- the override grant ----------------------------------------------------------

def alert(tmp_path):
    log = tmp_path / "alert.log"
    return (f'"{sys.executable}" -c "import sys,pathlib; '
            f'pathlib.Path(r\'{log}\').open(\'a\').write(sys.stdin.read())"'), log


def grant(tmp_path, store, **kwargs):
    command, log = alert(tmp_path)
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file=NAME,
                    alert_command=command, violations=[VIOLATION], reason="hotfix", **kwargs)
    return run_id, log


def nothing_granted(tmp_path, path, before: bytes, store) -> bool:
    return (path.read_bytes() == before and not (tmp_path / "alert.log").exists()
            and all(not store.read_overrides(run["id"]) for run in store.list_runs()))


def test_a_measured_grant_over_a_marked_scope_whose_rules_moved_grants_nothing(tmp_path):
    """Refused before the alert and the audit row: three records or nothing."""
    path = marks_file(tmp_path, SCOPED, entries=(MARK, WEB_MARK))
    before = path.read_bytes()
    store = SnapshotStore(tmp_path / "db.sqlite")

    with pytest.raises(ConfigError, match="web \\(typescript analysis 12 to 13\\)"):
        grant(tmp_path, store, metric=RUNNING, marked_scopes=frozenset({"api", "web"}))

    assert nothing_granted(tmp_path, path, before, store)


def test_a_measured_grant_on_a_single_number_file_converts_it(tmp_path):
    path = marks_file(tmp_path, EQUAL)
    store = SnapshotStore(tmp_path / "db.sqlite")

    grant(tmp_path, store, metric=RUNNING, marked_scopes=API)

    text = path.read_text(encoding="utf-8")
    assert read_stamp(text) == RUNNING
    assert RatchetEntry("api/b.py", "g( )", 8.0) in load_ratchet(text)


def test_a_measured_grant_on_an_older_single_number_file_grants_nothing(tmp_path):
    path = marks_file(tmp_path, OLDER)
    before = path.read_bytes()
    store = SnapshotStore(tmp_path / "db.sqlite")

    with pytest.raises(ConfigError, match="recorded under"):
        grant(tmp_path, store, metric=RUNNING, marked_scopes=API)

    assert nothing_granted(tmp_path, path, before, store)


@pytest.mark.parametrize("state", ["per-scope", "single-number older", "unstamped"])
def test_the_hooks_grant_keeps_every_recorded_stamp_line(tmp_path, state):
    """The hook's rule: its numbers come from ccn alone and compare no mark."""
    path = marks_file(tmp_path, STATES[state])
    before = stamp_lines(path.read_text(encoding="utf-8"))
    store = SnapshotStore(tmp_path / "db.sqlite")

    grant(tmp_path, store, raise_marks=False, metric=RUNNING)

    text = path.read_text(encoding="utf-8")
    assert stamp_lines(text) == before
    assert RatchetEntry("api/b.py", "g( )", 8.0) in load_ratchet(text)


def test_a_hook_grant_that_creates_the_file_signs_the_running_stamp(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")

    grant(tmp_path, store, raise_marks=False, metric=RUNNING)

    assert read_stamp((tmp_path / NAME).read_text(encoding="utf-8")) == RUNNING


PER_SCOPE_KEPT = dump_ratchet([RatchetEntry("api/b.py", "g( )", 12.0)], stamp=SCOPED, key_version=1,
                              file_name=NAME)


@pytest.mark.parametrize("encode", [
    pytest.param(lambda text: b"\xff\xfe" + text.encode("utf-16-le"), id="utf-16-le-bom"),
    pytest.param(lambda text: text.replace("\n", "\r\n").encode("utf-8"), id="crlf"),
])
def test_a_hook_grant_that_keeps_a_mark_leaves_a_per_scope_file_byte_identical(tmp_path, encode):
    """The byte-identity cases test_override.py holds for a single-number file,
    on a per-scope file: the grant adds no mark, so nothing is rewritten."""
    before = encode(PER_SCOPE_KEPT)
    (tmp_path / NAME).write_bytes(before)
    store = SnapshotStore(tmp_path / "db.sqlite")

    run_id, _ = grant(tmp_path, store, raise_marks=False, metric=RUNNING, key_version=1)

    assert (tmp_path / NAME).read_bytes() == before
    assert store.read_overrides(run_id) == [("api/b.py", "g( )", 8.0, "hotfix")]


def test_a_measured_grant_that_names_no_metric_grants_nothing(tmp_path):
    """Numbers that may raise a mark need the metric that produced them."""
    path = marks_file(tmp_path, EQUAL)
    before = path.read_bytes()
    store = SnapshotStore(tmp_path / "db.sqlite")

    with pytest.raises(ConfigError, match="names no metric"):
        grant(tmp_path, store, metric=MetricStamp.NONE, marked_scopes=API)

    assert nothing_granted(tmp_path, path, before, store)


def test_a_grant_that_leaves_out_its_metric_is_a_type_error(tmp_path):
    with pytest.raises(TypeError, match="metric"):
        record_override(store=None, run_id=1, root=tmp_path, ratchet_file=NAME,
                        alert_command="", violations=[], reason="")
