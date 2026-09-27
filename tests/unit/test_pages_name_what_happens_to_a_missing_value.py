"""The exit tables and the upgrade notes say what crapkit does with a value nobody measured.

Four pages carry exit codes a reader acts on: README's table, the verify step in
AGENTS.md, the recover skill an agent routes by, and docs/upgrading.md. A refusal
one page states and another leaves out sends a CI owner to the page that says the
job should have passed. Each check holds one claim on every page that makes it,
and holds it against the code where the code already prints the string.
"""
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit.gitio import _SHALLOW_FIX

ROOT = Path(__file__).resolve().parent.parent.parent
README = "README.md"
AGENTS = "AGENTS.md"
RECOVER = "plugin/skills/crapkit-recover/SKILL.md"
UPGRADING = "docs/upgrading.md"
UPGRADE_NOTES = "## Missing values that 0.8.1 names"
# The fix every shallow-clone refusal ends with, as gitio prints it.
FETCH_FIX = _SHALLOW_FIX.split(": ", 1)[1]


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _row(page: str, first_cell: str) -> str:
    rows = [ln for ln in _doc(page).splitlines() if ln.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"{page}: expected one row for {first_cell!r}, found {len(rows)}"
    return rows[0]


def _section(page: str, heading: str) -> str:
    """The body under one heading, down to the next heading of the same depth."""
    lines = _doc(page).splitlines()
    assert heading in lines, f"{page} lost its {heading!r} heading"
    depth = heading.split(" ", 1)[0] + " "
    rest = lines[lines.index(heading) + 1:]
    end = next((i for i, ln in enumerate(rest) if ln.startswith(depth)), len(rest))
    return "\n".join(rest[:end])


def test_the_fetch_fix_is_the_clause_after_gitio_names_the_shallow_clone():
    """Guards the tests below, which pass on an empty needle."""
    assert FETCH_FIX == "set fetch-depth: 0 on the checkout or run git fetch --unshallow"


@pytest.mark.parametrize("page", [README, RECOVER])
def test_each_exit_4_row_names_the_shallow_ratchet_report_refusal_and_its_fix(page: str):
    row = _row(page, "4")

    assert "ratchet report --enforce" in row
    assert "shallow clone" in row
    assert FETCH_FIX in row


def test_readme_says_ratchet_report_enforce_exits_4_not_1_in_a_shallow_clone():
    """The exit-1 table calls that exit a verdict; in a shallow clone there is none."""
    row = _row(README, "`ratchet report --enforce`")

    assert "shallow clone" in row and "exits 4" in row


@pytest.mark.parametrize("page", [README, AGENTS, RECOVER])
def test_each_exit_5_row_names_verify_over_a_declared_junit_it_cannot_read(page: str):
    row = _row(page, "5")

    assert "--reuse-artifacts" in row
    assert "`results_artifact`" in row
    assert "missing or unreadable" in row


def test_the_field_the_pages_give_a_lane_with_no_junit_is_one_verify_emits():
    from crapkit.cli import verifying

    source = Path(verifying.__file__).read_text(encoding="utf-8")

    assert '"lanes_without_results"' in source
    for page in (README, UPGRADING):
        assert "`lanes_without_results`" in _doc(page), page


def test_the_upgrade_notes_name_each_change_that_moves_an_exit_code_or_a_count():
    notes = _section(UPGRADING, UPGRADE_NOTES)

    assert "`verify --reuse-artifacts`" in notes and "exits 5" in notes
    assert "`ratchet report --enforce`" in notes and "exit 4" in notes
    assert FETCH_FIX in notes
    assert "`shallow: true`" in notes
    assert "`no_verdict`" in notes and "`timed_out`" in notes
    assert "0.4.15" in notes and "without `--reuse-artifacts`" in notes


# Every change in 0.8.1 that moves an exit code: (what the job meets, a command
# the row names, the 0.8.0 exit, the 0.8.1 exit). A change the table leaves out
# fails a CI job with no line in the guide that says why.
EXIT_MOVES = {
    "unread-file-gates": ("a changed file no reader could read", "`rescore --gate`", "0", "6"),
    "unread-file-hook": ("an edit that leaves a file no reader can read", "`claude-hook`",
                         "0", "2"),
    "unreadable-junit": ("a declared junit it reused and cannot read",
                         "`verify --reuse-artifacts`", "0", "5"),
    "shallow-enforce": ("a debt policy key in a shallow clone", "`ratchet report --enforce`",
                        "0 or 1", "4"),
    "artifact-count": ("a coverage artifact missing a count", "`coverage`, `verify`", "0", "5"),
    "unreadable-stamps": ("a `.crapkit/artifacts.json` that cannot be read",
                          "`coverage --reuse-artifacts`, `verify --reuse-artifacts`", "0", "5"),
    "marks-deleted": ("a deleted or emptied marks file", "`verify`", "0",
                      "7, or 4 when the clone lacks the history"),
    "failures-walk-back": ("a failure the baseline's own commit had", "`verify`", "8", "0"),
    "retried-pass-0.7": ("a failure a 0.7.x verify retried to a pass", "`verify`", "0", "8"),
    "renamed-marks-age": ("a marks file renamed with `git mv`, a mark past",
                          "`ratchet report --enforce`", "0", "1"),
    "renamed-marks-repaid": ("a marks file renamed with `git mv`, `repayment_min_per_30d` met",
                             "`ratchet report --enforce`", "1", "0"),
}


def _is_body_row(line: str) -> bool:
    """A table line other than the header row."""
    return line.startswith("| ") and not line.startswith("| What")


def _table_rows(text: str) -> list[list[str]]:
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in filter(_is_body_row, text.splitlines())]


