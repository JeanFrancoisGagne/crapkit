"""`crapkit mutate`'s verdicts against mutmut 3.8.0 (Python) and Stryker 10.0.0
(JavaScript): one fixture, one suite, three answers per mutant.

Each tool mutates the fixture's function bodies and runs the suite once per
mutant. The comparison keeps the operator pairs a tool shares with crapkit's
table (README.md:809: comparisons, boundary shifts, boolean connectives and
boolean literals):

- mutmut 3.8.0, mutation/mutators.py `_operator_mapping` and `operator_name`:
  == and != swap, < -> <=, <= -> <, > -> >=, >= -> >, and and or swap, True
  and False swap. crapkit's second comparison flip (>= -> <) has no mutmut twin.
- Stryker, https://stryker-mutator.io/docs/mutation-testing-elements/supported-mutators/
  (EqualityOperator, LogicalOperator, BooleanLiteral): every pair of crapkit's
  JavaScript table. Stryker's `?? -> &&` and `!a -> a` have no crapkit twin,
  and the fixture spells neither.

Three checks per fixture: both tools grow the same shared mutants, both give
each one the same verdict, and that verdict is the one worked out by hand in
PY_EXPECTED and JS_EXPECTED before any tool ran. A mutant no test reaches
survives: mutmut says `no tests` and Stryker `NoCoverage`, where crapkit, which
runs the whole suite, says survived.

crapkit's full mutant list comes from a run whose mutation_command always
passes, so every mutant is listed as a survivor; a second run with the real
suite gives the verdicts. mutmut forks, so its half runs on Linux only. Both
run nightly; the parsers they read tool output through are checked on push.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

import hang_guard
from accuracy.kit import drive, oracles, repos

PASS = 'python -c "pass"'
_TOKEN = re.compile(r"\w+|===|!==|==|!=|<=|>=|&&|\|\||\?\?|\S")

# --- Python: mutmut --------------------------------------------------------------------------

PY_SOURCE = """\
def at_least(a, b):
    return a >= b


def below(c, d):
    return c < d


def greater(e, f):
    return e > f


def at_most(g, h):
    return g <= h


def same(i, j):
    return i == j


def differ(k, m):
    return k != m


def both(n, p):
    return n and p


def either(q, r):
    return q or r


def enabled():
    return True


def disabled():
    return False
"""
PY_SUITE = """\
from rules import at_least, at_most, below, both, differ, either, enabled, greater, same


def test_at_least():
    assert at_least(2, 1)
    assert not at_least(1, 2)


def test_below():
    assert below(1, 2)
    assert not below(1, 1)


def test_greater():
    assert greater(2, 1)
    assert not greater(1, 1)


def test_at_most():
    assert at_most(1, 2)
    assert not at_most(2, 1)


def test_same():
    assert same(1, 1)


def test_differ():
    assert differ(1, 2)


def test_both():
    assert both(1, 1)


def test_either():
    assert either(0, 1)


def test_enabled():
    assert enabled() is True
"""
# Worked from PY_SUITE: a boundary pair no test checks survives (at_least(1, 1),
# at_most(1, 1)); 1 or 1 is as true as 1 and 1; nothing calls disabled().
PY_EXPECTED = {
    (2, ">= -> >"): "survived", (6, "< -> <="): "killed", (10, "> -> >="): "killed",
    (14, "<= -> <"): "survived", (18, "== -> !="): "killed", (22, "!= -> =="): "killed",
    (26, "and -> or"): "survived", (30, "or -> and"): "killed",
    (34, "True -> False"): "killed", (38, "False -> True"): "survived",
}
PY_SHARED = frozenset(op for _, op in PY_EXPECTED)
PY_FILES = {
    "src/rules.py": PY_SOURCE,
    "tests/test_rules.py": PY_SUITE,
    "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
}
PY_SUITE_COMMAND = "python -m pytest tests -q -x -p no:cacheprovider -p no:randomly"
MUTMUT_TABLE = ('\n[tool.mutmut]\nsource_paths = ["src/rules.py"]\n'
                'pytest_add_cli_args_test_selection = ["tests"]\n'
                'pytest_add_cli_args = ["-p", "no:cacheprovider", "-p", "no:randomly"]\n')
MUTMUT_VERDICTS = {"killed": "killed", "survived": "survived", "no tests": "survived"}

# --- JavaScript: Stryker ----------------------------------------------------------------------

JS_SOURCE = """\
export function atLeast(a, b) {
  return a >= b;
}

export function below(c, d) {
  return c < d;
}

export function greater(e, f) {
  return e > f;
}

export function atMost(g, h) {
  return g <= h;
}

