"""The three gates and the advisory: which edited functions each one refuses.

docs/ratchet.md#the-commit-gate-skips-marked-functions and README exit 6 give
the rules, restated in model_verdict:

- hook-precommit judges staged functions the commit changes: ccn over the
  ceiling, skipped when a mark exists for the function;
- claude-hook judges the edited file's changed functions the same way and
  exits 2 with an advisory;
- rescore --gate judges the functions the tree changed since HEAD: ccn over
  the ceiling, pardoned while the CRAP it computes from the fresh ccn and the
  last run's coverage sits at or under the mark;
- verify's gate judges touched functions by their fresh CRAP against the
  ceiling, pardoned at or under the mark, and its ratchet check reports any
  marked function above its mark, touched or not (exit 7 when only that fires).

Every case edits one seeded repository (marks on `marked` and `low`), runs the
four commands and compares the functions each one names with the model's.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

import pytest

from accuracy.kit import drive, exact, repos
from accuracy.verdict_model import cadence
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

APP = vw.FILES["app"]
BASE = (vw.World().with_fn("app", vw.Fn("marked", 6, 6)).with_fn("app", vw.Fn("low", 2, 0))
        .with_fn("app", vw.Fn("fine", 1, 2)).with_fn("lib", vw.Fn("g", 1, 2))
        .with_test(vw.Test("t1")).with_test(vw.Test("t2", lane="b")))
_LISTED = re.compile(r"^\s+ccn\s+\d+\s+(\S+):\d+\s+(.+?)\s*$", re.M)


def _fns(world: vw.World) -> dict:
    return {(vw.FILES[s], fn.long_name): fn for s in vw.FILES for fn in world.functions[s]}


def _marks(world: vw.World) -> dict:
    """What ratchet seed records on BASE: every function over the ceiling, at its CRAP."""
    marks, _, _ = model.seed({}, {key: fn.crap for key, fn in _fns(world).items()}, vw.TARGET)
    return marks


MARKS = _marks(BASE)


def _stale_crap(key, fn: vw.Fn) -> exact.Fraction:
    """rescore's CRAP: the fresh ccn over the last run's coverage, a new function at 0."""
    before = _fns(BASE).get(key)
    return exact.crap(fn.ccn, before.cov if before else 0)


@dataclass(frozen=True)
class Gates:
    hook: frozenset
    advisory: frozenset
    rescore: frozenset
    verify_gate: frozenset
    ratchet: frozenset


def _hook_breach(key, fn: vw.Fn) -> bool:
    return model.hook_gate(fn.ccn, vw.TARGET, key in MARKS)


def _rescore_breach(key, fn: vw.Fn) -> bool:
    return fn.ccn > vw.TARGET and not model.within_mark(_stale_crap(key, fn), MARKS.get(key))


def _verify_breach(key, fn: vw.Fn) -> bool:
    return model.verify_gate(model.Row(*key, 0, fn.crap), True, vw.TARGET, MARKS.get(key))


def _judged(rule, fns: dict, keys) -> frozenset:
    return frozenset(key for key in keys if rule(key, fns[key]))


def expected(world: vw.World) -> Gates:
    fns, touched = _fns(world), _touched(world)
    staged = _judged(_hook_breach, fns, touched)
    ratchet = frozenset(k for k, fn in fns.items() if model.regression(fn.crap, MARKS.get(k)))
    return Gates(staged, staged, _judged(_rescore_breach, fns, touched),
                 _judged(_verify_breach, fns, touched), ratchet)


def _touched(world: vw.World) -> set:
    before = {key: vw.fn_lines(fn) for key, fn in _fns(BASE).items()}
    return {key for key, fn in _fns(world).items() if before.get(key) != vw.fn_lines(fn)}


def listed(stderr: str) -> frozenset:
    return frozenset(_LISTED.findall(stderr))


def advisory(scenario: vw.Scenario, path: str) -> tuple[int, frozenset]:
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(scenario.root),
               "tool_input": {"file_path": str(scenario.root / path)}}
    result = scenario.run("claude-hook", "--protocol", "1", stdin=json.dumps(payload))
    return result.code, listed(result.stderr)


def hook(scenario: vw.Scenario) -> tuple[int, frozenset]:
    repos.git(scenario.top, "add", "-A")
    result = drive.Driver(scenario.root, env={"CRAPKIT_OVERRIDE_REASON": None}).run("hook-precommit")
    return result.code, listed(result.stdout + result.stderr)


def rescore(scenario: vw.Scenario, path: str) -> tuple[int, frozenset]:
    result = scenario.run("rescore", path, "--gate", "--json")
    return result.code, frozenset((b["path"], b["key_name"]) for b in result.json()["gate"]["breaches"])


def verify(scenario: vw.Scenario) -> tuple[int, frozenset, frozenset]:
    result = scenario.run("verify", "--json")
    payload = result.json()
    gate = frozenset((v["path"], v["key_name"]) for v in payload["gate_violations"])
    ratchet = frozenset((r["path"], r["long_name"]) for r in payload["ratchet_regressions"])
    return result.code, gate, ratchet


def measured(scenario: vw.Scenario, world: vw.World) -> Gates:
    """Every gate's verdict on `world`, each read from its own command. The hook
    runs last: it stages the tree, which the others read unstaged."""
    scenario.set(world)
    advised, rescored, verified = advisory(scenario, APP), rescore(scenario, APP), verify(scenario)
    staged = hook(scenario)
    codes = (staged[0], advised[0], rescored[0], verified[0])
    return codes, Gates(staged[1], advised[1], rescored[1], verified[1], verified[2])


def _code(found, code: int) -> int:
    return code if found else 0


def codes_of(gates: Gates) -> tuple:
    findings = {"gate": gates.verify_gate, "ratchet": gates.ratchet}
    verdict = model.exit_code(frozenset(name for name, found in findings.items() if found))
    return (_code(gates.hook, 6), _code(gates.advisory, 2), _code(gates.rescore, 6), verdict)


@pytest.fixture(scope="module")
def seeded(seeded_world):
    scenario = seeded_world(BASE)
    assert model.parse_marks(scenario.marks_text()).marks == MARKS
    return scenario


CASES = {
    "touch-marked": BASE.with_fn("app", vw.Fn("marked", 6, 6, "edited")),
    "grow-marked": BASE.with_fn("app", vw.Fn("marked", 8, 8)),
    "repay-marked": BASE.with_fn("app", vw.Fn("marked", 6, 12)),
    "touch-low": BASE.with_fn("app", vw.Fn("low", 2, 0, "edited")),
    "new-complex": BASE.with_fn("app", vw.Fn("big", 7, 0)),
    "new-simple-uncovered": BASE.with_fn("app", vw.Fn("plain", 3, 0)),
    "untouched-rot": BASE.with_fn("app", vw.Fn("marked", 6, 2)),
    "grow-fine": BASE.with_fn("app", vw.Fn("fine", 7, 14)),
}


@pytest.mark.process
@pytest.mark.parametrize("case", sorted(CASES))
def test_pardon_relation_at_the_mark(seeded, tmp_path, case):
    """Each gate names exactly the functions its rule names, and exits as it says."""
    world = CASES[case]
    want = expected(world)

    codes, got = measured(seeded.copy(tmp_path / "repo"), world)

    assert got == want
    assert codes == codes_of(want)


@pytest.mark.process
def test_the_hook_exempts_a_marked_function(seeded, tmp_path):
    """ratchet.md: the hook skips a staged marked function whatever the edit
    did, and says how many it skipped on one stderr line."""
    scenario = seeded.copy(tmp_path / "repo")
    scenario.set(CASES["grow-marked"])

    code, names = hook(scenario)

    assert (code, names) == (0, frozenset())


@pytest.mark.process
def test_renamed_file_faces_every_gate(seeded, tmp_path):
    """A mark is keyed on its path; git mv moves the function to a path no mark
    names, so an edit there faces every gate as unmarked debt."""
    scenario = seeded.copy(tmp_path / "repo")
    repos.git(scenario.top, "mv", APP, "src/renamed.py")
    world = CASES["grow-marked"]
    moved = vw.source(world.functions["app"])[0]
    (scenario.root / "src" / "renamed.py").write_bytes(moved.encode("utf-8"))
    key, low = ("src/renamed.py", "marked( x )"), ("src/renamed.py", "low( x )")

    assert advisory(scenario, "src/renamed.py") == (2, frozenset({key}))
    assert rescore(scenario, "src/renamed.py") == (6, frozenset({key}))
    # low: ccn 3 passes the ccn gates; CRAP 12 at a path no mark names fails verify's.
    assert verify(scenario)[:2] == (6, frozenset({key, low}))
    assert hook(scenario) == (6, frozenset({key}))


@pytest.mark.process
def test_the_advisory_reads_an_encoded_mark(seeded, tmp_path):
    """A mark on a path holding U+2028 is written as an encoded record
    (portable-records.md); the advisory still reads it and stays silent."""
    scenario = seeded.copy(tmp_path / "repo")
    odd = "src/odd" + chr(0x2028) + "name.py"
    world = vw.World(functions={"app": (vw.Fn("big", 7, 0),), "lib": ()})
    (scenario.root / odd).write_bytes(vw.source(world.functions["app"])[0].encode("utf-8"))
    repos.git(scenario.top, "add", "-A")
    scenario.commit("add the file")
    marks = scenario.root / "crapkit-ratchet.tsv"
    record = "@crapkit-record-v1\t" + json.dumps([odd, "big( x )", "72.0000"])
    marks.write_bytes(marks.read_bytes() + (record + "\n").encode("utf-8"))
    (scenario.root / odd).write_bytes(vw.source((vw.Fn("big", 7, 0, "edited"),))[0].encode("utf-8"))

    assert advisory(scenario, odd) == (0, frozenset())


# --- rescore --gate on the MCP surface ---------------------------------------------------------

@pytest.mark.process
@pytest.mark.cross_surface
@pytest.mark.parametrize("case", cadence.tiered(["grow-marked", "touch-marked", "new-complex"],
                                                push={"grow-marked"}))
def test_mcp_check_gate_answers_as_rescore_gate(seeded, tmp_path, case):
    scenario = seeded.copy(tmp_path / "repo")
    scenario.set(CASES[case])

    cli = scenario.run("rescore", APP, "--gate", "--json")
    reply = scenario.driver.mcp([("check_gate", {"path": APP})])[0]

    assert reply["structuredContent"]["gate"] == cli.json()["gate"]
    assert reply.get("isError", False) is False


# --- the advisory's Bash event: which files it judges -------------------------------------------

WINDOW = 12  # agent-json.md#claude-hook: the freshness window, in seconds


def _porcelain(top) -> dict:
    """{path: status} from `git status --porcelain -z -uall`, the read the docs name."""
    out = repos.git(top, "status", "--porcelain", "-z", "-uall")
    entries = [entry for entry in out.split("\0") if entry]
    return {entry[3:]: entry[:2] for entry in entries}


def _fresh_python(root, statuses: dict, now: float) -> set:
    """The dirty or untracked *.py files whose mtime is inside the window."""
    return {path for path in statuses if path.endswith(".py")
            and now - (root / path).stat().st_mtime <= WINDOW}


BIG = vw.source((vw.Fn("big", 7, 0),))[0]


def _plant(root, path: str, age: float, now: float) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(BIG.replace("big", "big_" + target.stem).encode("utf-8"))
    import os
    os.utime(target, (now - age, now - age))


@pytest.mark.process
def test_a_bash_event_judges_the_fresh_dirty_python_files_git_status_names(seeded, tmp_path):
    """agent-json.md#claude-hook: a Bash event judges the dirty or untracked
    *.py files whose mtime falls inside a 12-second window; each breaching
    file gets its own advisory block. Oracle: git status --porcelain -z -uall
    and os.stat, read before the hook runs."""
    import time
    scenario = seeded.copy(tmp_path / "repo")
    now = time.time()
    for path, age in (("src/new_fresh.py", 0), ("src/new_old.py", 120), ("src/deep/nested_fresh.py", 1),
                      ("src/notes.txt", 0), ("lib/other_fresh.py", 2)):
        _plant(scenario.root, path, age, now)
    (scenario.root / APP).write_bytes(vw.source(CASES["new-complex"].functions["app"])[0].encode(
        "utf-8"))
    expected = _fresh_python(scenario.root, _porcelain(scenario.top), time.time())
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(scenario.root),
               "tool_input": {"command": "python - <<'PY'"}}

    result = scenario.run("claude-hook", "--protocol", "1", stdin=json.dumps(payload))

    judged = set(re.findall(r"^crapkit advisory: .* in (\S+) \(", result.stderr, re.M))
    assert (result.code, judged) == (2, expected), result.stderr


# --- the advisory under a root below the git top, and a non-ASCII payload -------------------------

def _nested_spec(world: vw.World) -> repos.Spec:
    files = {f"pkg/{path}": text for path, text in vw.files(world).items()}
    return repos.Spec(steps=(repos.Commit(files=files, message="seed"),), root="pkg")


@pytest.mark.process
def test_advisory_under_a_nested_root(make_repo):
    """README: the root is the first crapkit.toml above the edited file, and
    the diff runs root-relative, so the advisory names the root's path."""
    built = make_repo(_nested_spec(BASE))
    edited = built.root / APP
    edited.write_bytes(vw.source(CASES["new-complex"].functions["app"])[0].encode("utf-8"))
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(built.top),
               "tool_input": {"file_path": str(edited)}}

    result = drive.Driver(built.top).run("claude-hook", "--protocol", "1", stdin=json.dumps(payload))

    assert (result.code, listed(result.stderr)) == (2, frozenset({(APP, "big( x )")}))
    assert result.stdout == ""


@pytest.mark.process
@pytest.mark.platform("win32")
def test_advisory_reads_a_non_ascii_payload_under_cp1252(seeded, tmp_path):
    """A payload naming a non-ASCII path, read by a child whose console code
    page is cp1252 (PYTHONUTF8=0, no PYTHONIOENCODING)."""
    scenario = seeded.copy(tmp_path / "repo")
    path = "src/caf" + chr(0xe9) + ".py"
    (scenario.root / path).write_bytes(BIG.encode("utf-8"))
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(scenario.root),
               "tool_input": {"file_path": str(scenario.root / path)}}
    env = {"PYTHONUTF8": "0", "PYTHONIOENCODING": None}

    result = drive.Driver(scenario.root, spawn=True, env=env).run(
        "claude-hook", "--protocol", "1", stdin=json.dumps(payload, ensure_ascii=False))

    assert (result.code, listed(result.stderr)) == (2, frozenset({(path, "big( x )")}))
