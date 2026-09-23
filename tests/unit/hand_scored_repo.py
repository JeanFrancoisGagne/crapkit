"""A committed git repo whose store holds runs written by hand.

The read commands (`next-item`, `brief`, `worklist`, `duplication`) need a run in
the store and a HEAD to compare it against, and nothing else: no lane has to run
for them to answer. Writing the run directly pins every number a test asserts,
so an expected value is arithmetic on the rows below rather than whatever a lane
happened to measure.
"""
from __future__ import annotations

import subprocess
from contextlib import closing
from pathlib import Path

from crapkit.cli import main
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

TOML = """[crapkit]
target = {target}
worklist_floor = 1

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest -q"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["src"]
"""

APP = "".join(f"# line {i}\n" for i in range(1, 60))


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                          cwd=root, check=True, capture_output=True, text=True,
                          encoding="utf-8")
    return done.stdout.strip()


def make_repo(root: Path, *, target: int = 6, files: dict | None = None) -> Path:
    """A repo on `main` holding `files` (src/app.py by default), one commit."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    write_toml(root, target)
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8", newline="\n")
    for rel, text in (files or {"src/app.py": APP}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    (root / ".crapkit").mkdir(exist_ok=True)
    return root


def write_toml(root: Path, target: int) -> None:
    """crapkit.toml with this repo ceiling. Called again after the run is written,
    it is the uncommitted ceiling edit: HEAD does not move."""
    (root / "crapkit.toml").write_text(TOML.format(target=target), encoding="utf-8",
                                       newline="\n")


def scored(name: str, start: int, end: int, *, ccn: int, cov: float, crap: float,
           remedy: str, flag: str = "measured", path: str = "src/app.py",
           occurrence: int = 1) -> ScoredRow:
    return ScoredRow("src", path, name, start, end, ccn, ccn, ccn, end - start + 1, 0, 0,
                     cov, flag, crap, remedy, 0, occurrence)


def write_run(root: Path, rows: list, *, kind: str = "coverage") -> int:
    """One run at HEAD holding exactly `rows`."""
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    with closing(store._conn):
        return store.write_run(commit=git(root, "rev-parse", "HEAD"),
                               tool_versions={"crapkit": "0", "lizard": "0"},
                               rows=rows, lanes={"py": {}}, kind=kind)


def run(root: Path, capsys, *argv: str) -> tuple[int, str, str]:
    """`crapkit <argv> --repo root` in process: exit code, stdout, stderr."""
    code = main([*argv, "--repo", str(root)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err
