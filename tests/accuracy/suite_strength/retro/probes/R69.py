"""R69: a JUnit report that says a pytest-xdist worker crashed was read as a
finished suite, so `crapkit coverage` scored the lane's partial coverage as a
full measurement.

    <retro venv python> R69.py WORKTREE

verdict_model's check reads coverage --json's lane_failures, which the fix
commit does not write yet. This probe asks the question through the exit code
both commits have. It builds a committed repo with one JavaScript function and
a lane that writes an istanbul artifact and the JUnit report pytest 9.1 wrote
when worker gw0 crashed (tests/accuracy/verdict_model/recorded/pytest-9.1/
worker_crash.xml, byte for byte), and runs `crapkit coverage`: the lane must
fail, which is exit 5.
"""
# source: docs/lanes.md "A junit that says the run did not finish": an `<error>` naming `worker 'gwN' crashed while running '<nodeid>'` fails the lane with exit 5, exactly the way a missing coverage artifact does
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CRASH = ('<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite '
         'name="pytest" errors="1" failures="0" skipped="0" tests="2" time="6.093" '
         'timestamp="recorded" hostname="recorded"><testcase classname="tests.test_crash" '
         'name="test_dies" time="0.001"><error message="failed on setup with &quot;worker \'gw0\' '
         'crashed while running \'tests/test_crash.py::test_dies\'&quot;">worker \'gw0\' crashed '
         'while running \'tests/test_crash.py::test_dies\'</error></testcase><testcase '
         'classname="tests.test_crash" name="test_lives" time="0.001" /></testsuite></testsuites>')
RUNNER = """import json
from pathlib import Path
folder = Path('.crapkit')
folder.mkdir(exist_ok=True)
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': 1},
    'statementMap': {'0': loc}, 's': {'0': 1}, 'branchMap': {}, 'b': {}}}
(folder / 'cov.json').write_text(json.dumps(data), encoding='utf-8')
(folder / 'junit.xml').write_bytes(Path('crash.xml').read_bytes())
"""
FAILED_LANE = 5


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    command = json.dumps(f'"{sys.executable}" measure.py')
    config = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n\n'
              f'[[lane]]\nname = "js"\nparser = "istanbul"\nscopes = ["src"]\ncommand = {command}\n'
              'artifact = ".crapkit/cov.json"\nresults_artifact = ".crapkit/junit.xml"\n')
    files = {"src/app.js": "function f() {\n  return 1;\n}\n", "measure.py": RUNNER,
             "crash.xml": CRASH, ".gitignore": ".crapkit/\n__pycache__/\n", "crapkit.toml": config}
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def main(argv: list[str]) -> int:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="crapkit-r69-") as scratch:
        done = subprocess.run([sys.executable, "-m", "crapkit", "coverage"], cwd=_repo(Path(scratch)),
                              env=env, capture_output=True)
    told = (done.stdout + done.stderr).decode(errors="replace")
    if done.returncode not in (0, FAILED_LANE):
        raise RuntimeError(f"crapkit coverage exited {done.returncode}: {told[-400:]}")
    assert done.returncode == FAILED_LANE, f"a crashed worker's report scored the lane: exit 0, {' '.join(told.split())[-200:]}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