@pytest.mark.parametrize("move", sorted(EXIT_MOVES))
def test_the_upgrade_table_names_each_change_that_moves_an_exit_code(move: str):
    meets, command, was, now = EXIT_MOVES[move]

    rows = [row for row in _table_rows(_section(UPGRADING, UPGRADE_NOTES))
            if row[0].startswith(meets)]

    assert len(rows) == 1, f"no single row for {meets!r}"
    assert command in rows[0][1] and rows[0][2:] == [was, now], rows[0]


def test_the_upgrade_table_holds_no_row_the_tests_do_not_know():
    rows = _table_rows(_section(UPGRADING, UPGRADE_NOTES))

    assert len(rows) == len(EXIT_MOVES), [row[0] for row in rows]


def test_the_changelog_counts_the_same_changes_the_upgrade_table_lists():
    words = {9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve"}

    assert (f"{words[len(EXIT_MOVES)]} changes in this release can move an exit code"
            in " ".join(_doc("CHANGELOG.md").split()))


def test_the_upgrade_notes_quote_the_fix_each_new_refusal_prints():
    """The paragraph under each row quotes the line the job's log shows."""
    import contextlib
    import io
    from types import SimpleNamespace

    from crapkit import coverage_istanbul, coverage_py
    from crapkit.cli.verifying import _warn_marks_stand_in

    printed = io.StringIO()
    with contextlib.redirect_stderr(printed):
        _warn_marks_stand_in(SimpleNamespace(text=None), SimpleNamespace(entries=[1, 2]),
                             "C", "crapkit-ratchet.tsv")
    marks_line = printed.getvalue().split(" has 2 mark(s)", 1)[0]
    notes = " ".join(_section(UPGRADING, UPGRADE_NOTES).split())

    for quoted in (coverage_py._REGENERATE, coverage_istanbul._REGENERATE,
                   "crapkit coverage --lane NAME", "delete `.crapkit/artifacts.json`",
                   marks_line + " has N mark(s)", "`git checkout`"):
        assert quoted in notes, quoted


def test_the_0_4_15_note_quotes_the_refusal_reuse_prints_once_a_real_run_records_it(tmp_path):
    """The note's remedy ends at the line the next reuse prints; quoted wrong,
    a reader cannot tell the remedy worked."""
    from crapkit.config import Lane
    from crapkit.lanes import _no_artifact_head

    lane = Lane(name="py", command="pytest", artifact="cov.json", parser="coveragepy",
                scopes=("src",))
    head = _no_artifact_head(tmp_path, lane, ["cov.json"], reuse=True)
    quoted = head.split(" - ", 1)[0]
    notes = " ".join(_section(UPGRADING, UPGRADE_NOTES).split())

    assert quoted == "wrote no artifact on its last attempt"
    assert f"`{quoted}`" in notes


def test_the_mutate_row_and_the_upgrade_note_name_the_same_two_counts():
    row = _row(README, "`mutate [--files F ...] [--max-mutants N] [--drop-pool] [--json]`")
    notes = _section(UPGRADING, UPGRADE_NOTES)

    for key in ("`timed_out`", "`no_verdict`"):
        assert key in row and key in notes, key


@pytest.mark.parametrize("page", [README, RECOVER])
def test_the_shallow_warning_the_pages_quote_carries_the_whole_sentence_gitio_prints(page: str):
    """A reader greps their log for the line the page shows; a clause the
    page shortened is a line they never find."""
    line = f"warning: churn counts read only the commits this clone holds; {_SHALLOW_FIX}"

    assert line in _doc(page)


def test_the_readme_names_every_command_that_marks_a_shallow_clone():
    """The Action section is where a CI owner reads why the ranking looks flat."""
    text = " ".join(_doc(README).split())

    assert "`worklist`, `next-item`, `brief` and `ratchet report` print one line" in text
    assert "`shallow: true`" in text
