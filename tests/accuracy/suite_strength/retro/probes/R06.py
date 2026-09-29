"""R06: a failed verify served as the next verify's baseline, so running verify
again on the same broken tree passed.

    <retro venv python> R06.py WORKTREE

verdict_model's check reads the `kind` column of the runs table, which the fix
added, so it cannot run on the commit before. This probe asks the question
through the CLI both commits have. It builds a committed repo with one Python
function and a pytest lane that writes coverage.py JSON and JUnit, runs
`crapkit coverage`, then adds a failing test without committing it. The first
`verify` must fail (if it passes, the probe cannot tell anything and stops with a
RuntimeError). The second `verify`, on the same tree, must fail with the same
exit code.

Both commits refuse a coverage.py lane inside a container unless the lane says
`container_ok = true` (lanes.py at both: the host-only guard, which reads
/.dockerenv), and the retro job replays in the accuracy image, so the lane says it.
"""
# requires: pytest==9.1.1 pytest-cov==7.1.0 coverage==7.16.1
# source: README "The trusted baseline": a failed verify is not the baseline, verify keeps measuring against the run before it "so those findings stay visible"; and verify judges the working tree against a baseline, so a second verify on a tree nothing touched sees the same new failing test (a judgement that reads the same inputs gives the same verdict)
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

MOD = "def f(x):\n    if x:\n        return 1\n    return 0\n"
TEST = "from pylib.mod import f\n\n\ndef test_f():\n    assert f(1) == 1\n"
BROKEN = "def test_fresh_regression():\n    assert 1 == 2\n"
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
command = '"{python}" -m pytest pylib -p no:cacheprovider -p no:randomly --cov=pylib --cov-branch --cov-report=json:coverage-py.json --junitxml=junit.xml -q'
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
full_suite = false
container_ok = true
results_artifact = "junit.xml"
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
    (repo / ".gitignore").write_bytes(b".crapkit/\ncoverage-py.json\njunit.xml\n.coverage\n__pycache__/\n")
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


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r06-") as scratch:
        repo = _repo(Path(scratch))
        measured = _crapkit(repo, "coverage")
        if measured.returncode != 0:
            raise RuntimeError(f"crapkit coverage exited {measured.returncode}: "
                               f"{measured.stderr.decode(errors='replace')[-400:]}")
        (repo / "pylib" / "test_new.py").write_bytes(BROKEN.encode())
        first = _crapkit(repo, "verify").returncode
        if first == 0:
            raise RuntimeError("verify passed with a new failing test, so a rerun can show nothing")
        second = _crapkit(repo, "verify").returncode
        assert second == first, f"verify exited {first}, then {second} on the same broken tree"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
