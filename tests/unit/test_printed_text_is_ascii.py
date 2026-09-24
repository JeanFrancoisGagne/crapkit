"""Every line crapkit writes from its own text is ASCII.

Windows PowerShell 5.1 reads a child's output through the console code page,
437 or 1252, so `$x = crapkit doctor` captured the UTF-8 bytes of an em dash as
three characters, `ΓÇö`. #68 moved six one-liners to ` - `; the rest kept the
dash, including the first two lines a new user meets: doctor on a repo with no
lane (stdout) and worklist or brief before the first run (stderr).

The scan reads every string literal in src/crapkit that is not a docstring. A
literal typed with a non-ASCII character fails. One written with `\\u` escapes is
data, a set of characters to match or a replacement character, and passes: a
message author types the character itself. Text crapkit quotes from elsewhere
(a path, a function name) is not a literal and keeps its characters.

The capture rows run the commands a new user runs first and read what the
consumer reads: the pipe's bytes on every OS, and `$x = ... 2>&1` in Windows
PowerShell 5.1 under code page 437 on Windows.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard

SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"
TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "pkg"\npaths = ["pkg"]\nlanguages = ["python"]\n')


def _docstrings(tree: ast.AST) -> set[int]:
    kinds = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, kinds) and node.body and _is_text(node.body[0])}


def _is_text(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) \
        and isinstance(statement.value.value, str)


def _typed_non_ascii(path: Path, root: Path = SRC) -> list[str]:
    """Each line holding a literal whose source text has a non-ASCII character.
    An f-string and the text pieces inside it name one line once."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    docs = _docstrings(tree)
    lines = {node.lineno: ast.get_source_segment(text, node) for node in ast.walk(tree)
             if _typed(node, text, docs)}
    where = path.relative_to(root).as_posix()
    return [f"{where}:{line}: {segment[:90]}" for line, segment in sorted(lines.items())]


def _typed(node: ast.AST, text: str, docs: set[int]) -> bool:
    """A literal, not a docstring, whose source holds a non-ASCII character."""
    if not _is_literal(node) or id(node) in docs:
        return False
    return not (ast.get_source_segment(text, node) or "").isascii()


def _is_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or isinstance(node, ast.Constant) and isinstance(node.value, str)


def test_no_literal_crapkit_prints_is_typed_with_a_non_ascii_character():
    found = [hit for path in sorted(SRC.rglob("*.py")) for hit in _typed_non_ascii(path)]

    assert found == [], "\n".join(found)


def test_the_scan_sees_a_typed_dash_and_passes_an_escaped_one(tmp_path):
    """The rule itself: the character typed fails, the escape passes, and a
    docstring or a comment is not printed text."""
    sample = tmp_path / "sample.py"
    sample.write_text('"""A docstring — kept."""\n'
                      'LINE = "no run — run coverage"  # a comment — kept\n'
                      'CHARS = "\\u201c\\u201d"\n'
                      'NAME = f"{LINE} → next"\n', encoding="utf-8")
    found = _typed_non_ascii(sample, tmp_path)

    assert [hit.split(":")[1] for hit in found] == ["2", "4"]


# --- what a shell captures ---------------------------------------------------

@pytest.fixture(scope="module")
def fresh_repo(tmp_path_factory) -> Path:
    """A repo with a scope and no lane, never measured: doctor, worklist and
    brief print the lines a new user meets first."""
    repo = tmp_path_factory.mktemp("fresh")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=repo, check=True, capture_output=True)
    return repo


FIRST_COMMANDS = {
    "doctor, stdout": (["doctor"], "stdout"),
    "worklist before any run, stderr": (["worklist"], "stderr"),
    "brief before any run, stderr": (["brief", "pkg/mod.py", "f"], "stderr"),
    "digest before any run, stderr": (["digest"], "stderr"),
}


@pytest.mark.parametrize("label", list(FIRST_COMMANDS))
def test_the_first_lines_a_new_user_meets_are_ascii_on_the_pipe(fresh_repo, label):
    argv, stream = FIRST_COMMANDS[label]
    result = hang_guard.run([sys.executable, "-m", "crapkit", *argv], cwd=fresh_repo)
    data = result.stdout if stream == "stdout" else result.stderr

    assert data.strip(), result
    assert data.isascii(), data.decode("utf-8", "replace")


@pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None,
                    reason="Windows PowerShell 5.1 runs on Windows only")
@pytest.mark.parametrize("label", list(FIRST_COMMANDS))
def test_powershell_captures_what_crapkit_wrote(fresh_repo, label):
    """`$x = ... 2>&1` decodes the child's bytes through [Console]::OutputEncoding,
    set here to code page 437, the default of an English Windows console."""
    argv, _stream = FIRST_COMMANDS[label]
    args = " ".join(f"'{arg}'" for arg in argv)
    script = ("[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437); "
              f"$x = & '{sys.executable}' -m crapkit {args} 2>&1 | ForEach-Object {{ \"$_\" }}; "
              "$hi = @($x | ForEach-Object { $_.ToCharArray() } | Where-Object { [int]$_ -gt 127 }); "
              "'LINES=' + $x.Count; 'HIGH=' + ($hi -join '')")
    result = hang_guard.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=fresh_repo, text=True, encoding="utf-8", errors="replace")
    report = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    assert int(report.get("LINES", "0")) > 0, result.stdout + result.stderr
    assert report.get("HIGH") == "", result.stdout
