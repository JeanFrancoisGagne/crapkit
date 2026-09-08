"""A lane may replace its report directory while it owns measurement outputs."""
import json
import sys

from crapkit.config import Lane
from crapkit.lanes import run_lane


RUNNER = """import json, shutil
from pathlib import Path
reports = Path('reports')
shutil.rmtree(reports)
reports.mkdir()
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': 1},
    'statementMap': {'0': loc}, 's': {'0': 1}, 'branchMap': {}, 'b': {}}}
(reports/'cov.json').write_text(json.dumps(data), encoding='utf-8')
(reports/'junit.xml').write_text('<testsuites><testsuite tests="1">'
    '<testcase classname="tests.test_app" name="test_f"/>'
    '</testsuite></testsuites>', encoding='utf-8')
"""


def test_runner_can_delete_and_recreate_its_owned_report_directory(tmp_path, monkeypatch):
    home = tmp_path / "private-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "stale.txt").write_text("remove this old output", encoding="utf-8")
    (tmp_path / "runner.py").write_text(RUNNER, encoding="utf-8")
    lane = Lane("reports", f'"{sys.executable}" runner.py', "reports/cov.json", "istanbul", (),
                results_artifact="reports/junit.xml", timeout_seconds=20)
    outcome = run_lane(tmp_path, lane)
    assert outcome.provenance["exit_code"] == 0
    assert outcome.provenance["tests_total"] == 1
    assert outcome.provenance["failures"] == []
    assert len(outcome.coverage) == 1
    assert json.loads((reports / "cov.json").read_text(encoding="utf-8"))["src/app.js"]["f"] == {"0": 1}
    assert not (reports / "stale.txt").exists()
