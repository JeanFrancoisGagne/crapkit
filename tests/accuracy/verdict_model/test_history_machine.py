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

- on a copy of the repo, `verify` against the store's baseline and `verify`
  against the TSV record it emits (--emit-baseline, then --baseline-tsv) give
  one verdict: the same exit and JSON but for the run ids, and under ruling
  V8 a failure the baseline had counts as new under the record. Both read the
  lanes' artifacts on disk, so only the baseline's source differs.

Each verify also checks the baseline it named, the gate, regression and
new-failure sets, the exit code and, on a pass, the tighten (with damping). The
`tsv` command verifies a fresh clone (no .crapkit/) against that record, which
must give the model's gate and ratchet findings, and its whole verdict when the
baseline had no failing test (ruling V8). Around a runs prune every read
command runs twice, and each answer is what test_retention.read_commands
allows: unchanged, or less the pruned runs where it lists the run history.

test_every_step_in_one_scripted_history walks one fixed history through every
command, so each is exercised on every push whatever the machine draws.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import itertools
import json
import os
import shutil
import stat
import sys

from hypothesis import strategies as st
from hypothesis.stateful import (RuleBasedStateMachine, initialize, invariant, rule,
                                 run_state_machine_as_test)
import pytest

from accuracy.kit import repos
from accuracy.kit.settings import process
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import test_retention as retention
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
READS = retention.read_commands({vw.FILES[scope]: names for scope, names in NAMES.items()})
# The store-against-record check: both verifies read the lanes' artifacts on disk
# (docs/lanes.md:982, --reuse-artifacts), so they score one measurement, and neither
# tightens, so the marks stay as the state left them.
ONE_MEASUREMENT = ("verify", "--reuse-artifacts", "--no-tighten", "--json")
RECORD = ".crapkit/record.tsv"
# The two answers differ here by design: the record names no run (README.md:794,
# --baseline-tsv), and each store numbers its own runs.
RUN_IDS = frozenset({"baseline_run", "run_id"})
# Weighted: a verify is the step most worth repeating.
COMMANDS = ("verify", "verify", "verify", "coverage", "coverage", "one_lane", "inventory",
            "override", "hook", "hook_grant", "seed", "prune", "runs_prune", "upgrade", "tsv")
