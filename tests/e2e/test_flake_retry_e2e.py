"""Flake retry before exit 8: a new failure that passes a targeted rerun is a
flake, not a regression. Lanes opt in with retest_command; without it nothing
changes. Also: verify warns when the suite shrinks or skips more vs baseline."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# One lane script, four suite states driven by state.txt:
#   green    - flaky + steady both pass (the baseline)
#   red      - flaky fails in the full run, passes on --retest (a flake)
#   stillred - flaky fails in the full run AND on --retest (a real regression)
#   shrunk   - only steady remains, and it is skipped (suite decay)
LANE_SCRIPT = """
import json, os, sys
mode = open('state.txt', encoding='utf-8').read().strip()
retest = '--retest' in sys.argv
app = os.path.join(os.getcwd(), 'src', 'app.ts')
cov = {app: {'path': app,
             'fnMap': {'0': {'name': 'f', 'decl': {'start': {'line': 1}},
                             'loc': {'start': {'line': 1}, 'end': {'line': 3}}}},
             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}
with open('cov.json', 'w', encoding='utf-8') as fh:
    json.dump(cov, fh)
PASS = '<testcase classname="t" name="flaky"/>'
FAIL = '<testcase classname="t" name="flaky"><failure>boom</failure></testcase>'
STEADY = '<testcase classname="t" name="steady"/>'
SKIPPED = '<testcase classname="t" name="steady"><skipped/></testcase>'
if retest:
    body = FAIL if mode == 'stillred' else PASS
elif mode == 'green':
    body = PASS + STEADY
elif mode == 'shrunk':
    body = SKIPPED
else:
    body = FAIL + STEADY
with open('junit.xml', 'w', encoding='utf-8') as fh:
    fh.write('<testsuite>' + body + '</testsuite>')
"""

TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
    'parser = "istanbul"\nscopes = ["src"]\nresults_artifact = "junit.xml"\n'
    'retest_command = "python lane.py --retest {tests}"\n'
)


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=120, env=dict(os.environ))


@pytest.fixture()
def flaky_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "flaky"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.ts").write_text("export function f() { return 1; }\n", encoding="utf-8")
    (repo / "lane.py").write_text(LANE_SCRIPT, encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (repo / "state.txt").write_text("green", encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncov.json\njunit.xml\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=repo, check=True, capture_output=True)
    assert run_cli(repo, "coverage", "--json").returncode == 0  # green baseline
    return repo


def test_a_flake_passes_its_retry_and_verify_stays_green(flaky_repo: Path):
    (flaky_repo / "state.txt").write_text("red", encoding="utf-8")
    res = run_cli(flaky_repo, "verify")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "flake retry: 1 of 1" in res.stderr


def test_a_real_failure_survives_its_retry_and_exits_8(flaky_repo: Path):
    (flaky_repo / "state.txt").write_text("stillred", encoding="utf-8")
    res = run_cli(flaky_repo, "verify")
    assert res.returncode == 8, res.stdout + res.stderr
    assert "t::flaky" in res.stdout


def test_verify_json_reports_diff_uncovered(flaky_repo: Path):
    res = run_cli(flaky_repo, "verify", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["diff_uncovered_count"] == 0  # the fixture artifact has no statementMap
    assert out["diff_uncovered"] == []


def test_verify_warns_when_the_suite_shrinks_or_skips_more(flaky_repo: Path):
    (flaky_repo / "state.txt").write_text("shrunk", encoding="utf-8")
    res = run_cli(flaky_repo, "verify")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "fewer tests" in res.stderr
    assert "skips" in res.stderr
