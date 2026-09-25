"""A small repository whose functions, coverage and test results a test sets directly.

A World holds two scopes, `app` (src/app.py) and `lib` (lib/util.py), each
measured by its own lane (`a` and `b`). Every function is `def NAME(x):`
followed by `decisions` two-line `if` statements and a `return`, so its
cyclomatic complexity is decisions + 1 by McCabe's count (one per `if`, plus
one), and `covered` of its 2 * decisions branch arms ran. A function with no
decision has no branch arm, and `covered` (0 or 1) says whether its one
statement ran.

A lane runs tests/cov_gen.py, which writes a coverage.py JSON report and a
JUnit file from the plan the World wrote under .plan/ (git-ignored, so changing
coverage or a test result dirties nothing). The report carries each function's
region as coverage.py 7.16 writes it: start_line, executed and missing lines,
and a summary of branch and statement counts. write_artifacts() writes the same
two files in this process, for a test that then reads them with
`--reuse-artifacts` instead of paying for the lane processes.

Expected values never come from crapkit: `cov` follows the README's
definition (branch coverage in the span; with no branches, statement
coverage) and `crap` is kit.exact's. The module imports no crapkit; tests
drive the CLI through kit.drive.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
import json
from pathlib import Path
import shutil

from accuracy.kit import drive, exact, repos

TARGET = 6
GEN = "tests/cov_gen.py"
PLAN = ".plan/plan.json"
FILES = {"app": "src/app.py", "lib": "lib/util.py"}
LANES = {"a": "app", "b": "lib"}
ALERT = "python -c \\\"import sys; sys.stdin.read()\\\""
DAY = 86_400

_WRITERS = '''\
def region(fn):
    summary = {"num_branches": fn["branches"], "covered_branches": fn["covered_branches"],
               "num_statements": fn["statements"], "covered_lines": fn["covered_lines"],
               "missing_lines": len(fn["missing"]), "excluded_lines": 0,
               "num_partial_branches": 0,
               "missing_branches": fn["branches"] - fn["covered_branches"]}
    return {"executed_lines": fn["executed"], "missing_lines": fn["missing"],
            "excluded_lines": [], "summary": summary, "start_line": fn["start"],
            "executed_branches": [], "missing_branches": []}


def report(files):
    out = {}
    for path, functions in files.items():
        executed = sorted({n for fn in functions for n in fn["executed"]})
        missing = sorted({n for fn in functions for n in fn["missing"]})
        out[path] = {"executed_lines": executed, "missing_lines": missing,
                     "excluded_lines": [], "summary": {},
                     "functions": {fn["name"]: region(fn) for fn in functions},
                     "classes": {}}
    return {"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True,
                     "show_contexts": False}, "files": out}


def junit(tests):
    cases = []
    for test in tests:
        failure = '<failure message="assert False"/>' if test["failed"] else ""
        cases.append('<testcase classname="%s" name="%s" time="0.001">%s</testcase>'
                     % (test["classname"], test["name"], failure))
    failed = sum(1 for test in tests if test["failed"])
    return ('<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
            '<testsuite name="pytest" errors="0" failures="%d" skipped="0" tests="%d" '
            'time="0.01">%s</testsuite></testsuites>' % (failed, len(tests), "".join(cases)))


def write(plan, lane, root="."):
    import json
    import os
    cov = os.path.join(root, ".crapkit", "cov")
    os.makedirs(cov, exist_ok=True)
    with open(os.path.join(cov, "%s.json" % lane), "w", encoding="utf-8") as handle:
        json.dump(report(plan["files"]), handle, sort_keys=True)
    if plan["junit"]:
        with open(os.path.join(cov, "%s-junit.xml" % lane), "w", encoding="utf-8") as handle:
            handle.write(junit(plan["tests"]))
'''

GEN_SOURCE = f'''\
"""Write one lane's coverage.py JSON report and JUnit file from the plan."""
import json
import sys


{_WRITERS}

def main(plan_path, lane):
    with open(plan_path, encoding="utf-8") as handle:
        plan = json.load(handle)[lane]
    if plan["fail"]:
        sys.exit("lane %s failed on purpose" % lane)
    write(plan, lane)