# How often each command ran this session, and how each state's record check ended,
# printed so a reader sees what was explored.
STEPS: Counter = Counter()
RECORDS: Counter = Counter()


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
        self._record_agrees()

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
        """Every read command before and after `runs prune --keep 1`; each
        answer after it is the one test_retention.read_commands allows. The
        index is refreshed first: ruling V12, see retention.fresh_index."""
        retention.fresh_index(self.sc)
        before = retention.answers(self.sc, READS)
        keep = model.keep_set([m.run for m in self.history], 1, self.overrides,
                              digest_pair(self.history))
        result = self.sc.run("runs", "prune", "--keep", "1")
        assert result.code == 0, result.stdout + result.stderr
        self.history = [m for m in self.history if m.id in keep]
        want, after = retention.after_prune(before, READS, keep), retention.answers(self.sc, READS)
        assert after == want, f"{retention.moved(want, after)}\n{self._tree_state(after)}"

    def _tree_state(self, after: dict) -> str:
        """What a staleness note reads, for the failure report: HEAD, each lane
        stamp's commit, git's view of the tree, the free disk, and whether a
        third read of the same state agrees with the second."""
        stamps = json.loads((self.sc.root / ".crapkit" / "artifacts.json").read_text(encoding="utf-8"))
        commits = {path: entry.get("commit") for path, entry in stamps.items()}
        third = retention.answers(self.sc, READS)
        return (f"HEAD {self.sc.head()} stamps {commits} status "
                f"{repos.git(self.sc.top, 'status', '--porcelain')!r} free_bytes "
                f"{shutil.disk_usage(self.sc.root).free} third read against the second:\n"
                f"{retention.moved(after, third) or 'the same'}")

    # --- the portable baseline ---------------------------------------------------------------

    def _copy(self, tag: str) -> vw.Scenario:
        """A private copy of the repo. A copied file is new to the filesystem,
        so the copy's index is stat-dirty, and verify's staleness reads race
        the index rewrite its own `git diff` makes (ruling V12); the copy's
        index is refreshed so the verdicts compared are not that race's."""
        copy = self.sc.copy(self.sc.top.parent / f"{self.sc.top.name}-{tag}{next(self.side)}")
        retention.fresh_index(copy)
        return copy

    def tsv(self):
        """On a copy: the store's baseline emitted as a TSV gives the model's
        verdict on a clone with no store. The record carries no test failures,
        so a failure the baseline had counts as new there (ruling V8, an open
        defect test_baseline_tsv pins); with none, the whole verdict agrees."""
        if not self._verdict_possible():
            return self.verify()
        copy = self._copy("tsv")
        base = self._baseline()
        emitted = copy.run("verify", "--emit-baseline", "b.tsv", "--no-tighten", "--json")
        assert emitted.json()["baseline_run"] == base.id
        shutil.rmtree(copy.root / ".crapkit")
        result = copy.run("verify", "--baseline-tsv", "b.tsv", "--json")
        expected = expected_verdict(self.sc.world, base, self._tree(base), self._marks())
        got = json_verdict(result.json())
        assert (got.gate, got.ratchet) == (expected.gate, expected.ratchet)
        if not base.failures:
            assert (result.code, got) == (expected.exit, expected)

    # --- after every step -------------------------------------------------------------------

    def _record_agrees(self) -> None:
        """On a copy, verify against the store's baseline and against the
        record it emits give one verdict. The store stays for the second
        verify, which README.md:794 lets --baseline-tsv pass over; `tsv` checks
        the clone that has none. A state whose verify refuses before it picks
        a baseline (no trusted run, exit 1; marks under another metric, exit
        3) emits no record, and `_refused` checks those exits."""
        if self._baseline() is None or self._stamp_refused():
            RECORDS["no record"] += 1
            return
        copy = self._copy("one")
        store = copy.run(*ONE_MEASUREMENT, "--emit-baseline", RECORD)
        record = copy.run(*ONE_MEASUREMENT, "--baseline-tsv", RECORD)
        _discard(copy.top)
        RECORDS[one_verdict(store, record)] += 1

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


def one_verdict(store, record) -> str:
    """The exit and every JSON field but the run ids agree. Ruling V8
    (calc-bug verdict-model-3, pinned in test_baseline_tsv.py): the record
    carries no test failures, so a failure the store's baseline had, and
    forgives, counts as new under the record. Returns which rule held."""
    ours, theirs = _verdict_fields(store), _verdict_fields(record)
    forgiven = frozenset(ours.get("forgiven_failures", ()))
    report = f"store: {store.code} {store.stdout}{store.stderr}\nrecord: {record.code} {record.stdout}{record.stderr}"
    if not forgiven:
        assert (record.code, theirs) == (store.code, ours), report
        return f"one verdict, exit {store.code}"
    base, got = json_verdict(store.json()), json_verdict(record.json())
    assert (got.gate, got.ratchet, got.new_failures) == \
        (base.gate, base.ratchet, base.new_failures | forgiven), report
    assert record.code == got.exit, report
    return "V8"


def _verdict_fields(result) -> dict:
    payload = retention.parsed(result.stdout)
    if not isinstance(payload, dict):
        return {"stdout": payload}
    return {key: value for key, value in payload.items() if key not in RUN_IDS}


# rmtree names its error hook onexc from Python 3.12 and onerror before it.
_ON_ERROR = "onexc" if sys.version_info >= (3, 12) else "onerror"


def _discard(top) -> None:
    """Delete a checked copy. git writes its objects read-only, which Windows
    will not delete until the bit is cleared."""
    shutil.rmtree(top, **{_ON_ERROR: _clear_and_retry})


def _clear_and_retry(function, path, _error) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _override_exit(expected: Verdict, granted: bool) -> int:
    """A granted override exits 0; a refused one keeps the verdict's exit."""
    return 0 if granted else expected.exit


