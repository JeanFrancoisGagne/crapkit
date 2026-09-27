"""`crapkit --version` has to say what produced the number.

A bare `0.1.0` pasted into a bug report names no program, and the number lives
in two places (pyproject.toml and the package), so the installed distribution is
the one that answers. The package constant is the fallback for a source tree
with nothing installed, which is where the ratchet merge driver runs.
"""
import importlib.metadata
import json
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, git

import crapkit
from crapkit import __version__
from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli import main, parser
from crapkit.cli.parser import _version_line


def _no_dist_info_reachable(monkeypatch) -> None:
    """An empty sys.path: no METADATA header to read, so the question lands on
    importlib.metadata, which is what these three tests are about. What happens
    when a header IS readable is test_version_metadata_cost.py's subject."""
    monkeypatch.setattr(sys, "path", [])


def test_the_line_leads_with_the_program_name(monkeypatch):
    _no_dist_info_reachable(monkeypatch)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")

    assert _version_line() == "crapkit 9.9.9"


def test_the_number_comes_from_the_installed_distribution(monkeypatch):
    asked = []

    def record(name: str) -> str:
        asked.append(name)
        return "9.9.9"

    _no_dist_info_reachable(monkeypatch)
    monkeypatch.setattr(importlib.metadata, "version", record)
    _version_line()

    assert asked == ["crapkit"]


def test_a_source_tree_with_nothing_installed_falls_back_to_the_package(monkeypatch):
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    _no_dist_info_reachable(monkeypatch)
    monkeypatch.setattr(importlib.metadata, "version", missing)

    assert _version_line() == f"crapkit {__version__}"


def test_the_parser_serves_that_line_and_exits_zero(capsys):
    from crapkit.cli.parser import build_parser

    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == _version_line()


def test_building_the_parser_costs_no_distribution_lookup(monkeypatch):
    """Every command builds the parser, `hook-precommit` at every commit
    included, and the lookup costs ~30ms. Nobody pays it to run the hook."""
    from crapkit.cli.parser import build_parser

    def refuse(name: str) -> str:
        raise AssertionError("--version resolved before anyone asked for it")

    monkeypatch.setattr(importlib.metadata, "version", refuse)

    assert build_parser().prog == "crapkit"


# --- the build a crapkit runs from (Q12) ----------------------------------------
#
# A crapkit run from a git checkout, a source tree or an editable install, is a
# build of a commit, and a pasted `--version` or a deploy record has to say
# which. The package is found at <checkout>/src/crapkit; the tests point that
# lookup at a checkout of their own.


def _checkout(root) -> str:
    """A committed checkout holding src/crapkit, as a crapkit clone does."""
    (root / "src" / "crapkit").mkdir(parents=True)
    (root / "src" / "crapkit" / "__init__.py").write_text("", encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "a checkout")
    return git(root, "rev-parse", "HEAD").strip()


def _runs_from(monkeypatch, package) -> None:
    monkeypatch.setattr(parser, "_package_dir", lambda: package)


def _version_json(capsys, *argv: str) -> dict:
    assert main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("argv", [("--version", "--json"), ("--json", "--version")])
def test_version_json_is_one_object_naming_the_commit_a_checkout_is_at(monkeypatch, capsys,
                                                                        tmp_path, argv):
    commit = _checkout(tmp_path)
    _runs_from(monkeypatch, tmp_path / "src" / "crapkit")

    assert _version_json(capsys, *argv) == {
        "analysis_version": ANALYSIS_VERSION, "commit": commit, "dirty": False, "schema": 1,
        "version": _version_line().split()[1]}


