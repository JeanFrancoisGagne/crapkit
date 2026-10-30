"""The metric stamp: marks carry the analysis version and the lizard that measured them.

Pure seam. Without it a scoring change leaves 40k marks in place and every later
verify silently compares new scores against numbers the old rules produced.
"""
import argparse
import ast
import codecs
import copy
from pathlib import Path
import pickle
import re
import subprocess
import sys

import pytest

from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.errors import ConfigError
from crapkit.ratchet import (MetricStamp, RatchetEntry, ScopeStamp, ScopeStamps, SingleNumberStamp,
                             UnreadableStamp, dump_ratchet, load_ratchet, metric_version,
                             read_key_version, read_ratchet, read_stamp, run_stamp, stamp_conflict,
                             stamp_text)
from crapkit.ratchetfile import RatchetFile
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

STAMP = "crapkit-analysis=9 lizard=0.1.2"
LEGACY = "path\tlong_name\tcrap\nsrc/a.ts\tf( )\t30.0000\n"


def test_stamp_text_is_the_committed_wire_format():
    assert stamp_text(3, "1.24.0") == "crapkit-analysis=3 lizard=1.24.0"


def test_dump_writes_the_stamp_above_the_header():
    lines = dump_ratchet([RatchetEntry("src/a.ts", "f( )", 30.0)], stamp=STAMP).splitlines()
    assert lines[0] == "# crapkit-analysis=9 lizard=0.1.2"
    assert lines[1] == "path\tlong_name\tcrap"
    assert lines[2] == "src/a.ts\tf( )\t30.0000"


def test_dump_names_no_stamp_by_default():
    """A default stamped by omission, and a write that added no numbers relabeled
    marks another metric recorded; the marks file's stamp rules choose it now."""
    with pytest.raises(TypeError):
        dump_ratchet([RatchetEntry("src/a.ts", "f( )", 30.0)])


def test_an_empty_stamp_writes_no_comment_line():
    lines = dump_ratchet([RatchetEntry("src/a.ts", "f( )", 30.0)], stamp="").splitlines()
    assert lines[0] == "path\tlong_name\tcrap"


def test_readers_skip_comment_lines():
    text = f"# {STAMP}\n{LEGACY}"
    assert load_ratchet(text) == [RatchetEntry("src/a.ts", "f( )", 30.0)]


def test_dump_load_dump_is_a_fixed_point_with_a_stamp():
    text = dump_ratchet([RatchetEntry("src/a.ts", "f( )", 30.0)], stamp=STAMP)
    assert dump_ratchet(load_ratchet(text), stamp=read_stamp(text)) == text


def test_read_stamp_of_a_file_written_before_stamping_is_empty():
    assert read_stamp(LEGACY) == ""


def test_read_stamp_returns_the_version_without_its_comment_marker():
    assert read_stamp(f"# {STAMP}\n{LEGACY}") == STAMP


def test_an_unstamped_file_never_conflicts():
    assert stamp_conflict("", "crapkit-analysis=3 lizard=1.24.0") is None


def test_matching_stamps_do_not_conflict():
    assert stamp_conflict("crapkit-analysis=3 lizard=1.24.0",
                          "crapkit-analysis=3 lizard=1.24.0") is None


def test_a_differing_stamp_names_both_versions_and_the_fix():
    msg = stamp_conflict("crapkit-analysis=2 lizard=1.17.10", "crapkit-analysis=3 lizard=1.24.0")
    assert "crapkit-analysis=2 lizard=1.17.10" in msg
    assert "crapkit-analysis=3 lizard=1.24.0" in msg
    assert "ratchet seed" in msg


def test_a_lizard_bump_alone_conflicts():
    assert stamp_conflict("crapkit-analysis=3 lizard=1.17.10",
                          "crapkit-analysis=3 lizard=1.24.0") is not None


def test_marks_stamped_by_0_8_0_are_refused_until_one_reseed():
    """A change that can move an existing function's ccn, name or coverage
    on the same tree bumps the analysis version. 0.8.1's coverage.py reader
    refuses a report without start_line and scores a nested function on its
    own region; it reads a UTF-16 source that 0.8.0 scored as empty, and keys
    an identifier holding one of the five bytes cp1252 leaves undefined by its
    name, where it read `U+FFFD( x )` or C's `if( x)` at ccn 1 took the
    function's place, and its nesting column reads the cognitive pass in every
    language. So a marks file stamped under 0.8.0's version 11 is not
    comparable and re-seeds once. 0.8.1's one bump lands on 13 (12 was an
    unreleased step). Kept at 11, verify compared the new scores against the
    old numbers."""
    import lizard

    from crapkit.analyze import ANALYSIS_VERSION
    from crapkit.ratchet import metric_version

    refusal = stamp_conflict(stamp_text(11, lizard.version), metric_version())

    assert ANALYSIS_VERSION == 13
    assert refusal is not None and "ratchet seed" in refusal, refusal


