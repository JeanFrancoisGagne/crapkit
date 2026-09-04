"""Where crapkit.toml is, found upward from where the caller stands (ADR 0002).

The advisory hook walked this way from 0.4.5 on while every command read one
directory. 0.5.0 makes the walk the rule, so it lives in one stdlib-only module
the hook, the commands and the MCP server all call. These pin the walk itself;
each caller is pinned at its own seam.
"""
import ast
import sys
from pathlib import Path

import crapkit.rootfind
from crapkit.rootfind import find_root


def _config_at(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "crapkit.toml").write_text("", encoding="utf-8")


def test_a_configuration_above_the_start_is_found(tmp_path):
    _config_at(tmp_path / "mono")
    (tmp_path / "mono" / "web" / "src").mkdir(parents=True)

    assert find_root(tmp_path / "mono" / "web" / "src") == tmp_path / "mono"


def test_the_nearest_configuration_wins_over_an_ancestors(tmp_path):
    """A nested configuration shadows the root's, as the hook already behaved."""
    _config_at(tmp_path / "mono")
    _config_at(tmp_path / "mono" / "web")
    (tmp_path / "mono" / "web" / "src").mkdir()

    assert find_root(tmp_path / "mono" / "web" / "src") == tmp_path / "mono" / "web"


def test_a_git_directory_without_a_configuration_stops_the_walk(tmp_path):
    """A nested repository never borrows the configuration, or the store, of the
    tree it sits in."""
    _config_at(tmp_path / "mono")
    (tmp_path / "mono" / "lib" / ".git").mkdir(parents=True)

    assert find_root(tmp_path / "mono" / "lib") is None


def test_a_git_file_stops_the_walk_the_same_way(tmp_path):
    """A linked worktree carries `.git` as a file."""
    _config_at(tmp_path)
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert find_root(tmp_path / "wt") is None


def test_a_configuration_beside_the_git_entry_still_wins(tmp_path):
    (tmp_path / ".git").mkdir()
    _config_at(tmp_path)

    assert find_root(tmp_path) == tmp_path


def test_a_start_under_no_configuration_finds_none(tmp_path):
    (tmp_path / "loose").mkdir()

    assert find_root(tmp_path / "loose") is None


def test_the_module_imports_only_the_stdlib():
    """The advisory hook imports this per edit, in every repository on the
    machine, and its warm-path budget pins a stdlib-only path before any other
    crapkit import."""
    tree = ast.parse(Path(crapkit.rootfind.__file__).read_text(encoding="utf-8"))
    imported = {node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level == 0}
    imported |= {alias.name.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.Import) for alias in node.names}
    relative = [node for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level > 0]

    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
    assert relative == [], "no crapkit module may be imported here"