@pytest.mark.parametrize("change", ["edited", "staged", "untracked"])
def test_a_checkout_holding_changes_its_commit_does_not_is_dirty(monkeypatch, capsys,
                                                                  tmp_path, change):
    _checkout(tmp_path)
    _runs_from(monkeypatch, tmp_path / "src" / "crapkit")
    source = tmp_path / "src" / "crapkit" / ("new.py" if change == "untracked" else "__init__.py")
    source.write_text("X = 1\n", encoding="utf-8")
    if change == "staged":
        git(tmp_path, "add", "-A")

    assert _version_json(capsys, "--version", "--json")["dirty"] is True


def test_an_ignored_file_leaves_the_checkout_clean(monkeypatch, capsys, tmp_path):
    """A virtual environment or build output git ignores is not a change."""
    _checkout(tmp_path)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    commit_all(tmp_path, "ignore build")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.py").write_text("X = 1\n", encoding="utf-8")
    _runs_from(monkeypatch, tmp_path / "src" / "crapkit")

    assert _version_json(capsys, "--version", "--json")["dirty"] is False


@pytest.mark.parametrize("where", ["site-packages", "venv-inside-a-repo"])
def test_an_installed_build_names_no_commit(monkeypatch, capsys, tmp_path, where):
    """A wheel carries no commit. One installed into a virtual environment
    inside some other repository must not borrow that repository's HEAD."""
    if where == "venv-inside-a-repo":
        _checkout(tmp_path)
    package = tmp_path / ".venv" / "Lib" / "site-packages" / "crapkit"
    package.mkdir(parents=True)
    _runs_from(monkeypatch, package)

    payload = _version_json(capsys, "--version", "--json")

    assert (payload["commit"], payload["dirty"]) == (None, None)


def test_a_checkout_git_cannot_read_names_no_commit(monkeypatch, capsys, tmp_path):
    """A .git with no commit behind it: the answer is null, never a traceback."""
    (tmp_path / "src" / "crapkit").mkdir(parents=True)
    git(tmp_path, "init", "-q")
    _runs_from(monkeypatch, tmp_path / "src" / "crapkit")

    assert _version_json(capsys, "--version", "--json")["commit"] is None


class _Terminal:
    """A stream that says it is a terminal, as a person's console does."""

    def __init__(self) -> None:
        self.written = []

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


def _line_on(monkeypatch, stream) -> str:
    monkeypatch.setattr(sys, "stdout", stream)
    with pytest.raises(SystemExit):
        parser.build_parser().parse_args(["--version"])
    return "".join(stream.written).strip()


def test_a_person_at_a_terminal_reads_the_commit_and_the_dirty_flag(monkeypatch, tmp_path):
    commit = _checkout(tmp_path)
    _runs_from(monkeypatch, tmp_path / "src" / "crapkit")
    clean = _line_on(monkeypatch, _Terminal())
    (tmp_path / "src" / "crapkit" / "__init__.py").write_text("X = 1\n", encoding="utf-8")

    assert clean == f"{_version_line()} (commit {commit}, clean)"
    assert _line_on(monkeypatch, _Terminal()) == f"{_version_line()} (commit {commit}, dirty)"


def test_a_pipe_keeps_the_two_words_every_script_reads(monkeypatch, capsys, tmp_path):
    """doctor --plugin-root reads `crapkit X.Y.Z` off the launcher's stdout, and
    a third word reads as no version: the commit reaches a program as JSON."""
    _checkout(tmp_path)
    _runs_from(monkeypatch, tmp_path / "src" / "crapkit")

    with pytest.raises(SystemExit):
        parser.build_parser().parse_args(["--version"])

    assert capsys.readouterr().out.strip() == _version_line()


def test_an_installed_build_at_a_terminal_prints_the_plain_line(monkeypatch, tmp_path):
    package = tmp_path / "site-packages" / "crapkit"
    package.mkdir(parents=True)
    _runs_from(monkeypatch, package)

    assert _line_on(monkeypatch, _Terminal()) == _version_line()


def test_the_package_is_looked_for_where_crapkit_runs_from():
    assert parser._package_dir() == Path(crapkit.__file__).resolve().parent
