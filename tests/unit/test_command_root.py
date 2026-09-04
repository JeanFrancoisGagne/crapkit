"""The root a command works in, and a path argument said from below it.

`_command_root` is the one line every subcommand opens with; `_repo_relative`
is the one spelling of a file argument. Both are pinned here in-process, the
commands that call them at the CLI seam (tests/e2e/test_discovery_e2e.py).
"""
from pathlib import Path

import pytest

from crapkit.cli import build_parser
from crapkit.cli._shared import _command_root, _repo_relative
from crapkit.errors import ConfigError


def _config_at(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "crapkit.toml").write_text("", encoding="utf-8")
    return directory


# --- the root -----------------------------------------------------------------

def test_no_repo_flag_means_the_walk(tmp_path, monkeypatch, capsys):
    root = _config_at(tmp_path / "mono")
    (root / "web").mkdir()
    monkeypatch.chdir(root / "web")

    assert _command_root(None) == root
    assert capsys.readouterr().err == f"crapkit: using crapkit.toml at {root}\n"


def test_the_working_directory_itself_is_named_by_no_line(tmp_path, monkeypatch, capsys):
    root = _config_at(tmp_path / "mono")
    monkeypatch.chdir(root)

    assert _command_root(None) == root
    assert capsys.readouterr().err == ""


def test_nothing_found_answers_the_working_directory_so_the_refusal_names_it(
        tmp_path, monkeypatch, capsys):
    (tmp_path / "loose").mkdir()
    monkeypatch.chdir(tmp_path / "loose")

    assert _command_root(None) == tmp_path / "loose"
    assert capsys.readouterr().err == ""


def test_an_explicit_repo_is_an_exact_root(tmp_path, monkeypatch, capsys):
    root = _config_at(tmp_path / "mono")
    (root / "web").mkdir()
    monkeypatch.chdir(root)

    assert _command_root("web") == root / "web"
    assert capsys.readouterr().err == ""


def test_every_subcommand_defaults_repo_to_the_walk():
    parser = build_parser()
    subs = [a for a in parser._actions if hasattr(a, "choices") and a.choices][0]
    defaults = {name: sub.get_default("repo") for name, sub in subs.choices.items()
                if any("--repo" in act.option_strings for act in sub._actions)}

    assert defaults and set(defaults.values()) == {None}, defaults


# --- a path argument ----------------------------------------------------------

def test_a_relative_argument_below_the_root_is_rebased_from_where_the_user_stands(tmp_path):
    root = _config_at(tmp_path / "mono")
    (root / "web" / "src").mkdir(parents=True)

    assert _repo_relative("grade.py", root, root / "web" / "src") == "web/src/grade.py"
    assert _repo_relative("../lib/x.py", root, root / "web" / "src") == "web/lib/x.py"


def test_at_the_root_a_relative_argument_reads_as_before(tmp_path):
    root = _config_at(tmp_path / "mono")

    assert _repo_relative("./web/src/grade.py", root, root) == "web/src/grade.py"
    assert _repo_relative("web\\src\\grade.py", root, root) == "web/src/grade.py"


def test_a_working_directory_outside_the_root_leaves_the_argument_root_relative(tmp_path):
    """`crapkit rescore --repo /elsewhere src/a.py` from an unrelated directory
    names a file under /elsewhere, as it always did."""
    root = _config_at(tmp_path / "mono")
    (tmp_path / "elsewhere").mkdir()

    assert _repo_relative("src/a.py", root, tmp_path / "elsewhere") == "src/a.py"


def test_no_working_directory_means_no_rebase(tmp_path):
    root = _config_at(tmp_path / "mono")

    assert _repo_relative("src/a.py", root) == "src/a.py"


def test_an_argument_climbing_out_of_the_root_is_refused(tmp_path):
    root = _config_at(tmp_path / "mono")
    (root / "web").mkdir()

    with pytest.raises(ConfigError, match="is outside the repo at"):
        _repo_relative("../../x.py", root, root / "web")
