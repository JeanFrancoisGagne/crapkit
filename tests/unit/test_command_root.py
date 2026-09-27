"""Two parser contracts behind ADR 0002: `--repo` defaults to the walk on every
subcommand, and each path argument says where it is read from. The behavior
itself is pinned at the CLI seam in tests/e2e/test_discovery_e2e.py.
"""
import pytest

from crapkit.cli.parser import build_parser


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


# --- a leading ~ in --repo ---------------------------------------------------------
#
# An MCP client starts `crapkit mcp --repo ~/app` without a shell, and cmd.exe
# never expands `~` either: the root read `<cwd>/~/app` and every tool answered
# `no crapkit.toml` there. A leading `~` is the user's home on every command.

def _home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "app").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_a_repo_flag_starting_with_a_tilde_names_the_home_directory(monkeypatch, tmp_path):
    from crapkit.cli._shared import _command_root

    home = _home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    assert _command_root("~/app") == (home / "app").resolve()
    assert _command_root("~") == home.resolve()


def test_an_mcp_repo_argument_starting_with_a_tilde_names_the_home_directory(monkeypatch, tmp_path):
    from crapkit import mcp_server

    home = _home(monkeypatch, tmp_path)
    (home / "app" / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n',
        encoding="utf-8")
    ran = []

    mcp_server._call_tool(tmp_path, "list_runs", {"repo": "~/app"},
                          run_cli=lambda tool, arguments, repo: ran.append(repo))

    assert ran == [str((home / "app").resolve())]


def test_a_tilde_naming_no_known_user_is_a_plain_path(monkeypatch, tmp_path):
    from crapkit.cli._shared import _command_root

    monkeypatch.chdir(tmp_path)

    assert _command_root("~no-such-user-9f3e/app").name == "app"
