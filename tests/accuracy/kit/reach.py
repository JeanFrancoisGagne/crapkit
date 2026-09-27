"""Whether production runs each function a calc names.

A test of a copy of a rule proves nothing about the rule: doctor's tests once
exercised a helper that production never called (16d7bfd). Each calcs.tsv row
names the production functions its calculation lives in, as
`src/crapkit/score.py:crap` or `path:Class.method`. measured_lines() runs a
corpus through kit.corpus_run with every crapkit command under coverage.py,
subprocess children and pool workers included. That golden run starts no hook,
gate, ratchet, claim or mutation command and no tool under tools/, so
independent_lines() measures one test the same way, src/crapkit and tools/
together: a calc's independent test drives crapkit the way a user does, so
what it runs is production code. unreached() names each function whose body
ran no line. A function's `def` line runs at import, so only body lines count.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import sys

import hang_guard

from . import corpus_run, tiers

REPO = Path(__file__).resolve().parents[3]
RC = """\
[run]
source = crapkit
branch = false
parallel = true
data_file = {data}
patch = subprocess
concurrency = multiprocessing,thread
"""
TEST_RC = """\
[run]
source =
    {src}
    {tools}
branch = false
parallel = true
data_file = {data}
patch = subprocess
concurrency = multiprocessing,thread
"""
# One whole independent test under coverage: a history state machine or a
# corpus sweep takes minutes, so the bound against a hang is ten of the suite's.
MEASURED_TEST_SECONDS = 10 * hang_guard.HANG_SECONDS
# The measured test runs whatever its tier and platform, at the push tier's
# example counts: one example reaches a function, a nightly's 200 only repeat it.
MEASURED_TEST_ENV = {tiers.COLLECT_ALL_ENV: "1", tiers.TIER_ENV: "push",
                     "PYTHONDONTWRITEBYTECODE": "1"}


class ReachError(AssertionError):
    """A measured test run that failed, so the lines it ran prove nothing."""


def _defs(tree: ast.AST, prefix: str = ""):
    """(qualified name, node) for every function, nested ones as outer.inner."""
    for node in ast.iter_child_nodes(tree):
        name = f"{prefix}{getattr(node, 'name', '')}"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield name, node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _defs(node, name + ".")


def _body_start(node) -> int:
    body = node.body
    docstring = isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None),
                                                             ast.Constant)
    return body[1].lineno if docstring and len(body) > 1 else body[0].lineno


def body_lines(path: Path, qualname: str) -> range | None:
    """The lines of a function's body after its docstring; None when there is no such function."""
    found = dict(_defs(ast.parse(path.read_bytes())))
    node = found.get(qualname)
    return range(_body_start(node), node.end_lineno + 1) if node else None


def _launch(rc: Path) -> tuple[str, ...]:
    return ("-m", "coverage", "run", f"--rcfile={rc}", "-m")


def measured_lines(corpus: Path, base: Path) -> dict[Path, set[int]]:
    """{source file: executed lines} over one coverage-measured run of `corpus`."""
    data = base / "coverage-data"
    data.mkdir(parents=True, exist_ok=True)
    rc = base / "coveragerc"
    rc.write_text(RC.format(data=(data / ".coverage").as_posix()), encoding="utf-8")
    corpus_run.measure(corpus, base, launch=_launch(rc))
    return _combined(rc, data)


def _combined(rc: Path, data: Path) -> dict[Path, set[int]]:
    import coverage
    cov = coverage.Coverage(config_file=str(rc))
    cov.combine([str(data)], keep=True)
    got = cov.get_data()
    return {Path(name).resolve(): set(got.lines(name) or ()) for name in got.measured_files()}


def _test_rc(base: Path) -> tuple[Path, Path]:
    data = base / "test-coverage-data"
    data.mkdir(parents=True, exist_ok=True)
    rc = base / "test-coveragerc"
    rc.write_text(TEST_RC.format(src=(REPO / "src" / "crapkit").as_posix(),
                                 tools=(REPO / "tools").as_posix(),
                                 data=(data / ".coverage").as_posix()), encoding="utf-8")
    return rc, data


def independent_lines(node_id: str, base: Path) -> dict[Path, set[int]]:
    """{source file: executed lines} over one coverage-measured pytest run of
    `node_id` from the repo root (MEASURED_TEST_ENV)."""
    rc, data = _test_rc(base)
    argv = [sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", "-m", "pytest", node_id,
            "-q", "-p", "no:cacheprovider", "-p", "no:randomly"]
    done = hang_guard.run(argv, cwd=REPO, env={**os.environ, **MEASURED_TEST_ENV},
                          timeout=MEASURED_TEST_SECONDS, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode != 0:
        raise ReachError(f"{node_id} failed under coverage, so its lines prove nothing:\n"
                         f"{done.stdout[-2000:]}{done.stderr[-2000:]}")
    return _combined(rc, data)


def _problem(function: str, measured: dict, repo: Path) -> str | None:
    path, _, qualname = function.partition(":")
    lines = body_lines(repo / path, qualname)
    if lines is None:
        return f"{function}: no such function"
    ran = measured.get((repo / path).resolve(), set())
    return None if ran.intersection(lines) else f"{function}: no line of its body ran"


def unreached(functions: list[str], measured: dict, repo: Path = REPO) -> list[str]:
    """One line per function that is missing or whose body never ran."""
    found = (_problem(function, measured, repo) for function in functions)
    return [problem for problem in found if problem]