def _hook_exit(violations: set, grant: bool) -> int:
    """The hook refuses a staged violation with 6 unless the override reason grants it."""
    return 6 if violations and not grant else 0


def _machine(tmp_path, templates):
    return type("Run", (History,), {"__init__": lambda self: History.__init__(self, tmp_path,
                                                                              templates)})


@pytest.mark.nightly
@pytest.mark.process
def test_the_run_history_follows_the_model(repo_templates, tmp_path):
    run_state_machine_as_test(_machine(tmp_path, repo_templates), settings=process)
    print("history machine commands:", dict(STEPS), "record checks:", dict(RECORDS))


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


@pytest.mark.nightly
@pytest.mark.process
def test_the_prune_report_names_each_moved_field_and_the_tree(repo_templates, tmp_path):
    """The runs_prune failure report: one line per moved field with both
    values, then HEAD, the stamps, git status and a third read of the state."""
    want = {("r",): {"a": 1, "b": [1, 2]}, ("same",): "x"}
    got = {("r",): {"a": 1, "b": [1, 3], "c": 0}, ("same",): "x"}
    assert retention.moved(want, got) == ("('r',) .b[1]: want=2 got=3\n"
                                          "('r',) .c: want=None got=0")
    machine = History(tmp_path, repo_templates)
    machine.measured()
    report = machine._tree_state(retention.answers(machine.sc, READS))
    assert (f"HEAD {machine.sc.head()}" in report, report.endswith("the same")) == (True, True), report


def _verified(code: int, **fields) -> vw.drive.Result:
    """A verify answer, as the two sides of the record check print one."""
    payload = {"gate_violations": [], "ratchet_regressions": [], "new_failures": [],
               "forgiven_failures": [], "baseline_run": 4, "run_id": 9, **fields}
    return vw.drive.Result(("verify",), code, json.dumps(payload), "")


GATE = [{"path": "src/app.py", "key_name": "a3( x )", "long_name": "a3( x )"}]
ONE_VERDICT = {
    "only the run ids differ": (_verified(0), _verified(0, baseline_run=None, run_id=1), True),
    "a gate finding differs": (_verified(6, gate_violations=GATE), _verified(0), False),
    "the exit differs": (_verified(0), _verified(6), False),
    "V8: the forgiven failure is new": (_verified(0, forgiven_failures=["t::known"]),
                                        _verified(8, new_failures=["t::known"]), True),
    "V8, but the record exits 0": (_verified(0, forgiven_failures=["t::known"]),
                                   _verified(0, new_failures=["t::known"]), False),
    "both refuse alike": (vw.drive.Result(("verify",), 5, "", "a"), vw.drive.Result(("verify",), 5, "", "b"), True),
    "one refuses": (_verified(0), vw.drive.Result(("verify",), 5, "", ""), False),
}


@pytest.mark.parametrize("case", sorted(ONE_VERDICT))
def test_one_verdict_allows_only_the_run_ids_and_v8(case):
    """one_verdict's table, worked from its docstring."""
    store, record, holds = ONE_VERDICT[case]
    try:
        one_verdict(store, record)
    except AssertionError:
        assert not holds
    else:
        assert holds


# --- one history per past defect -------------------------------------------------------------
# Each scenario is the shortest run history that shows one rule, with the
# expected run, mark or exit taken from model_verdict. They read exit codes,
# `verify --json` and the marks file, so an older crapkit replays them.

WORSE = vw.Fn("a3", 7, 0)            # ccn 8, cov 0: CRAP 72, past the ceiling of 6
# ccn 7, cov 2/3: CRAP 8.814814..., stored as 8.8148, so the fresh score sits
# above its own mark until the two are compared at the mark's four decimals.
ROUNDS_DOWN = vw.Fn("a3", 6, 8)


def _measured(make_repo, world: vw.World = START) -> vw.Scenario:
    scenario = vw.Scenario.build(make_repo, world)
    assert scenario.run("coverage").code == 0
    return scenario


