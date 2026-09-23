"""`ratchet move` files a mark under the repo-relative key every scored row carries.

Before, OLD and NEW went into the marks file as typed. `./web/lib/a.py`, a
Windows backslash path, or a path typed from a subdirectory all exited 0 and
filed the mark under a key no scored row has, so the moved function read as
unmarked and its debt left the ratchet. Every other path argument is read the
way ADR 0002 says: repo-relative at the root, rebased from the directory the
user stands in when the root came from the upward walk.
"""
import argparse
import os
from pathlib import Path

import pytest

from crapkit.cli.ratchet_cmds import cmd_ratchet
from crapkit.errors import ConfigError

CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "web"\npaths = ["web"]\nlanguages = ["python"]\n')
STAMPS = "# crapkit-analysis=10 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "crapkit-ratchet.tsv").write_text(STAMPS + "web/src/a.py\tf( )\t40.0000\n",
                                                  encoding="utf-8")
    return tmp_path


def move(old: str, new: str, repo: str | None = None) -> int:
    return cmd_ratchet(argparse.Namespace(action="move", repo=repo, files=[old, new]))


def marks(repo: Path) -> list[str]:
    return (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()[3:]


def test_a_dot_slash_target_files_the_mark_under_the_plain_repo_path(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)

    assert move("web/src/a.py", "./web/lib/a.py") == 0

    assert marks(repo) == ["web/lib/a.py\tf( )\t40.0000"]
    assert capsys.readouterr().out == ("crapkit-ratchet.tsv: moved 1 mark(s) from web/src/a.py "
                                       "to web/lib/a.py\n")


@pytest.mark.skipif(os.name != "nt", reason="a backslash is a path separator only on Windows")
def test_a_backslash_target_files_the_mark_under_the_forward_slash_path(repo, monkeypatch):
    monkeypatch.chdir(repo)

    assert move("web\\src\\a.py", "web\\lib\\a.py") == 0

    assert marks(repo) == ["web/lib/a.py\tf( )\t40.0000"]


def test_paths_typed_from_a_subdirectory_are_read_from_there(repo, monkeypatch):
    monkeypatch.chdir(repo / "web")

    assert move("src/a.py", "lib/a.py") == 0

    assert marks(repo) == ["web/lib/a.py\tf( )\t40.0000"]


def test_a_directory_move_typed_from_a_subdirectory_keeps_its_trailing_slash(repo, monkeypatch,
                                                                              capsys):
    monkeypatch.chdir(repo / "web")

    assert move("./src/", "lib/") == 0

    assert marks(repo) == ["web/lib/a.py\tf( )\t40.0000"]
    assert capsys.readouterr().out == ("crapkit-ratchet.tsv: moved 1 mark(s) from web/src/ "
                                       "to web/lib/\n")


def test_under_repo_the_paths_stay_root_relative_wherever_the_shell_stands(repo, monkeypatch):
    monkeypatch.chdir(repo / "web")

    assert move("web/src/a.py", "web/lib/a.py", repo=str(repo)) == 0

    assert marks(repo) == ["web/lib/a.py\tf( )\t40.0000"]


def test_a_miss_names_the_path_it_looked_under(repo, monkeypatch):
    monkeypatch.chdir(repo / "web")

    with pytest.raises(ConfigError, match="no mark under web/web/src/a.py in crapkit-ratchet.tsv"):
        move("web/src/a.py", "lib/a.py")

    assert marks(repo) == ["web/src/a.py\tf( )\t40.0000"]


def test_the_move_takes_the_directory_its_paths_were_typed_in(repo):
    """No default. A caller that left the directory out would read every path
    root-relative from any directory, the bug this file pins, and say nothing."""
    from crapkit.cli.ratchet_cmds import _ratchet_move

    with pytest.raises(TypeError, match="cwd"):
        _ratchet_move(repo, None, ["web/src/a.py", "web/lib/a.py"])
