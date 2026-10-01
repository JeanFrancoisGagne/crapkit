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
import subprocess
from pathlib import Path

from cli_inproc_repo import commit_all, git, repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.marks_history import newest_committed_marks
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


# --- a merge resolution no patch shows ---------------------------------------
# Branch b repays k3 on day 2 and branch a loosens it on day 3. Replayed by date,
# a's change reopens k3 after b's repayment, and the merge that resolved k3 as
# repaid prints no patch, so the replay ended with k3 open. The file on disk
# said which marks were open until it was deleted; after that the report read
# 6 open, k3 among them, while verify's stand-in read the merge's 5. The open
# marks now come from the newest revision that held any, and the replay gives
# only their ages and the repayments.

def _dated(monkeypatch, day: int) -> None:
    stamp = f"2026-09-{day:02d}T12:00:00+00:00"
    monkeypatch.setenv("GIT_AUTHOR_DATE", stamp)
    monkeypatch.setenv("GIT_COMMITTER_DATE", stamp)


def _write_worth(root: Path, worth: dict[str, float]) -> None:
    entries = [RatchetEntry("src/app.ts", f"{name} ( n )", crap) for name, crap in worth.items()]
    (root / MARKS).write_text(dump_ratchet(entries, stamp=metric_version()),
                              encoding="utf-8", newline="\n")


def _merge_resolved_as_repaid(root: Path, monkeypatch) -> str:
    """k3 loosened on a, repaid on b, and b's conflicted merge keeps the
    repayment. Answers the base commit's sha."""
    main_branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _dated(monkeypatch, 1)
    _write_worth(root, {"k1": 20.0, "k2": 20.0, "k3": 20.0, "k4": 20.0})
    commit_all(root, "base marks")
    base = git(root, "rev-parse", "HEAD").strip()
    _dated(monkeypatch, 2)
    git(root, "checkout", "-q", "-b", "b", base)
    _write_worth(root, {"k1": 20.0, "k2": 20.0, "k4": 20.0, "mb": 20.0})
    commit_all(root, "b repays k3 and marks mb")
    _dated(monkeypatch, 3)
    git(root, "checkout", "-q", "-b", "a", base)
    _write_worth(root, {"k1": 20.0, "k2": 20.0, "k3": 30.0, "k4": 20.0, "ma": 20.0})
    commit_all(root, "a loosens k3 and marks ma")
    git(root, "checkout", "-q", main_branch)
    _dated(monkeypatch, 4)
    merge = ("-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false",
             "merge", "-q", "--no-ff")
    git(root, *merge, "-m", "merge a", "a")
    _dated(monkeypatch, 5)
    conflicted = subprocess.run(["git", *merge, "-m", "merge b", "b"], cwd=root,
                                capture_output=True, text=True)
    assert conflicted.returncode != 0, "b's repayment of k3 conflicts with a's loosening"
    _write_worth(root, {"k1": 20.0, "k2": 20.0, "k4": 20.0, "ma": 20.0, "mb": 20.0})
    commit_all(root, "merge b, k3 resolved as repaid")
    return base


def test_a_mark_a_merge_resolved_as_repaid_stays_repaid_after_a_delete(repo, monkeypatch, capsys):  # noqa: F811
    base = _merge_resolved_as_repaid(repo, monkeypatch)
    merged = _names("k1", "k2", "k4", "ma", "mb")
    before = _report(repo, capsys)
    _, stand_in = newest_committed_marks(repo, base, MARKS)
    _dated(monkeypatch, 6)
    _delete(repo)

    assert main(["ratchet", "report", "--json", "--repo", str(repo)]) == 0
    out = capsys.readouterr()
    after = json.loads(out.out)

    assert sorted(e.long_name for e in stand_in.entries) == merged
    assert (_open(before), before["open"]) == (merged, 5), before
    assert (_open(after), after["open"]) == (merged, 5), after
    assert after["dropped_total"] == before["dropped_total"], "deleting the file repays none"
    assert "reads the 5 mark(s) its history last committed" in out.err, out.err



def test_a_clean_tree_after_a_conflicted_merge_holds_no_uncommitted_mark(repo, monkeypatch, capsys):  # noqa: F811
    """The replay ends with k3 open, since the merge that repaid it prints no
    patch, while HEAD's file and the file on disk agree. The report counted k3
    as a mark the working tree changed and not committed yet."""
    _merge_resolved_as_repaid(repo, monkeypatch)
    assert git(repo, "status", "--porcelain").strip() == ""

    report = _report(repo, capsys)

    assert (report["open"], report["uncommitted"]) == (5, 0), report
