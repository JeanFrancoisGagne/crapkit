"""`crapkit --version` has to say what produced the number.

A bare `0.1.0` pasted into a bug report names no program, and the number lives
in two places (pyproject.toml and the package), so the installed distribution is
the one that answers. The package constant is the fallback for a source tree
with nothing installed, which is where the ratchet merge driver runs.
"""
import importlib.metadata
import sys

import pytest

from crapkit import __version__
from crapkit.cli import _version_line


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
    from crapkit.cli import build_parser

    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == _version_line()


def test_building_the_parser_costs_no_distribution_lookup(monkeypatch):
    """Every command builds the parser, `hook-precommit` at every commit
    included, and the lookup costs ~30ms. Nobody pays it to run the hook."""
    from crapkit.cli import build_parser

    def refuse(name: str) -> str:
        raise AssertionError("--version resolved before anyone asked for it")

    monkeypatch.setattr(importlib.metadata, "version", refuse)

    assert build_parser().prog == "crapkit"
