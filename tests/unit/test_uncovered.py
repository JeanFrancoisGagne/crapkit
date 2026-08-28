"""Dark lines per function: the missing-line sets the coverage parsers already
produce, cut to one function's span, plus the note that stands in when nothing
on disk can answer. Pure seam — no lane runs here."""
from types import SimpleNamespace

import pytest

from crapkit.config import load_config_text
from crapkit.uncovered import MissingLines, load_uncovered

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "core"
paths = ["core"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["core"]
full_suite = false
"""

# core/beta.py holds two functions: beta over lines 1-13 and helper over 16-19,
# with line 14 blank between them.
BETA_MISSING = {"core/beta.py": {4, 8, 14, 18}}


def test_a_span_takes_only_the_dark_lines_inside_it():
    src = MissingLines(BETA_MISSING, "")
    assert src.in_span("core/beta.py", 1, 13) == [4, 8]
    assert src.in_span("core/beta.py", 16, 19) == [18]


def test_dark_lines_come_back_sorted_whatever_order_the_set_iterates():
    src = MissingLines({"core/a.py": {31, 4, 17}}, "")
    assert src.in_span("core/a.py", 1, 40) == [4, 17, 31]


def test_a_measured_span_with_nothing_dark_carries_no_note():
    src = MissingLines({"core/a.py": set()}, "")
    assert src.in_span("core/a.py", 1, 40) == []
    assert src.note_for("core/a.py") == ""


def test_a_file_no_artifact_spoke_about_says_so_instead_of_reading_as_covered():
    src = MissingLines({"core/a.py": {4}}, "")
    assert src.in_span("core/b.py", 1, 40) == []
    assert "core/b.py" in src.note_for("core/b.py")


def test_a_note_blanks_every_span_rather_than_serving_stale_line_numbers():
    src = MissingLines(BETA_MISSING, "lane 'py': no artifact at cov.json")
    assert src.in_span("core/beta.py", 1, 13) == []
    assert src.note_for("core/beta.py") == "lane 'py': no artifact at cov.json"


def test_a_missing_artifact_is_a_note_not_a_crash(tmp_path):
    src = load_uncovered(tmp_path, load_config_text(CONFIG))
    assert src.in_span("core/beta.py", 1, 13) == []
    assert "cov.json" in src.note_for("core/beta.py")
    assert "'py'" in src.note_for("core/beta.py"), "the note names the lane to rerun"


def test_a_repo_with_no_lane_says_that_rather_than_naming_one(tmp_path):
    cfg = load_config_text(CONFIG.split("[[lane]]")[0])
    note = load_uncovered(tmp_path, cfg).note_for("core/beta.py")
    assert "lane" in note and "cov.json" not in note


# --- an unrecognised parser is a bug, not a coveragepy artifact ------------
# The dispatch used to be an if/else whose else was coveragepy, so the day a
# third parser lands in SUPPORTED_PARSERS its lane routes into the coverage.py
# reader and blames a perfectly good artifact for being unparseable.

def _unknown_parser_lane(tmp_path):
    from crapkit.config import Lane

    (tmp_path / "cov.xml").write_text("<coverage/>", encoding="utf-8")
    return SimpleNamespace(lanes=[Lane(name="py", command="", artifact="cov.xml",
                                       parser="cobertura", scopes=("core",))])


def test_an_unknown_parser_raises_instead_of_reading_it_as_coveragepy(tmp_path):
    from crapkit.errors import ToolError
    from crapkit.uncovered import missing_by_path

    with pytest.raises(ToolError) as exc:
        missing_by_path(tmp_path, _unknown_parser_lane(tmp_path))
    assert str(exc.value) == "lane 'py': parser 'cobertura' not implemented yet"


def test_the_two_dispatches_word_the_same_refusal_the_same_way(tmp_path):
    """lanes._read_and_parse already refuses; a reader who meets one message
    should not have to learn a second one for the same cause."""
    from crapkit.errors import ToolError
    from crapkit.lanes import _read_and_parse
    from crapkit.uncovered import missing_by_path

    cfg = _unknown_parser_lane(tmp_path)
    with pytest.raises(ToolError) as from_lanes:
        _read_and_parse(cfg.lanes[0], tmp_path, tmp_path / "cov.xml")
    with pytest.raises(ToolError) as from_uncovered:
        missing_by_path(tmp_path, cfg)
    assert str(from_uncovered.value) == str(from_lanes.value)
