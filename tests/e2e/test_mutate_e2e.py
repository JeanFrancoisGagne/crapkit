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

# The scope declares its one file by name, the documented form for a flat
# repo: a bare `.` claims nothing in scoring, and from 0.5.0 mutate places
# mutants only in files scoring would score.
TOML = (
    '[crapkit]\ntarget = 6\n'
    'mutation_command = "python -m pytest test_clamp.py -q -p no:cacheprovider -p no:randomly"\n\n'
    '[[scope]]\nname = "py"\npaths = ["clamp.py"]\nlanguages = ["python"]\n'
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
    by name on stderr and the rest of the run still reports. The scope declares
    shell here so the corpus cut admits the file and the language refusal is
    what names it; in a python-only scope the corpus cut names it first."""
    (clamp_repo / "deploy.sh").write_text(SHELL, encoding="utf-8")
    toml = clamp_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'paths = ["clamp.py"]\nlanguages = ["python"]',
        'paths = ["clamp.py", "deploy.sh"]\nlanguages = ["python", "shell"]'), encoding="utf-8")

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


# --- the corpus cut: a test file in the diff never grows a mutant ---------------

EDGE = (
    "from clamp import clamp\n"
    "def test_edge():\n"
    "    assert clamp(10) == 10\n"
)

NEW_ASSERT = "    assert clamp(0) == 0\n"


@pytest.fixture()
def tested_repo(clamp_repo: Path) -> Path:
    """clamp_repo plus a committed test under tests/, with the scope claiming
    that directory by prefix: the tree the corpus cut is about. Scoring never
    scores tests/ whatever the scope says, and from 0.5.0 mutate never mutates it."""
    toml = clamp_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'paths = ["clamp.py"]', 'paths = ["clamp.py", "tests"]'), encoding="utf-8")
    (clamp_repo / "tests").mkdir()
    (clamp_repo / "tests" / "test_edge.py").write_text(EDGE, encoding="utf-8")
    commit(clamp_repo, "crapkit.toml", "tests/test_edge.py")
    return clamp_repo


def test_a_test_file_in_the_diff_grows_no_mutants(tested_repo: Path):
    """The diff touches clamp.py and tests/test_edge.py. The guard line grows its
    two mutants; the new assertion under tests/ grows none, because the diff's
    files pass through the corpus predicate scoring uses (scopes, excludes and
    the test-file cut) before a mutant is placed. On 0.4.15 the assertion's
    `==` was the third mutant and a survivor in a test read as a real hole."""
    (tested_repo / "clamp.py").write_text(CLAMP.replace("x > 10:", "x > 10:  # ceiling"),
                                          encoding="utf-8")
    (tested_repo / "tests" / "test_edge.py").write_text(EDGE + NEW_ASSERT, encoding="utf-8")

    res = run_cli(tested_repo, "mutate", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["mutants"] == 2, "clamp.py's guard line alone; the assertion under tests/ grew none"
    assert out["outside_corpus"] == ["tests/test_edge.py"]
    assert {m["path"] for m in out["survivors"]} == {"clamp.py"}
    assert "not mutating tests/test_edge.py" in res.stderr


def test_a_diff_that_touches_only_a_test_says_nothing_to_mutate(tested_repo: Path):
    """Zero mutants, exit 0, and stdout says why. The mutation_command here
    cannot pass, so an exit 0 also proves the suite was never started for a
    diff with nothing left to mutate."""
    toml = tested_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        TOML.splitlines()[2], "mutation_command = 'python -c \"raise SystemExit(1)\"'"),
        encoding="utf-8")
    commit(tested_repo, "crapkit.toml")  # so the diff holds the test file alone
    (tested_repo / "tests" / "test_edge.py").write_text(EDGE + NEW_ASSERT, encoding="utf-8")

    text = run_cli(tested_repo, "mutate")
    as_json = run_cli(tested_repo, "mutate", "--json")

    assert text.returncode == 0, text.stdout + text.stderr
    assert text.stdout.strip() == ("mutation: nothing to mutate; outside the scored corpus "
                                   "(scopes, excludes, test files, max_file_bytes): tests/test_edge.py")
    assert as_json.returncode == 0, as_json.stdout + as_json.stderr
    out = json.loads(as_json.stdout)
    assert (out["mutants"], out["outside_corpus"]) == (0, ["tests/test_edge.py"])


def test_files_naming_a_test_file_still_never_mutates_it(tested_repo: Path):
    """`--files` widens the line scope to the whole file; it does not widen the
    corpus. `rescore PATH` puts its explicit paths through the same predicate."""
    res = run_cli(tested_repo, "mutate", "--files", "tests/test_edge.py", "clamp.py", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["mutants"] == 2, "clamp.py's two, and none from the test file named beside it"
    assert out["outside_corpus"] == ["tests/test_edge.py"]
    assert "not mutating tests/test_edge.py: outside the scored corpus" in res.stderr


# --- the other cuts: an exclude glob and the byte ceiling name the file too ------

GEN = (
    "def client(x):\n"
    "    if x > 1:\n"
    "        return 1\n"
    "    return x\n"
)


def test_an_excluded_path_in_the_diff_grows_no_mutants(clamp_repo: Path):
    """`[exclude] globs` is the second cut. gen/client.py sits under a declared
    scope path and holds a comparison a mutant could flip; the glob takes it out
    of the corpus, so the diff's mutants are clamp.py's two and the excluded
    file is named on stderr with the same reason a test file gets."""
    toml = clamp_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'paths = ["clamp.py"]', 'paths = ["clamp.py", "gen"]')
        + '\n[exclude]\nglobs = ["gen/**"]\n', encoding="utf-8")
    (clamp_repo / "gen").mkdir()
    (clamp_repo / "gen" / "client.py").write_text(GEN, encoding="utf-8")
    commit(clamp_repo, "crapkit.toml", "gen/client.py")
    (clamp_repo / "clamp.py").write_text(CLAMP.replace("x > 10:", "x > 10:  # ceiling"),
                                         encoding="utf-8")
    (clamp_repo / "gen" / "client.py").write_text(GEN.replace("x > 1:", "x > 1:  # generated"),
                                                  encoding="utf-8")

    res = run_cli(clamp_repo, "mutate", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["mutants"] == 2, "clamp.py's guard line alone; the excluded file grew none"
    assert out["outside_corpus"] == ["gen/client.py"]
    assert ("crapkit: not mutating gen/client.py: outside the scored corpus "
            "(scopes, excludes, test files, max_file_bytes)") in res.stderr


def test_a_file_over_max_file_bytes_reads_a_reason_that_names_the_ceiling(clamp_repo: Path):
    """The byte ceiling is the fourth cut the corpus predicate makes, and the
    reason a user reads must name it: clamp.py grows to 71 bytes under a
    40-byte `max_file_bytes`, `doctor` on the same tree says `skipped: over
    max_file_bytes`, and `mutate` says so in the same sentence it uses for the
    other cuts. Nothing is left, so the suite never starts and stdout says why."""
    toml = clamp_repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[exclude]\nmax_file_bytes = 40\n",
                    encoding="utf-8")
    commit(clamp_repo, "crapkit.toml")
    (clamp_repo / "clamp.py").write_text(CLAMP.replace("x > 10:", "x > 10:  # ceiling"),
                                         encoding="utf-8")

    res = run_cli(clamp_repo, "mutate")

    assert res.returncode == 0, res.stdout + res.stderr
    assert ("crapkit: not mutating clamp.py: outside the scored corpus "
            "(scopes, excludes, test files, max_file_bytes)") in res.stderr
    assert res.stdout.strip() == ("mutation: nothing to mutate; outside the scored corpus "
                                  "(scopes, excludes, test files, max_file_bytes): clamp.py")
