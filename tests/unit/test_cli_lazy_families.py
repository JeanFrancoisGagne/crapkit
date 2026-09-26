"""One command, one family.

`crapkit runs list` used to import admin, analyses, queue, ratchet_cmds,
reports, scoring and verifying — every subcommand handler in the tool — because
the parser named all of them in `set_defaults(func=...)` while building the
parser. Naming a handler is not calling it, so the import was pure cost on
every invocation, `hook-precommit` at every git commit included.

These tests pin the mechanism (which modules end up in sys.modules) rather than
a clock, and they pin the two surfaces the mechanism could break: every
subcommand still reaches its handler, and the public entry point stays lazy.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import crapkit
import crapkit.cli
from crapkit.cli.parser import build_parser

FAMILIES = ("admin", "analyses", "queue", "ratchet_cmds", "reports", "scoring", "verifying")


def _child_env() -> dict:
    """The subprocess imports the crapkit this test imported, not an installed one."""
    env = dict(os.environ)
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH", "")) if p)
    return env


def _loaded_cli_modules(snippet: str, tmp_path) -> set[str]:
    """The crapkit.cli.* modules a fresh interpreter ends up holding."""
    probe = (snippet + "\nimport sys, json\n"
             "print(json.dumps(sorted(m for m in sys.modules if m.startswith('crapkit.cli.'))))\n")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env=_child_env(), cwd=str(tmp_path))
    return set(json.loads(done.stdout.splitlines()[-1]))


def test_building_the_parser_loads_no_command_family(tmp_path):
    loaded = _loaded_cli_modules("from crapkit.cli.parser import build_parser\nbuild_parser()", tmp_path)

    assert loaded == {"crapkit.cli.parser"}


def test_running_a_command_loads_its_family_and_no_other(tmp_path):
    """`runs list` on a repo with no snapshot: the report family runs, refuses,
    and the six families it never calls stay unimported."""
    loaded = _loaded_cli_modules(
        "from crapkit.cli import main\nmain(['runs', 'list', '--repo', '.'])", tmp_path)

    assert "crapkit.cli.reports" in loaded
    assert loaded.isdisjoint({f"crapkit.cli.{f}" for f in FAMILIES if f != "reports"})


def test_asking_for_help_loads_no_command_family(tmp_path):
    """--help prints help strings the parser already holds; no handler runs."""
    probe = ("from crapkit.cli import main\n"
             "try:\n    main(['--help'])\nexcept SystemExit:\n    pass")

    assert _loaded_cli_modules(probe, tmp_path) == {"crapkit.cli.parser"}


def _subcommands() -> dict:
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


@pytest.mark.parametrize("name", sorted(_subcommands()))
def test_every_subcommand_dispatches_to_the_handler_named_for_it(name, monkeypatch):
    """The parser's own choices list drives this: whatever `crapkit X` is, it
    calls cmd_X, resolved from the family module at the moment it runs."""
    handler = "cmd_" + name.replace("-", "_")
    owner = __import__(f"crapkit.cli.{_owner_of(handler)}", fromlist=["x"])
    monkeypatch.setattr(owner, handler, lambda args: f"ran {handler}")

    func = _subcommands()[name].get_default("func")

    assert func(argparse.Namespace()) == f"ran {handler}"


def _owner_of(handler: str) -> str:
    command = handler.removeprefix("cmd_").replace("_", "-")
    return _subcommands()[command].get_default("func")._family


def test_the_help_text_still_names_every_subcommand():
    text = build_parser().format_help()

    assert [n for n in sorted(_subcommands()) if n not in text] == []


# --- a path where a subcommand belongs ---------------------------------------

@pytest.mark.parametrize("arg", ["~/some-repo", "./mini", "/abs/path", ".."])
def test_a_path_as_the_first_argument_names_the_repo_flag(arg, capsys):
    """`crapkit ./mini` reads as "score this repo". argparse answered it with the
    invalid-choice dump of every subcommand and never printed the word repo,
    which is where the path goes."""
    from crapkit.cli import main

    code = main([arg])

    err = capsys.readouterr().err
    assert code == 2
    assert "--repo" in err
    assert "invalid choice" not in err


WINDOWS_PATHS = [r"C:\work\app", r"C:\my repos\app"] if os.name == "nt" else []


@pytest.mark.parametrize("arg", ["my repos/app", "./a b/c", "/home/a b/app", *WINDOWS_PATHS])
def test_the_command_a_path_refusal_prints_keeps_the_path_one_argument(arg, capsys):
    r"""The refusal prints `inventory --repo <the path>`. Printed as it came, a
    path holding a space reached crapkit as two arguments from every shell, and
    Git Bash read `C:\work\app` as `C:workapp`. sh's reading of the printed
    word is Git Bash's, and on Windows cmd.exe and PowerShell read a word of
    forward slashes and quoted segments the same way."""
    from crapkit.cli import main

    main([arg])

    printed = re.search(r"e\.g\. `[^`]* inventory --repo (.+?)` ", capsys.readouterr().err)
    assert [Path(word) for word in shlex.split(printed.group(1))] == [Path(arg)]


@pytest.mark.parametrize(("arg", "word"), [("my repos/app", '"my repos/app"'),
                                           ("a b/c d/e", '"a b/c d/e"'),
                                           (r"C:\my repos\app", '"C:/my repos/app"'),
                                           (r"C:\work\app", "C:/work/app")])
def test_a_refused_windows_path_that_needs_quotes_goes_in_one_pair(arg, word, capsys,
                                                                  monkeypatch):
    """PowerShell ends an argument that opens with a quote at the closing quote,
    so `--repo "my repos"/app` reached crapkit as `my repos` and `/app`. One pair
    of quotes around the whole path is one argument in cmd.exe, PowerShell and
    Git Bash."""
    from types import SimpleNamespace

    from crapkit import invocation
    from crapkit.cli import main

    monkeypatch.setattr(invocation, "os", SimpleNamespace(name="nt"))
    main([arg])

    assert f" inventory --repo {word}` " in capsys.readouterr().err


def test_a_misspelled_subcommand_is_still_argparses_error():
    """Shape is the whole trigger, so a typo carrying no path separator still
    gets the usage dump a human is there to read."""
    from crapkit.cli import main

    with pytest.raises(SystemExit) as exit_code:
        main(["inventry"])

    assert exit_code.value.code == 2
