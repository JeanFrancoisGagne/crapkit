"""A git question that fails is named, and the reader takes its safe action.

Three readers turned a failed git read into an answer. `ratchet prune` read a
missing anchor commit as "nothing was renamed" and deleted a renamed file's
debt marks as repaid. verify read "this clone does not hold the baseline
commit" as "a rebase or amend rewrote history" and sent the reader after a
fresh baseline. The Action read a failed base diff as an empty change list and
logged "0 changed file(s)". Each case here builds the clone that makes git
fail and asserts what the reader says and does instead.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from conftest import cli_runner, git_commit_all, git_init_repo

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""

run_cli = cli_runner(env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "gc.auto=0",
                          *args], cwd=repo, check=True, capture_output=True, text=True,
                         encoding="utf-8")
    return res.stdout


def tangled(name: str) -> str:
    """Seven decisions: ccn 8, over the ceiling of 6 in a cc-only scope."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def cc_only_repo(repo: Path, files: dict[str, str]) -> Path:
    """One cc-only Python scope, committed on `main`, crapkit's state ignored."""
    repo.mkdir(parents=True)
    git_init_repo(repo)
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git_commit_all(repo, "init")
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def clone_with_store(src: Path, dst: Path, *clone_args: str) -> Path:
    """A clone with the source's .crapkit copied in, the way a CI cache restore
    or a store carried over from another checkout arrives."""
    subprocess.run(["git", "clone", "-q", *clone_args, src.as_uri(), str(dst)],
                   check=True, capture_output=True)
    shutil.copytree(src / ".crapkit", dst / ".crapkit")
    return dst


# --- verify: a baseline commit this clone does not hold -------------------------

def test_a_baseline_commit_this_clone_never_fetched_is_named_as_missing(tmp_path: Path):
    """The store came from a checkout that measured a side branch; this clone
    fetched main alone. Nothing rewrote history, so the fix is a fetch, and the
    refusal names the commit and the command that brings it. Same exit 4."""
    origin = cc_only_repo(tmp_path / "origin", {"src/app.py": "def f(n):\n    return n\n"})
    git(origin, "checkout", "-q", "-b", "side")
    (origin / "src" / "side.py").write_text("def g(n):\n    return n\n", encoding="utf-8")
    git_commit_all(origin, "side work")
    assert run_cli(origin, "coverage").returncode == 0
    missing = head(origin)
    git(origin, "checkout", "-q", "main")
    clone = clone_with_store(origin, tmp_path / "clone", "--single-branch", "--branch", "main")

    res = run_cli(clone, "verify", "--no-tighten")

    assert res.returncode == 4, res.stdout + res.stderr
    assert f"baseline commit {missing[:11]} is not in this clone" in res.stderr, res.stderr
    assert f"git fetch origin {missing}" in res.stderr, res.stderr
    assert "rewrote history" not in res.stderr, res.stderr
    assert "shallow" not in res.stderr, res.stderr
