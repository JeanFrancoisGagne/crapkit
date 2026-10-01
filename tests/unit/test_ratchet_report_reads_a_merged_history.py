"""ratchet report on a marks file whose history holds a merge.

`git log -p` prints no patch for a merge commit, so a line both branches added,
or both removed, shows twice in the log and once in the file. The report found
the newest commit that held marks by a running count of those lines. Counted
twice, an added line kept the count above zero after the commit that deleted
the file, and the deletion read as every mark repaid. Removed lines counted
twice dropped the count below zero while the merged file still held marks, and
the marks only the other branch brought read as never open. The report now
reads each commit's own revision of the file, newest first, by the rule
verify's stand-in reads it with.

The same double count reached the repayments: two branches that repaid one
mark counted two repayments. A mark repaid once now counts once.

Each branch also marks a function of its own in its own part of the file, so
the merge commit matches neither side and `git log` keeps both branches.
"""
from __future__ import annotations

import json
from pathlib import Path

from cli_inproc_repo import commit_all, git, repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

MARKS = "crapkit-ratchet.tsv"


def _names(*short: str) -> list[str]:
    return [f"{name} ( n )" for name in short]


def _k(*numbers: int) -> list[str]:
    return _names(*(f"k{n}" for n in numbers))


def _write_marks(root: Path, names: list[str]) -> None:
    entries = [RatchetEntry("src/app.ts", name, 20.0) for name in names]
    (root / MARKS).write_text(dump_ratchet(entries, stamp=metric_version()),
                              encoding="utf-8", newline="\n")


def _branch(root: Path, name: str, start: str, names: list[str]) -> None:
    git(root, "checkout", "-q", "-b", name, start)
    _write_marks(root, names)
    commit_all(root, name)


def _two_branches(root: Path, base: list[str], side_a: list[str], side_b: list[str]) -> None:
    """`base` committed on the main branch, then a branch to `side_a` and one
    to `side_b`, each from that commit, merged back with --no-ff in turn."""
    main_branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _write_marks(root, base)
    commit_all(root, "base marks")
    start = git(root, "rev-parse", "HEAD").strip()
    _branch(root, "side-a", start, side_a)
    _branch(root, "side-b", start, side_b)
    git(root, "checkout", "-q", main_branch)
    for branch in ("side-a", "side-b"):
        git(root, "-c", "user.email=t@example.com", "-c", "user.name=t",
            "-c", "commit.gpgsign=false", "merge", "-q", "--no-ff", "-m", f"merge {branch}",
            branch)


def _delete(root: Path) -> None:
    git(root, "rm", "-q", MARKS)
    commit_all(root, "delete the marks")


def _report(root: Path, capsys) -> dict:
    assert main(["ratchet", "report", "--json", "--repo", str(root)]) == 0
    return json.loads(capsys.readouterr().out)


def _open(report: dict) -> list[str]:
    return sorted(row["long_name"] for row in report["oldest"])


def test_a_line_both_branches_added_leaves_the_merged_marks_open_after_a_delete(repo, capsys):  # noqa: F811
    """Both sides marked k9: the count read one line more than the file held,
    so the deleting commit still held one, and the report read 0 open, 9 repaid."""
    _two_branches(repo, _k(1, 2, 3, 4, 5, 6, 7, 8),
                  side_a=_k(1, 3, 4, 5, 6, 7, 8, 9), side_b=_k(1, 2, 3, 4, 6, 7, 8, 9))
    merged = _k(1, 3, 4, 6, 7, 8, 9)
    assert _open(_report(repo, capsys)) == merged
    _delete(repo)

    report = _report(repo, capsys)

    assert (report["open"], report["dropped_total"], _open(report)) == (7, 2, merged), report


def test_a_prune_both_branches_made_leaves_the_merged_marks_open_after_a_delete(repo, capsys):  # noqa: F811
    """Both sides repaid k1 to k8: the count fell below zero while the merged
    file held four marks, so the report replayed up to side a's commit alone
    and m4, side b's mark, read as never open."""
    _two_branches(repo, _k(1, 2, 3, 4, 5, 6, 7, 8) + _names("m1", "m3"),
                  side_a=_names("m1", "m2", "m3"), side_b=_names("m1", "m3", "m4"))
    merged = _names("m1", "m2", "m3", "m4")
    assert _open(_report(repo, capsys)) == merged
    _delete(repo)

    report = _report(repo, capsys)

    assert (report["open"], _open(report)) == (4, merged), report


def test_a_mark_both_branches_repaid_counts_one_repayment(repo, capsys):  # noqa: F811
    _two_branches(repo, _k(1, 2, 3, 4) + _names("m1", "m3"),
                  side_a=_k(2, 3, 4) + _names("m1", "m2", "m3"),
                  side_b=_k(2, 3, 4) + _names("m1", "m3", "m4"))

    report = _report(repo, capsys)

    assert (report["open"], report["dropped_total"], report["dropped_last_30d"]) == (7, 1, 1), report