# --- the typed stamp ------------------------------------------------------------

THIRTEEN = "# crapkit-analysis=13 lizard=1.24.0"
ROWS = ["path\tlong_name\tcrap", "src/a.py\tf( )\t40.0000", "src/b.ts\tg( x )\t12.5000"]


def _spelled(text: str, spelling: str) -> bytes:
    if spelling == "crlf":
        return text.replace("\n", "\r\n").encode("utf-8")
    if spelling == "bom":
        return codecs.BOM_UTF8 + text.encode("utf-8")
    if spelling == "utf-16":
        return codecs.BOM_UTF16_LE + text.encode("utf-16-le")
    return text.encode("utf-8")


@pytest.mark.parametrize("keys", [False, True], ids=["no-key-stamp", "key-stamp"])
@pytest.mark.parametrize("spelling", ["lf", "bom", "crlf", "utf-16"])
@pytest.mark.parametrize("stamp", [None, THIRTEEN], ids=["unstamped", "stamped"])
def test_every_committed_spelling_round_trips_byte_for_byte(tmp_path, stamp, spelling, keys):
    """A write that adds no number renders the stamp it read back as the same
    line, so the file on disk keeps every byte, endings and encoding included."""
    lines = [stamp] * bool(stamp) + ["# crapkit-keys=1"] * keys + ROWS
    data = _spelled("\n".join(lines) + "\n", spelling)
    path = tmp_path / "crapkit-ratchet.tsv"
    path.write_bytes(data)
    saved = RatchetFile.read(path)
    text = saved.text.replace("\r\n", "\n")

    read = read_stamp(text)
    rendered = dump_ratchet(load_ratchet(text), stamp=read, key_version=read_key_version(text))

    assert isinstance(read, MetricStamp)
    assert read == (SingleNumberStamp(13, "1.24.0") if stamp else MetricStamp.NONE)
    assert rendered == text
    assert saved.publish(saved.kept(saved.entries)) is False
    assert path.read_bytes() == data


def test_no_stamp_is_falsy_and_renders_no_line():
    assert not MetricStamp.NONE
    assert (MetricStamp.NONE.render(), MetricStamp.NONE.analysis) == ("", None)
    assert read_stamp(LEGACY) is MetricStamp.NONE
    assert read_stamp(f"#\n{LEGACY}") is MetricStamp.NONE
    assert dump_ratchet([], stamp=MetricStamp.NONE) == "path\tlong_name\tcrap\n"


@pytest.mark.parametrize("other, equal", [
    (SingleNumberStamp(13, "1.24.0"), True),
    (SingleNumberStamp(12, "1.24.0"), False),
    (SingleNumberStamp(13, "1.17.10"), False),
    (SingleNumberStamp(130, "1.24.0"), False),
])
def test_two_single_number_stamps_are_equal_exactly_when_both_numbers_are(other, equal):
    stamp = SingleNumberStamp(13, "1.24.0")

    assert (stamp == other, stamp != other) == (equal, not equal)
    assert (hash(stamp) == hash(other)) is equal


def test_a_single_number_stamp_holds_its_two_versions_and_renders_the_wire_text():
    stamp = read_stamp(f"{THIRTEEN}\n{LEGACY}")

    assert type(stamp) is SingleNumberStamp
    assert (stamp.analysis, stamp.lizard) == (13, "1.24.0")
    assert stamp.render() == "crapkit-analysis=13 lizard=1.24.0"
    assert type(stamp.render()) is str


def test_the_runs_and_the_running_metric_read_as_typed_stamps():
    import lizard

    from crapkit.analyze import ANALYSIS_VERSION

    stored = run_stamp({"analysis_version": 13, "lizard": "1.24.0"})
    assert (type(stored), stored) == (SingleNumberStamp, SingleNumberStamp(13, "1.24.0"))
    assert run_stamp({"analysis_version": 13}) is MetricStamp.NONE
    assert metric_version() == SingleNumberStamp(ANALYSIS_VERSION, lizard.version)
    assert type(metric_version()) is SingleNumberStamp


