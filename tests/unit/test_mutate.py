"""Deterministic line-based mutants, diff-scoped: flip comparisons and boolean
operators on changed lines only. String literals and comment lines are never
mutated — a survivor in dead text would erode trust in the survivor list."""
from crapkit.mutate import apply_mutant, file_mutants

PY = (
    "def clamp(x):\n"
    "    # boundary guard\n"
    "    if x > 10:\n"
    "        return 10\n"
    "    return x\n"
)


def test_python_comparison_flips_on_changed_lines_only():
    mutants = file_mutants(PY, changed_lines={3}, language="python")
    mutated = {m.mutated.strip() for m in mutants}
    assert "if x <= 10:" in mutated
    assert "if x >= 10:" in mutated
    assert all(m.line == 3 for m in mutants)


def test_unchanged_lines_produce_no_mutants():
    assert file_mutants(PY, changed_lines={4}, language="python") == []


def test_comment_lines_are_never_mutated():
    assert file_mutants(PY, changed_lines={2}, language="python") == []


def test_string_literals_are_never_mutated():
    src = 'def f():\n    return "a == b"\n'
    assert file_mutants(src, changed_lines={2}, language="python") == []


def test_typescript_strict_equality_flips():
    src = "export function eq(a: number, b: number) {\n  return a === b && a > 0;\n}\n"
    mutants = file_mutants(src, changed_lines={2}, language="typescript")
    mutated = {m.mutated.strip() for m in mutants}
    assert "return a !== b && a > 0;" in mutated
    assert "return a === b || a > 0;" in mutated


def test_apply_mutant_replaces_exactly_one_line():
    (first, *_) = file_mutants(PY, changed_lines={3}, language="python")
    out = apply_mutant(PY, first)
    assert out.splitlines()[2] == first.mutated
    assert out.splitlines()[3] == "        return 10"
    assert len(out.splitlines()) == len(PY.splitlines())
