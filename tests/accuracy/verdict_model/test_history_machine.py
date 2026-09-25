"""A run history driven through the CLI, checked after every step against model_verdict.

Each step changes the repository one way (edit a function's complexity or
coverage, add or remove a function, fail or pass a test, break or mend lane b,
commit) and then runs one command that writes a run or a mark: coverage (full,
one lane, with a failed lane), inventory, verify (passing and failing), verify
--override, the hook's override, ratchet seed and prune, runs prune --keep 1,
or an upgrade that leaves the marks under an older analysis version. After
every step:

- the store holds the runs the model says each command wrote, with their kinds
  and verdicts, and `runs list` marks the model's baseline;
- the marks file holds the model's marks under the model's metric stamp, and no
  mark rose;
- `trend` lists exactly the trusted runs.

Each verify also checks the baseline it named, the gate, regression and
new-failure sets, the exit code and, on a pass, the tighten (with damping). The
`tsv` command verifies a copy of the repo against the TSV the store's baseline
emits, which must give the model's verdict with nothing forgiven (the TSV
carries no failures). After a runs prune every read command answers as before.

test_every_step_in_one_scripted_history walks one fixed history through every
command, so each is exercised on every push whatever the machine draws.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import itertools
import json
import shutil

from hypothesis import strategies as st
from hypothesis.stateful import (RuleBasedStateMachine, initialize, invariant, rule,
                                 run_state_machine_as_test)
import pytest

from accuracy.kit import repos
from accuracy.kit.settings import process
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

START = (vw.World()
         .with_fn("app", vw.Fn("a1", 1, 2)).with_fn("app", vw.Fn("a2", 3, 6))
         .with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_fn("lib", vw.Fn("b2", 7, 7))
         .with_fn("lib", vw.Fn("b3", 0, 1))
         .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b"))
         .with_test(vw.Test("flaky")))
NAMES = {"app": ("a1", "a2", "a3"), "lib": ("b1", "b2", "b3")}
OLD_METRIC = "crapkit-analysis=10 lizard=1.24.0"
READS = (("worklist", "--json"), ("next-item",), ("overrides", "--json"))
# Weighted: a verify is the step most worth repeating.
COMMANDS = ("verify", "verify", "verify", "coverage", "coverage", "one_lane", "inventory",
            "override", "hook", "hook_grant", "seed", "prune", "runs_prune", "upgrade", "tsv")
# How often each command ran this session, printed so a reader sees what was explored.
STEPS: Counter = Counter()


@dataclass
class Measured:
    """One run the model expects in the store."""
    run: model.Run
    world: vw.World | None = None
    failures: frozenset = frozenset()
    lanes: tuple = ()

    @property
    def id(self) -> int:
        return self.run.id


def _each(world: vw.World, value) -> dict:
    return {(vw.FILES[scope], fn.long_name): value(fn)
            for scope in vw.FILES for fn in world.functions[scope]}


def scores(world: vw.World) -> dict:
    """{(path, key): crap} for every function the world holds."""
    return _each(world, lambda fn: fn.crap)


def changed(old: vw.World, new: vw.World) -> set:
    """The functions whose text differs, or that are new. World source lines
    are unique, so this is exactly the set a diff's hunks overlap."""
    before = _each(old, vw.fn_lines)
    return {key for key, lines in _each(new, vw.fn_lines).items() if before.get(key) != lines}


def failed_tests(world: vw.World) -> frozenset:
    return frozenset(t.id for t in world.tests if t.failed)


@dataclass(frozen=True)
class Verdict:
    gate: frozenset
    ratchet: frozenset
    new_failures: frozenset

    @property
    def exit(self) -> int:
        found = {name for name, value in (("gate", self.gate), ("ratchet", self.ratchet),
                                          ("failures", self.new_failures)) if value}
        return model.exit_code(frozenset(found))


