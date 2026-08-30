"""Protocol 1: a recorded Claude Code hook payload on stdin, an exit code and a
line of stderr out.

Each golden under tests/goldens/claude_hook/ carries a payload shaped the way a
live PostToolUse event arrives, the fixture repo it was recorded against, and
the verdict it must draw. The test builds the repo, spawns the real subcommand,
and diffs. Nothing here reaches into the module: an adapter for another harness
maps its own payload onto the same JSON and these files still decide.

Two rules hold across every case, so they are asserted on every case:

- stdout is always empty. Protocol 1 reserves it for a future JSON channel, and
  Claude Code parses stdout JSON on exit 0.
- the process runs from a directory that is not the repo. The root comes from
  the edited file's own path, never from cwd and never from
  ${CLAUDE_PROJECT_DIR}, which stays at the session root while an edit follows a
  worktree.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import crapkit
from conftest import child_env, run_cli

PY = sys.executable
GOLDENS = Path(__file__).resolve().parent.parent / "goldens" / "claude_hook"

# ccn 2, under the ceiling of 6: what the fixture repos commit.
CLEAN = "def grade(n):\n    if n > 1:\n        return n + 1\n    return n\n"
_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
# ccn 8, over it. lizard names it `sprawl( n )` and spans it 1 to 16.
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"
# The same function with its parameter list left open: lizard reports 0 functions.
BROKEN = f"def sprawl(n:\n{_BRANCHES}    return n\n"
COMMENTED = f"{BREACH}\n\n# a note\n"
COMMENT_EDITED = f"{BREACH}\n\n# a different note\n"

TOML = """[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]
"""

MARK = ("# crapkit-analysis=5 lizard=1.24.0\n"
        "path\tlong_name\tcrap\n"
        "calc/grade.py\tsprawl( n )\t72.0000\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   text=True, encoding="utf-8")


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")


def init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q")


def measured(repo: Path, source: str = BREACH) -> None:
    """A repo crapkit measures, with the edit already landed on disk: the clean
    version is HEAD, the working tree holds `source`."""
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", CLEAN)
    commit(repo)
    write(repo, "calc/grade.py", source)


def fx_measured_breach(repo: Path) -> None:
    measured(repo)


def fx_broken_syntax(repo: Path) -> None:
    measured(repo, BROKEN)


def fx_no_toml(repo: Path) -> None:
    """A git repo with no crapkit.toml above the edited file."""
    init(repo)
    write(repo, "calc/grade.py", CLEAN)
    commit(repo)
    write(repo, "calc/grade.py", BREACH)


def fx_rebase_marker(repo: Path) -> None:
    measured(repo)
    write(repo, ".git/MERGE_HEAD", "0" * 40 + "\n")


def fx_worktree_git_file(repo: Path) -> None:
    """The parent checkout carries the toml; `wt/` is shaped like a linked
    worktree, with a `.git` FILE and no config of its own."""
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", CLEAN)
    commit(repo)
    write(repo, "wt/.git", f"gitdir: {repo.as_posix()}/.git/worktrees/wt\n")
    write(repo, "wt/calc/grade.py", BREACH)


def fx_unscoped_breach(repo: Path) -> None:
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "other/thing.py", CLEAN)
    commit(repo)
    write(repo, "other/thing.py", BREACH)


def fx_comment_only(repo: Path) -> None:
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", COMMENTED)
    commit(repo)
    write(repo, "calc/grade.py", COMMENT_EDITED)


def fx_untracked_breach(repo: Path) -> None:
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", CLEAN)
    commit(repo)
    write(repo, "calc/new.py", BREACH)


def fx_marked_breach(repo: Path) -> None:
    measured(repo)
    write(repo, "crapkit-ratchet.tsv", MARK)


FIXTURES = {
    "measured_breach": fx_measured_breach,
    "broken_syntax": fx_broken_syntax,
    "no_toml": fx_no_toml,
    "rebase_marker": fx_rebase_marker,
    "worktree_git_file": fx_worktree_git_file,
    "unscoped_breach": fx_unscoped_breach,
    "comment_only": fx_comment_only,
    "untracked_breach": fx_untracked_breach,
    "marked_breach": fx_marked_breach,
}


def _goldens() -> list[dict]:
    files = sorted(GOLDENS.glob("*.json"))
    assert files, f"no protocol goldens under {GOLDENS}"
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


CASES = _goldens()
IDS = [g["case"] for g in CASES]


def _resolved(value, repo: str):
    """`{REPO}` in a recorded payload, pointed at the repo this run built. The
    substitution happens after the JSON is parsed, so a Windows path's
    backslashes never have to survive an escape round trip."""
    if isinstance(value, str):
        return value.replace("{REPO}", repo)
    if isinstance(value, dict):
        return {key: _resolved(item, repo) for key, item in value.items()}
    return value


def _stdin_text(golden: dict, repo: str) -> str:
    """A recorded payload re-serialized, or the raw bytes a malformed case pins."""
    if "payload" in golden:
        return json.dumps(_resolved(golden["payload"], repo))
    return golden["stdin"].replace("{REPO}", repo)


def _child_overrides() -> dict:
    """The environment overrides that make a child import the crapkit this test imported, not an installed one."""
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    inherited = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join(p for p in (src, inherited) if p),
            "CRAPKIT_OVERRIDE_REASON": None}


def run_hook(golden: dict, repo: Path, cwd: Path) -> subprocess.CompletedProcess:
    return run_cli(cwd, *golden["argv"], stdin=_stdin_text(golden, str(repo)),
                   timeout=300, encoding="utf-8", errors="replace",
                   env_extra=_child_overrides())


def _built(golden: dict, tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    FIXTURES[golden["fixture"]](repo)
    return repo


@pytest.mark.parametrize("golden", CASES, ids=IDS)
def test_the_recorded_payload_draws_the_recorded_verdict(golden: dict, tmp_path):
    done = run_hook(golden, _built(golden, tmp_path), tmp_path)

    assert done.stderr.splitlines() == golden["expect"]["stderr"], golden["why"]
    assert done.returncode == golden["expect"]["exit"], golden["why"]


@pytest.mark.parametrize("golden", CASES, ids=IDS)
def test_stdout_stays_empty_whatever_the_verdict(golden: dict, tmp_path):
    """Protocol 1 reserves stdout for a future JSON channel, and Claude Code
    parses stdout JSON on exit 0. A stray print there is a protocol break."""
    done = run_hook(golden, _built(golden, tmp_path), tmp_path)

    assert done.stdout == ""


# --- import hygiene, and the store that is never opened ----------------------
#
# SnapshotStore.__init__ has no read-only path: it runs the schema script, seeds,
# and applies ALTER TABLE migrations, so a per-edit hook that touched a store
# would migrate it, and with two crapkit versions installed whichever fired first
# would rewrite the schema. It carries no busy timeout either, so a store the
# weekly job holds for ~56 minutes means an uncaught OperationalError and a
# multi-second stall PostToolUse shows nobody. These pin the module, not the
# behavior: an import added at module scope fails here before it can cost
# anything in the field.

_PROBE = ("import json, sys\n"
          "from crapkit.cli import main\n"
          "code = main(['claude-hook', '--protocol', '1'])\n"
          "print(json.dumps({'code': code,\n"
          "                  'store': 'crapkit.store' in sys.modules,\n"
          "                  'lizard': 'lizard' in sys.modules,\n"
          "                  'analyze': 'crapkit.analyze' in sys.modules}))\n")


def _probe(repo: Path, rel: str, tmp_path: Path) -> dict:
    """One run inside a fresh interpreter, reporting what it ended up holding."""
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "cwd": str(repo), "tool_input": {"file_path": str(repo / rel)}})
    done = subprocess.run([PY, "-c", _PROBE], input=payload, cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300,
                          env=child_env(_child_overrides()))
    return json.loads(done.stdout.splitlines()[-1])


def test_a_no_toml_run_opens_no_store_and_imports_no_analysis_stack(tmp_path):
    """A stat on crapkit.toml is 0.058 ms and it comes before every crapkit
    import there is. Paying 38 ms of lizard for a repo crapkit does not measure
    is the store mistake again, cheaper."""
    repo = tmp_path / "repo"
    fx_no_toml(repo)

    assert _probe(repo, "calc/grade.py", tmp_path) == {
        "code": 0, "store": False, "lizard": False, "analyze": False}


def test_even_a_breach_run_never_reaches_the_store(tmp_path):
    """The analysis path runs in full (lizard imported, verdict rendered, exit 2)
    and the store still never appears. Coverage, and the crap score that needs
    it, stay `verify`'s job."""
    repo = tmp_path / "repo"
    fx_measured_breach(repo)

    probed = _probe(repo, "calc/grade.py", tmp_path)

    assert (probed["code"], probed["store"]) == (2, False)
    assert probed["lizard"] is True, "the analysis path did not actually run"


