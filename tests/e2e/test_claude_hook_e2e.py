"""Protocol 1: a recorded PostToolUse payload on stdin, an exit code and the
advisory out, on stderr or as one line of JSON on stdout.

Each golden under tests/goldens/claude_hook/ carries a payload shaped the way a
live PostToolUse event arrives from one harness (Claude Code unless the case
names Copilot CLI, Cursor or VS Code), the fixture repo it was recorded
against, and the verdict it must draw. The test builds the repo, spawns the
real subcommand, and diffs. Nothing here reaches into the module.

Two rules hold across every case, so they are asserted on every case:

- stdout is empty unless the golden records the JSON a harness reads there.
  Claude Code parses stdout JSON on exit 0, so its cases and Copilot CLI's
  print nothing there; Cursor and VS Code read exit 2 as a deny or a block, so
  theirs carry the advisory as one JSON object on exit 0.
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
from conftest import child_env, cli_runner
from name_bytes import ANY_BYTE_NAMES

# The hook is a process Claude Code starts per edit: stdin payload, start
# time and PYTHONPATH shims all need the real child.
run_cli = cli_runner(spawn=True)

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


def fx_measured_clean(repo: Path) -> None:
    """Everything committed: the tree a Bash command that wrote nothing leaves."""
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", CLEAN)
    commit(repo)


def fx_stale_breach(repo: Path) -> None:
    """The breach is dirty but OLD: written well before the command this event
    reports, which is what the mtime freshness window has to screen out."""
    measured(repo)
    stale = time.time() - 3600
    os.utime(repo / "calc" / "grade.py", (stale, stale))


def fx_unborn_staged_breach(repo: Path) -> None:
    """A new project after `git init` and `git add`: no commit yet."""
    init(repo)
    write(repo, "crapkit.toml", TOML)
    write(repo, "calc/grade.py", BREACH)
    git(repo, "add", "-A")


def fx_claimed_non_utf8_name(repo: Path) -> None:
    """Scope `src` takes src/caf\\xe9.py, a name in Latin-1 bytes that git
    names as it is stored. Only a file system that stores any byte holds it."""
    init(repo)
    write(repo, "crapkit.toml", TOML.replace('"calc"', '"src"'))
    write(repo, "src/grade.py", CLEAN)
    commit(repo)
    (repo / os.fsdecode(b"src/caf\xe9.py")).write_text(BREACH, encoding="utf-8", newline="\n")


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
    "measured_clean": fx_measured_clean,
    "stale_breach": fx_stale_breach,
    "unborn_staged_breach": fx_unborn_staged_breach,
    "claimed_non_utf8_name": fx_claimed_non_utf8_name,
}
# What a golden's `needs` asks of the machine: a name that is not UTF-8 is a
# file only where the file system stores any byte (not NTFS, not APFS).
NEEDS = {"any_byte_names": ANY_BYTE_NAMES}


def _goldens() -> list[dict]:
    files = sorted(GOLDENS.glob("*.json"))
    assert files, f"no protocol goldens under {GOLDENS}"
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


CASES = _goldens()
PARAMS = [pytest.param(g, id=g["case"], marks=[NEEDS[g["needs"]]] if "needs" in g else [])
          for g in CASES]


def _resolved(value, repo: str):
    """`{REPO}` in a recorded payload, pointed at the repo this run built. The
    substitution happens after the JSON is parsed, so a Windows path's
    backslashes never have to survive an escape round trip."""
    if isinstance(value, str):
        return value.replace("{REPO}", repo)
    return _resolved_items(value, repo) if isinstance(value, (dict, list)) else value


def _resolved_items(value, repo: str):
    """A payload object or list with `{REPO}` resolved in every value under it:
    Cursor's `workspace_roots` is a list of paths."""
    if isinstance(value, dict):
        return {key: _resolved(item, repo) for key, item in value.items()}
    return [_resolved(item, repo) for item in value]


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


def run_hook(golden: dict, repo: Path, cwd: Path, *, env_extra=None) -> subprocess.CompletedProcess:
    return run_cli(cwd, *golden["argv"], stdin=_stdin_text(golden, str(repo)),
                   timeout=300, encoding="utf-8", errors="replace",
                   env_extra={**_child_overrides(), **(env_extra or {})})


