"""The three gates and the advisory: which edited functions each one refuses.

docs/ratchet.md#the-commit-gate-skips-marked-functions and README exit 6 give
the rules, restated in model_verdict:

- hook-precommit judges staged functions the commit changes: ccn over the
  ceiling, skipped while the ccn sits at or under the function's mark, since
  a blob has no coverage and CRAP is never under ccn;
- claude-hook judges the edited file's changed functions by ccn over the
  ceiling, skipped when a mark exists for the function, and exits 2 with an
  advisory;
- rescore --gate judges the functions the tree changed since HEAD: ccn over
  the ceiling, pardoned while the CRAP it computes from the fresh ccn and the
  last run's coverage sits at or under the mark;
- verify's gate judges touched functions by their fresh CRAP against the
  ceiling, pardoned at or under the mark, and its ratchet check reports any
  marked function above its mark, touched or not (exit 7 when only that fires).

Every case edits one seeded repository (marks on `marked` and `low`), runs the
four commands and compares the functions each one names with the model's. The
hook is also held to the model alone on generated trees: every ccn from 1 to
20, marked at each end of its range and one step either side, under both flags.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
# A refused function's row: `ccn N  path:line  name`, and on the hook's marked
# rise block `  mark M.MMMM` after the name.
_LISTED = re.compile(r"^\s+ccn\s+\d+\s+(\S+):\d+\s+(.+?)(?:\s+mark \d+\.\d{4})?\s*$", re.M)


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
    return model.hook_gate(fn.ccn, vw.TARGET, MARKS.get(key))


def _advisory_breach(key, fn: vw.Fn) -> bool:
    return model.advisory_gate(fn.ccn, vw.TARGET, key in MARKS)


def _rescore_breach(key, fn: vw.Fn) -> bool:
    return fn.ccn > vw.TARGET and not model.within_mark(_stale_crap(key, fn), MARKS.get(key))


def _verify_breach(key, fn: vw.Fn) -> bool:
    return model.verify_gate(model.Row(*key, 0, fn.crap), True, vw.TARGET, MARKS.get(key))


def _judged(rule, fns: dict, keys) -> frozenset:
    return frozenset(key for key in keys if rule(key, fns[key]))


def expected(world: vw.World) -> Gates:
    fns, touched = _fns(world), _touched(world)
    staged, advised = _judged(_hook_breach, fns, touched), _judged(_advisory_breach, fns, touched)
    ratchet = frozenset(k for k, fn in fns.items() if model.regression(fn.crap, MARKS.get(k)))
    return Gates(staged, advised, _judged(_rescore_breach, fns, touched),
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


def findings_of(payload: dict, kind: str) -> list[dict]:
    """verify --json's findings items of one kind. A crapkit before 0.9.0,
    which a retro replay runs, printed one list per kind instead."""
    if "findings" not in payload:
        return payload["gate_violations" if kind == "gate_violation" else "ratchet_regressions"]
    return [item for item in payload["findings"] if item["kind"] == kind]


def verify(scenario: vw.Scenario) -> tuple[int, frozenset, frozenset]:
    result = scenario.run("verify", "--json")
    payload = result.json()
    gate = frozenset((v["path"], v["key_name"]) for v in findings_of(payload, "gate_violation"))
    ratchet = frozenset((r["path"], r["long_name"]) for r in findings_of(payload, "ratchet_regression"))
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
    # ccn 14 against the mark of 13.125: CRAP is at least 14 at any coverage.
    "grow-marked-past-its-ccn": BASE.with_fn("app", vw.Fn("marked", 13, 13)),
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
def test_verify_passes_an_edit_inside_a_marked_function_at_its_mark(seeded, tmp_path):
    """docs/ratchet.md, three gates: verify's gate skips a touched function
    whose CRAP sits at or under its mark, as rescore --gate does. An edit that
    leaves `marked` at its recorded CRAP is no finding, so verify exits 0.
    Reads only the exit code and each violation's long_name, fields every
    release printed; before 13a42ff verify's gate read no marks and exited 6."""
    world = CASES["touch-marked"]
    scenario = seeded.copy(tmp_path / "repo")
    scenario.set(world)

    result = scenario.run("verify", "--json")
    gate = {v["long_name"] for v in findings_of(result.json(), "gate_violation")}

    assert expected(world).verify_gate == frozenset()
    assert (result.code, gate) == (0, set())


@pytest.mark.process
def test_the_hook_exempts_a_marked_function(seeded, tmp_path):
    """ratchet.md: the hook skips a staged marked function while its ccn
    sits at or under the mark (ccn 9 against 13.125), and says how many it
    skipped on one stderr line."""
    scenario = seeded.copy(tmp_path / "repo")
    scenario.set(CASES["grow-marked"])

    code, names = hook(scenario)

    assert (code, names) == (0, frozenset())