@pytest.mark.parametrize("line, analysis", [
    ("lizard 0.0.1 analysis 0", None),
    ("crapkit-analysis=013 lizard=1.24.0", 13),
    ("crapkit-analysis=13  lizard=1.24.0", 13),
    ("crapkit-analysis=12", 12),
    ("crapkit-analysis=12x lizard=1.24.0", None),
])
def test_a_stamp_crapkit_cannot_parse_keeps_its_line_and_the_number_it_opens_with(line, analysis):
    """It is not unstamped: verify and the merge driver refuse it by quoting it,
    and a write that adds no number writes it back as it was. The expression
    reader check still reads the analysis number the line opens with."""
    stamp = read_stamp(f"# {line}\n{LEGACY}")

    assert type(stamp) is UnreadableStamp and stamp
    assert (stamp.render(), stamp.analysis) == (line, analysis)
    assert stamp_conflict(stamp, metric_version()).startswith(
        f"ratchet marks were recorded under [{line}] but")
    assert dump_ratchet([], stamp=stamp).startswith(f"# {line}\n")


def test_a_stamp_survives_a_copy_and_a_pickle():
    stamp = SingleNumberStamp(13, "1.24.0")

    for copied in (copy.deepcopy(stamp), pickle.loads(pickle.dumps(stamp))):
        assert type(copied) is SingleNumberStamp
        assert (copied, copied.analysis, copied.lizard) == (stamp, 13, "1.24.0")


def test_the_merge_driver_needs_no_analysis_stack(tmp_path, monkeypatch, capsys):
    """git runs the driver in a temp dir with no crapkit.toml, and the stamps it
    compares come from the three files, never from the running metric."""
    stamp = SingleNumberStamp(13, "1.24.0")
    files = []
    for side, crap in (("base", 50.0), ("ours", 40.0), ("theirs", 30.0)):
        path = tmp_path / f"{side}.tsv"
        path.write_text(dump_ratchet([RatchetEntry("src/a.py", "f( )", crap)], stamp=stamp,
                                     key_version=1), encoding="utf-8", newline="\n")
        files.append(str(path))
    for name in ("lizard", "crapkit.analyze", "crapkit.config"):
        monkeypatch.setitem(sys.modules, name, None)

    code = cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))

    assert (code, capsys.readouterr().out) == (0, "ratchet merge: 1 mark(s)\n")
    merged = (tmp_path / "ours.tsv").read_text(encoding="utf-8")
    assert read_stamp(merged) == stamp
    assert load_ratchet(merged) == [RatchetEntry("src/a.py", "f( )", 30.0)]


# --- the per-scope stamp ------------------------------------------------------------
# The form a 0.9.0 seed writes (metric-stamp-06): line 1 names the marks file and
# the release that reads it, then one `#scope` line per scope, sorted by name.
# Every reader takes it beside the single-number line; no command writes it yet.

MARKS = "crapkit-ratchet.tsv"
SCOPES = ScopeStamps("1.24.0", {
    "web": ScopeStamp({"typescript": 13, "tsx": 13}, {"istanbul": 1}),
    "api": ScopeStamp({"python": 13}, {"coveragepy": 1}),
    "tools": ScopeStamp({"python": 13}, "cc-only"),
})
HEAD = ("# crapkit-stamp=2 lizard=1.24.0: crapkit-ratchet.tsv holds one stamp per scope, "
        "which crapkit 0.9.0 or later reads")
SCOPE_LINES = ["#scope\tapi\tanalysis python=13; coverage coveragepy=1",
               "#scope\ttools\tanalysis python=13; coverage cc-only",
               "#scope\tweb\tanalysis tsx=13 typescript=13; coverage istanbul=1"]
MARKED = [RatchetEntry("src/a.py", "f( )", 40.0), RatchetEntry("src/b.ts", "g( x )", 12.5)]
LEGACY_SCOPE_MARK = "#scope\tg( )\t12.0000"


