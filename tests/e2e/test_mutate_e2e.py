"""Mutation e2e: a boundary hole the tests do not pin (clamp(10)) leaves the
`> -> >=` mutant alive; the negation mutant dies. The source file must come
back byte-identical whatever happened."""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

CLAMP = (
    "def clamp(x):\n"
    "    if x > 10:\n"
    "        return 10\n"
    "    return x\n"
)

TESTS = (
    "from clamp import clamp\n"
    "def test_over():\n"
    "    assert clamp(11) == 10\n"
    "def test_under():\n"
    "    assert clamp(5) == 5\n"
)

TOML = (
    '[crapkit]\ntarget = 6\n'
    'mutation_command = "python -m pytest test_clamp.py -q -p no:cacheprovider -p no:randomly"\n\n'
    '[[scope]]\nname = "py"\npaths = ["."]\nlanguages = ["python"]\n'
)


run_cli = cli_runner(timeout=300)


@pytest.fixture()
def clamp_repo(tmp_path: Path) -> Path:
    (tmp_path / "clamp.py").write_text(CLAMP, encoding="utf-8")
    (tmp_path / "test_clamp.py").write_text(TESTS, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_boundary_mutant_survives_and_negation_dies(clamp_repo: Path):
    before = (clamp_repo / "clamp.py").read_bytes()
    res = run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["mutants"] == 2
    assert out["killed"] == 1
    (survivor,) = out["survivors"]
    assert survivor["op"] == "> -> >=", "clamp(10) is untested, so >= behaves identically"
    assert (clamp_repo / "clamp.py").read_bytes() == before, "the original always comes back"


SHELL = 'save() {\n  echo "$1" > out.txt\n}\n'


def test_a_shell_file_is_named_and_skipped_not_mutated(clamp_repo: Path):
    """`mutate` is diff-scoped, so a changed .sh file arrives beside the .py ones
    it was aimed at. Its mutants would flip redirections, so the file is refused
    by name on stderr and the rest of the run still reports."""
    (clamp_repo / "deploy.sh").write_text(SHELL, encoding="utf-8")

    res = run_cli(clamp_repo, "mutate", "--files", "deploy.sh", "clamp.py", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["mutants"] == 2, "clamp.py's two mutants, and no more"
    assert "deploy.sh" in res.stderr and "redirection" in res.stderr


def test_mutate_without_changes_or_files_says_so(clamp_repo: Path):
    res = run_cli(clamp_repo, "mutate")
    assert res.returncode != 0
    assert "--files" in res.stderr


def set_workers(repo: Path, workers: int) -> None:
    toml = repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        "target = 6", f"target = 6\nmutation_workers = {workers}"), encoding="utf-8")


def commit(repo: Path, *paths: str) -> None:
    """Worktrees check out HEAD, so fixture files a worker needs must be in it."""
    subprocess.run(["git", "add", *paths], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "-m", "fixture"], cwd=repo, check=True, capture_output=True)


def worktrees(repo: Path) -> list[str]:
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("worktree ")]


def pooled(repo: Path) -> list[str]:
    """Worker worktrees, which live in the pool and only there. A path anywhere
    else is a leak: the run's own temp base, or a tree left registered."""
    pool = str(repo / ".crapkit" / "mutate-pool").replace("\\", "/")
    return [line for line in worktrees(repo)[1:] if line[len("worktree "):].startswith(pool)]


def test_two_workers_report_byte_identically_to_one(clamp_repo: Path):
    serial = run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json")
    assert serial.returncode == 0, serial.stdout + serial.stderr
    set_workers(clamp_repo, 2)
    parallel = run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json")
    assert parallel.returncode == 0, parallel.stdout + parallel.stderr
    assert parallel.stdout == serial.stdout, "worker count must not move the verdict"
    assert len(pooled(clamp_repo)) == 2, "the two worker trees are kept, in the pool"
    assert len(worktrees(clamp_repo)) == 3, "and nothing was left anywhere else"


def test_a_second_pooled_run_reports_byte_identically_to_the_first(clamp_repo: Path):
    """The pool's whole claim: run two reuses run one's trees and reads the same
    verdict out of them. A tree still holding run one's mutant, or missing the
    file run one deleted, would show up here as a different survivor list."""
    set_workers(clamp_repo, 2)
    cold = run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json")
    assert cold.returncode == 0, cold.stdout + cold.stderr
    trees = pooled(clamp_repo)

    warm = run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json")

    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout
    assert pooled(clamp_repo) == trees, "the same two trees, not two more"


def test_drop_pool_removes_the_kept_worktrees(clamp_repo: Path):
    set_workers(clamp_repo, 2)
    assert run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json").returncode == 0
    assert len(pooled(clamp_repo)) == 2

    res = run_cli(clamp_repo, "mutate", "--drop-pool")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "removed 2 pooled worktrees" in res.stdout
    assert len(worktrees(clamp_repo)) == 1, "the admin entries went with the directories"
    assert not (clamp_repo / ".crapkit" / "mutate-pool").exists()


FLOOR = (
    "\n"
    "def floor(x):\n"
    "    if x < 0:\n"
    "        return 0\n"
    "    return x\n"
)


def test_workers_mutate_uncommitted_lines_not_the_committed_file(clamp_repo: Path):
    """Diff-scoped mutate targets working-tree changes, and a fresh worktree is
    a checkout of HEAD: without the copy step floor() is not there to mutate."""
    src = clamp_repo / "clamp.py"
    src.write_text(CLAMP + FLOOR, encoding="utf-8")
    serial = run_cli(clamp_repo, "mutate", "--json")
    assert serial.returncode == 0, serial.stdout + serial.stderr
    assert json.loads(serial.stdout)["survived"] == 2, "nothing tests floor()"
    set_workers(clamp_repo, 3)
    commit(clamp_repo, "crapkit.toml")  # so both runs see one changed file: clamp.py
    parallel = run_cli(clamp_repo, "mutate", "--json")
    assert parallel.returncode == 0, parallel.stdout + parallel.stderr
    assert parallel.stdout == serial.stdout
    assert len(pooled(clamp_repo)) == 2, "two mutants, so two trees however many workers"


SABOTAGE = "import os\nos.remove('clamp.py')\nos.mkdir('clamp.py')\n"


def test_a_broken_run_leaves_the_pool_usable_and_the_live_tree_alone(clamp_repo: Path):
    """The command leaves a directory where its source file was, so restoring
    the original raises inside the worker. The pooled trees stay — the next
    run's re-prepare is what a dirty tree is for — and the run still fails."""
    before = (clamp_repo / "clamp.py").read_bytes()
    (clamp_repo / "sabotage.py").write_text(SABOTAGE, encoding="utf-8")
    toml = clamp_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        TOML.splitlines()[2], 'mutation_command = "python sabotage.py"'), encoding="utf-8")
    set_workers(clamp_repo, 2)
    commit(clamp_repo, "crapkit.toml", "sabotage.py")
    res = run_cli(clamp_repo, "mutate", "--files", "clamp.py", "--json")
    assert res.returncode != 0, "a worker that cannot restore its file must not report success"
    assert len(worktrees(clamp_repo)) == 3, "the pool is where the trees are, and all of it"
    assert (clamp_repo / "clamp.py").read_bytes() == before, "the live tree is untouched"

    after = run_cli(clamp_repo, "mutate", "--drop-pool")
    assert after.returncode == 0 and "removed 2" in after.stdout, after.stdout + after.stderr