@pytest.mark.process
def test_the_hook_refuses_a_marked_function_its_ccn_took_past_the_mark(seeded, tmp_path):
    """ratchet.md: ccn 14 against the mark of 13.125 is a rise at any
    coverage, so the hook exits 6 naming the function and the mark, where
    the advisory, on the mark's existence, stays silent."""
    scenario = seeded.copy(tmp_path / "repo")
    scenario.set(CASES["grow-marked-past-its-ccn"])
    key = (APP, "marked( x )")

    code, names = hook(scenario)

    assert model.hook_finding(14, vw.TARGET, MARKS[key]) == "marked_rise"
    assert (code, names) == (6, frozenset({key}))


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


# --- the commit hook against the model on generated trees ----------------------------------------
#
# model_verdict.hook_finding restates docs/ratchet.md's commit gate: a staged
# function over its ceiling with no mark is refused; a marked one is held as the
# range from its ccn to its untested CRAP (ccn^2 + ccn, or ccn in a
# coverage_optional scope) against the mark at the mark's 4 places, and refused
# only when its ccn alone passes the mark. Each tree holds one file per ccn from
# 1 to 20, one function per mark: no mark, and each end of the range, one step
# under it and one step over it. The hook judges one file per run, and its exit
# code, the functions each refusal block names and the count on its stderr line
# are each held to the model, never to crapkit.

GENERATED_CEILING = 2
GENERATED_CCNS = range(1, 21)
STEP = Decimal("0.0001")
FLAGS = {"measured": "", "cc-only": "coverage_optional = true\n"}
_RISE = re.compile(r"^  ccn +\d+  (\S+):\d+  (.+?)  mark \d+\.\d{4}$", re.M)
_OVER = re.compile(r"^  ccn +\d+  (\S+):\d+  ((?:(?!  mark ).)+?)$", re.M)
_COUNT = re.compile(r"^crapkit gate: (\d+) staged function\(s\) carry a ratchet mark", re.M)
REFUSED, PASSED = ("over_ceiling", "marked_rise"), ("pardoned", "unproven")


def _generated_config(flag: str) -> str:
    return (f'[crapkit]\ntarget = {GENERATED_CEILING}\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
            f'languages = ["python"]\n{FLAGS[flag]}')


def _source(name: str, ccn: int) -> str:
    """`def NAME(n)` with ccn - 1 ifs: McCabe ccn."""
    body = "".join(f"    if n > {i}:\n        n += 1\n" for i in range(1, ccn))
    return f"def {name}(n):\n{body}    return n\n"


def _marks_at_the_ends(ccn: int, cc_only: bool) -> list:
    """No mark, then each end of the hook's range, one step under it and one over."""
    ends = [model.mark_value(end) for end in model.hook_range(ccn, cc_only)]
    return [None, *sorted({end + delta for end in ends for delta in (-STEP, Decimal(0), STEP)})]


def _generated_tree(flag: str) -> tuple[dict, str]:
    """{path: {function: its mark}} for every ccn, and the marks file over them."""
    files = {f"src/c{ccn}.py": {f"c{ccn}_m{i}": mark for i, mark in
                                enumerate(_marks_at_the_ends(ccn, flag == "cc-only"))}
             for ccn in GENERATED_CCNS}
    rows = [f"{path}\t{name}( n )\t{mark:.4f}" for path, functions in files.items()
            for name, mark in functions.items() if mark is not None]
    return files, "path\tlong_name\tcrap\n" + "".join(row + "\n" for row in rows)


def _modelled(path: str, ccn: int, functions: dict, cc_only: bool) -> dict[str, set]:
    """The model's finding for each function of one file, grouped by kind."""
    found: dict[str, set] = {kind: set() for kind in (*REFUSED, *PASSED)}
    for name, mark in functions.items():
        kind = model.hook_finding(ccn, GENERATED_CEILING, mark, cc_only)
        if kind is not None:
            found[kind].add((path, f"{name}( n )"))
    return found