def _lines(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("keys", [1, 0])
def test_a_per_scope_stamp_writes_its_first_line_then_the_key_stamp_then_one_line_per_scope(keys):
    text = dump_ratchet(MARKED, stamp=SCOPES, key_version=keys, file_name=MARKS)

    assert text == _lines(HEAD, *[f"# crapkit-keys={keys}"] * bool(keys), *SCOPE_LINES, *ROWS)


@pytest.mark.parametrize("keys", [False, True], ids=["no-key-stamp", "key-stamp"])
@pytest.mark.parametrize("spelling", ["lf", "bom", "crlf", "utf-16"])
def test_a_per_scope_file_round_trips_byte_for_byte(tmp_path, spelling, keys):
    """A parsed per-scope stamp renders the lines it read, its first line's
    file name included, so a write that adds no number keeps every byte."""
    data = _spelled(_lines(HEAD, *["# crapkit-keys=1"] * keys, *SCOPE_LINES, *ROWS), spelling)
    path = tmp_path / MARKS
    path.write_bytes(data)
    saved = RatchetFile.read(path)
    text = saved.text.replace("\r\n", "\n")

    read = read_stamp(text)
    rendered = dump_ratchet(load_ratchet(text), stamp=read, key_version=read_key_version(text))

    assert (type(read), read, read.scopes, read.lizard) == (ScopeStamps, SCOPES, SCOPES.scopes, "1.24.0")
    assert rendered == text
    assert saved.publish(saved.kept(saved.entries)) is False
    assert path.read_bytes() == data


def test_scope_lines_come_out_sorted_by_name():
    text = _lines(HEAD, *reversed(SCOPE_LINES), *ROWS)

    assert dump_ratchet(load_ratchet(text), stamp=read_stamp(text)) == _lines(HEAD, *SCOPE_LINES, *ROWS)


@pytest.mark.parametrize("name, encoded", [
    ("café", False), ("#tools", False), ("we\tb", True), ("a b", True),
], ids=["non-ascii", "opens-with-hash", "tab", "line-separator"])
@pytest.mark.parametrize("spelling", ["lf", "utf-16"])
def test_a_scope_name_round_trips_whatever_it_holds(tmp_path, name, encoded, spelling):
    """A name holding a character records.encode_record escapes takes the
    record encoding, as a mark's name does, and reads back as itself."""
    stamp = ScopeStamps("1.24.0", {name: ScopeStamp({"python": 13}, "no-lane"), "api": SCOPES.scopes["api"]})
    data = _spelled(dump_ratchet(MARKED, stamp=stamp, key_version=1, file_name=MARKS), spelling)
    path = tmp_path / MARKS
    path.write_bytes(data)
    saved = RatchetFile.read(path)

    assert ("@crapkit-record-v1" in saved.text) is encoded
    assert (saved.metric_stamp, saved.metric_stamp.scopes, saved.entries) == (stamp, stamp.scopes, MARKED)
    assert saved.publish(saved.kept(saved.entries)) is False
    assert path.read_bytes() == data


@pytest.mark.parametrize("stamp", [SingleNumberStamp(13, "1.24.0"), SCOPES], ids=["single-number", "per-scope"])
def test_both_stamp_forms_hold_the_same_marks_and_no_scope_line_is_one(stamp):
    """A legacy raw mark whose path is `#scope` still reads as a mark: its
    third field is a finite number. A scope stamp line read alone, as a
    history reader reads a row, holds none."""
    text = dump_ratchet(MARKED, stamp=stamp, key_version=1, file_name=MARKS) + LEGACY_SCOPE_MARK + "\n"
    marked = [*MARKED, RatchetEntry("#scope", "g( )", 12.0)]

    assert (load_ratchet(text), read_ratchet(text)) == (marked, (marked, []))
    assert read_stamp(text) == stamp
    assert [read_ratchet(row)[0] for row in (LEGACY_SCOPE_MARK, *SCOPE_LINES)] == [marked[-1:], [], [], []]


SINGLE_LINE = "# crapkit-analysis=13 lizard=1.24.0"
TWELVE = "#scope\tapi\tanalysis python=twelve; coverage coveragepy=1"
ONE_FORM = "a marks file holds one stamp form, single-number or per scope"


def _single_after(number: int) -> str:
    return (f"ratchet line {number} stamps the file [crapkit-analysis=13 lizard=1.24.0], but line 1 "
            f"stamps it per scope; {ONE_FORM}")


def _unreadable(number: int) -> str:
    return f"ratchet line {number} has an unreadable scope stamp: {TWELVE!r}"


@pytest.mark.parametrize("text, refusal", [
    (_lines(SINGLE_LINE, "# crapkit-keys=1", *SCOPE_LINES, *ROWS),
     f"ratchet line 3 stamps the file per scope, but line 1 stamps it [crapkit-analysis=13 lizard=1.24.0]; "
     f"{ONE_FORM}"),
    (_lines(HEAD, SINGLE_LINE, *SCOPE_LINES, *ROWS), _single_after(2)),
    (_lines(HEAD, "# crapkit-keys=1", *SCOPE_LINES, SINGLE_LINE, *ROWS), _single_after(6)),
    (_lines(HEAD, *SCOPE_LINES, *ROWS, SINGLE_LINE), _single_after(8)),
    (_lines(SINGLE_LINE, HEAD, *ROWS),
     f"ratchet line 2 stamps the file per scope, but line 1 stamps it [crapkit-analysis=13 lizard=1.24.0]; "
     f"{ONE_FORM}"),
    (_lines(HEAD, TWELVE, *SCOPE_LINES[1:], *ROWS), _unreadable(2)),
    (_lines(SINGLE_LINE, TWELVE, *ROWS), _unreadable(2)),
    (_lines(TWELVE, *ROWS), _unreadable(1)),
    (_lines(HEAD, *SCOPE_LINES, SCOPE_LINES[0], *ROWS),
     "ratchet line 5 stamps scope 'api' a second time"),
    (_lines(*SCOPE_LINES, *ROWS),
     f"ratchet line 1 is a scope stamp, but no stamp line opens the file; {ONE_FORM}"),
], ids=["both-forms-single-first", "both-forms-single-second", "both-forms-single-after-the-scope-lines",
        "both-forms-single-after-the-header", "both-forms-per-scope-first-line-second",
        "unreadable-scope-line", "unreadable-scope-line-under-a-single-number",
        "unreadable-scope-line-unstamped", "scope-twice", "no-first-line"])
def test_a_file_whose_scope_stamps_cannot_stand_is_refused_by_its_line(tmp_path, text, refusal):
    """load_ratchet refuses it, naming the line; read_ratchet keeps the marks
    and makes one complaint; the stamp reads as unreadable, so verify's stamp
    check refuses before any lane runs. Both forms in one file refuse in any
    order, naming the later line, since a write would keep only one. An
    unreadable scope stamp line costs one complaint whatever stamps the file."""
    path = tmp_path / MARKS
    path.write_text(text, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError) as refused:
        load_ratchet(text)

    assert str(refused.value) == refusal
    assert read_ratchet(text) == (MARKED, [refusal])
    assert type(read_stamp(text)) is UnreadableStamp
    assert stamp_conflict(read_stamp(text), metric_version()) is not None
    with pytest.raises(ConfigError, match=f"unreadable ratchet file {MARKS}: ratchet line"):
        RatchetFile.read(path).entries


def test_only_a_comment_line_opens_the_per_scope_form():
    """A mark whose path holds `crapkit-stamp=2` is a mark, and the file keeps its single-number stamp."""
    marked = RatchetEntry("src/crapkit-stamp=2.py", "h( )", 9.0)
    text = _lines(SINGLE_LINE, *ROWS, "src/crapkit-stamp=2.py\th( )\t9.0000")

    assert read_stamp(text) == SingleNumberStamp(13, "1.24.0")
    assert (load_ratchet(text), read_ratchet(text)) == ([*MARKED, marked], ([*MARKED, marked], []))


@pytest.mark.parametrize("text", [
    "analysis typescript=13 python=13; coverage cc-only",
    "analysis python=013; coverage cc-only",
    "analysis python=13; coverage",
    "analysis python=13 python=14; coverage no-lane",
    "analysis python=13; coverage coveragepy=1 coveragepy=1",
])
def test_a_scope_line_that_would_not_render_back_as_itself_is_unreadable(text):
    assert ScopeStamp.parse(text) is None
    assert ScopeStamp.parse("analysis python=13; coverage cc-only") == ScopeStamp({"python": 13}, "cc-only")


def test_a_per_scope_stamp_names_its_file_or_refuses_to_render():
    """A stamp built in code names no file until its writer passes one."""
    with pytest.raises(ValueError, match="names its marks file"):
        dump_ratchet(MARKED, stamp=SCOPES)
    assert read_stamp(_lines(HEAD.replace(MARKS, "debt.tsv"), *SCOPE_LINES, *ROWS)) == SCOPES


def test_a_per_scope_stamp_survives_a_copy_and_a_pickle():
    stamp = read_stamp(_lines(HEAD, *SCOPE_LINES, *ROWS))

    for copied in (copy.deepcopy(stamp), pickle.loads(pickle.dumps(stamp))):
        assert type(copied) is ScopeStamps
        assert (copied, copied.scopes) == (stamp, stamp.scopes)
        assert dump_ratchet([], stamp=copied).startswith(f"{HEAD}\n")


def test_the_number_a_stamp_gives_a_language():
    """One number for every language under a single-number stamp; the lowest
    number any scope records under a per-scope one; None for none recorded."""
    legacy = ScopeStamps("1.24.0", {**SCOPES.scopes, "old": ScopeStamp({"typescript": 9}, "cc-only")})

    assert [SingleNumberStamp(9, "1.24.0").language_number(name) for name in ("tsx", "python")] == [9, 9]
    assert [SCOPES.language_number(name) for name in ("typescript", "python", "javascript")] == [13, 13, None]
    assert legacy.language_number("typescript") == 9
    assert MetricStamp.NONE.language_number("typescript") is None


# --- writers keep a per-scope file's stamp lines -----------------------------------

CONFIG = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
SRC_SCOPE = ScopeStamps("1.24.0", {"src": ScopeStamp({"python": 13}, {"coveragepy": 1})})
SRC_HEAD = [HEAD, "# crapkit-keys=1", "#scope\tsrc\tanalysis python=13; coverage coveragepy=1"]


@pytest.fixture()
def scoped_repo(tmp_path):
    """A repo whose marks file holds two marks under a per-scope stamp, and one
    run, under the running metric, that scored only the first."""
    import lizard

    from crapkit.analyze import ANALYSIS_VERSION

    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    marks = [RatchetEntry("src/a.py", "hot( n )", 72.0), RatchetEntry("src/gone.py", "gone( )", 30.0)]
    (tmp_path / MARKS).write_text(dump_ratchet(marks, stamp=SRC_SCOPE, key_version=1, file_name=MARKS),
                                  encoding="utf-8", newline="\n")
    store = SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")
    hot = ScoredRow("src", "src/a.py", "hot( n )", 1, 9, 8, 8, 8, 5, 1, 1, 0.0, "measured", 72.0,
                    "decompose")
    store.write_run(commit=SHA, tool_versions={"lizard": lizard.version,
                                               "analysis_version": str(ANALYSIS_VERSION)},
                    rows=[hot], kind="coverage", lanes={"unit": {}})
    store._conn.close()
    return tmp_path


@pytest.mark.parametrize("action, files, rows", [
    ("prune", None, ["src/a.py\thot( n )\t72.0000"]),
    ("move", ["src/gone.py", "src/kept.py"], ["src/a.py\thot( n )\t72.0000", "src/kept.py\tgone( )\t30.0000"]),
], ids=["prune", "move"])
def test_prune_and_move_write_a_per_scope_file_through_kept(scoped_repo, capsys, action, files, rows):
    """Neither adds a number, so both keep the first line and every scope line verbatim."""
    args = argparse.Namespace(action=action, files=files, repo=str(scoped_repo), baseline=None)

    assert cmd_ratchet(args) == 0

    written = (scoped_repo / MARKS).read_text(encoding="utf-8").splitlines()
    assert written == [*SRC_HEAD, "path\tlong_name\tcrap", *rows], capsys.readouterr()


# --- the merge driver reads per-scope files with no analysis stack -------------------

def _sides(tmp_path, theirs: MetricStamp) -> list[str]:
    files = []
    for side, crap, stamp in (("base", 50.0, SCOPES), ("ours", 40.0, SCOPES), ("theirs", 30.0, theirs)):
        path = tmp_path / f"{side}.tsv"
        path.write_text(dump_ratchet([RatchetEntry("src/a.py", "f( )", crap)], stamp=stamp, key_version=1,
                                     file_name=MARKS), encoding="utf-8", newline="\n")
        files.append(str(path))
    return files


def _blocked(monkeypatch) -> None:
    for name in ("lizard", "crapkit.analyze", "crapkit.config"):
        monkeypatch.setitem(sys.modules, name, None)


def test_the_merge_driver_merges_three_per_scope_files_with_no_analysis_stack(tmp_path, monkeypatch, capsys):
    """OURS is git's temporary file, so the merged first line keeps the name it read."""
    files = _sides(tmp_path, SCOPES)
    _blocked(monkeypatch)

    code = cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))

    assert (code, capsys.readouterr().out) == (0, "ratchet merge: 1 mark(s)\n")
    assert (tmp_path / "ours.tsv").read_text(encoding="utf-8") == _lines(
        HEAD, "# crapkit-keys=1", *SCOPE_LINES, "path\tlong_name\tcrap", "src/a.py\tf( )\t30.0000")