def _verify(scenario: vw.Scenario, *extra: str) -> tuple[int, dict]:
    result = scenario.run("verify", "--json", *extra)
    return result.code, result.json()


def _runs(scenario: vw.Scenario) -> list[model.Run]:
    kinds = {"coverage": model.COVERAGE, "verify": model.VERIFY, "partial": model.PARTIAL,
             "inventory": model.INVENTORY, "hook": model.HOOK}
    return [model.Run(row["id"], kinds[row["kind"]], row["commit_sha"],
                      None if row["verdict_ok"] is None else bool(row["verdict_ok"]))
            for row in scenario.runs()]


def _failed_verify(scenario: vw.Scenario) -> None:
    """Touch a3 past its ceiling and let verify refuse it (exit 6)."""
    scenario.set(scenario.world.with_fn("app", WORSE))
    assert _verify(scenario)[0] == 6


def _metric(scenario: vw.Scenario, run: int = -1) -> str:
    versions = json.loads(scenario.runs()[run]["tool_versions"])
    return f"crapkit-analysis={versions['analysis_version']} lizard={versions['lizard']}"


@pytest.mark.nightly
@pytest.mark.process
def test_seed_then_verify_same_run_passes(make_repo):
    """docs/ratchet.md: a mark is CRAP to four decimals and verify compares at
    them, so the run a mark was seeded from never regresses against it."""
    world = START.with_fn("app", ROUNDS_DOWN)
    assert ROUNDS_DOWN.crap > model.mark_value(ROUNDS_DOWN.crap) == model.Decimal("8.8148")
    scenario = _measured(make_repo, world)
    assert scenario.run("ratchet", "seed").code == 0
    scenario.commit("seed")
    assert scenario.run("verify").code == model.exit_code(frozenset()) == 0
    assert model.parse_marks(scenario.marks_text()).marks == model.seed({}, scores(world), vw.TARGET)[0]


@pytest.mark.nightly
@pytest.mark.process
def test_passing_verify_advances_baseline_and_trend(make_repo):
    """README, The trusted baseline: a passing verify qualifies, so the next
    verify measures against it and trend lists it."""
    scenario = _measured(make_repo)
    first, second = _verify(scenario), _verify(scenario)
    runs = _runs(scenario)
    assert (first[0], first[1]["baseline_run"]) == (0, model.baseline(runs[:1]).id) == (0, 1)
    assert (second[0], second[1]["baseline_run"]) == (0, model.baseline(runs[:2]).id) == (0, 2)
    trend = scenario.json("trend")["runs"]
    assert [run["run_id"] for run in trend] == [r.id for r in runs if model.trusted(r)] == [1, 2, 3]


@pytest.mark.nightly
@pytest.mark.process
def test_failed_verify_never_serves_as_baseline(make_repo):
    """README: a failed verify never qualifies, so the next verify still
    measures against the coverage run and still reports the gate."""
    scenario = _measured(make_repo)
    _failed_verify(scenario)
    code, payload = _verify(scenario)
    assert model.baseline(_runs(scenario)[:2]).id == 1
    assert (code, payload["baseline_run"]) == (6, 1)


@pytest.mark.nightly
@pytest.mark.process
def test_coverage_never_retires_a_failed_verify(make_repo):
    """README, The taint rule: a coverage run taken after a failed verify does
    not become the baseline until some verify passes."""
    scenario = _measured(make_repo)
    _failed_verify(scenario)
    assert scenario.run("coverage").code == 0
    before = _runs(scenario)
    code, payload = _verify(scenario)
    assert model.standing_failure(before).id == 2 and model.baseline(before).id == 1
    assert (code, payload["baseline_run"]) == (6, 1)
    assert "run 2" in scenario.run("verify").stderr