def expected_verdict(world, base: Measured, tree: vw.World, marks: dict,
                     forgive: bool = True) -> Verdict:
    """The verdict against `base`. The diff that decides what was touched runs
    from `tree`, the world committed at the baseline's commit: a baseline run
    that measured uncommitted edits stores HEAD, and git can only diff from a
    commit (README, What the verdict line covers: verify measures the diff from
    the baseline's commit)."""
    fresh = scores(world)
    base_failed = base.failures if forgive else frozenset()
    return Verdict(_gated(fresh, changed(tree, world), marks), _regressed(fresh, marks),
                   failed_tests(world) - base_failed)


def _gated(fresh: dict, touched: set, marks: dict) -> frozenset:
    return frozenset(key for key, crap in fresh.items()
                     if model.verify_gate(model.Row(*key, 0, crap), key in touched, vw.TARGET,
                                          marks.get(key)))


def _regressed(fresh: dict, marks: dict) -> frozenset:
    return frozenset(key for key, crap in fresh.items() if model.regression(crap, marks.get(key)))


def json_verdict(payload: dict) -> Verdict:
    gate = {(v["path"], v["key_name"] or v["long_name"]) for v in payload["gate_violations"]}
    ratchet = {(r["path"], r["long_name"]) for r in payload["ratchet_regressions"]}
    return Verdict(frozenset(gate), frozenset(ratchet), frozenset(payload["new_failures"]))


def digest_pair(history: list[Measured]) -> set[int]:
    """README: the two newest runs with identical lane sets, among trusted runs."""
    trusted = [m for m in history if model.trusted(m.run)]
    for newer, older in itertools.combinations(reversed(trusted), 2):
        if set(newer.lanes) == set(older.lanes):
            return {newer.id, older.id}
    return set()


def _ok(ok) -> int | None:
    return None if ok is None else int(ok)


def _covered(decisions: int, share: int) -> int:
    return (share * 2 * decisions) // 4 if decisions else min(share, 1)


EDITS = st.tuples(st.just("edit"), st.sampled_from(sorted(vw.FILES)), st.integers(0, 2),
                  st.integers(0, 7), st.integers(0, 4))
CHANGES = st.one_of(st.just(("none",)), st.just(("commit",)), EDITS,
                    st.tuples(st.just("toggle"), st.sampled_from(sorted(vw.FILES))),
                    st.tuples(st.just("flaky"), st.booleans()),
                    st.tuples(st.just("lane_b"), st.booleans()))


