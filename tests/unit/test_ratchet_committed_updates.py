"""Committed value changes keep debt age and working-tree attribution accurate."""
import json
import os
import subprocess
import sys

import pytest

from crapkit.marks_history import marks_history
from crapkit.ratchet_report import held_event, mark_events, report_from_events
from raw_git import commit, repository
from test_marks_history import _same_events_both_ways


def _history(tmp_path, *texts: str) -> list:
    """The marks file's revisions, one commit per text, 90 days apart. The
    frozen 0.8.1 read of the same commits gives the same events (check 3)."""
    root = repository(tmp_path / "history")
    for age, text in zip(range(90 * (len(texts) - 1), -1, -90), texts):
        commit(root, files={b"crapkit-ratchet.tsv": text.encode()}, age_days=age)
    revisions = marks_history(root, "crapkit-ratchet.tsv")
    assert _same_events_both_ways(root) == mark_events(revisions)
    return revisions


@pytest.mark.parametrize("value", ["nan", "inf", "-Infinity", "1e999"])
def test_history_cannot_admit_nonfinite_marks(tmp_path, value):
    revisions = _history(tmp_path, f"a.py\tf( )\t{value}\n")
    report = report_from_events(mark_events(revisions) + held_event(revisions))
    assert report["open"] == 0


def test_history_keeps_a_source_file_literally_named_path(tmp_path):
    report = report_from_events(mark_events(_history(tmp_path, "path\tf( )\t20\n")))
    assert report["open"] == 1


@pytest.mark.parametrize("value", [20, 70])
def test_committed_value_change_keeps_entry_age_and_updates_current_value(tmp_path, value):
    revisions = _history(tmp_path, "src/a.py\tf( )\t50\n", f"src/a.py\tf( )\t{value}\n")
    report = report_from_events(mark_events(revisions) + held_event(revisions),
                                {("src/a.py", "f( )"): value})
    assert (report["anchor_ts"] - revisions[0].time) // 86400 == 90
    assert report["oldest"][0]["age_days"] == 90
    assert report["uncommitted"] == 0
    assert report["dropped_total"] == 0


def test_comment_only_ratchet_commit_advances_the_documented_history_clock(tmp_path):
    revisions = _history(tmp_path, "# previous note\nsrc/a.py\tf( )\t50\n",
                         "# updated note\nsrc/a.py\tf( )\t50\n")
    report = report_from_events(mark_events(revisions) + held_event(revisions),
                                {("src/a.py", "f( )"): 50})
    assert report["oldest"][0]["age_days"] == 90
    assert report["uncommitted"] == 0
    assert report["dropped_total"] == 0


def test_public_ratchet_report_enforces_age_after_a_committed_tighten(tmp_path):
    def git(*args, when=None):
        env = dict(os.environ)
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        return subprocess.check_output(["git", "-c", "user.name=Review",
                                        "-c", "user.email=review@example.test", *args],
                                       cwd=tmp_path, env=env, text=True).strip()

    git("init", "-q")
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\ndebt_max_age_months=1\n[[scope]]\n'
        'name="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n',
        encoding="utf-8")
    for value, date in [(50, "2026-01-01T12:00:00+0000"), (20, "2026-04-01T12:00:00+0000")]:
        (tmp_path / "crapkit-ratchet.tsv").write_text(
            f"path\tlong_name\tcrap\nsrc/a.py\tf( )\t{value}\n", encoding="utf-8")
        git("add", "crapkit.toml", "crapkit-ratchet.tsv")
        git("commit", "-qm", f"mark {value}", when=date)
    assert git("status", "--porcelain") == ""
    assert len(_same_events_both_ways(tmp_path)) == 2

    result = subprocess.run([sys.executable, "-m", "crapkit", "ratchet", "report", "--json", "--enforce"],
                            cwd=tmp_path, text=True, encoding="utf-8", capture_output=True)
    report = json.loads(result.stdout)
    assert result.returncode != 0
    assert report["uncommitted"] == 0
    assert report["oldest"][0]["age_days"] == 90
    assert report["policy_violations"]
