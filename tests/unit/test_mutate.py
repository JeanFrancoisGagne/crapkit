"""Deterministic line-based mutants, diff-scoped: flip comparisons and boolean
operators on changed lines only. String literals and comment lines are never
mutated — a survivor in dead text would erode trust in the survivor list."""
import pytest

from crapkit.errors import ToolError
from crapkit.mutate import apply_mutant, file_mutants, mutation_language

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


# --- the language dispatch: what the path decides ------------------------------

RUST = (
    "fn total(v: &[i32], n: usize) -> i32 {\n"
    "    let mut s = 0;\n"
    "    for i in 0..=n {\n"
    "        if v[i] > 0 {\n"
    "            s += v[i];\n"
    "        }\n"
    "    }\n"
    "    s\n"
    "}\n"
)

SHELL = 'save() {\n  echo "$1" > out.txt\n}\n'

POWERSHELL = ('function Save-Line {\n'
              '  param($Line)\n'
              '  $Line | Out-File -Append "out.txt"\n'
              '  Write-Output $Line > out.txt\n'
              '}\n')


def test_the_path_decides_the_operator_set():
    assert mutation_language("src/mod.py") == "python"
    assert mutation_language("src/lib.rs") == "rust"
    assert mutation_language("scripts/deploy.sh") == "shell"
    assert mutation_language("scripts/lib.bash") == "shell"
    assert mutation_language("scripts/deploy.ps1") == "powershell"
    assert mutation_language("scripts/Tools.psm1") == "powershell"
    assert mutation_language("src/app.ts") == "typescript"


def test_a_rust_comparison_flips_on_the_c_family_table():
    """Rust spells `==`, `!=`, `<`, `>`, `&&`, `||`, `true` and `false` exactly as
    the C family does, so it takes that table rather than one of its own."""
    mutated = {m.mutated.strip() for m in file_mutants(RUST, changed_lines={4}, language="rust")}

    assert mutated == {"if v[i] >= 0 {", "if v[i] <= 0 {"}


def test_a_rust_range_holds_no_operator_and_needs_no_protection():
    """`0..n` and `0..=n` contain none of the table's tokens, so neither needs a
    `_PROTECT` entry the way Swift's `0..<n` did. Measured, not assumed."""
    assert file_mutants(RUST, changed_lines={3}, language="rust") == []


def test_shell_is_refused_rather_than_mutated():
    """Shell's `<` and `>` are redirections. On a 9-line function 8 of 11 mutants
    flipped one, and a `>=>` is a syntax error the runner scores as a kill: a
    mutation score built out of those is worse than no score."""
    with pytest.raises(ToolError, match="redirection"):
        file_mutants(SHELL, changed_lines=None, language="shell")


def test_powershell_is_refused_for_the_same_reason_shell_is():
    """`>` redirects in PowerShell too, and its comparisons are `-eq`/`-gt`/`-lt`
    and its connectives `-and`/`-or`, none of which this module's table carries.
    Left on the C-family default, every mutant it produced would flip a
    redirection into `>=`."""
    with pytest.raises(ToolError, match="redirection"):
        file_mutants(POWERSHELL, changed_lines=None, language="powershell")


def test_apply_mutant_replaces_exactly_one_line():
    (first, *_) = file_mutants(PY, changed_lines={3}, language="python")
    out = apply_mutant(PY, first)
    assert out.splitlines()[2] == first.mutated
    assert out.splitlines()[3] == "        return 10"
    assert len(out.splitlines()) == len(PY.splitlines())


# --- the corpus cut: which of the diff's files may grow mutants at all -----------

CORPUS_TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    '[exclude]\nglobs = ["src/gen/**"]\nmax_file_bytes = 100\n'
)


def test_the_corpus_cut_is_the_one_scoring_uses():
    """Scopes, excludes, the test-file cut and the byte ceiling, all through
    `universe.assign_files`, so mutate cannot drift from what `coverage` scores."""
    from crapkit.config import load_config_text
    from crapkit.mutate import partition_by_corpus

    targets = {"src/a.py": None, "src/tests/test_a.py": {3}, "src/gen/client.py": None,
               "src/b.rb": None, "tests/test_b.py": None, "src/big.py": {1}}
    sizes = {"src/big.py": 101}

    kept, outside = partition_by_corpus(targets, load_config_text(CORPUS_TOML),
                                        size_of=lambda p: sizes.get(p, 1))

    assert kept == {"src/a.py": None}
    assert outside == ["src/b.rb", "src/big.py", "src/gen/client.py",
                       "src/tests/test_a.py", "tests/test_b.py"]