def test_a_scope_stamp_only_theirs_raised_wins_the_merge(tmp_path, monkeypatch, capsys):
    """BASE and OURS keep web at typescript 13 and THEIRS raised it to 14, so
    THEIRS's web line wins; OURS changed a mark, so the note names web."""
    raised = ScopeStamps("1.24.0", {**SCOPES.scopes,
                                    "web": ScopeStamp({"typescript": 14, "tsx": 13}, {"istanbul": 1})})
    files = _sides(tmp_path, raised)
    _blocked(monkeypatch)

    code = cmd_ratchet(argparse.Namespace(action="merge", files=files, repo=None, baseline=None))

    output = capsys.readouterr()
    assert (code, output.out) == (0, "ratchet merge: 1 mark(s)\n"), output.err
    assert output.err == ("ratchet merge: scope web's stamp came from THEIRS and OURS changed 1 mark(s); "
                          "if verify names web, run crapkit ratchet seed --scope web\n")
    web = "#scope\tweb\tanalysis tsx=13 typescript=14; coverage istanbul=1"
    assert (tmp_path / "ours.tsv").read_text(encoding="utf-8") == _lines(
        HEAD, "# crapkit-keys=1", *SCOPE_LINES[:2], web, "path\tlong_name\tcrap", "src/a.py\tf( )\t30.0000")