export function same(i, j) {
  return i === j;
}

export function differ(k, m) {
  return k !== m;
}

export function loose(n, p) {
  return n == p;
}

export function unlike(q, r) {
  return q != r;
}

export function both(s, t) {
  return s && t;
}

export function either(u, v) {
  return u || v;
}

export function enabled() {
  return true;
}

export function disabled() {
  return false;
}
"""
JS_SUITE = """\
import { atLeast, atMost, below, both, differ, either, enabled, greater, loose, same }
  from "../src/rules.js";

test("atLeast", () => {
  expect(atLeast(2, 1)).toBe(true);
  expect(atLeast(1, 2)).toBe(false);
});

test("below", () => {
  expect(below(1, 2)).toBe(true);
  expect(below(1, 1)).toBe(false);
});

test("greater", () => {
  expect(greater(2, 1)).toBe(true);
  expect(greater(1, 1)).toBe(false);
});

test("atMost", () => {
  expect(atMost(1, 2)).toBe(true);
  expect(atMost(2, 1)).toBe(false);
});

test("same", () => {
  expect(same(1, 1)).toBe(true);
});

test("differ", () => {
  expect(differ(1, 2)).toBe(true);
});

test("loose", () => {
  expect(loose(1, 1)).toBe(true);
});

test("both", () => {
  expect(both(1, 1)).toBeTruthy();
});

test("either", () => {
  expect(either(0, 1)).toBeTruthy();
});

