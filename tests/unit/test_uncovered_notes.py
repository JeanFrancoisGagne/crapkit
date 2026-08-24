"""The note that stands in for `uncovered_lines: null`.

Two states read the same on the surface and want opposite moves. A stale or
missing artifact clears on the next commit. A file no artifact mentions is
`flag: untested` on a clean tree, and committing does nothing for it: the move
is a first test that imports the file.
"""
from crapkit.uncovered import MissingLines


MEASURED = MissingLines({"calc/report.py": {5, 6}}, "")


def test_a_measured_file_has_no_note():
    assert MEASURED.note_for("calc/report.py", "measured") == ""


def test_an_untested_file_names_the_flag_and_the_move():
    note = MEASURED.note_for("calc/curve.py", "untested")

    assert note.startswith("no lane artifact measured calc/curve.py")
    assert "untested" in note, "the cause is the flag, not an uncommitted edit"
    assert "test" in note and "import" in note, "the move is to write the first test"
    assert "commit" not in note, "committing never clears an untested file"


def test_a_stale_artifact_keeps_the_uncommitted_edits_wording():
    stale = MissingLines({}, "lane 'py': files in its scopes changed since cov.json "
                             "was written (uncommitted edits count)")

    note = stale.note_for("calc/curve.py", "untested")

    assert "uncommitted edits" in note, "the global staleness note outranks the flag"
    assert "untested" not in note


def test_a_file_absent_without_a_flag_keeps_the_bare_note():
    assert MEASURED.note_for("calc/curve.py") == "no lane artifact measured calc/curve.py"


def test_a_cc_only_row_names_its_own_scopes_setting():
    """cc-only means no coverage number can exist for the scope. The note used to
    hand back a stale-lane message naming a lane that does not cover it, and
    following it (commit, rerun coverage) changed nothing."""
    stale = MissingLines({}, "lane 'py': files in its scopes changed since cov.json "
                             "was written (uncommitted edits count)")

    note = stale.note_for("tools/helper.py", "cc-only", "tools")

    assert note == ("scope 'tools' sets coverage_optional = true, so no artifact "
                    "can name uncovered lines for tools/helper.py")


def test_the_cc_only_note_outranks_a_measuring_artifact_too():
    """A lane may still write about the file. The flag says the number is not a
    coverage verdict, so line numbers must not read as one either."""
    note = MEASURED.note_for("calc/report.py", "cc-only", "calc")

    assert "coverage_optional" in note