class History(RuleBasedStateMachine):
    """The whole run history of one repository, in the model and in crapkit."""

    numbers = itertools.count()

    def __init__(self, base, templates):
        super().__init__()
        top = base / f"history{next(History.numbers)}"
        self.sc = vw.Scenario(templates.copy(vw.spec(START), top), START)
        self.head = START
        self.trees = {self.sc.head(): START}
        self.history: list[Measured] = []
        self.next_id = 1
        self.marks: dict | None = None
        self.stamp: str | None = None
        self.running: str | None = None
        self.overrides: set[int] = set()
        self.last_marks: dict = {}
        self.side = itertools.count()

    # --- the machine ----------------------------------------------------------------------

    @initialize()
    def measured(self):
        self.coverage()

    @rule(change=CHANGES, command=st.sampled_from(COMMANDS))
    def step(self, change, command):
        getattr(self, f"change_{change[0]}")(*change[1:])
        STEPS[command] += 1
        getattr(self, command)()

    @invariant()
    def agrees_with_the_model(self):
        stored = self.sc.runs() if self.history else []
        assert [(r["id"], r["kind"], r["verdict_ok"]) for r in stored] == \
            [(m.id, m.run.kind, _ok(m.run.ok)) for m in self.history]
        self._check_marks()
        if self.history:
            self._check_listing()

    # --- model helpers ---------------------------------------------------------------------

    def _append(self, kind: str, ok=None, world=None, lanes=()) -> Measured:
        run = model.Run(self.next_id, kind, self.sc.head(), ok, self.running or "")
        measured = Measured(run, world, failed_tests(world) if world else frozenset(), lanes)
        self.history.append(measured)
        self.next_id += 1
        return measured

    def _learn_metric(self) -> None:
        """The running metric is an input, read off the first scored run."""
        if self.running is None:
            versions = json.loads(self.sc.runs()[-1]["tool_versions"])
            self.running = f"crapkit-analysis={versions['analysis_version']} lizard={versions['lizard']}"
            self.history[-1].run = replace(self.history[-1].run, metric=self.running)

    def _baseline(self) -> Measured | None:
        return self._measured(model.baseline([m.run for m in self.history]))

    def _measured(self, run: model.Run | None) -> Measured | None:
        return next((m for m in self.history if run and m.id == run.id), None)

    def _marks(self) -> dict:
        return dict(self.marks or {})

    def _tree(self, base: Measured) -> vw.World:
        return self.trees[base.run.commit]

    def _recorded(self) -> str | None:
        return self.stamp if self.marks is not None else None

    def _stamp_refused(self) -> bool:
        return self.marks is not None and model.stamp_refused(self.stamp, self.running)

    def _lanes(self) -> tuple:
        return tuple(lane for lane in vw.LANES if lane not in self.sc.world.failing_lanes)

    def _verdict_possible(self) -> bool:
        return (self._baseline() is not None and not self._stamp_refused()
                and not self.sc.world.failing_lanes)

    # --- changes ---------------------------------------------------------------------------

    def change_none(self):
        pass

    def change_commit(self):
        self.trees[self.sc.commit()] = self.head = self.sc.world

    def change_edit(self, scope, index, decisions, share):
        name = NAMES[scope][index]
        fn = vw.Fn(name, decisions, _covered(decisions, share))
        self.sc.set(self.sc.world.with_fn(scope, fn))

    def change_toggle(self, scope):
        name, world = NAMES[scope][2], self.sc.world
        present = any(f.name == name for f in world.functions[scope])
        self.sc.set(world.without_fn(scope, name) if present
                    else world.with_fn(scope, START.fn(scope, name)))

    def change_flaky(self, failed):
        self.sc.set(self.sc.world.with_test(vw.Test("flaky", failed)))

    def change_lane_b(self, broken):
        lanes = frozenset({"b"}) if broken else frozenset()
        self.sc.set(replace(self.sc.world, failing_lanes=lanes))

    # --- commands that write runs ------------------------------------------------------------

    def coverage(self):
        result = self.sc.run("coverage")
        partial = bool(self.sc.world.failing_lanes)
        assert result.code == (5 if partial else 0), result.stdout + result.stderr
        self._append(model.PARTIAL if partial else model.COVERAGE, world=self.sc.world,
                     lanes=self._lanes())
        self._learn_metric()

    def one_lane(self):
        result = self.sc.run("coverage", "--lane", "a")
        assert result.code == 0, result.stdout + result.stderr
        self._append(model.PARTIAL, world=self.sc.world, lanes=("a",))

    def inventory(self):
        assert self.sc.run("inventory").code == 0
        self._append(model.INVENTORY)

    def _refused(self, result) -> bool:
        """The verifies that store no run: no trusted baseline (exit 1), marks
        under another metric (exit 3), a lane that failed (exit 5)."""
        expected = (1 if self._baseline() is None else 3 if self._stamp_refused()
                    else 5 if self.sc.world.failing_lanes else None)
        if expected is not None:
            assert result.code == expected, result.stdout + result.stderr
        return expected is not None

    def verify(self):
        before, base = self._marks(), self._baseline()
        result = self.sc.run("verify", "--json")
        if self._refused(result):
            return
        expected = expected_verdict(self.sc.world, base, self._tree(base), before)
        payload = result.json()
        assert (result.code, payload["baseline_run"]) == (expected.exit, base.id), payload
        assert json_verdict(payload) == expected
        ok = expected.exit == 0
        measured = self._append(model.VERIFY, ok, self.sc.world, self._lanes())
        if ok:
            self._tighten(measured, before)

    def _previous_same_commit(self, measured: Measured) -> dict:
        older = [m for m in self.history[:-1]
                 if model.trusted(m.run) and m.run.commit == measured.run.commit]
        return scores(older[-1].world) if older else {}

    def _tighten(self, measured: Measured, before: dict) -> None:
        if self.marks is None:
            return
        after = model.tighten(before, scores(measured.world), vw.TARGET,
                              self._previous_same_commit(measured))
        if after != before:
            self.marks, self.stamp = after, self.running

    def _expected_now(self) -> Verdict | None:
        base = self._baseline()
        if not self._verdict_possible():
            return None
        return expected_verdict(self.sc.world, base, self._tree(base), self._marks())

    def override(self):
        expected = self._expected_now()
        if expected is None or not expected.gate:
            return self.verify()
        result = self.sc.run("verify", "--override", "accepted by the machine", "--json")
        granted = not (expected.ratchet or expected.new_failures)
        assert result.code == _override_exit(expected, granted), result.stdout + result.stderr
        measured = self._append(model.VERIFY, granted, self.sc.world, self._lanes())
        if granted:
            self._grant(measured, expected.gate)

    def _grant(self, measured: Measured, violations) -> None:
        fresh = scores(measured.world)
        self.marks = {**self._marks(), **{key: model.mark_value(fresh[key]) for key in violations}}
        self.stamp = self.running
        self.overrides.add(measured.id)

    def _staged_violations(self, ccn: dict) -> set:
        return {key for key in changed(self.head, self.sc.world)
                if model.hook_gate(ccn[key], vw.TARGET, key in self._marks())}

    def hook(self, grant: bool = False):
        repos.git(self.sc.top, "add", "-A")
        ccn = _each(self.sc.world, lambda fn: fn.ccn)
        violations = self._staged_violations(ccn)
        reason = "the machine accepts it" if grant else None
        result = vw.drive.Driver(self.sc.root, env={"CRAPKIT_OVERRIDE_REASON": reason}).run(
            "hook-precommit")
        assert result.code == _hook_exit(violations, grant), result.stdout + result.stderr
        if violations and grant:
            self._hook_grant(violations, ccn)

    def hook_grant(self):
        self.hook(grant=True)

    def _hook_grant(self, violations, ccn) -> None:
        """The hook synthesizes the worst case, ccn^2 + ccn, keeps the recorded
        stamp and records a hook run."""
        worst = {key: model.mark_value(ccn[key] * ccn[key] + ccn[key]) for key in violations}
        self.stamp = model.stamp_after("hook-override", self._recorded(), self.running)
        self.marks = {**self._marks(), **worst}
        self.overrides.add(self._append(model.HOOK).id)

    # --- commands that write marks ---------------------------------------------------------

    def seed(self):
        base = self._baseline()
        result = self.sc.run("ratchet", "seed")
        assert result.code == (1 if base is None else 0), result.stdout + result.stderr
        if base is not None:
            self.marks, _, _ = model.seed(self._marks(), scores(base.world), vw.TARGET)
            self.stamp = base.run.metric

    def prune(self):
        base = self._baseline()
        result = self.sc.run("ratchet", "prune")
        assert result.code == (1 if base is None else 0), result.stdout + result.stderr
        if base is not None:
            self.stamp = model.stamp_after("prune", self._recorded(), self.running)
            self.marks = model.prune(self._marks(), set(scores(base.world)))

    def upgrade(self):
        """The marks as an older analysis version left them."""
        if self.marks is None:
            return self.seed()
        path = self.sc.root / "crapkit-ratchet.tsv"
        lines = path.read_bytes().decode("utf-8").split("\n")
        path.write_bytes("\n".join([f"# {OLD_METRIC}", *lines[1:]]).encode("utf-8"))
        self.stamp = OLD_METRIC

    # --- retention ---------------------------------------------------------------------------

    def runs_prune(self):
        before = self._reads()
        keep = model.keep_set([m.run for m in self.history], 1, self.overrides,
                              digest_pair(self.history))
        result = self.sc.run("runs", "prune", "--keep", "1")
        assert result.code == 0, result.stdout + result.stderr
        self.history = [m for m in self.history if m.id in keep]
        assert self._reads() == before

    def _reads(self) -> dict:
        answers = {args: self.sc.run(*args).stdout for args in READS}
        trend = self.sc.json("trend")["runs"] if self._baseline() else []
        answers["newest trend row"] = json.dumps(trend[-1:], sort_keys=True)
        return answers

    # --- the portable baseline ---------------------------------------------------------------

    def tsv(self):
        """On a copy: the store's baseline emitted as a TSV gives the model's
        verdict with nothing forgiven, on a clone with no store."""
        if not self._verdict_possible():
            return self.verify()
        copy = self.sc.copy(self.sc.top.parent / f"{self.sc.top.name}-tsv{next(self.side)}")
        base = self._baseline()
        emitted = copy.run("verify", "--emit-baseline", "b.tsv", "--no-tighten", "--json")
        assert emitted.json()["baseline_run"] == base.id
        shutil.rmtree(copy.root / ".crapkit")
        result = copy.run("verify", "--baseline-tsv", "b.tsv", "--json")
        expected = expected_verdict(self.sc.world, base, self._tree(base), self._marks(),
                                    forgive=False)
        assert (result.code, json_verdict(result.json())) == (expected.exit, expected)

    # --- after every step -------------------------------------------------------------------

    def _check_marks(self) -> None:
        text = self.sc.marks_text()
        assert (text is None) == (self.marks is None)
        if text is not None:
            parsed = model.parse_marks(text)
            assert (parsed.stamp, parsed.marks) == (self.stamp, self.marks)
        now = self._marks()
        assert {key: (self.last_marks[key], value) for key, value in now.items()
                if key in self.last_marks and value > self.last_marks[key]} == {}
        self.last_marks = now

    def _check_listing(self) -> None:
        base = self._baseline()
        listed = self.sc.json("runs", "list")["runs"]
        assert [run["id"] for run in listed if run["baseline"]] == ([base.id] if base else [])
        self._check_trend(base)

    def _check_trend(self, base) -> None:
        trend = self.sc.json("trend")["runs"] if base else []
        assert [run["run_id"] for run in trend] == \
            [m.id for m in self.history if model.trusted(m.run)]


