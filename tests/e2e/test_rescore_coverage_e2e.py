"""`rescore --coverage PATH --gate` fails a changed function on a scoped run's CRAP (#76).

The caller runs its own selection of tests with the lane's coverage flags and
hands the artifact over. The branch below rewrites `fork` at ccn 5, one under the
ceiling, so the ccn-only gate passes it whatever its tests walk. With one test
walking only the fall-through path, the scoped run leaves half of it dark and the
gate exits 6; a second test that walks every branch clears it.
"""
import sys
from pathlib import Path

import hang_guard
from conftest import child_env, cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})

TOML = """[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest -p no:cacheprovider --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json -q"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["calc"]
"""

BEFORE = "def fork(n):\n    return n\n"
AFTER = ("def fork(n):\n"
         + "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(4))
         + "    return n\n")
ONE_PATH = "from calc.grade import fork\n\n\ndef test_fall_through():\n    assert fork(-1) == -1\n"
EVERY_PATH = ONE_PATH + "\n\ndef test_every_branch():\n    assert fork(10) == 14\n"

# The suite's own subprocess coverage stays out of the caller's run: an empty
# COVERAGE_PROCESS_CONFIG disarms coverage's hook, an empty COV_CORE_DATAFILE
# pytest-cov 6's, as the mini_repo fixture's lanes do.
QUIET_COVERAGE = {"COVERAGE_PROCESS_CONFIG": "", "COV_CORE_DATAFILE": ""}


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def scoped_run(repo: Path) -> None:
    """The caller's own run, with the lane's coverage flags, into scoped.json."""
    done = hang_guard.run([sys.executable, "-m", "pytest", "tests", "-p", "no:cacheprovider",
                           "--cov=calc", "--cov-branch", "--cov-report=json:scoped.json", "-q"],
                          cwd=repo, env=child_env(QUIET_COVERAGE), text=True,
                          encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr


def branch_repo(tmp_path: Path) -> Path:
    repo = git_init_repo(tmp_path)
    write(repo, ".gitignore", ".crapkit/\n__pycache__/\n.coverage\nscoped.json\n")
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", BEFORE)
    write(repo, "tests/test_grade.py", ONE_PATH)
    git_commit_all(repo, "base")
    write(repo, "calc/grade.py", AFTER)
    return repo


def test_a_scoped_run_that_leaves_a_branch_dark_fails_the_gate_until_a_test_walks_it(tmp_path):
    repo = branch_repo(tmp_path)
    scoped_run(repo)

    red = run_cli(repo, "rescore", "calc/grade.py", "--coverage", "scoped.json", "--gate")

    assert red.returncode == 6, red.stdout + red.stderr
    gate = [ln for ln in red.stderr.splitlines() if "GATE" in ln]
    assert len(gate) == 1 and "fork( n )" in gate[0] and "ccn   5" in gate[0], red.stderr

    write(repo, "tests/test_grade.py", EVERY_PATH)
    scoped_run(repo)

    green = run_cli(repo, "rescore", "calc/grade.py", "--coverage", "scoped.json", "--gate")

    assert green.returncode == 0, green.stdout + green.stderr
    assert green.stdout.rstrip().endswith("gate: 1 changed function(s) judged, 0 over ceiling 6")