@pytest.mark.nightly
@pytest.mark.process
def test_seed_reads_the_run_verify_reads(make_repo):
    """README: seed asks verify's question. After [coverage, failed verify,
    coverage] it seeds from run 1, not from the coverage run the taint rule
    refuses, and names the failed verify it stepped over."""
    scenario = _measured(make_repo)
    first = scenario.world
    _failed_verify(scenario)
    assert scenario.run("coverage").code == 0
    picked = model.baseline(_runs(scenario))
    result = scenario.run("ratchet", "seed")
    assert (result.code, picked.id) == (0, 1)
    assert "2" in result.stdout + result.stderr
    marks = model.parse_marks(scenario.marks_text()).marks
    assert marks == model.seed({}, scores(first), vw.TARGET)[0]


@pytest.mark.nightly
@pytest.mark.process
def test_bouncing_measurement_holds_marks(make_repo):
    """docs/ratchet.md, damping: one commit measured twice cannot have improved.
    b2 reads CRAP 72 on the seeded run, then 8.19 on a verify of the same commit
    (a ratio past tighten_max_jump 2.0), so its mark is held at 72."""
    low, high = vw.Fn("b2", 7, 0), vw.Fn("b2", 7, 12)
    scenario = _measured(make_repo, START.with_fn("lib", low))
    assert scenario.run("ratchet", "seed").code == 0
    marks = model.parse_marks(scenario.marks_text()).marks
    scenario.world = scenario.world.with_fn("lib", high)
    scenario.write_plan()
    key = ("lib/util.py", "b2( x )")
    held = model.tighten(marks, scores(scenario.world), vw.TARGET, previous=scores(START.with_fn("lib", low)))
    assert model.moved_past(low.crap, high.crap) and held[key] == marks[key] == model.Decimal("72.0000")
    result = scenario.run("verify")
    assert result.code == 0, result.stdout + result.stderr
    assert model.parse_marks(scenario.marks_text()).marks == held
    assert "b2" in result.stderr


def _age_runs(scenario: vw.Scenario, analysis: int) -> str:
    """Relabel every stored run as measured under an older analysis version,
    as the store an upgrade finds holds them. Returns that metric."""
    import sqlite3
    connection = sqlite3.connect(scenario.root / ".crapkit" / "crap.sqlite")
    with connection:
        for run_id, text in connection.execute("SELECT id, tool_versions FROM runs").fetchall():
            versions = {**json.loads(text), "analysis_version": analysis}
            connection.execute("UPDATE runs SET tool_versions = ? WHERE id = ?",
                               (json.dumps(versions), run_id))
    connection.close()
    return _metric(scenario)


def _stamp_marks(scenario: vw.Scenario, stamp: str) -> None:
    path = scenario.root / "crapkit-ratchet.tsv"
    lines = path.read_bytes().decode("utf-8").split("\n")
    path.write_bytes("\n".join([f"# {stamp}", *lines[1:]]).encode("utf-8"))


def _stamp(scenario: vw.Scenario) -> str | None:
    return model.parse_marks(scenario.marks_text()).stamp


@pytest.mark.nightly
@pytest.mark.process
def test_seed_stamps_the_metric_of_the_run_it_read(make_repo):
    """docs/ratchet.md, The metric stamp: seed leaves the metric the run it read
    was measured under, not the running one."""
    scenario = _measured(make_repo)
    running = _metric(scenario)
    old = _age_runs(scenario, 7)
    assert scenario.run("ratchet", "seed").code == 0
    assert _stamp(scenario) == model.stamp_after("seed", None, running, run=old) == old != running


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("write", ["prune", "move"])
def test_a_write_that_adds_no_number_keeps_the_recorded_stamp(make_repo, write):
    """prune and move add no number: both stamps the file recorded stay."""
    scenario = _measured(make_repo)
    assert scenario.run("ratchet", "seed").code == 0
    _stamp_marks(scenario, OLD_METRIC)
    before = model.parse_marks(scenario.marks_text())
    args = {"prune": ("ratchet", "prune"),
            "move": ("ratchet", "move", "src/app.py", "src/moved.py")}[write]
    assert scenario.run(*args).code == 0
    after = model.parse_marks(scenario.marks_text())
    assert (after.stamp, after.keys_version) == (before.stamp, before.keys_version)
    assert after.stamp == model.stamp_after(write, OLD_METRIC, _metric(scenario)) == OLD_METRIC