# --- crapkit 0.8.x meets a per-scope file --------------------------------------------
# Deploy cell lin-mixed-0.8.0-reads-0.9.0-marks runs the released 0.8.0 and 0.8.1
# wheels on a per-scope file at slot close. Every unit run holds the reads its
# lines rest on: the code below is 0.8.x's marks reader, copied word for word
# from records.py and ratchet.py, and the first test holds each definition to
# both tags' source. The two releases share all of it but `_finite_mark`, which
# 0.8.1 taught to round to four decimals.

ROOT = Path(__file__).resolve().parents[2]
OLD_RELEASES = ("v0.8.0", "v0.8.1")

_RELEASE_RECORDS = r'''
_PREFIX = "@crapkit-record-"
_MARKER = _PREFIX + "v1"


def record_lines(text: str):
    """Only LF and CRLF frame physical rows; Unicode separators are data."""
    return (line.removesuffix("\r") for line in text.split("\n"))


def decode_record(line: str) -> list[str]:
    """Read ordinary fields or a self-described row; reject unknown encodings."""
    parts = line.split("\t")
    if len(parts) != 2 or not parts[0].startswith(_PREFIX):
        return parts
    if parts[0] != _MARKER:
        raise ValueError(f"unsupported portable record encoding {parts[0]!r}")
    return _string_fields(parts[1])


def _string_fields(text: str) -> list[str]:
    values = json.loads(text)
    if not isinstance(values, list) or any(type(value) is not str for value in values):
        raise ValueError("portable record must contain a JSON array of string fields")
    return values
'''