def _built(golden: dict, tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    FIXTURES[golden["fixture"]](repo)
    return repo


@pytest.mark.parametrize("golden", PARAMS)
def test_the_recorded_payload_draws_the_recorded_verdict(golden: dict, tmp_path):
    done = run_hook(golden, _built(golden, tmp_path), tmp_path)

    assert done.stderr.splitlines() == golden["expect"]["stderr"], golden["why"]
    assert done.returncode == golden["expect"]["exit"], golden["why"]


@pytest.mark.parametrize("golden", PARAMS)
def test_stdout_carries_only_the_json_the_harness_reads(golden: dict, tmp_path):
    """Claude Code parses stdout JSON on exit 0, so a stray print there is a
    protocol break. The one thing stdout may carry is the single JSON object a
    Cursor or VS Code golden records."""
    done = run_hook(golden, _built(golden, tmp_path), tmp_path)

    expected = golden["expect"].get("stdout")
    if expected is None:
        assert done.stdout == ""
    else:
        assert done.stdout.count("\n") == 1 and json.loads(done.stdout) == expected


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


def test_importing_the_hook_module_loads_no_gate_and_no_analysis(tmp_path):
    """The module scope stays stdlib-only: the gate module, the key and scope
    rules and lizard are imported inside the judging functions, so an edit in
    a repo crapkit never measures pays for none of them."""
    done = subprocess.run([PY, "-X", "importtime", "-c", "import crapkit.cli.claude_hook"], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, env=child_env(_child_overrides()))
    imported = {line.rsplit("|", 1)[-1].strip() for line in done.stderr.splitlines() if "|" in line}

    assert done.returncode == 0, done.stderr
    assert "crapkit.cli.claude_hook" in imported, done.stderr
    assert imported & {"crapkit.gate", "crapkit.keys", "crapkit.universe", "lizard"} == set()


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


def test_a_touch_after_a_bash_advisory_repeats_nothing_in_the_next_process(tmp_path):
    """The session memory outlives the process that wrote it, and lives under
    the git directory: a second Bash event in the same session, after a touch
    that moved no byte, is silent, and the working tree stays byte-identical."""
    golden = _case("bash_written_breach")
    repo = _built(golden, tmp_path)
    before = _tree(repo)
    first = run_hook(golden, repo, tmp_path)
    grade = repo / "calc" / "grade.py"
    stale = time.time() - 60
    os.utime(grade, (stale, stale))
    os.utime(grade, None)

    again = run_hook(golden, repo, tmp_path)

    assert (first.returncode, again.returncode, again.stderr) == (2, 0, "")
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
    return _best_ms(lambda: run_hook(golden, repo, tmp_path, env_extra=_timing_overrides()))


def _timing_overrides() -> dict:
    """Measure shipped startup cost; golden cases separately collect coverage."""
    return {key: None for key in os.environ if key.startswith(("COVERAGE_", "COV_CORE_"))}


def _floor_ms() -> float:
    """What a spawned interpreter costs here before crapkit exists at all."""
    return _best_ms(lambda: subprocess.run([PY, "-c", "pass"], capture_output=True,
                                           timeout=300, env=child_env(_timing_overrides())))


# A box whose bare interpreter needs this long to start is busy with something
# else, and an absolute ceiling then measures that something: 666 ms against the
# 500 ms ceiling while another repository's coverage ran beside the suite. The
# design's floor is 29.3 ms. CI is never excused, since its runners are the
# machines the hard ceiling was written for.
_SATURATED_FLOOR_MS = 250.0


def _excuse_a_saturated_box() -> None:
    if os.environ.get("CI"):
        return
    floor = _floor_ms()
    if floor > _SATURATED_FLOOR_MS:
        pytest.skip(f"python -c pass took {floor:.0f} ms to start here against a design floor of "
                    "29.3 ms: this box is too busy to measure a wall-clock ceiling, and CI enforces it")


@pytest.mark.parametrize("name", sorted(BUDGETS))
def test_the_warm_path_stays_inside_the_ci_budget(name: str, tmp_path):
    hard, _ = BUDGETS[name]
    _excuse_a_saturated_box()

    assert _warm_ms(name, tmp_path) < hard


def test_a_saturated_box_is_excused_from_the_wall_clock_ceiling(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys.modules[__name__], "_floor_ms", lambda: 600.0)

    with pytest.raises(pytest.skip.Exception, match="600 ms"):
        _excuse_a_saturated_box()


def test_ci_is_never_excused_from_the_wall_clock_ceiling(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys.modules[__name__], "_floor_ms", lambda: 600.0)

    _excuse_a_saturated_box()


def test_a_quiet_box_is_held_to_the_wall_clock_ceiling(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys.modules[__name__], "_floor_ms", lambda: 31.0)

    _excuse_a_saturated_box()


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