@pytest.mark.nightly
@pytest.mark.process
def test_prune_creates_a_file_under_the_running_metric(make_repo):
    """A marks file prune creates holds no mark and takes the running metric,
    even when the run it read was measured under an older one; verify then
    has nothing to refuse."""
    scenario = _measured(make_repo)
    running = _metric(scenario)
    _age_runs(scenario, 7)
    assert scenario.marks_text() is None
    assert scenario.run("ratchet", "prune").code == 0
    assert _stamp(scenario) == model.stamp_after("prune", None, running) == running
    assert scenario.run("verify").code != 3


@pytest.mark.nightly
@pytest.mark.process
def test_the_hook_s_grant_keeps_the_recorded_stamp(make_repo):
    """The hook's override adds ccn-only numbers and compares no mark, so it
    keeps a stale stamp; verify keeps refusing that file."""
    scenario = _measured(make_repo)
    assert scenario.run("ratchet", "seed").code == 0
    _stamp_marks(scenario, OLD_METRIC)
    scenario.set(scenario.world.with_fn("app", vw.Fn("a1", 7, 14)))
    repos.git(scenario.top, "add", "-A")
    grant = vw.drive.Driver(scenario.root, env={"CRAPKIT_OVERRIDE_REASON": "reviewed"})
    assert grant.run("hook-precommit").code == 0
    assert _stamp(scenario) == model.stamp_after("hook-override", OLD_METRIC, _metric(scenario, 0))
    assert _stamp(scenario) == OLD_METRIC
    assert scenario.run("verify").code == 3


@pytest.mark.process
def test_override_checks_the_stamp(make_repo):
    """docs/ratchet.md: verify --override stamps the running metric, so marks
    another metric recorded are refused (exit 3) before anything is granted."""
    scenario = _measured(make_repo)
    assert scenario.run("ratchet", "seed").code == 0
    _stamp_marks(scenario, OLD_METRIC)
    before = scenario.marks_text()
    scenario.set(scenario.world.with_fn("app", WORSE))
    result = scenario.run("verify", "--override", "reviewed", "--json")
    assert model.stamp_refused(OLD_METRIC, _metric(scenario)) and result.code == 3
    assert scenario.marks_text() == before
    assert scenario.json("overrides")["overrides"] == []


def _start_line_owner(world: vw.World, scope: str, line: int) -> str:
    return next(fn.long_name for fn, start, _ in vw.source(world.functions[scope])[1]
                if start == line)


@pytest.mark.nightly
@pytest.mark.process
def test_explain_reads_the_run_brief_reads(make_repo):
    """agent-json.md, Name resolution: explain resolves a start line against the
    run brief reads, the newest trusted one. A failed verify that swapped a1 and
    a2 moves neither command: line 1 is a1, as run 1 measured it."""
    scenario = _measured(make_repo)
    a1, a2, a3 = START.functions["app"]
    swapped = replace(START, functions={**START.functions, "app": (a2, a1, a3)})
    scenario.set(swapped.with_test(vw.Test("flaky", True)))
    assert _verify(scenario)[0] == 8
    newest_trusted = [run for run in _runs(scenario) if model.trusted(run)][-1]
    assert newest_trusted.id == 1
    want = _start_line_owner(START, "app", 1)
    explained = scenario.run("explain", "src/app.py", "1", "--json").json()["functions"]
    brief = scenario.run("brief", "src/app.py", "1", "--json").json()
    assert [f["long_name"] for f in explained] == [brief["function"]] == [want] == ["a1( x )"]


