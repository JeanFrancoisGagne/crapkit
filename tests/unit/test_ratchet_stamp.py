"""The metric stamp: marks carry the analysis version and the lizard that measured them.

Pure seam. Without it a scoring change leaves 40k marks in place and every later
verify silently compares new scores against numbers the old rules produced.
"""
from crapkit.ratchet import (RatchetEntry, dump_ratchet, load_ratchet, read_stamp,
                             stamp_conflict, stamp_text)

STAMP = "crapkit-analysis=9 lizard=0.1.2"
LEGACY = "path\tlong_name\tcrap\nsrc/a.ts\tf( )\t30.0000\n"


def test_stamp_text_is_the_committed_wire_format():
    assert stamp_text(3, "1.24.0") == "crapkit-analysis=3 lizard=1.24.0"


def test_dump_writes_the_stamp_above_the_header():
    lines = dump_ratchet([RatchetEntry("src/a.ts", "f( )", 30.0)], stamp=STAMP).splitlines()
    assert lines[0] == "# crapkit-analysis=9 lizard=0.1.2"
    assert lines[1] == "path\tlong_name\tcrap"
    assert lines[2] == "src/a.ts\tf( )\t30.0000"


def test_dump_stamps_with_the_running_metric_by_default():
    first = dump_ratchet([RatchetEntry("src/a.ts", "f( )", 30.0)]).splitlines()[0]
    assert first.startswith("# crapkit-analysis=") and " lizard=" in first


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
