"""mutate --files and claims release rebase a relative path from the working
directory when the root came from the walk, like explain and rescore do, so their
help says so too."""
import pytest

from crapkit.cli import main

NOTE = "(repo-relative; without --repo, read from the working directory)"


def _help(capsys, command: str) -> str:
    with pytest.raises(SystemExit) as stop:
        main([command, "--help"])
    assert stop.value.code == 0
    return " ".join(capsys.readouterr().out.split())


def test_mutate_help_says_where_its_files_are_read_from(capsys):
    text = _help(capsys, "mutate")

    assert f"--files [FILES ...] mutate these whole files {NOTE}" in text, text


def test_claims_help_says_where_the_release_path_is_read_from(capsys):
    text = _help(capsys, "claims")

    assert f"PATH {NOTE}" in text, text
