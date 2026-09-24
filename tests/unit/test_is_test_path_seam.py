r"""test-scoped hands `_is_test_path` the path git spells, whatever the shell typed.

The report named `_is_test_path` (cli/verifying.py): it splits on `/` only, so
`tests\test_a.py` would read as no test file at all. It never receives one. Its
caller from outside git, `_group_files_by_scope`, reads every argument through
`_repo_relative` first, and its other caller, brief's coupling partners, takes
paths from `git log`, which writes `/` on every OS. The fold belongs to that
boundary (Q42), and this file holds both halves: the predicate receives
`tests/test_a.py` for each spelling the report's loop typed, and called
directly with a backslash it still answers as the text reads, so a second
fold inside it shows up here as a failure. The name rules and the cmd.exe
`{files}` rows ride along: a test file outside every scope reaches the one
template, and the runner gets its name as git spells it, metacharacters and
all.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import git
from crapkit.cli import main, verifying
from crapkit.cli.verifying import _is_test_path

from path_spellings import WINDOWS, need_case_insensitive, only_posix

# Test files outside every scope whose names carry what cmd.exe and sh would
# otherwise read: a space, non-ASCII, a quote, %x%, & ^ and !.
METACHARACTERS = ["my tests/test_c.py", "tests/tést_é.py", "tests/test_it's.py",
                  "tests/test_100%x%.py", "tests/test_a&b^c.py", "tests/test_wow!.py"]
FILES = ["src/a.py", "tests/test_a.py", "tests/unit/test_b.py", "web/__tests__/a.test.ts",
         "tools/test_tool.py", "tools/x.spec.ts", *METACHARACTERS]
RECORD = ("import json, sys\nfrom pathlib import Path\n"
          "Path('argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n")


@pytest.fixture()
def routed(tmp_path: Path) -> Path:
    """One scope, src, whose {files} template runs record.py; every test file
    sits outside it."""
    root = tmp_path.resolve() / "repo"
    for rel in FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "record.py").write_text(RECORD, encoding="utf-8")
    command = f'"{sys.executable}" record.py {{files}}'
    (root / "crapkit.toml").write_text(
        f"[crapkit]\ntarget = 6\n\n[crapkit.scoped_tests]\nsrc = '''{command}'''\n\n"
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n', encoding="utf-8")
    git(root, "init", "-q")
    return root


def _spy(monkeypatch) -> list[str]:
    """Every path `_is_test_path` is asked about, in order."""
    seen: list[str] = []
    real = verifying._is_test_path

    def spy(path: str) -> bool:
        seen.append(path)
        return real(path)

    monkeypatch.setattr(verifying, "_is_test_path", spy)
    return seen


def _scoped(root: Path, monkeypatch, typed: str, stand: str = "") -> list[str]:
    """test-scoped from `root / stand` with no --repo, as the report's loop
    ran it; the argv the runner received."""
    monkeypatch.chdir(root / stand)
    assert main(["test-scoped", typed]) == 0
    return json.loads((root / "argv.json").read_text(encoding="utf-8"))


def _need(need: str, root: Path) -> None:
    if "windows" in need and not WINDOWS:
        pytest.skip("needs Windows path rules")
    if "case" in need:
        need_case_insensitive(root)


# id -> (need, typed argument, directory typed from, path git spells). The first
# seven are the report's loop (backslash-testpath/loop.py); the last three are
# their POSIX twins.
LOOP_SPELLINGS = {
    "forward": ("", lambda root: "tests/test_a.py", "", "tests/test_a.py"),
    "backslash": ("windows", lambda root: "tests\\test_a.py", "", "tests/test_a.py"),
    "nested-backslash": ("windows", lambda root: "tests\\unit\\test_b.py", "",
                         "tests/unit/test_b.py"),
    "dot-backslash": ("windows", lambda root: ".\\tests\\test_a.py", "", "tests/test_a.py"),
    "absolute": ("", lambda root: str(root / "tests" / "test_a.py"), "", "tests/test_a.py"),
    "up-from-src": ("windows", lambda root: "..\\tests\\test_a.py", "src", "tests/test_a.py"),
    "case": ("windows case", lambda root: "TESTS\\test_a.py", "", "tests/test_a.py"),
    "dot-slash": ("", lambda root: "./tests/test_a.py", "", "tests/test_a.py"),
    "up-from-src-forward": ("", lambda root: "../tests/test_a.py", "src", "tests/test_a.py"),
    "case-forward": ("case", lambda root: "TESTS/test_a.py", "", "tests/test_a.py"),
}


@pytest.mark.parametrize("which", LOOP_SPELLINGS)
def test_is_test_path_receives_the_path_git_spells(routed, monkeypatch, which):
    need, typed, stand, spelled = LOOP_SPELLINGS[which]
    _need(need, routed)
    seen = _spy(monkeypatch)

    argv = _scoped(routed, monkeypatch, typed(routed), stand)

    assert seen == [spelled]
    assert argv == [spelled]


@pytest.mark.parametrize("folded", ["tests/test_a.py", "tests/unit/helpers.py",
                                    "tools/test_tool.py", "src/tests/helpers.py"])
def test_the_predicate_reads_slashes_only_so_the_fold_stays_at_the_boundary(folded):
    """Q42: `_is_test_path` reads `/` and nothing else. A backslash reaching it
    would be a boundary that forgot to fold, and folding here as well would
    hide that boundary's bug from every other reader of the same argument."""
    assert _is_test_path(folded) is True
    assert _is_test_path(folded.replace("/", "\\")) is False


# id -> the file git spells; typed with this OS's separator (a backslash on
# Windows, where the rule is a name rule and not a directory).
NAME_RULES = {
    "__tests__ directory": "web/__tests__/a.test.ts",
    "test_ prefix": "tools/test_tool.py",
    ".spec. infix": "tools/x.spec.ts",
    "nested tests directory": "tests/unit/test_b.py",
}


@pytest.mark.parametrize("which", NAME_RULES)
def test_a_test_file_named_by_each_rule_reaches_the_template(routed, monkeypatch, which):
    spelled = NAME_RULES[which]

    assert _scoped(routed, monkeypatch, spelled.replace("/", os.sep)) == [spelled]


@pytest.mark.skipif(not WINDOWS, reason="needs Windows path rules")
def test_a_mixed_spelling_reaches_the_template_as_git_spells_it(routed, monkeypatch):
    assert _scoped(routed, monkeypatch, "tests\\unit/test_b.py") == ["tests/unit/test_b.py"]


@pytest.mark.parametrize("spelled", METACHARACTERS)
def test_the_runner_gets_a_test_file_name_as_written(routed, monkeypatch, spelled):
    """The template runs through cmd.exe on Windows and sh elsewhere. `%x%`
    must not expand, `&` must not end the command, `^` and `!` must stay."""
    monkeypatch.setenv("x", "EXPANDED")

    assert _scoped(routed, monkeypatch, spelled.replace("/", os.sep)) == [spelled]


@only_posix
def test_posix_reads_a_backslash_as_part_of_a_file_name(routed, monkeypatch, capsys):
    r"""On POSIX `tests\test_a.py` is one file at the root, and no test file:
    the documented rule, not a fold that was forgotten."""
    (routed / "tests\\test_a.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(routed)

    assert main(["test-scoped", "tests\\test_a.py"]) == 3
    assert "belongs to no declared scope" in capsys.readouterr().err
