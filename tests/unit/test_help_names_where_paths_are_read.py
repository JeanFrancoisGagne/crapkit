"""mutate --files, claims release and ratchet move rebase a relative path from the
working directory when the root came from the walk, like explain and rescore do,
so their help says so too. Every command that takes a file argument also names
the spellings it reads as git's path, and next-item's --exclude names the ones
it reads in a path fragment, each checked against the reader it describes."""
import os

import pytest

from crapkit.cli import main
from crapkit.cli._shared import _repo_relative
from crapkit.cli.queue import _path_fragment

WHERE = ("(repo-relative or absolute; ./src/a.py, SRC/a.py where the disk ignores case, "
         "and on Windows src\\a.py, /c/... and /mnt/c/... name src/a.py; "
         "without --repo, read from the working directory)")


def _help(capsys, command: str) -> str:
    with pytest.raises(SystemExit) as stop:
        main([command, "--help"])
    assert stop.value.code == 0
    return " ".join(capsys.readouterr().out.split())


def test_mutate_help_says_where_its_files_are_read_from(capsys):
    text = _help(capsys, "mutate")

    assert f"--files [FILES ...] mutate these whole files {WHERE}" in text, text


def test_claims_help_says_where_the_release_path_is_read_from(capsys):
    text = _help(capsys, "claims")

    assert f"PATH {WHERE}" in text, text


def test_ratchet_help_says_where_move_paths_are_read_from(capsys):
    text = _help(capsys, "ratchet")

    assert f"for move: OLD NEW {WHERE}" in text, text


@pytest.mark.parametrize("command", ["explain", "brief", "rescore", "test-scoped"])
def test_each_file_argument_help_names_the_spellings_it_reads(capsys, command):
    text = _help(capsys, command)

    assert WHERE in text, text


def test_the_spellings_the_help_names_read_as_the_file_git_names(tmp_path):
    """The help's examples, fed through the reader every file argument takes."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")

    assert _repo_relative("./src/a.py", tmp_path) == "src/a.py"
    if os.name == "nt":
        assert _repo_relative("src\\a.py", tmp_path) == "src/a.py"
        assert _repo_relative("SRC/a.py", tmp_path) == "src/a.py"
        drive = str(tmp_path.resolve())
        msys = "/" + drive[0].lower() + drive[2:].replace("\\", "/") + "/src/a.py"
        assert _repo_relative(msys, tmp_path) == "src/a.py"
        assert _repo_relative("/mnt" + msys, tmp_path) == "src/a.py"


def test_next_item_exclude_help_names_the_path_fragment_spellings(capsys):
    text = _help(capsys, "next-item")

    assert ("--exclude EXCLUDE skip items whose path or function name contains this "
            "(repeatable); a path fragment reads as git spells it: ./pkg/legacy, "
            "PKG/Legacy where the disk ignores case, and pkg\\legacy on Windows all "
            "skip pkg/legacy") in text, text


@pytest.mark.parametrize("fragment", ["./pkg/legacy", "PKG/Legacy"])
def test_the_exclude_fragments_the_help_names_read_as_git_s_path(fragment):
    assert _path_fragment(fragment, folds=True) == "pkg/legacy"


@pytest.mark.skipif(os.name != "nt", reason="a backslash is a separator on Windows only")
def test_the_backslash_fragment_the_help_names_reads_as_git_s_path_on_windows():
    assert _path_fragment("pkg\\legacy", folds=False) == "pkg/legacy"
