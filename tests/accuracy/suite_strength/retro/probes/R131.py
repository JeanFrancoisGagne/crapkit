"""R131: `crapkit coverage --reuse-unchanged` reran a lane nothing it reads had
changed: after a cd between two runs, and when git did not ignore the lane's own
artifact.

    <retro venv python> R131.py WORKTREE

verdict_model's check reads `lanes.<name>.rerun_reason`, which the fix added, so
it cannot run on the commit before. This probe asks the question through the
CLI both commits have. It builds a committed repo with one function and one lane
that measures it through the coverage API and appends a line to a counter file
outside the repo each time it runs. `coverage` runs once, then
`coverage --reuse-unchanged` runs with nothing moved (it must reuse, or the probe
cannot tell anything and stops with a RuntimeError), then with only OLDPWD moved.
A second repo leaves the lane artifact untracked and not ignored. In both, the
lane must not run a second time.
"""
# requires: coverage==7.16.1
# source: POSIX cd (IEEE Std 1003.1-2024, utility cd, ENVIRONMENT VARIABLES) sets OLDPWD and PWD to record the shell's own previous and current directory, and a lane reads neither; README `--reuse-unchanged` reuses a lane whose stamp proves nothing it reads changed, and a lane reads its own artifact only by writing it
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

APP = "def f(x):\n    if x:\n        return 1\n    return 0\n"
LANE = """import os, sys
from pathlib import Path
import coverage
with open(os.environ["R131_COUNTER"], "a", encoding="utf-8") as handle:
    handle.write("run\\n")
cov = coverage.Coverage(branch=True, data_file=None, include=["src/*"])
cov.start()
sys.path.insert(0, "src")
import app
app.f(1)
cov.stop()
Path(".crapkit/cov").mkdir(parents=True, exist_ok=True)
cov.json_report(outfile=".crapkit/cov/py.json")
"""
CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = '"{python}" lane.py'
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["src"]
"""
IGNORE_ALL = ".crapkit/\n__pycache__/\n"
ARTIFACT_UNTRACKED = ".crapkit/*\n!.crapkit/cov/\n__pycache__/\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path, ignore: str) -> Path:
    repo = base / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_bytes(APP.encode())
    (repo / "lane.py").write_bytes(LANE.encode())
    (repo / "crapkit.toml").write_bytes(CONFIG.format(python=sys.executable).encode())
    (repo / ".gitignore").write_bytes(ignore.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _runs(counter: Path) -> int:
    return len(counter.read_bytes().splitlines()) if counter.is_file() else 0


def _coverage(repo: Path, counter: Path, oldpwd: str, *flags: str) -> int:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env.update(R131_COUNTER=str(counter), OLDPWD=oldpwd, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-m", "crapkit", "coverage", *flags], cwd=repo, env=env,
                          capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"crapkit coverage {' '.join(flags)} exited {done.returncode}: "
                           f"{done.stderr.decode(errors='replace')[-400:]}")
    return _runs(counter)


def _after_a_cd(base: Path) -> None:
    repo, counter = _repo(base, IGNORE_ALL), base / "runs.txt"
    _coverage(repo, counter, "/one")
    if _coverage(repo, counter, "/one", "--reuse-unchanged") != 1:
        raise RuntimeError("the lane reran with nothing moved, so a cd can show nothing")
    runs = _coverage(repo, counter, "/two", "--reuse-unchanged")
    assert runs == 1, f"a cd (OLDPWD /one to /two) made the unchanged lane run again: {runs} runs"


def _own_artifact_untracked(base: Path) -> None:
    repo, counter = _repo(base, ARTIFACT_UNTRACKED), base / "runs.txt"
    _coverage(repo, counter, "/one")
    runs = _coverage(repo, counter, "/one", "--reuse-unchanged")
    assert runs == 1, f"the lane's own untracked artifact made the unchanged lane run again: {runs} runs"


def main(argv: list[str]) -> int:
    for case in (_after_a_cd, _own_artifact_untracked):
        with tempfile.TemporaryDirectory(prefix="crapkit-r131-") as scratch:
            case(Path(scratch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
