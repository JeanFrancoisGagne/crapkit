"""The hook's two git reads start before the analysis stack is imported.

Neither answer is needed until both have been paid for: on Windows each git
spawn costs ~18ms of process creation and importing lizard costs ~31ms, and the
staged diff and `cat-file --batch` do not depend on the import at all. Starting
them first runs the spawns underneath it.

The order is the whole change, so the order is what is asserted — and with it
the contract it could quietly break: an installation with no lizard still exits
5 with the same sentence, rather than reporting a gate verdict it cannot have.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.cli import build_parser, cmd_hook_precommit, verifying
from crapkit.errors import GitError

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SRC = str(Path(gitio.__file__).resolve().parent.parent)

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   text=True, encoding="utf-8")


@pytest.fixture()
def staged(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (repo / "src" / "base.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    (repo / "src" / "mod.py").write_text("def fn(n):\n    return n + 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    return repo


def hook_args(repo: Path):
    return build_parser().parse_args(["hook-precommit", "--repo", str(repo)])


def test_the_staged_reads_start_before_the_analysis_import(staged, monkeypatch, capsys):
    order: list[str] = []
    real_reads, real_tools = gitio.staged_reads, verifying._analysis_tools

    def traced_reads(root):
        order.append("git")
        return real_reads(root)

    def traced_tools():
        order.append("analysis")
        return real_tools()

    monkeypatch.setattr(gitio, "staged_reads", traced_reads)
    monkeypatch.setattr(verifying, "_analysis_tools", traced_tools)

    assert cmd_hook_precommit(hook_args(staged)) == 0
    capsys.readouterr()

    assert order == ["git", "analysis"]


def test_an_unstaged_tree_still_closes_the_reads_it_started(staged, capsys):
    """Nothing staged means the batch process is never asked for a blob; it still
    has to be shut down rather than left holding a pipe."""
    git(staged, "reset", "-q")

    assert cmd_hook_precommit(hook_args(staged)) == 0
    capsys.readouterr()


def test_a_failing_git_read_still_reaches_the_caller_as_a_git_error(tmp_path, capsys):
    """A started process reports its own failure: outside a repo the staged diff
    exits nonzero, and that has to surface as GitError (exit 4), not as an empty
    diff that reads like a commit with nothing in it."""
    loose = tmp_path / "loose"
    (loose / "src").mkdir(parents=True)
    (loose / "crapkit.toml").write_text(CONFIG, encoding="utf-8")

    with pytest.raises(GitError) as exc:
        cmd_hook_precommit(hook_args(loose))

    assert exc.value.exit_code == 4
    assert "git diff --cached" in str(exc.value)


def test_a_missing_lizard_still_exits_five_with_the_same_sentence(staged):
    """The gate must never answer without the analyzer that decides it. The shim
    first on PYTHONPATH makes `import lizard` fail the way an absent install does."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(FIXTURES / "shims" / "lizard"), SRC])

    res = subprocess.run([sys.executable, "-m", "crapkit", "hook-precommit"], cwd=staged,
                         capture_output=True, text=True, timeout=180, env=env)

    assert res.returncode == 5, (res.returncode, res.stdout, res.stderr)
    assert "required analysis tool unavailable" in res.stderr
    assert "crapkit gate" not in res.stdout, "no verdict without the analyzer"
