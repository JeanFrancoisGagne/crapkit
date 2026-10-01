"""Every command release.py starts finds the release venv's own tools first.

0.8.1's stage 2a contract run failed when crapkit printed hints of the form
`<venv python> -m crapkit` and the tests ran the bare `crapkit` those hints
stand for: release.py was launched by the venv's python by absolute path, the
shell's PATH did not hold the venv, and `crapkit` resolved to nothing or to
another install. release.py now starts every child with this interpreter's
scripts directory (the venv's Scripts on Windows, bin elsewhere) first on PATH.
"""
from __future__ import annotations

import ast
import os
import sysconfig
from pathlib import Path

import pytest

from test_release_tool import release

SCRIPTS = sysconfig.get_path("scripts")
SOURCE = Path(release.__file__).read_text(encoding="utf-8")


def _same(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


@pytest.fixture
def shell_path(monkeypatch):
    """The shell's PATH without the release venv, as 0.8.1's stage 2a had it."""
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
               if entry and not _same(entry, SCRIPTS)]
    monkeypatch.setenv("PATH", os.pathsep.join(entries))
    return entries


def _child_says(capfd, code: str, root: Path) -> str:
    release._execute((release.PY, "-c", code), root, False)
    return capfd.readouterr().out.splitlines()[-1]


def test_a_child_finds_the_venvs_scripts_first_on_path(shell_path, capfd, tmp_path):
    first = _child_says(capfd, "import os; print(os.environ['PATH'].split(os.pathsep)[0])", tmp_path)

    assert _same(first, SCRIPTS)


def test_a_bare_crapkit_in_a_child_is_the_venvs_crapkit(shell_path, capfd, tmp_path):
    if not any(Path(SCRIPTS).glob("crapkit*")):
        pytest.skip("this interpreter has no crapkit console script")

    found = _child_says(capfd, "import shutil; print(shutil.which('crapkit'))", tmp_path)

    assert _same(str(Path(found).parent), SCRIPTS)


def test_the_rest_of_the_shell_path_follows_in_its_order(shell_path, monkeypatch):
    path = release._child_env()["PATH"].split(os.pathsep)

    assert _same(path[0], SCRIPTS) and path[1:] == shell_path


def test_a_path_that_already_leads_with_the_venv_holds_it_once(monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([SCRIPTS, "elsewhere", SCRIPTS]))

    assert release._child_env()["PATH"].split(os.pathsep) == [SCRIPTS, "elsewhere"]


def test_a_command_name_resolves_through_the_childs_path(shell_path):
    if not any(Path(SCRIPTS).glob("crapkit*")):
        pytest.skip("this interpreter has no crapkit console script")

    assert _same(str(Path(release._executable("crapkit")).parent), SCRIPTS)


def _runs_without_env() -> list[int]:
    """Lines of every subprocess.run call in release.py that passes no env."""
    calls = [node for node in ast.walk(ast.parse(SOURCE)) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "subprocess.run"]
    return [call.lineno for call in calls if "env" not in {keyword.arg for keyword in call.keywords}]


def test_every_child_release_py_starts_takes_the_child_environment():
    assert _runs_without_env() == []