_RELEASE_RATCHET = r'''
_HEADER = "path\tlong_name\tcrap"
KEY_VERSION = 1
_KEY_STAMP = "# crapkit-keys="


class RatchetEntry(NamedTuple):
    path: str
    long_name: str
    crap: float


def read_key_version(text: str) -> int:
    """Missing means the old start-only identity; an unknown format cannot compare."""
    stamps = _key_stamps(text)
    if not stamps:
        return 0
    if len(stamps) != 1 or stamps[0] not in ("0", str(KEY_VERSION)):
        raise ValueError("unreadable or unsupported ratchet key identity version")
    return int(stamps[0])


def _key_stamps(text: str) -> list[str]:
    return [line[len(_KEY_STAMP):] for line in record_lines(text) if _key_stamp(line)]


def comment_line(line: str) -> bool:
    """A format comment: the metric stamp or the key stamp. A `#` line that holds
    a tab is a legacy raw mark whose path starts with `#`, not a comment."""
    return line.startswith("#") and "\t" not in line


def _key_stamp(line: str) -> bool:
    return comment_line(line) and line.startswith(_KEY_STAMP)


def _is_skippable(line: str) -> bool:
    """Blank lines, the header and comment lines (the metric stamp) carry no mark."""
    return not line.strip() or line == _HEADER or comment_line(line)


def _read_mark(line: str, number: int) -> RatchetEntry:
    try:
        parts = decode_record(line)
    except ValueError as exc:
        raise ValueError(f"ratchet line {number} has an unreadable record: {exc}") from exc
    if len(parts) != 3:
        raise ValueError(f"ratchet line {number} has {len(parts)} fields, expected 3: {line!r}")
    try:
        return RatchetEntry(parts[0], parts[1], _finite_mark(parts[2]))
    except ValueError as exc:
        raise ValueError(f"ratchet line {number} has an unreadable mark: {line!r}") from exc


def read_stamp(text: str) -> str:
    """The metric a marks file was written under; "" for one written before stamping."""
    for line in record_lines(text):
        if line.strip() and not _key_stamp(line):
            return line[1:].strip() if comment_line(line) else ""
    return ""


def read_ratchet(text: str) -> tuple[list[RatchetEntry], list[str]]:
    """The marks, and one complaint per line that carries none.

    Split out from `load_ratchet` so a READ-ONLY caller can salvage the file. A
    mark is one optional field of what `explain` and `brief` answer, so a single
    hand-edited line used to cost the whole answer: a ValueError traceback where
    a trajectory, a source listing and a churn count were all available. Two
    shapes reach here, and a hand edit or a botched merge produces both: too few
    fields, and three fields whose mark is empty or is not a number. Dropping a
    line can only take a ceiling away from a gate, never raise one, so the read
    side is safe to salvage. The write side is not, and keeps `load_ratchet`.
    """
    entries, complaints = [], []
    for i, line in enumerate(record_lines(text)):
        if _is_skippable(line):
            continue
        try:
            entries.append(_read_mark(line, i + 1))
        except ValueError as exc:
            complaints.append(str(exc))
    return entries, complaints


def load_ratchet(text: str) -> list[RatchetEntry]:
    """Every mark, refusing a file that holds a line carrying none. What every
    caller that REWRITES the file reads with: a line skipped there would delete
    a mark the repo signed for."""
    read_key_version(text)
    entries, complaints = read_ratchet(text)
    if complaints:
        raise ValueError(complaints[0])
    return entries
'''

