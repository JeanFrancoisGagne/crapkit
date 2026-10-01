"""Deleting or emptying the marks file repays no mark.

`ratchet report` replayed the marks file's history as written, so the commit
that deleted it dropped every mark: the burn-down read `0 open mark(s), 4
repaid`, and `--enforce` passed a repayment quota the restored file fails.
verify judges the same tree against the newest committed marks. The report now
reads those marks as open and counts the deleting commit as a clock tick only.
A file that holds a stamp and no rows is not blank: it is every mark repaid.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, git, repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

MARKS = "crapkit-ratchet.tsv"
NAMES = ("dispatch ( kind )", "plain ( x )", "render ( n )", "knotty ( n )")


def _write_marks(root: Path, names) -> None:
    entries = [RatchetEntry("src/app.ts", name, 20.0 + i) for i, name in enumerate(names)]
    (root / MARKS).write_text(dump_ratchet(entries, stamp=metric_version()),
                              encoding="utf-8", newline="\n")


@pytest.fixture()
def marked(repo: Path) -> Path:  # noqa: F811
    """Four committed marks, and a debt policy that wants ten repaid a month."""
    toml = repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        "target = 6\n", "target = 6\ndebt_max_age_months = 0\nrepayment_min_per_30d = 10\n"),
        encoding="utf-8", newline="\n")
    _write_marks(repo, NAMES)
    commit_all(repo, "four marks")
    return repo


def _report(root: Path, capsys, *flags: str) -> tuple[int, dict, str]:
    code = main(["ratchet", "report", "--json", *flags, "--repo", str(root)])
    out = capsys.readouterr()
    return code, json.loads(out.out), out.err


def _counts(report: dict) -> tuple:
    return report["open"], report["dropped_total"], report["dropped_last_30d"]


def _delete(root: Path) -> None:
    git(root, "rm", "-q", MARKS)
    commit_all(root, "delete the marks")


def _empty(root: Path) -> None:
    (root / MARKS).write_text("\n\n", encoding="utf-8", newline="\n")
    commit_all(root, "empty the marks")


@pytest.mark.parametrize("gone", [_delete, _empty], ids=["deleted", "emptied"])
def test_a_committed_deletion_leaves_the_marks_open(marked, capsys, gone):
    before = _report(marked, capsys, "--enforce")
    gone(marked)

    code, report, err = _report(marked, capsys, "--enforce")

    assert _counts(report) == _counts(before[1]) == (4, 0, 0)
    assert (code, before[0]) == (1, 1), "the quota the restored file fails still fails"
    assert report["policy_violations"] == ["repayment stalled: 0 mark(s) repaid in 30d "
                                           "(policy wants 10)"]
    assert f"warning: {MARKS} is " in err and "reads the 4 mark(s) its history last " \
           "committed as open" in err, err


def test_the_text_report_reads_the_same_marks(marked, capsys):
    _delete(marked)

    assert main(["ratchet", "report", "--repo", str(marked)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines()[0] == "ratchet burn-down: 4 open mark(s), 0 repaid " \
                                      "(0 in the last 30d, 0 in 90d)"
    assert f"warning: {MARKS} is missing, so the report reads" in out.err


def test_an_uncommitted_deletion_reads_the_committed_marks(marked, capsys):
    (marked / MARKS).unlink()

    _, report, err = _report(marked, capsys)

    assert (_counts(report), report["uncommitted"]) == ((4, 0, 0), 0)
    assert f"warning: {MARKS} is missing" in err


def test_a_real_repayment_before_the_deletion_still_counts(marked, capsys):
    _write_marks(marked, NAMES[:3])
    commit_all(marked, "repay knotty")
    _delete(marked)

    _, report, _ = _report(marked, capsys)

    assert _counts(report) == (3, 1, 1)
    assert [row["long_name"] for row in report["oldest"]] == sorted(NAMES[:3])


def test_a_stamp_with_no_rows_repays_every_mark(marked, capsys):
    _write_marks(marked, ())
    commit_all(marked, "every mark repaid")

    _, report, err = _report(marked, capsys)

    assert _counts(report) == (0, 4, 4)
    assert "warning" not in err


def test_a_repo_that_never_committed_marks_reports_zeros(repo, capsys):  # noqa: F811
    code, report, err = _report(repo, capsys)

    assert (code, _counts(report), err) == (0, (0, 0, 0), "")

