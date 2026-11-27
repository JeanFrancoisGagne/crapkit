"""score.py owns the coverage join, so it imports no coverage adapter.

FnCoverage (the producer record), coverage_count (the count admission) and
span_owners (the innermost-span rule) live in score.py. Both adapters import
them from there; a join that reads a third format calls the same span rule
without importing istanbul's reader. These tests pin both halves: score loads
neither adapter, and nothing reaches the moved names through the istanbul
adapter any more.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import crapkit

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ("crapkit.coverage_istanbul", "crapkit.coverage_py")
MOVED = frozenset({"FnCoverage", "coverage_count", "_span_owners", "span_owners",
                   "_push_started", "_drop_ended"})


def _child_env() -> dict:
    """The subprocess imports the crapkit this test imported, not an installed one."""
    env = dict(os.environ)
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH", "")) if p)
    return env


def test_importing_score_loads_no_coverage_adapter(tmp_path):
    probe = ("import sys, json\nimport crapkit.score\n"
             "print(json.dumps(sorted(m for m in sys.modules if m.startswith('crapkit.'))))\n")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env=_child_env(), cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr
    loaded = set(json.loads(done.stdout.splitlines()[-1]))

    assert "crapkit.score" in loaded
    assert loaded.isdisjoint(ADAPTERS), sorted(loaded & set(ADAPTERS))


def _from_istanbul(node: ast.AST) -> list[str]:
    """The moved names one node reaches through the istanbul adapter: an
    import from it, or an attribute read off the module."""
    if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("coverage_istanbul"):
        return [alias.name for alias in node.names if alias.name in MOVED]
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "coverage_istanbul" and node.attr in MOVED):
        return [node.attr]
    return []


def _trees():
    """Each module under src and tests with its syntax tree. A file this Python
    cannot parse is a probe fixture written for another version or language,
    never a module that imports crapkit, so it is passed over."""
    for top in (ROOT / "src", ROOT / "tests"):
        for path in sorted(top.rglob("*.py")):
            try:
                yield path, ast.parse(path.read_bytes(), filename=str(path))
            except SyntaxError:
                continue


def test_no_module_reaches_the_moved_names_through_the_istanbul_adapter():
    found = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            found += [f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: {name}"
                      for name in _from_istanbul(node)]

    assert found == []


def test_the_moved_names_live_in_score():
    from crapkit import coverage_istanbul, score

    assert {"FnCoverage", "coverage_count", "span_owners", "_push_started",
            "_drop_ended"} <= set(vars(score))
    assert score.FnCoverage.__module__ == "crapkit.score"
    assert not MOVED & set(vars(coverage_istanbul)), "the adapter re-exports none of them"