def _override_exit(expected: Verdict, granted: bool) -> int:
    """A granted override exits 0; a refused one keeps the verdict's exit."""
    return 0 if granted else expected.exit


def _hook_exit(violations: set, grant: bool) -> int:
    """The hook refuses a staged violation with 6 unless the override reason grants it."""
    return 6 if violations and not grant else 0


def _machine(tmp_path, templates):
    return type("Run", (History,), {"__init__": lambda self: History.__init__(self, tmp_path,
                                                                              templates)})


@pytest.mark.process
def test_the_run_history_follows_the_model(repo_templates, tmp_path):
    run_state_machine_as_test(_machine(tmp_path, repo_templates), settings=process)
    print("history machine commands:", dict(STEPS))


SCRIPT = (
    (("edit", "app", 2, 7, 4), "verify"),         # a3 touched past the ceiling: gate
    (("commit",), "coverage"),                    # the tree the gate refused, measured anyway
    (("none",), "seed"),                          # seed reads the run in front of the failure
    (("flaky", True), "verify"),                  # a new failure
    (("flaky", False), "override"),               # nothing touched is past its ceiling now
    (("edit", "lib", 1, 7, 1), "verify"),         # b2 loses coverage: regression, untouched
    (("edit", "lib", 1, 7, 2), "verify"),         # back where it was: passes and tightens
    (("lane_b", True), "coverage"),               # a partial run
    (("lane_b", False), "one_lane"),              # a lane subset
    (("none",), "inventory"),
    (("edit", "app", 0, 7, 4), "hook"),           # a1 staged past the ceiling: exit 6
    (("none",), "hook_grant"),                    # the hook's override grants it
    (("commit",), "verify"),
    (("toggle", "lib"), "coverage"),              # b3 leaves the run
    (("none",), "prune"),                         # its mark, if any, goes
    (("none",), "upgrade"),                       # marks under an older analysis: refused
    (("none",), "verify"),
    (("none",), "seed"),                          # seed restamps from a run this crapkit measured
    (("edit", "app", 1, 7, 0), "override"),       # a2 past the ceiling, granted
    (("none",), "tsv"),
    (("none",), "runs_prune"),
)


@pytest.mark.process
def test_every_step_in_one_scripted_history(repo_templates, tmp_path):
    machine = History(tmp_path, repo_templates)
    machine.measured()
    machine.agrees_with_the_model()
    for change, command in SCRIPT:
        machine.step(change, command)
        machine.agrees_with_the_model()