def _tree(repo: Path) -> dict:
    """Every file in the repo but git's own, by content. `git diff` refreshes
    .git/index as a side effect of its stat check, and that is git's business."""
    return {str(path.relative_to(repo)): path.read_bytes()
            for path in sorted(repo.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(repo).parts}


def test_a_breach_run_leaves_the_repo_byte_identical(tmp_path):
    """No .crapkit/ to create, no cache.json to tear (a bare open("w") there has
    a 33 ms window, and load_cache swallows the corruption as a silently cold
    cache), no ratchet staging, no store. The hook reads."""
    golden = _case("scoped_breach")
    repo = _built(golden, tmp_path)
    before = _tree(repo)

    done = run_hook(golden, repo, tmp_path)

    assert done.returncode == 2, "the run has to have done its work"
    assert _tree(repo) == before


# --- latency -----------------------------------------------------------------
#
# Two tiers. The hard one is an absolute wall-clock ceiling CI enforces on a
# machine whose speed nobody controls: it catches a hook that hangs, shells out
# per function, or opens the store.
#
# The strict one is the real budget, and it is measured ABOVE `python -c pass`
# rather than as an absolute. The design's own floor table is why: interpreter
# startup is 29.3 ms of a 40.4 ms no-toml run there, so an absolute number pins
# the machine's Python far more than it pins this code. Above the floor, the
# same numbers say what the code actually owns, and a module-level import added
# to claude_hook.py or a command family imported at parser-build time moves them
# immediately.

_STRICT = pytest.mark.skipif(os.environ.get("CRAPKIT_STRICT_TIMING") != "1",
                             reason="strict latency budget: set CRAPKIT_STRICT_TIMING=1")
# name -> (absolute hard ceiling, milliseconds allowed above the interpreter floor)
BUDGETS = {"no_toml": (500.0, 40.0), "scoped_breach": (2000.0, 140.0)}


def _case(name: str) -> dict:
    return next(g for g in CASES if g["case"] == name)


def _best_ms(call, reps: int = 5) -> float:
    """Best of `reps` after one warming call. The fastest run is the one the
    machine was not doing something else during; a median carries whatever else
    the box was up to."""
    call()
    runs = []
    for _ in range(reps):
        start = time.perf_counter()
        call()
        runs.append((time.perf_counter() - start) * 1000)
    return min(runs)


def _warm_ms(name: str, tmp_path: Path) -> float:
    golden = _case(name)
    repo = _built(golden, tmp_path)
    return _best_ms(lambda: run_hook(golden, repo, tmp_path))


def _floor_ms() -> float:
    """What a spawned interpreter costs here before crapkit exists at all."""
    return _best_ms(lambda: subprocess.run([PY, "-c", "pass"], capture_output=True,
                                           timeout=300))


@pytest.mark.parametrize("name", sorted(BUDGETS))
def test_the_warm_path_stays_inside_the_ci_budget(name: str, tmp_path):
    hard, _ = BUDGETS[name]

    assert _warm_ms(name, tmp_path) < hard


@_STRICT
@pytest.mark.parametrize("name", sorted(BUDGETS))
def test_the_warm_path_costs_little_more_than_starting_python(name: str, tmp_path):
    """no_toml: a stdlib-only module scope and a 0.058 ms toml stat before any
    crapkit import, so an unmeasured repo pays for the interpreter and almost
    nothing else. scoped_breach: the scoped `git diff` started before the lizard
    import and finishing inside it, and the ratchet answered off the TSV's own
    lines rather than through 40,303 parsed entries.
    """
    _, allowed = BUDGETS[name]

    assert _warm_ms(name, tmp_path) - _floor_ms() < allowed
