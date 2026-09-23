"""Every test-side wait on a child takes its bound from the hang guard.

Before tests/hang_guard.py, 34 `time.monotonic() + N` literals in 15 files each
guessed how slow a child can be, and a loaded machine proved the guesses wrong
one file at a time. A wait bound spelled as a number is that guess made again.

Numbers that measure the product stay, each listed below with its reason: a
deadline the test expects to expire, or a window the test measures.
"""
import ast
from pathlib import Path
import re

TESTS = Path(__file__).resolve().parents[1]
CLOCK_BOUND = re.compile(r"monotonic\(\)\s*\+\s*\d")
WAIT_CALLS = {"wait", "result", "receive", "get"}

WINDOWS = {
    "e2e/test_parallel_lanes_e2e.py": "a window: two lanes started together must meet inside it",
}
DEADLINES = {
    ("unit/test_hang_guard.py", "run([*PYTHON, '-c', 'import time; time.sleep(600)'], timeout=0)"):
        "forces the miss the test is about",
    ("unit/test_mutation_cancellation.py", "wait(future, timeout=0.02)"):
        "a poll slice: the loop checks its interrupt condition between slices and retries",
}


def _relative(path):
    return path.relative_to(TESTS).as_posix()


def _clock_bounds(path):
    text = path.read_text(encoding="utf-8")
    return [f"{_relative(path)}:{text.count(chr(10), 0, found.start()) + 1}"
            for found in CLOCK_BOUND.finditer(text)]


def test_no_wait_spells_its_own_clock_bound():
    spelled = [site for path in sorted(TESTS.rglob("*.py")) if _relative(path) not in WINDOWS
               for site in _clock_bounds(path)]

    assert spelled == [], "wait through tests/hang_guard.py, or spell CHILD_WAIT in a child script"


def _names_hang_guard(node):
    if isinstance(node, ast.ImportFrom):
        return node.module == "hang_guard"
    return isinstance(node, ast.Import) and "hang_guard" in {alias.name for alias in node.names}


def _is_number(node):
    return isinstance(node, ast.Constant) and type(node.value) in (int, float)


def _bound_candidates(call):
    keywords = [keyword.value for keyword in call.keywords if keyword.arg == "timeout"]
    positional = call.args[:1] if getattr(call.func, "attr", None) in WAIT_CALLS else []
    return keywords + positional


def _spells_a_bound(node):
    return isinstance(node, ast.Call) and any(map(_is_number, _bound_candidates(node)))


def _spelled_bounds(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if not any(map(_names_hang_guard, ast.walk(tree))):
        return []
    return [(_relative(path), ast.unparse(node)) for node in ast.walk(tree) if _spells_a_bound(node)]


def test_a_file_that_waits_through_the_hang_guard_spells_no_other_bound():
    spelled = [site for path in sorted(TESTS.rglob("*.py")) for site in _spelled_bounds(path)
               if site not in DEADLINES]

    assert spelled == [], "a file on the hang guard waits its bound, or lists the deadline here"