@pytest.mark.nightly
@pytest.mark.process
def test_explain_reads_the_newest_trusted_run_that_holds_the_file(make_repo):
    """agent-json.md: when the newest trusted run dropped the file, explain
    reads the newest trusted run that still holds it. Run 2 measured lib/util.py
    with no function left; b2's start line still resolves in run 1."""
    scenario = _measured(make_repo)
    line = vw.spans(START, "lib")["b2"][0]
    scenario.set(replace(START, functions={**START.functions, "lib": ()}))
    scenario.commit("drop lib")
    assert scenario.run("coverage").code == 0
    explained = scenario.run("explain", "lib/util.py", str(line), "--json")
    assert explained.code == 0, explained.stdout + explained.stderr
    functions = explained.json()["functions"]
    assert [f["long_name"] for f in functions] == [_start_line_owner(START, "lib", line)]
    assert [row["run_id"] for row in functions[0]["history"]] == [1]


TWIN_ONE = "def dup(x):\n    return x\n\n\n"


def _twin_two(branches: int) -> str:
    ifs = "".join(f"    if x > {n}:\n        x += {n}\n" for n in range(branches))
    return f"def dup(x):\n{ifs}    return x\n"


def _twin_config() -> str:
    return (f"[crapkit]\ntarget = 1\nalert_command = \"{vw.ALERT}\"\n\n"
            '[[scope]]\nname = "py"\npaths = ["py"]\nlanguages = ["python"]\n'
            "coverage_optional = true\n")


@pytest.mark.process
def test_override_records_canonical_key(make_repo):
    """docs/ratchet.md, Overrides: the grant lands in the marks file and the
    store's override log. Both name the second dup by its key, dup( x )#2
    (docs/ratchet.md, Twins; the `function` field of agent-json.md's
    `overrides --json`), and the mark is its CRAP: in a coverage_optional scope
    with no lane, its ccn (McCabe: 2 ifs + 1)."""
    spec = repos.Spec(steps=(repos.Commit(files={
        "crapkit.toml": _twin_config(), "py/twins.py": TWIN_ONE + _twin_two(1)}, message="seed"),))
    built = make_repo(spec)
    driver = vw.drive.Driver(built.root)
    assert driver.run("coverage").code == 0
    (built.root / "py" / "twins.py").write_bytes((TWIN_ONE + _twin_two(2)).encode("utf-8"))
    result = driver.run("verify", "--override", "reviewed debt", "--json")
    assert result.code == 0, result.stdout + result.stderr
    first = model.Row("py/twins.py", "dup( x )", 1, model.Fraction(1))
    second = model.Row("py/twins.py", "dup( x )", 5, model.Fraction(3), ccn=3)
    key = model.keys([first, second])[second]
    marks = model.parse_marks((built.root / "crapkit-ratchet.tsv").read_bytes().decode("utf-8"))
    assert marks.marks == {key: model.mark_value(3)} == {("py/twins.py", "dup( x )#2"): model.Decimal("3.0000")}
    logged = driver.json("overrides")["overrides"]
    assert [(row["path"], row["function"], row["crap"]) for row in logged] == [(*key, 3.0)]


# --- legacy identity: renames and runs that lost same-line order -----------------------------------

def _marks_of(built) -> dict:
    return model.parse_marks((built.root / "crapkit-ratchet.tsv").read_bytes().decode("utf-8")).marks


def _keys_of(driver: vw.drive.Driver, run: int) -> set:
    """The keys docs/ratchet.md gives run `run`'s rows, read with sqlite3."""
    found = driver.store("SELECT i.path, i.long_name, f.start, f.crap, f.occurrence FROM functions f "
                         "JOIN identities i ON i.id = f.identity_id WHERE f.run_id = ?", (run,))
    rows = [model.Row(r["path"], r["long_name"], r["start"], model.Fraction(r["crap"]),
                      occurrence=r["occurrence"]) for r in found]
    return set(model.keys(rows).values())


