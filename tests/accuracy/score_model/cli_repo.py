"""A small Python repo whose coverage runs replay artifacts written here.

Each function is an if-chain with a known ccn (k - 1 ifs read ccn k, McCabe
1976: one plus the decision points) and a coverage.py JSON entry (format 3)
naming the branches its tests took. So every number a test expects is
chosen here: ccn, covered and total branches, and so the CRAP. Lanes copy the
recorded artifacts into place with kit.repos's copy command, and the repo is
built with kit.repos, dated and reproducible.

Nothing here imports crapkit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import json

from accuracy.kit import drive, repos


@dataclass(frozen=True)
class Fn:
    name: str
    ccn: int
    covered: int  # branches taken, of 2 * (ccn - 1)


@dataclass(frozen=True)
class Module:
    scope: str
    path: str
    functions: tuple[Fn, ...]


@dataclass(frozen=True)
class Layout:
    modules: tuple[Module, ...]
    target: int = 6
    scope_targets: dict = field(default_factory=dict)
    floor: int = 1
    no_lane: tuple[str, ...] = ()  # scopes declared with no lane measuring them
    parallel_lanes: int | None = None  # [crapkit] max_parallel_lanes, when set


def _body(fn: Fn) -> list[str]:
    lines = [f"def {fn.name}(x):"]
    for number in range(1, fn.ccn):
        lines += [f"    if x == {number}:", f"        return {number}"]
    return lines + ["    return 0", "", ""]


def source(module: Module) -> tuple[str, dict[str, tuple[int, int]]]:
    """The module text and each function's (start, end) line span."""
    lines, spans = ['"""Generated for score-model."""', "", ""], {}
    for fn in module.functions:
        start = len(lines) + 1
        body = _body(fn)
        lines += body
        spans[fn.name] = (start, start + len(body) - 3)
    return "\n".join(lines), spans


def _summary(fn: Fn, statements: int) -> dict:
    return {"covered_lines": statements, "num_statements": statements,
            "num_branches": 2 * (fn.ccn - 1), "covered_branches": fn.covered,
            "missing_lines": 0, "excluded_lines": 0, "num_partial_branches": 0,
            "missing_branches": 2 * (fn.ccn - 1) - fn.covered}


def _entry(fn: Fn, span: tuple[int, int]) -> dict:
    body = list(range(span[0] + 1, span[1] + 1))
    return {"executed_lines": body, "missing_lines": [], "excluded_lines": [],
            "executed_branches": [], "missing_branches": [], "start_line": span[0],
            "summary": _summary(fn, len(body))}


def artifact(modules: list[Module]) -> str:
    """One coverage.py JSON report over `modules`."""
    files = {}
    for module in modules:
        spans = source(module)[1]
        functions = {fn.name: _entry(fn, spans[fn.name]) for fn in module.functions}
        files[module.path] = {"executed_lines": [], "missing_lines": [], "excluded_lines": [],
                              "executed_branches": [], "missing_branches": [],
                              "summary": {}, "functions": functions, "classes": {}}
    meta = {"format": 3, "version": "7.16.1", "branch_coverage": True, "show_contexts": False}
    return json.dumps({"meta": meta, "files": files}, indent=1)


def _scope_block(scope: str, own: int | None) -> str:
    target = f"target = {own}\n" if own is not None else ""
    return f'[[scope]]\nname = "{scope}"\npaths = ["src/{scope}"]\nlanguages = ["python"]\n{target}'


def config(layout: Layout) -> str:
    scopes = sorted({module.scope for module in layout.modules})
    knob = "" if layout.parallel_lanes is None else f"max_parallel_lanes = {layout.parallel_lanes}\n"
    head = f"[crapkit]\ntarget = {layout.target}\nworklist_floor = {layout.floor}\n{knob}\n"
    blocks = [_scope_block(scope, layout.scope_targets.get(scope)) for scope in scopes]
    lanes = [repos.lane_toml(scope, f".crapkit/cov/{scope}.json", "coveragepy", [scope],
                             f"recorded/{scope}.json") for scope in _laned(layout)]
    exclude = '[exclude]\nglobs = ["recorded/**"]\n'
    return head + "\n".join(blocks) + "\n" + exclude + "\n" + "\n".join(lanes)


def files(layout: Layout) -> dict[str, str]:
    """Every file of the repo: crapkit.toml, the modules and one recording per scope."""
    found = {"crapkit.toml": config(layout)}
    found.update({module.path: source(module)[0] for module in layout.modules})
    found.update({f"recorded/{scope}.json": artifact(_in_scope(layout, scope))
                  for scope in _laned(layout)})
    return found


def _laned(layout: Layout) -> list[str]:
    return sorted({module.scope for module in layout.modules} - set(layout.no_lane))


def _in_scope(layout: Layout, scope: str) -> list[Module]:
    return [module for module in layout.modules if module.scope == scope]


def spec(layout: Layout, date: int = repos.EPOCH) -> repos.Spec:
    return repos.Spec(steps=(repos.Commit(files=files(layout), message="seed", date=date),))


def driver(make_repo, layout: Layout) -> drive.Driver:
    """A Driver on a fresh copy of the layout's repo, its clock a day past the commit."""
    built = make_repo(spec(layout))
    return drive.Driver(built.root, date_now=repos.EPOCH + 86_400)


@dataclass(frozen=True)
class Expected:
    """One function as this module wrote it: the numbers a check expects."""
    scope: str
    path: str
    name: str
    start: int
    ccn: int
    cov: Fraction
    ceiling: int


def _cov(fn: Fn) -> Fraction:
    """Branch coverage (README.md:22-31): covered of 2 * (ccn - 1) arms; a
    branchless function runs every statement, so 1."""
    return Fraction(fn.covered, 2 * (fn.ccn - 1)) if fn.ccn > 1 else Fraction(1)


def expected(layout: Layout) -> list[Expected]:
    """Every function of a layout with no two functions of one name in a file."""
    rows = []
    for module in layout.modules:
        spans = source(module)[1]
        ceiling = layout.scope_targets.get(module.scope, layout.target)
        rows += [Expected(module.scope, module.path, f"{fn.name}( x )", spans[fn.name][0], fn.ccn,
                          _cov(fn), ceiling) for fn in module.functions]
    return rows


# Three scopes for the cross-surface checks: a at the repo ceiling of 6 with
# decompose, add-tests and ok rows; b at its own ceiling of 12, where a ccn-8
# function is ok fully covered and add-tests at 3 of 14 arms; c all ok (grade A+).
SURFACES = Layout(modules=(
    Module("a", "src/a/mod.py", (Fn("hot", 5, 2), Fn("cool", 2, 2), Fn("deep", 14, 6))),
    Module("b", "src/b/mod.py", (Fn("wide", 8, 14), Fn("tangled", 8, 3))),
    Module("c", "src/c/mod.py", (Fn("flat", 3, 4),))),
    scope_targets={"b": 12})