@pytest.mark.process
@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_every_hook_verdict_on_the_range_ends_is_the_models(make_repo, flag):
    files, marks = _generated_tree(flag)
    built = make_repo(repos.Spec(steps=(repos.Commit(
        files={"crapkit.toml": _generated_config(flag), "crapkit-ratchet.tsv": marks}, message="seed"),)))
    driver = drive.Driver(built.root, env={"CRAPKIT_OVERRIDE_REASON": None})
    kinds_seen = set()
    for ccn, (path, functions) in zip(GENERATED_CCNS, files.items()):
        (built.root / path).parent.mkdir(exist_ok=True)
        (built.root / path).write_bytes("\n\n".join(_source(name, ccn) for name in functions).encode("utf-8"))
        repos.git(built.top, "add", "--", path)
        want = _modelled(path, ccn, functions, flag == "cc-only")

        result = driver.run("hook-precommit")

        refused = want["over_ceiling"] | want["marked_rise"]
        carried = len(want["pardoned"]) + len(want["unproven"])
        assert result.code == (6 if refused else 0), (ccn, result.stdout, result.stderr)
        assert set(_RISE.findall(result.stdout)) == want["marked_rise"], (ccn, result.stdout)
        assert set(_OVER.findall(result.stdout)) == want["over_ceiling"], (ccn, result.stdout)
        assert [int(n) for n in _COUNT.findall(result.stderr)] == ([carried] if carried else []), ccn
        kinds_seen |= {kind for kind, keys in want.items() if keys}
        repos.git(built.top, "commit", "-q", "--no-verify", "-m", path)

    # A cc-only range is one point, so no mark straddles it there.
    assert kinds_seen == ({*REFUSED, *PASSED} if flag == "measured" else {*REFUSED, "pardoned"})


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


# --- the hook's grant on an anonymous callback under an old stamp ---------------------------------

LOGGED_ALERT = "python -c \\\"import sys; open('alerts.log', 'a').write(sys.stdin.read())\\\""
CALLBACK_CFG = (f'[crapkit]\ntarget = 2\nalert_command = "{LOGGED_ALERT}"\n\n[[scope]]\nname = "web"\n'
                'paths = ["web"]\nlanguages = ["typescript"]\ncoverage_optional = true\n')
NAMED_TS = "function f(x: number) {\n  if (x > 1) { return 1; }\n  return 2;\n}\n"
# McCabe 3: two ternaries. Over the ceiling (2), so the hook needs a grant.
CALLBACK_TS = "export const h = (a: number) => [a].map(x => x > 1 ? 1 : x > 0 ? 2 : 3);\n"


def _hook_grant_on_a_callback(make_repo, stamp_of) -> tuple:
    """A committed marks file with no rows under the stamp stamp_of(running)
    returns, then a staged callback over the ceiling and a hook run with an
    override reason. Returns the hook's result, the marks before and after,
    and the store's override rows."""
    built = make_repo(repos.Spec(steps=(repos.Commit(
        files={"crapkit.toml": CALLBACK_CFG, "web/a.ts": NAMED_TS}, message="seed"),)))
    driver = drive.Driver(built.root)
    assert driver.run("coverage").code == 0
    versions = json.loads(driver.store("SELECT tool_versions FROM runs ORDER BY id DESC")[0]["tool_versions"])
    running = f"crapkit-analysis={versions['analysis_version']} lizard={versions['lizard']}"
    marks = built.root / "crapkit-ratchet.tsv"
    marks.write_bytes(f"# {stamp_of(running)}\n# crapkit-keys=1\npath\tlong_name\tcrap\n".encode("utf-8"))
    (built.root / "web" / "a.ts").write_bytes((NAMED_TS + CALLBACK_TS).encode("utf-8"))
    repos.git(built.top, "add", "--", "crapkit-ratchet.tsv", "web/a.ts")
    before = marks.read_bytes()
    result = drive.Driver(built.root, env={"CRAPKIT_OVERRIDE_REASON": "reviewed"}).run("hook-precommit")
    logged = (built.root / "alerts.log").exists()
    return result, before, marks.read_bytes(), logged, driver.json("overrides")["overrides"]


@pytest.mark.nightly
@pytest.mark.process
def test_hook_grant_checks_reader_proof_on_the_text_it_publishes(make_repo):
    """docs/ratchet.md: under a stamp older than analysis version 10, the
    (anonymous) mark the grant would add has no reader proof, so the hook
    refuses it and writes no alert line, no store row and no mark."""
    result, before, after, logged, overrides = _hook_grant_on_a_callback(
        make_repo, lambda running: "crapkit-analysis=9 lizard=1.24.0")
    assert result.code != 0, result.stdout + result.stderr
    assert (after, logged, overrides) == (before, False, [])


@pytest.mark.nightly
@pytest.mark.process
def test_hook_grant_under_the_running_stamp_marks_the_callback(make_repo):
    """The control: under the running stamp the same grant goes through, and
    the mark is the CRAP the callback's scope scores with no coverage. The web
    scope is coverage_optional, which scores CRAP = ccn, so the mark is 3
    (docs/ratchet.md; 0.8.0 wrote ccn^2 + ccn = 12 there)."""
    result, _, after, logged, overrides = _hook_grant_on_a_callback(make_repo, lambda running: running)
    assert result.code == 0, result.stdout + result.stderr
    marks = model.parse_marks(after.decode("utf-8")).marks
    assert marks == {("web/a.ts", "(anonymous)"): model.mark_value(3)}
    assert logged and [(row["path"], row["function"]) for row in overrides] == [("web/a.ts", "(anonymous)")]