main(sys.argv[1], sys.argv[2])
'''

_GEN: dict = {}
exec(compile(_WRITERS, GEN, "exec"), _GEN)


@dataclass(frozen=True)
class Fn:
    """One function: `decisions` ifs, `covered` branch arms ran (or, with no
    decision, whether its one statement ran)."""
    name: str
    decisions: int = 0
    covered: int = 0

    @property
    def ccn(self) -> int:
        return self.decisions + 1

    @property
    def cov(self) -> Fraction:
        """README: branch coverage in the span; with no branches, statement coverage."""
        if self.decisions:
            return exact.ratio(self.covered, 2 * self.decisions)
        return exact.ratio(self.covered, 1)

    @property
    def crap(self) -> Fraction:
        return exact.crap(self.ccn, self.cov)

    @property
    def long_name(self) -> str:
        return f"{self.name}( x )"


@dataclass(frozen=True)
class Test:
    name: str
    failed: bool = False
    lane: str = "a"

    @property
    def id(self) -> str:
        return f"tests.test_{self.lane}::{self.name}"


@dataclass(frozen=True)
class World:
    """What a test sets: functions per scope, tests, lanes that fail, lanes
    that write no JUnit, and extra [crapkit] lines."""
    functions: dict = field(default_factory=lambda: {"app": (), "lib": ()})
    tests: tuple = ()
    failing_lanes: frozenset = frozenset()
    no_junit: frozenset = frozenset()
    config_extra: str = ""

    def with_fn(self, scope: str, fn: Fn) -> "World":
        """The world with `fn` replacing the function of its name, or appended."""
        current = self.functions[scope]
        names = [f.name for f in current]
        kept = (tuple(fn if f.name == fn.name else f for f in current) if fn.name in names
                else current + (fn,))
        return replace(self, functions={**self.functions, scope: kept})

    def without_fn(self, scope: str, name: str) -> "World":
        kept = tuple(f for f in self.functions[scope] if f.name != name)
        return replace(self, functions={**self.functions, scope: kept})

    def fn(self, scope: str, name: str) -> Fn:
        return next(f for f in self.functions[scope] if f.name == name)

    def with_test(self, test: Test) -> "World":
        kept = tuple(t for t in self.tests if t.name != test.name)
        return replace(self, tests=kept + (test,))


def source(fns) -> tuple[str, list[tuple[Fn, int, int]]]:
    """The file's text and each function's (fn, start, end) lines."""
    lines, spans = [], []
    for fn in fns:
        start = len(lines) + 1
        lines.append(f"def {fn.name}(x):")
        for number in range(fn.decisions):
            lines += [f"    if x > {number}:", "        x += 1"]
        lines.append("    return x")
        spans.append((fn, start, len(lines)))
        lines.append("")
    return "\n".join(lines) + "\n", spans


def spans(world: World, scope: str) -> dict[str, tuple[int, int]]:
    """{name: (start, end)}; a twin name keeps the last one, so twins read source()."""
    return {fn.name: (start, end) for fn, start, end in source(world.functions[scope])[1]}


def _region(fn: Fn, start: int, end: int) -> dict:
    """coverage.py's view of one function: the true arm of the first `covered`
    ifs ran (their body lines executed), the rest did not."""
    body = list(range(start + 1, end + 1))
    missing = _missing(fn, start, body)
    executed = [line for line in body if line not in missing]
    return {"name": fn.name, "start": start, "executed": executed, "missing": missing,
            "branches": 2 * fn.decisions,
            "covered_branches": fn.covered if fn.decisions else 0,
            "statements": 2 * fn.decisions + 1, "covered_lines": len(executed)}


def _missing(fn: Fn, start: int, body: list[int]) -> list[int]:
    if fn.covered == 0:
        return body
    bodies = [start + 2 + 2 * number for number in range(fn.decisions)]
    return bodies[min(fn.decisions, fn.covered):]


def _lane_plan(world: World, lane: str, scope: str) -> dict:
    regions = [_region(fn, start, end) for fn, start, end in source(world.functions[scope])[1]]
    tests = [{"classname": f"tests.test_{lane}", "name": t.name, "failed": t.failed}
             for t in world.tests if t.lane == lane]
    return {"files": {FILES[scope]: regions} if regions else {}, "tests": tests,
            "fail": lane in world.failing_lanes, "junit": lane not in world.no_junit}


def plan(world: World) -> dict:
    return {lane: _lane_plan(world, lane, scope) for lane, scope in LANES.items()}


def _lane(name: str, scope: str, junit: bool) -> str:
    command = f"python {GEN} {PLAN} {name}"
    results = f'results_artifact = ".crapkit/cov/{name}-junit.xml"\n' if junit else ""
    return (f'[[lane]]\nname = "{name}"\ncommand = "{command}"\n'
            f'artifact = ".crapkit/cov/{name}.json"\n{results}'
            f'parser = "coveragepy"\nscopes = ["{scope}"]\ncontainer_ok = true\n'
            f"env = {repos.LANE_ENV}\n")


def config(world: World) -> str:
    scopes = "".join(f'[[scope]]\nname = "{scope}"\npaths = ["{Path(path).parent.as_posix()}"]\n'
                     'languages = ["python"]\n\n' for scope, path in FILES.items())
    lanes = "\n".join(_lane(name, scope, name not in world.no_junit)
                      for name, scope in LANES.items())
    return (f"[crapkit]\ntarget = {TARGET}\nalert_command = \"{ALERT}\"\n{world.config_extra}\n"
            f'{scopes}[exclude]\nglobs = ["tests/**"]\n\n{lanes}')


def files(world: World) -> dict:
    """Every tracked file of the world, as a Commit's files."""
    out = {".gitignore": ".crapkit/\n.plan/\n__pycache__/\n", GEN: GEN_SOURCE,
           "crapkit.toml": config(world)}
    out.update({path: source(world.functions[scope])[0] for scope, path in FILES.items()})
    return out


def spec(world: World, date: int = repos.EPOCH) -> repos.Spec:
    return repos.Spec(steps=(repos.Commit(files=files(world), message="seed", date=date),))


class Scenario:
    """One world's repository and the calls a test makes on it."""

    def __init__(self, built: repos.Built, world: World, date: int = repos.EPOCH):
        self.top, self.root, self.world, self.date = built.top, built.root, world, date
        self.driver = drive.Driver(self.root, date_now=date + DAY)
        self.write_plan()

    @classmethod
    def build(cls, make_repo, world: World, date: int = repos.EPOCH) -> "Scenario":
        return cls(make_repo(spec(world, date)), world, date)

    def copy(self, dest: Path) -> "Scenario":
        """A private copy, store and working tree included."""
        shutil.copytree(self.top, dest, symlinks=True)
        return Scenario(repos.Built(dest, dest), self.world, self.date)

    def write_plan(self) -> None:
        target = self.top / PLAN
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(plan(self.world), indent=1), encoding="utf-8")

    def set(self, world: World) -> "Scenario":
        """The world's sources, config and plan into the working tree, uncommitted."""
        self.world = world
        for path, text in files(world).items():
            (self.top / path).parent.mkdir(parents=True, exist_ok=True)
            (self.top / path).write_bytes(text.encode("utf-8"))
        self.write_plan()
        return self

    def commit(self, message: str = "change") -> str:
        self.date += 60
        repos.git(self.top, "add", "-A")
        repos.git(self.top, "commit", "-q", "--allow-empty", "-m", message, date=self.date)
        return self.head()

    def head(self) -> str:
        return repos.git(self.top, "rev-parse", "HEAD").strip()

    def write_artifacts(self) -> None:
        """What the lanes would write, written here, for a --reuse-artifacts call."""
        for lane, lane_plan in plan(self.world).items():
            _GEN["write"](lane_plan, lane, str(self.root))

    def run(self, *args: str, **kwargs) -> drive.Result:
        return self.driver.run(*args, **kwargs)

    def json(self, *args: str):
        return self.driver.json(*args)

    def marks_text(self) -> str | None:
        path = self.root / "crapkit-ratchet.tsv"
        return path.read_bytes().decode("utf-8") if path.exists() else None

    def runs(self) -> list[dict]:
        return self.driver.store("SELECT id, kind, verdict_ok, commit_sha, tool_versions "
                                 "FROM runs ORDER BY id")