test("enabled", () => {
  expect(enabled()).toBe(true);
});
"""
# Worked from JS_SUITE: each function's two calls pin every flip but the
# boundary the suite skips (atLeast(1, 1), atMost(1, 1)); 1 || 1 is as truthy
# as 1 && 1; nothing calls unlike() or disabled().
JS_EXPECTED = {
    (2, ">= -> >"): "survived", (2, ">= -> <"): "killed",
    (6, "< -> <="): "killed", (6, "< -> >="): "killed",
    (10, "> -> >="): "killed", (10, "> -> <="): "killed",
    (14, "<= -> <"): "survived", (14, "<= -> >"): "killed",
    (18, "=== -> !=="): "killed", (22, "!== -> ==="): "killed",
    (26, "== -> !="): "killed", (30, "!= -> =="): "survived",
    (34, "&& -> ||"): "survived", (38, "|| -> &&"): "killed",
    (42, "true -> false"): "killed", (46, "false -> true"): "survived",
}
JS_SHARED = frozenset(op for _, op in JS_EXPECTED)
JS_FILES = {
    "src/rules.js": JS_SOURCE,
    "tests/rules.test.js": JS_SUITE,
    "package.json": '{"name": "fixture", "private": true, "type": "module"}\n',
    "vitest.config.mjs": 'export default { test: { globals: true, include: ["tests/**/*.test.js"] } };\n',
    ".gitignore": "node_modules/\n",
}
STRYKER_CONFIG = {
    "testRunner": "vitest", "plugins": ["@stryker-mutator/vitest-runner"],
    "mutate": ["src/rules.js"], "reporters": ["json"], "coverageAnalysis": "perTest",
    "concurrency": 1, "vitest": {"configFile": "vitest.config.mjs"},
    "mutator": {"excludedMutations": ["BlockStatement", "ConditionalExpression"]},
}
STRYKER_VERDICTS = {"Killed": "killed", "Survived": "survived", "NoCoverage": "survived"}


# --- shared helpers ----------------------------------------------------------------------------

def op_of(before: str, after: str) -> str | None:
    """The one token swap that turns `before` into `after`, as "old -> new"."""
    old, new = _TOKEN.findall(before), _TOKEN.findall(after)
    swaps = [(a, b) for a, b in zip(old, new) if a != b]
    if len(old) != len(new) or len(swaps) != 1:
        return None
    return f"{swaps[0][0]} -> {swaps[0][1]}"


def shared(mutants: dict, ops: frozenset) -> dict:
    return {key: verdict for key, verdict in mutants.items() if key[1] in ops}


def _config(command: str, language: str) -> str:
    return "\n".join(["[crapkit]", "target = 6", f"mutation_command = {json.dumps(command)}",
                      f"mutation_timeout_seconds = {hang_guard.HANG_SECONDS}", "",
                      "[[scope]]", 'name = "src"', 'paths = ["src"]',
                      f"languages = {json.dumps([language])}"]) + "\n"


def _survivors(root: Path, source: str) -> set[tuple[int, str]]:
    done = drive.Driver(root, spawn=True).run("mutate", "--files", source, "--json")
    assert done.code == 0, f"mutate exited {done.code}:\n{done.stdout}\n{done.stderr}"
    return {(row["line"], row["op"]) for row in json.loads(done.stdout)["survivors"]}


def crapkit_mutants(root: Path, source: str, command: str, language: str) -> dict:
    """Every (line, op) crapkit grows in `source`, with its verdict under `command`."""
    (root / "crapkit.toml").write_text(_config(PASS, language), encoding="utf-8")
    grown = _survivors(root, source)
    (root / "crapkit.toml").write_text(_config(command, language), encoding="utf-8")
    alive = _survivors(root, source)
    return {key: "survived" if key in alive else "killed" for key in grown}


def _fixture(repo_templates, directory: Path, files: dict) -> Path:
    spec = repos.Spec(steps=(repos.Commit(files=files, message="fixture"),))
    return repo_templates.copy(spec, directory).root


def _copy(fixture: Path, directory: Path) -> Path:
    shutil.copytree(fixture, directory, ignore=shutil.ignore_patterns(".git", ".crapkit"))
    return directory


def _run(argv: list[str], cwd: Path, env: dict | None = None) -> str:
    done = hang_guard.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    said = f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
    assert done.returncode == 0, f"{' '.join(argv)} exited {done.returncode}:\n{said}"
    return done.stdout


# --- mutmut --------------------------------------------------------------------------------------

def _mutmut(work: Path, *args: str) -> str:
    return _run([sys.executable, "-m", "mutmut", *args], work)


def _side(diff: str, sign: str) -> list[str]:
    """The lines a unified diff marks with `sign`, its file headers left out."""
    return [line[1:] for line in diff.splitlines()
            if line.startswith(sign) and not line.startswith(sign * 3)]


def _removed_and_added(diff: str) -> tuple[list[str], list[str]]:
    return _side(diff, "-"), _side(diff, "+")


def mutmut_key(diff: str, source_lines: list[str]) -> tuple[int, str] | None:
    """(line, op) of a one-line mutmut diff, the line found by its text in the source."""
    removed, added = _removed_and_added(diff)
    if len(removed) != 1 or len(added) != 1 or removed[0] not in source_lines:
        return None
    op = op_of(removed[0], added[0])
    return (source_lines.index(removed[0]) + 1, op) if op else None


def mutmut_statuses(listing: str) -> dict[str, str]:
    """`mutmut results --all true` lines, `    name: status`, as {name: status}."""
    rows = [line.strip().rsplit(": ", 1) for line in listing.splitlines() if ": " in line]
    return {name: status for name, status in rows}


def mutmut_mutants(fixture: Path, work: Path) -> dict:
    work = _copy(fixture, work)
    with (work / "pyproject.toml").open("a", encoding="utf-8") as handle:
        handle.write(MUTMUT_TABLE)
    _mutmut(work, "run")
    source_lines = PY_SOURCE.splitlines()
    found = {}
    for name, status in mutmut_statuses(_mutmut(work, "results", "--all", "true")).items():
        key = mutmut_key(_mutmut(work, "show", name), source_lines)
        if key:
            found[key] = MUTMUT_VERDICTS.get(status, status)
    return found


# --- Stryker -------------------------------------------------------------------------------------

def _link(target: Path, link: Path) -> None:
    """node_modules beside the fixture: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def stryker_key(mutant: dict, source_lines: list[str]) -> tuple[int, str] | None:
    start, end = mutant["location"]["start"], mutant["location"]["end"]
    if start["line"] != end["line"]:
        return None
    text = source_lines[start["line"] - 1][start["column"] - 1:end["column"] - 1]
    op = op_of(text, mutant["replacement"])
    return (start["line"], op) if op else None


# Stryker ends each test runner through tree-kill, which asks `ps` for the
# runner's children on Linux. The accuracy image ships no procps, so where `ps`
# is missing this shim answers that one question from /proc, exiting 1 when
# there is no child, as procps does (tree-kill reads exit 0 as "pids follow").
PS_SHIM = """#!/bin/sh
exec python3 -c '
import os, sys
children = []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        stat = open(f"/proc/{pid}/stat").read()
    except OSError:
        continue
    if stat.rsplit(")", 1)[1].split()[1] == sys.argv[-1]:
        children.append(pid)
print("\\n".join(children))
sys.exit(0 if children else 1)
' "$@"
"""


def with_ps(work: Path) -> dict:
    """What Stryker's environment adds: a `ps` on PATH wherever none is."""
    if os.name == "nt" or shutil.which("ps"):
        return {}
    shim = work.parent / "ps-shim"
    shim.mkdir(exist_ok=True)
    (shim / "ps").write_text(PS_SHIM, encoding="utf-8")
    (shim / "ps").chmod(0o755)
    return {"PATH": os.pathsep.join((str(shim), os.environ.get("PATH", "")))}


