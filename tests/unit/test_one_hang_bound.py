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

import pytest

from hang_guard import HANG_SECONDS

TESTS = Path(__file__).resolve().parents[1]
CLOCK_BOUND = re.compile(r"(monotonic|time)\(\)\s*\+\s*\d")
WAIT_CALLS = {"wait", "result", "receive", "get", "join"}

WINDOWS = {
    "e2e/test_parallel_lanes_e2e.py": "a window: two lanes started together must meet inside it",
}
DEADLINES = {
    ("unit/test_r2_execution_adapters.py", "proc.wait.assert_called_once_with(timeout=2)"):
        "asserts the deadline the product hands Popen.wait; nothing waits on it",
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


def _under_the_bound(node):
    """Zero is no wait at all. A number past the hang bound is an allowance for a
    run that does real work, and costs a loaded machine nothing."""
    return (isinstance(node, ast.Constant) and type(node.value) in (int, float)
            and 0 < node.value < HANG_SECONDS)


def _bound_candidates(call):
    keywords = [keyword.value for keyword in call.keywords if keyword.arg == "timeout"]
    positional = call.args[:1] if getattr(call.func, "attr", None) in WAIT_CALLS else []
    return keywords + positional


def _spells_a_bound(node):
    return isinstance(node, ast.Call) and any(map(_under_the_bound, _bound_candidates(node)))


@pytest.mark.parametrize("call, spelled", [
    ("process.communicate(timeout=10)", True),
    ("subprocess.run(argv, capture_output=True, timeout=15)", True),
    ("done.wait(5)", True),
    ("subprocess.run(argv, timeout=HANG_SECONDS)", False),
    ("sqlite3.connect(path, timeout=0)", False),
    ("run_cli(repo, 'coverage', timeout=300)", False),
])
def test_a_wait_spells_a_bound_only_when_its_number_is_under_the_hang_bound(call, spelled):
    assert _spells_a_bound(ast.parse(call, mode="eval").body) is spelled


def _spelled_bounds(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(_relative(path), ast.unparse(node)) for node in ast.walk(tree) if _spells_a_bound(node)]


def test_no_wait_spells_a_bound_under_the_hang_bound():
    """Every file, not only the ones that import the hang guard: a new file that
    waits 10 s on a child is the guess this suite removed."""
    spelled = [site for path in sorted(TESTS.rglob("*.py")) for site in _spelled_bounds(path)
               if site not in DEADLINES]

    assert spelled == [], "wait through tests/hang_guard.py, or list the deadline here"
