"""R102: `crapkit explain FILE LINE` answered "no function matching" for a start
line, where `brief` already took one.

    <retro venv python> R102.py WORKTREE

verdict_model's check asks for one of two same-line twins by its start line, and
the twin it expects is chosen by a later fix, so the check fails at this bug's
fix commit on a question the fix never answered. This probe asks only this bug's
question. It builds a committed repo with two Python functions, measures it with
a pytest lane, and asks `explain` for the second function by name (it must
answer, or the probe cannot tell anything and stops with a RuntimeError), then by
its start line: the answer must name the same function.
"""
# requires: pytest==9.1.1 pytest-cov==7.1.0 coverage==7.16.1
# source: docs/agent-json.md, the explain row of the command table: "`NAME` takes a start line as of 0.4.5, the same form `brief` takes"; the function g below starts on line 6 of mod.py, counted by hand
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

MOD = ("def f(x):\n    if x:\n        return 1\n    return 0\n\n"
       "def g(y):\n    if y:\n        return 2\n    return 3\n")
G_LINE = "6"
TEST = ("from pylib.mod import f, g\n\n\ndef test_f():\n    assert f(1) == 1\n\n\n"
        "def test_g():\n    assert g(0) == 3\n")
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
command = '"{python}" -m pytest pylib -p no:cacheprovider -p no:randomly --cov=pylib --cov-branch --cov-report=json:coverage-py.json -q'
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
    (repo / "pylib" / "__init__.py").write_bytes(b"")
    (repo / "pylib" / "mod.py").write_bytes(MOD.encode())
    (repo / "pylib" / "test_mod.py").write_bytes(TEST.encode())
    (repo / "crapkit.toml").write_bytes(CONFIG.format(python=sys.executable).encode())
    (repo / ".gitignore").write_bytes(b".crapkit/\ncoverage-py.json\n.coverage\n__pycache__/\n")
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _crapkit(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=env,
                          capture_output=True)


def _text(done: subprocess.CompletedProcess) -> str:
    return (done.stdout + done.stderr).decode(errors="replace")


def _measured(repo: Path) -> None:
    """coverage, then explain by name: both must answer, or the probe can tell nothing."""
    measured = _crapkit(repo, "coverage")
    if measured.returncode != 0:
        raise RuntimeError(f"crapkit coverage exited {measured.returncode}: {_text(measured)[-400:]}")
    by_name = _crapkit(repo, "explain", "pylib/mod.py", "g")
    if by_name.returncode != 0 or "g( y )" not in _text(by_name):
        raise RuntimeError(f"explain by name exited {by_name.returncode}: {_text(by_name)[-400:]}")


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r102-") as scratch:
        repo = _repo(Path(scratch))
        _measured(repo)
        by_line = _crapkit(repo, "explain", "pylib/mod.py", G_LINE)
    answer = _text(by_line)
    assert by_line.returncode == 0 and "g( y )" in answer and "f( x )" not in answer, (
        f"explain pylib/mod.py {G_LINE} exited {by_line.returncode}: {' '.join(answer.split())[-300:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
