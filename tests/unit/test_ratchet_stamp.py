"""The metric stamp: marks carry the analysis version and the lizard that measured them.

Pure seam. Without it a scoring change leaves 40k marks in place and every later
verify silently compares new scores against numbers the old rules produced.
"""
import argparse
import codecs
import copy
import pickle
import sys

import pytest

from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.ratchet import (MetricStamp, RatchetEntry, SingleNumberStamp, UnreadableStamp,
                             dump_ratchet, load_ratchet, metric_version, read_key_version,
                             read_stamp, run_stamp, stamp_conflict, stamp_text)
from crapkit.ratchetfile import RatchetFile

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
