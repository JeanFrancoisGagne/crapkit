"""Two parser contracts behind ADR 0002: `--repo` defaults to the walk on every
subcommand, and each path argument says where it is read from. The behavior
itself is pinned at the CLI seam in tests/e2e/test_discovery_e2e.py.
"""
import pytest

from crapkit.cli import build_parser


def _subcommands() -> dict:
    parser = build_parser()
    return [a for a in parser._actions if hasattr(a, "choices") and a.choices][0].choices


def test_every_subcommand_defaults_repo_to_the_walk():
    defaults = {name: sub.get_default("repo") for name, sub in _subcommands().items()
                if any("--repo" in act.option_strings for act in sub._actions)}

    assert defaults and set(defaults.values()) == {None}, defaults


@pytest.mark.parametrize("command, argument", [
    ("explain", "path"), ("brief", "path"), ("rescore", "files"), ("test-scoped", "files"),
])
def test_the_path_arguments_say_where_they_are_read_from(command, argument):
    (act,) = [a for a in _subcommands()[command]._actions if a.dest == argument]

    assert "without --repo, read from the working directory" in act.help, act.help
