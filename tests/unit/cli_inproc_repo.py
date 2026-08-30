"""A tiny git repo the CLI can be driven over in-process, plus canned artifacts.

`main(["coverage", "--repo", str(repo)])` is the whole seam these tests use: the
same entry point `python -m crapkit` reaches, minus the process. What made that
expensive was never the CLI, it was `git init` + `git add` + `git commit`, so the
repo is built once per session and copied per test (measured: 0.20 s to build,
0.03 s to copy).

The lanes parse artifacts this module writes rather than artifacts a lane
command produced, which is what `--reuse-artifacts` is for. No lane subprocess
runs unless a test asks for one by name.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_TS = """export function dispatch(kind: string): number {
  switch (kind) {
    case "a": return 1;
    case "b": return 2;
    case "c": return 3;
    case "d": return 4;
    case "e": return 5;
    case "f": return 6;
    default: return 0;
  }
}

export function plain(x: number): number {
  if (x > 10) {
    return x;
  }
  return -x;
}
"""

UI_TS = """export function render(n: number): string {
  return n > 0 ? "up" : "down";
}
"""

# A function well past the ceiling of 6, for the gate paths. Appended to a file
# so the change lands in a diff range the gate actually looks at.
KNOTTY = """
export function knotty(n: number): number {
  if (n > 1) { return 1; }
  if (n > 2) { return 2; }
  if (n > 3) { return 3; }
  if (n > 4) { return 4; }
  if (n > 5) { return 5; }
  if (n > 6) { return 6; }
  if (n > 7) { return 7; }
  return 0;
}
"""

ALERT_PY = """import sys

with open("alerts.log", "a", encoding="utf-8") as fh:
    fh.write(sys.stdin.read())
"""

TOML = """[crapkit]
target = 6
alert_command = "python append_alert.py"

[crapkit.scoped_tests]
src = "python -c pass"

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[scope]]
name = "web"
paths = ["web"]
languages = ["typescript"]

[[lane]]
name = "unit"
command = "python -c pass"
artifact = "coverage/unit.json"
parser = "istanbul"
scopes = ["src"]

[[lane]]
name = "ui"
command = "python -c pass"
artifact = "coverage/ui.json"
parser = "istanbul"
scopes = ["web"]
"""

GITIGNORE = ".crapkit/\ncoverage/\nalerts.log\n"


def git(root: Path, *args: str) -> str:
    """One git command in `root`, refusing to continue on a non-zero exit."""
    done = subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True)
    return done.stdout


def commit_all(root: Path, message: str) -> None:
    """Stage everything and commit it under a fixed identity, signing disabled:
    a developer's global `commit.gpgsign = true` must not stall the suite."""
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)


def _build(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "web").mkdir(parents=True)
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8")
    (root / "web" / "ui.ts").write_text(UI_TS, encoding="utf-8")
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (root / "append_alert.py").write_text(ALERT_PY, encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "init")
    return root


@pytest.fixture(scope="session")
def template_repo(tmp_path_factory) -> Path:
    return _build(tmp_path_factory.mktemp("crapkit-template"))


@pytest.fixture()
def repo(template_repo: Path, tmp_path: Path) -> Path:
    """A private copy of the template, one per test."""
    dest = tmp_path / "repo"
    shutil.copytree(template_repo, dest)
    return dest


# --- canned lane artifacts ----------------------------------------------------

def _fn_entry(name: str, start: int, end: int) -> dict:
    return {"name": name, "decl": {"start": {"line": start}},
            "loc": {"start": {"line": start}, "end": {"line": end}}}


def istanbul(root: Path, rel: str, source: str, functions: dict,
             dead: tuple[int, ...] = ()) -> None:
    """Write an istanbul artifact for `source`, `functions` being
    name -> (start line, end line, branches covered of two).

    `dead` names lines whose statement never ran, which is the line-level truth
    the diff-coverage check reads. The absolute path is the key, the way
    @vitest/coverage-v8 writes it, so the parser has a root to relativize against.
    """
    abs_path = str((root / source).resolve())
    fn_map, hits, branch_map, branches = {}, {}, {}, {}
    for i, (name, (start, end, covered)) in enumerate(sorted(functions.items())):
        fn_map[str(i)] = _fn_entry(name, start, end)
        hits[str(i)] = 1
        branch_map[str(i)] = {"loc": {"start": {"line": start}},
                              "locations": [{"start": {"line": start}},
                                            {"start": {"line": start}}]}
        branches[str(i)] = [1, 1] if covered == 2 else [covered, 0]
    stmt_map = {str(i): {"start": {"line": line}, "end": {"line": line}}
                for i, line in enumerate(dead)}
    payload = {abs_path: {"path": abs_path, "fnMap": fn_map, "f": hits,
                          "branchMap": branch_map, "b": branches,
                          "statementMap": stmt_map, "s": {k: 0 for k in stmt_map}}}
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")


def seed_artifacts(root: Path, *, unit: bool = True, ui: bool = True) -> None:
    """Both lanes' artifacts as they stand after a clean run of the template."""
    if unit:
        istanbul(root, "coverage/unit.json", "src/app.ts",
                 {"dispatch": (1, 13, 2), "plain": (13, 20, 1)})
    if ui:
        istanbul(root, "coverage/ui.json", "web/ui.ts", {"render": (1, 3, 2)})


def add_knotty(root: Path, source: str = "src/app.ts") -> None:
    """Append a function over the ceiling; the append is the changed range."""
    with open(root / source, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(KNOTTY)
