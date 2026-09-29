"""R170: doctor split a lane command on whitespace while the rest of crapkit
reads it with the shell's word rules, and checked only the first word, so a
runner after `&&` that no shell can find passed doctor's lane check.

    <retro venv python> R170.py WORKTREE

verdict_model's check loads the lanes through inventory's reader and never runs
doctor, the reader the fix changed, so it passes at both commits. This probe
asks doctor. It builds a committed repo with one Python function and a pytest
lane whose command ends in `&& crapkit-probe-no-such-runner --report`, runs
`crapkit doctor`, and requires that doctor names that runner: the shell would
run it once pytest succeeds, and it exists nowhere.
"""
# requires: pytest==9.1.1 pytest-cov==7.1.0 coverage==7.16.1
# source: POSIX.1-2017 XCU 2.9.3 "AND Lists": in `a && b`, b runs when a exits 0, as it does under cmd.exe; no PATH entry holds crapkit-probe-no-such-runner, so the lane's second command cannot start
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER = "crapkit-probe-no-such-runner"
MOD = "def f(x):\n    if x:\n        return 1\n    return 0\n"
TEST = "from pylib.mod import f\n\n\ndef test_f():\n    assert f(1) == 1\n"
CONFIG = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]

[exclude]
globs = ["**/test_*.py"]

[[lane]]
name = "py"
command = '"{python}" -m pytest pylib -p no:cacheprovider --cov=pylib --cov-branch --cov-report=json:coverage-py.json -q && {runner} --report'
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
full_suite = false
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "pylib").mkdir(parents=True)
    for path, text in (("pylib/__init__.py", ""), ("pylib/mod.py", MOD), ("pylib/test_mod.py", TEST),
                       ("crapkit.toml", CONFIG.format(python=sys.executable, runner=RUNNER)),
                       (".gitignore", ".crapkit/\ncoverage-py.json\n.coverage\n__pycache__/\n")):
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _doctor(repo: Path) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    done = subprocess.run([sys.executable, "-m", "crapkit", "doctor"], cwd=repo,
                          env={**env, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True)
    told = (done.stdout + done.stderr).decode(errors="replace")
    if not told.strip():
        raise RuntimeError(f"crapkit doctor exited {done.returncode} and printed nothing")
    return told


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r170-") as scratch:
        told = _doctor(_repo(Path(scratch)))
    assert RUNNER in told, f"doctor never named {RUNNER}, which the lane runs after &&: {' '.join(told.split())[-300:]}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