@pytest.mark.nightly
@pytest.mark.process
def test_prune_across_a_rename_follows_current_keys(make_repo):
    """git renamed web/b.ts to web/c.ts. Under `# crapkit-keys=1` marks, prune
    moves both callback marks to the new path (docs/ratchet.md, A rename
    follows instead of dropping), and every key it writes is one run 2 holds."""
    built = make_repo(repos.Spec(steps=(repos.Commit(files=vw.LEGACY_FILES, message="seed"),)))
    driver = vw.drive.Driver(built.root)
    assert driver.run("coverage").code == 0 and driver.run("ratchet", "seed").code == 0
    marks = _marks_of(built)
    repos.git(built.top, "mv", "web/b.ts", "web/c.ts")
    repos.git(built.top, "commit", "-q", "-m", "rename", date=vw.LEGACY_DATE)
    assert driver.run("coverage").code == 0
    placed = _keys_of(driver, 2)
    assert driver.run("ratchet", "prune").code == 0
    want = model.prune(model.follow_renames(marks, placed, {"web/b.ts": "web/c.ts"}), placed)
    assert _marks_of(built) == want and set(want) <= placed and len(want) == 3


# Twelve unchanged lines keep git calling the edited file a rename.
PADDING = "".join(f"// kept line {line}\n" for line in range(12))
SPLIT = ("export const h = (a: number) => [a].map(x => x > 1 ? 1 : 2)\n"
         "  .filter(y => y > 2 || y < 0);\n")


@pytest.mark.nightly
@pytest.mark.process
def test_prune_across_a_rename_writes_a_placeable_key(make_repo):
    """Legacy-format marks name web/b.ts's two line-1 callbacks. git renamed
    the file to web/c.ts and split the line, so run 2 holds the callbacks on
    lines 1 and 2. Run 1 still holds the old group on one line, and available
    historical runs take part in the check (docs/ratchet.md, Same-line
    collisions): the group needs review before any mark moves, so prune
    refuses, names the old path and writes nothing."""
    files = {**vw.LEGACY_FILES, "web/b.ts": vw.LEGACY_FILES["web/b.ts"] + PADDING}
    built = make_repo(repos.Spec(steps=(repos.Commit(files=files, message="seed"),)))
    driver = vw.drive.Driver(built.root)
    assert driver.run("coverage").code == 0 and driver.run("ratchet", "seed").code == 0
    vw.legacy_keys(built.root)
    repos.git(built.top, "mv", "web/b.ts", "web/c.ts")
    (built.root / "web" / "c.ts").write_bytes((SPLIT + PADDING).encode("utf-8"))
    repos.git(built.top, "commit", "-q", "-am", "rename and split", date=vw.LEGACY_DATE)
    assert driver.run("coverage").code == 0
    before = (built.root / "crapkit-ratchet.tsv").read_bytes()
    result = driver.run("ratchet", "prune")
    assert result.code != 0 and "web/b.ts" in result.stderr, result.stdout + result.stderr
    assert (built.root / "crapkit-ratchet.tsv").read_bytes() == before


def _forget_order(root, run: int) -> None:
    """Run `run` as a store written before same-line order was recorded holds it."""
    import sqlite3
    connection = sqlite3.connect(root / ".crapkit" / "crap.sqlite")
    with connection:
        connection.execute("UPDATE functions SET occurrence = 0 WHERE run_id = ?", (run,))
    connection.close()


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("path, name, runs", [("web/b.ts", "(anonymous)#2", [2]),
                                              ("web/b.ts", "(anonymous)#1", [2]),
                                              ("web/a.ts", "f", [1, 2])])
def test_history_leaves_out_only_runs_that_cannot_place_twins(make_repo, path, name, runs):
    """CONTEXT.md, Legacy run: run 1 lost the order of web/b.ts's two line-1
    callbacks, so their histories leave run 1 out and answer from run 2; f,
    alone in its group, keeps both runs."""
    built = make_repo(repos.Spec(steps=(repos.Commit(files=vw.LEGACY_FILES, message="seed"),)))
    driver = vw.drive.Driver(built.root)
    assert driver.run("coverage").code == 0
    _forget_order(built.root, 1)
    assert driver.run("coverage").code == 0
    result = driver.run("explain", path, name, "--json")
    assert result.code == 0, result.stdout + result.stderr
    (function,) = result.json()["functions"]
    assert [row["run_id"] for row in function["history"]] == runs