def stryker_mutants(fixture: Path, work: Path, node_modules: Path) -> dict:
    work = _copy(fixture, work)
    _link(node_modules, work / "node_modules")
    (work / "stryker.config.json").write_text(json.dumps(STRYKER_CONFIG), encoding="utf-8")
    stryker = node_modules / "@stryker-mutator" / "core" / "bin" / "stryker.js"
    _run([shutil.which("node") or "node", str(stryker), "run"], work, with_ps(work))
    report = json.loads((work / "reports" / "mutation" / "mutation.json").read_text(encoding="utf-8"))
    source_lines = JS_SOURCE.splitlines()
    found = {}
    for mutant in report["files"]["src/rules.js"]["mutants"]:
        key = stryker_key(mutant, source_lines)
        if key:
            found[key] = STRYKER_VERDICTS.get(mutant["status"], mutant["status"])
    return found


def js_suite_command(node_modules: Path) -> str:
    """vitest from the pinned install, by path: crapkit's worktrees hold no node_modules."""
    vitest = (node_modules / "vitest" / "vitest.mjs").as_posix()
    return f'node "{vitest}" run --config vitest.config.mjs'


# --- the checks ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def py_answers(repo_templates, tmp_path_factory, oracle) -> tuple[dict, dict]:
    oracle("mutmut")
    base = tmp_path_factory.mktemp("mutmut-diff")
    fixture = _fixture(repo_templates, base / "repo", PY_FILES)
    mine = crapkit_mutants(fixture, "src/rules.py", PY_SUITE_COMMAND, "python")
    theirs = mutmut_mutants(fixture, base / "mutmut")
    return shared(mine, PY_SHARED), shared(theirs, PY_SHARED)


@pytest.fixture(scope="module")
def js_answers(repo_templates, tmp_path_factory, oracle) -> tuple[dict, dict]:
    oracle("@stryker-mutator/core")
    oracle("vitest")
    node_modules = oracles.node_modules("nightly")
    base = tmp_path_factory.mktemp("stryker-diff")
    fixture = _fixture(repo_templates, base / "repo", JS_FILES)
    mine = crapkit_mutants(fixture, "src/rules.js", js_suite_command(node_modules), "javascript")
    theirs = stryker_mutants(fixture, base / "stryker", node_modules)
    return shared(mine, JS_SHARED), shared(theirs, JS_SHARED)


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.platform("linux")
def test_python_mutants_and_verdicts_match_mutmut(py_answers):
    crapkit, mutmut = py_answers
    assert sorted(crapkit) == sorted(mutmut) == sorted(PY_EXPECTED)
    assert crapkit == mutmut == PY_EXPECTED


@pytest.mark.nightly
@pytest.mark.process
def test_javascript_mutants_and_verdicts_match_stryker(js_answers):
    crapkit, stryker = js_answers
    assert sorted(crapkit) == sorted(stryker) == sorted(JS_EXPECTED)
    assert crapkit == stryker == JS_EXPECTED


# --- the parsers the comparison reads through, on hand-written tool output -------------------------

def test_a_token_swap_names_its_operator():
    assert op_of("    return a >= b", "    return a > b") == ">= -> >"
    assert op_of("    return n and p", "    return n or p") == "and -> or"
    assert op_of("i === j", "i !== j") == "=== -> !=="
    assert op_of("return x + 1", "return x + 2") == "1 -> 2"
    assert op_of("a < b", "true") is None
    assert op_of("a < b and c", "a <= b or c") is None


def test_a_mutmut_diff_names_its_source_line():
    diff = ("--- src/rules.py\n+++ src/rules.py\n@@ -1,2 +1,2 @@\n def below(c, d):\n"
            "-    return c < d\n+    return c <= d\n")
    assert mutmut_key(diff, PY_SOURCE.splitlines()) == (6, "< -> <=")


def test_mutmut_results_read_as_names_and_statuses():
    listing = "    rules.x_below__mutmut_1: killed\n    rules.x_disabled__mutmut_1: no tests\n"
    assert mutmut_statuses(listing) == {"rules.x_below__mutmut_1": "killed",
                                        "rules.x_disabled__mutmut_1": "no tests"}


def test_a_stryker_mutant_names_its_line_and_operator():
    mutant = {"location": {"start": {"line": 6, "column": 10}, "end": {"line": 6, "column": 15}},
              "replacement": "c <= d"}
    assert stryker_key(mutant, JS_SOURCE.splitlines()) == (6, "< -> <=")
