"""The churn log cache through the CLI, on a real repo with real git.

Two things have to hold at once: the second read is byte-identical to the first,
and it really came off disk. The second is proved by a census of the git
commands the run spawned (GIT_TRACE2_EVENT), so a served cache shows up as a
history walk that never happened — a stopwatch would prove nothing about
correctness, and a mock proves nothing about the wiring.

`brief` and `worklist --batches` are the two commands that need per-commit
structure, so they are the two commands measured here.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

CRAPKIT = Path(".crapkit")
LOG_Z = CRAPKIT / "churn-log-v2.z"
LOG_KEY = CRAPKIT / "churn-log-v2.json"
COUPLING_CACHE = CRAPKIT / "coupling-cache-v1.json"

APP_PY = """def plain(x):
    a = x + 1
    b = a + 2
    return a + b


def pick(kind):
    if kind == "a":
        return 1
    if kind == "b":
        return 2
    return 0
"""

UTIL_PY = """def widen(kind, n):
    if kind == "a":
        return n + 1
    if kind == "b":
        return n + 2
    if kind == "c":
        return n + 3
    return n
"""

# Stand-in for `pytest --cov`: a fixed coverage.py artifact, so the queue has a
# scored run without this file owning a real suite.
LANE_SCRIPT = '''import json
from pathlib import Path

def fn(start, covered, statements, branches, taken):
    return {"start_line": start, "executed_lines": list(range(start, start + covered)),
            "missing_lines": list(range(start + covered, start + statements)),
            "summary": {"covered_lines": covered, "num_statements": statements,
                        "num_branches": branches, "covered_branches": taken}}

report = {"meta": {"branch_coverage": True}, "files": {
    "src/app.py": {"functions": {"plain": fn(1, 4, 4, 0, 0), "pick": fn(7, 2, 6, 4, 1)}},
    "src/util.py": {"functions": {"widen": fn(1, 2, 8, 6, 1)}},
}}
Path("cov.json").write_text(json.dumps(report), encoding="utf-8")
'''

TOML = (
    '[crapkit]\ntarget = 6\nworklist_floor = 1\nchurn_window_months = 12\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
    'parser = "coveragepy"\nscopes = ["src"]\n'
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message],
                   cwd=repo, check=True, capture_output=True)


def _argvs(trace_dir: Path) -> list[list[str]]:
    """Every git command a traced run spawned, in the order git started them."""
    out = []
    for path in sorted(trace_dir.glob("*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            event = json.loads(line)
            if event.get("event") == "start":
                out.append(event["argv"])
    return out


def traced(repo: Path, tmp_path: Path, tag: str, *args: str):
    """Run the CLI with a git command census attached."""
    trace = tmp_path / f"trace-{tag}"
    trace.mkdir()
    res = run_cli(repo, *args, env_extra={"GIT_TRACE2_EVENT": str(trace)})
    return res, [argv for argv in _argvs(trace) if _subcommand(argv) == "log"]


def _subcommand(argv: list[str]) -> str:
    """The git subcommand, past git's own flags: crapkit spawns every git with
    `-c diff.relative=true`, so the subcommand is not argv[1]."""
    i = 1
    while argv[i:i + 1] == ["-c"]:
        i += 2
    return argv[i] if i < len(argv) else ""


def window_walks(walks: list[list[str]]) -> list[list[str]]:
    return [argv for argv in walks if any(a.startswith("--since") for a in argv)]


def range_walks(walks: list[list[str]]) -> list[list[str]]:
    return [argv for argv in walks if any(".." in a for a in argv)]


@pytest.fixture()
def coupled_repo(tmp_path: Path) -> Path:
    """src/app.py and src/util.py land in six commits together, so they are coupled
    at the default min-support of 5 and coupling has something to report."""
    repo = tmp_path / "coupled"
    write(repo / "crapkit.toml", TOML)
    write(repo / "lane.py", LANE_SCRIPT)
    write(repo / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    for i in range(6):
        write(repo / "src" / "app.py", APP_PY + f"\nBUILD = {i}\n")
        write(repo / "src" / "util.py", UTIL_PY + f"\nBUILD = {i}\n")
        commit(repo, f"edit {i}")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    return repo


BRIEF = ("brief", "src/app.py", "pick", "--json")
BATCHES = ("worklist", "--batches", "2", "--top", "10", "--json")


def test_brief_answers_from_the_log_without_walking_history(coupled_repo, tmp_path):
    cold, cold_walks = traced(coupled_repo, tmp_path, "cold", *BRIEF)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert window_walks(cold_walks), "the cold run is the walk this cache exists to save"
    assert (coupled_repo / LOG_Z).is_file(), "and it must lay the log down"

    warm, warm_walks = traced(coupled_repo, tmp_path, "warm", *BRIEF)
    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout, "same tree, same answer, byte for byte"
    assert warm_walks == [], "a warm brief must not walk git history at all"


def test_brief_really_reports_coupling(coupled_repo):
    """A byte-identity test over an empty list would pass on a broken cache."""
    out = json.loads(run_cli(coupled_repo, *BRIEF).stdout)

    assert [p["path"] for p in out["coupling"]] == ["src/util.py"]


def test_the_cached_log_is_the_log_git_streams(coupled_repo):
    """The contract under every byte-identity claim: same lines as the git door
    this one caches, cold and warm alike."""
    from crapkit.churn_log import log_lines
    from crapkit.gitio import churn_log_lines

    from_git = [line.rstrip("\n") for line in churn_log_lines(coupled_repo, 12)]
    cold = [line.rstrip("\n") for line in log_lines(coupled_repo, 12)]
    warm = [line.rstrip("\n") for line in log_lines(coupled_repo, 12)]

    assert cold == from_git
    assert warm == from_git


def test_batches_answer_from_the_log_without_walking_history(coupled_repo, tmp_path):
    cold, cold_walks = traced(coupled_repo, tmp_path, "cold", *BATCHES)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert window_walks(cold_walks)

    warm, warm_walks = traced(coupled_repo, tmp_path, "warm", *BATCHES)
    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout
    assert warm_walks == []


def test_brief_and_batches_share_one_log(coupled_repo, tmp_path):
    assert run_cli(coupled_repo, *BRIEF).returncode == 0

    warm, warm_walks = traced(coupled_repo, tmp_path, "warm", *BATCHES)
    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm_walks == [], "batches must read brief's log, not write a rival one"
    assert sorted(p.name for p in (coupled_repo / CRAPKIT).glob("churn-log*")) == \
        ["churn-log-v2.json", "churn-log-v2.z"]


def test_the_queue_commands_that_need_no_structure_never_build_the_log(coupled_repo):
    """worklist and next-item read the per-file map, which churn-cache.json already
    holds. Building a 23 MB log for them would cost the walk this cache removes."""
    assert run_cli(coupled_repo, "worklist", "--json").returncode == 0
    assert run_cli(coupled_repo, "next-item").returncode == 0

    assert list((coupled_repo / CRAPKIT).glob("churn-log*")) == []


def test_a_new_commit_refreshes_to_the_answer_a_rebuild_gives(coupled_repo, tmp_path):
    assert run_cli(coupled_repo, *BRIEF).returncode == 0

    write(coupled_repo / "src" / "app.py", APP_PY + "\nBUILD = 9\n")
    write(coupled_repo / "src" / "util.py", UTIL_PY + "\nBUILD = 9\n")
    commit(coupled_repo, "move head")
    assert run_cli(coupled_repo, "coverage", "--json").returncode == 0

    refreshed, walks = traced(coupled_repo, tmp_path, "refresh", *BRIEF)
    assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
    assert range_walks(walks), "a moved HEAD costs cached..HEAD, not the window"

    (coupled_repo / LOG_Z).unlink()
    (coupled_repo / LOG_KEY).unlink()
    rebuilt = run_cli(coupled_repo, *BRIEF)

    assert rebuilt.stdout == refreshed.stdout, \
        "a refresh must land where the walk it replaced would have"


def needs_the_log(repo: Path) -> None:
    """Drop the ranking brief would otherwise answer from.

    A warm coupling cache is a complete answer, so brief never opens the log to
    reach it. Nothing here is about that cache: these tests are about what the
    log does when something does read it, and this is what makes brief one of
    the things that do.
    """
    (repo / COUPLING_CACHE).unlink(missing_ok=True)


def test_a_truncated_log_is_ignored_and_rebuilt(coupled_repo, tmp_path):
    cold = run_cli(coupled_repo, *BRIEF)
    torn = coupled_repo / LOG_Z
    torn.write_bytes(torn.read_bytes()[:-8])
    needs_the_log(coupled_repo)

    again, walks = traced(coupled_repo, tmp_path, "torn", *BRIEF)
    assert again.returncode == 0, again.stdout + again.stderr
    assert again.stdout == cold.stdout
    assert window_walks(walks), "a torn log is a miss, never a crash"

    needs_the_log(coupled_repo)
    warm, warm_walks = traced(coupled_repo, tmp_path, "healed", *BRIEF)
    assert warm_walks == [], "and it is replaced, not left torn"
    assert warm.stdout == cold.stdout


COUPLING = ("coupling", "--json")


def test_coupling_answers_from_the_log_without_walking_history(coupled_repo, tmp_path):
    cold, cold_walks = traced(coupled_repo, tmp_path, "cold", *COUPLING)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert window_walks(cold_walks), "the cold run is the walk the log exists to save"
    assert (coupled_repo / LOG_Z).is_file(), "coupling needs structure, so it lays the log"

    warm, warm_walks = traced(coupled_repo, tmp_path, "warm", *COUPLING)
    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout
    assert warm_walks == [], "a warm coupling must not walk git history at all"


def test_the_map_reads_through_a_laid_down_log_when_head_moves(coupled_repo, tmp_path):
    """worklist never builds the log, but it must not ignore one either: at a
    moved HEAD the map rebuild costs cached..HEAD through the log, not the
    whole window against git."""
    assert run_cli(coupled_repo, *BRIEF).returncode == 0

    write(coupled_repo / "src" / "app.py", APP_PY + "\nMAP = 4\n")
    commit(coupled_repo, "move head")

    moved, walks = traced(coupled_repo, tmp_path, "map", "worklist", "--json")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert window_walks(walks) == [], \
        "the map must refresh through the log, never re-walk the window"
