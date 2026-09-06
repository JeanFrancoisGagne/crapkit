"""Committed value changes keep debt age and working-tree attribution accurate."""
import json
import os
import subprocess
import sys

import pytest

from crapkit.ratchet_report import mark_events, report_from_events


@pytest.mark.parametrize("value", ["nan", "inf", "-Infinity", "1e999"])
def test_history_cannot_admit_nonfinite_marks(value):
    report = report_from_events(mark_events([(1000, f"+a.py\tf( )\t{value}")]))
    assert report["open"] == 0


def test_history_keeps_a_source_file_literally_named_path():
    report = report_from_events(mark_events([(1000, "+path\tf( )\t20")]))
    assert report["open"] == 1


@pytest.mark.parametrize("value", [20, 70])
def test_committed_value_change_keeps_entry_age_and_updates_current_value(value):
    patches = [(1000, "+src/a.py\tf( )\t50"),
               (1000 + 90 * 86400, f"-src/a.py\tf( )\t50\n+src/a.py\tf( )\t{value}")]
    report = report_from_events(mark_events(patches), {("src/a.py", "f( )"): value})
    assert report["anchor_ts"] == 1000 + 90 * 86400
    assert report["oldest"][0]["age_days"] == 90
    assert report["uncommitted"] == 0
    assert report["dropped_total"] == 0


def test_comment_only_ratchet_commit_advances_the_documented_history_clock():
    patches = [(1000, "+src/a.py\tf( )\t50"),
               (1000 + 90 * 86400, "-# previous note\n+# updated note")]
    report = report_from_events(mark_events(patches), {("src/a.py", "f( )"): 50})
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

    result = subprocess.run([sys.executable, "-m", "crapkit", "ratchet", "report", "--json", "--enforce"],
                            cwd=tmp_path, text=True, encoding="utf-8", capture_output=True)
    report = json.loads(result.stdout)
    assert result.returncode != 0
    assert report["uncommitted"] == 0
    assert report["oldest"][0]["age_days"] == 90
    assert report["policy_violations"]