_FINITE_MARK = {
    "v0.8.0": r'''
def _finite_mark(text: str) -> float:
    mark = float(text)
    if not math.isfinite(mark):
        raise ValueError("ratchet mark must be finite")
    return mark
''',
    "v0.8.1": r'''
def _finite_mark(text: str) -> float:
    """The mark as the file holds it, at four decimals: a hand-typed mark with more
    reads as the number the next write gives it (a tie rounds as round() rounds
    the double), so every mark in memory is one `dump_ratchet` writes unchanged."""
    mark = float(text)
    if not math.isfinite(mark):
        raise ValueError("ratchet mark must be finite")
    return float(f"{mark:.4f}")
''',
}

# The first rule of both tags' stamp_conflict, which verify and verify
# --override apply before any lane runs: a stamp that is set and differs from
# the running metric refuses, and the refusal quotes it.
_STAMP_RULE = "    if not recorded or recorded == current:\n        return None\n"


def _tag_source(tag: str, module: str) -> str:
    shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{tag}:src/crapkit/{module}"],
                           capture_output=True)
    if shown.returncode:
        pytest.skip(f"the crapkit history at {ROOT} holds no tag {tag} (a shallow or tagless "
                    "clone), and this test reads that release's source from it")
    return shown.stdout.decode("utf-8").replace("\r\n", "\n")


def _release_reader(tag: str) -> dict:
    """0.8.x's marks reader as `tag` ran it, in a namespace of its own."""
    reader: dict = {}
    exec("import json\nimport math\nfrom typing import NamedTuple\n" + _RELEASE_RECORDS
         + _RELEASE_RATCHET + _FINITE_MARK[tag], reader)
    return reader


@pytest.mark.parametrize("tag", OLD_RELEASES)
def test_the_frozen_0_8_x_reader_is_each_releases_own_code(tag):
    """Each definition stands word for word in the tag's records.py or
    ratchet.py, and so does the stamp check's first rule."""
    records, ratchet = _tag_source(tag, "records.py"), _tag_source(tag, "ratchet.py")
    frozen = {_RELEASE_RECORDS: records, _RELEASE_RATCHET + _FINITE_MARK[tag]: ratchet}

    for code, source in frozen.items():
        pieces = [ast.get_source_segment(code, node) for node in ast.parse(code).body]
        assert len(pieces) > 3 and [piece for piece in pieces if piece not in source] == [], tag
    assert _STAMP_RULE in ratchet


TABBED = ScopeStamps("1.24.0", {"we\tb": ScopeStamp({"python": 13}, "no-lane"), "api": SCOPES.scopes["api"]})


@pytest.mark.parametrize("stamp", [SCOPES, TABBED], ids=["plain-names", "encoded-name"])
@pytest.mark.parametrize("tag", OLD_RELEASES)
def test_crapkit_0_8_x_reads_a_foreign_stamp_and_unparseable_marks_in_a_per_scope_file(tag, stamp):
    """What each line of the cell rests on, read by 0.8.x's own reader.

    verify and verify --override check the stamp before any lane runs. Line 1
    reads as a stamp that is set and is no `crapkit-analysis=N lizard=X.Y.Z`,
    so it differs from every metric 0.8.x runs, and the refusal quotes it,
    the file's name and 0.9.0 with it. seed, prune and move load the marks
    strictly before they write, and so does the merge driver on each of git's
    temporary files: the first scope line reads as a mark whose number does not
    parse, and RatchetFile's refusal names the file it read
    (`unreadable ratchet file <name>: ...`), so nothing is written. brief,
    explain and worklist read leniently: every mark, and one skipped line per
    scope stamp line, so they answer.
    """
    reader = _release_reader(tag)
    text = dump_ratchet(MARKED, stamp=stamp, key_version=1, file_name=MARKS)
    lines = text.split("\n")
    scope_lines = lines[2:2 + len(stamp.scopes)]

    recorded = reader["read_stamp"](text)
    assert recorded == lines[0][2:] and MARKS in recorded and "0.9.0" in recorded
    assert recorded and not re.fullmatch(r"crapkit-analysis=[0-9]+ lizard=\S+", recorded)
    assert reader["read_key_version"](text) == 1
    with pytest.raises(ValueError) as refused:
        reader["load_ratchet"](text)
    assert str(refused.value) == f"ratchet line 3 has an unreadable mark: {scope_lines[0]!r}"
    entries, complaints = reader["read_ratchet"](text)
    assert [tuple(entry) for entry in entries] == [tuple(entry) for entry in MARKED]
    assert complaints == [f"ratchet line {number} has an unreadable mark: {line!r}"
                          for number, line in enumerate(scope_lines, 3)]

