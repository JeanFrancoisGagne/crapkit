"""A claude-hook argument this build does not know exits 0 with one line.

The plugin's hooks.json ships machine-wide and can be newer than the installed
CLI. An unknown `claude-*` subcommand already exits 0 in silence, but a flag
this build lacks reached argparse, which exited 2 with its usage block, and
PostToolUse hands the model the stderr of an exit 2 on every Edit or Write.
The `--protocol` value meant to absorb that drift was never read, because
argparse refused the argv first.

Now the flags this build knows are read, `--protocol` included, and anything
left over ends the hook with exit 0 and one stderr line that names it and says
the hook was written for a newer crapkit. The edit is not judged: a flag whose
meaning this build cannot know is not guessed at.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import main

# (argv, what the line names) for each shape a newer hook command can take.
UNKNOWN = {
    "protocol 2 then a new flag": (["claude-hook", "--protocol", "2", "--budget", "5"],
                                   "`--budget 5`"),
    "protocol 1 then a new flag": (["claude-hook", "--protocol", "1", "--budget", "5"],
                                   "`--budget 5`"),
    "a new flag before protocol": (["claude-hook", "--budget", "5", "--protocol", "1"],
                                   "`--budget 5`"),
    "a bound new flag": (["claude-hook", "--budget=5"], "`--budget=5`"),
    "a bare new switch": (["claude-hook", "--quiet"], "`--quiet`"),
    "a positional": (["claude-hook", "extra"], "`extra`"),
}

# Each environment that colours 3.14's argparse in a pipe, then the controls
# that keep it plain even before 166d147's parser fix.
COLOUR = {
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"},
    "no colour env": {},
    "NO_COLOR=1 FORCE_COLOR=1": {"NO_COLOR": "1", "FORCE_COLOR": "1"},
    "PYTHON_COLORS=0 FORCE_COLOR=1": {"PYTHON_COLORS": "0", "FORCE_COLOR": "1"},
}
_COLOUR_NAMES = ("FORCE_COLOR", "NO_COLOR", "PYTHON_COLORS", "PY_COLORS", "TERM", "CLICOLOR_FORCE")


def _payload(file_path: str) -> str:
    """A PostToolUse Edit event shaped the way Claude Code sends one."""
    return json.dumps({"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "Edit",
                       "cwd": str(Path(file_path).parent),
                       "tool_input": {"file_path": file_path, "old_string": "a",
                                      "new_string": "b"},
                       "tool_response": {"filePath": file_path, "success": True}})


@pytest.fixture(params=sorted(COLOUR))
def colour_env(request, monkeypatch):
    for name in _COLOUR_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in COLOUR[request.param].items():
        monkeypatch.setenv(name, value)
    return COLOUR[request.param]


def _one_line(err: str) -> str:
    lines = err.splitlines()
    assert len(lines) == 1, err
    return lines[0]


@pytest.mark.parametrize("argv, named", UNKNOWN.values(), ids=list(UNKNOWN))
def test_an_argument_this_build_lacks_exits_zero_with_one_line(argv, named, colour_env,
                                                                capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", io.StringIO(_payload(str(tmp_path / "a.py"))))

    code = main(argv)
    out, err = capsys.readouterr()

    line = _one_line(err)
    assert code == 0
    assert out == ""
    assert named in line and "newer crapkit" in line, line
    assert "crapkit doctor --plugin-root" in line, "the line names the next step"
    assert "usage:" not in err and "\x1b" not in err, err


def test_the_line_is_ascii_so_every_console_prints_it(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    main(["claude-hook", "--budget", "5"])

    assert _one_line(capsys.readouterr().err).isascii()


def test_the_page_quotes_the_line_the_hook_prints(capsys, monkeypatch):
    """docs/agent-json.md shows this exact argv and its line."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    page = (Path(__file__).resolve().parents[2] / "docs" / "agent-json.md").read_text(
        encoding="utf-8").splitlines()

    main(["claude-hook", "--protocol", "1", "--budget", "5"])

    shown = page[page.index("$ crapkit claude-hook --protocol 1 --budget 5") + 1]
    assert shown == _one_line(capsys.readouterr().err)


@pytest.mark.parametrize("protocol", ["2", "x", "99"])
def test_a_protocol_this_build_does_not_speak_stays_silent(protocol, colour_env, capsys,
                                                           monkeypatch, tmp_path):
    """`--protocol N` is the documented drift lever: read, and silent."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(_payload(str(tmp_path / "a.py"))))

    assert main(["claude-hook", "--protocol", protocol]) == 0
    assert capsys.readouterr() == ("", "")


def test_an_unknown_flag_on_another_subcommand_is_still_argparse_s_refusal(capsys):
    """A human typed it and a human reads the answer; only claude-hook is
    spoken to by a program that can be newer than this build."""
    with pytest.raises(SystemExit) as stop:
        main(["worklist", "--budget", "5"])

    assert stop.value.code == 2
    assert "unrecognized arguments: --budget 5" in capsys.readouterr().err


# --- the process Claude Code starts -------------------------------------------

TOML = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "pkg"\npaths = ["pkg"]\nlanguages = ["python"]\n'
_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c",
                    "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture(scope="module")
def breach_repo(tmp_path_factory) -> Path:
    """An edit that lands a function over the ceiling: under `--protocol 1` alone
    the hook exits 2 with its advisory, so a skipped judgement is visible."""
    repo = tmp_path_factory.mktemp("hook")
    (repo / "pkg").mkdir()
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (repo / "pkg" / "mod.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "pkg" / "mod.py").write_text(BREACH, encoding="utf-8", newline="\n")
    return repo


def _hook(repo: Path, argv: list[str], env: dict) -> subprocess.CompletedProcess:
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    child = {k: v for k, v in os.environ.items() if k not in _COLOUR_NAMES}
    child.update(env, PYTHONPATH=os.pathsep.join(p for p in (src, os.environ.get("PYTHONPATH")) if p))
    return subprocess.run([sys.executable, "-m", "crapkit", *argv],
                          input=_payload(str(repo / "pkg" / "mod.py")), cwd=repo.parent,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=child, timeout=300)


@pytest.mark.parametrize("colour", sorted(COLOUR))
@pytest.mark.parametrize("protocol", ["1", "2"])
def test_the_hook_process_exits_zero_with_one_plain_line(breach_repo, protocol, colour):
    done = _hook(breach_repo, ["claude-hook", "--protocol", protocol, "--budget", "5"],
                 COLOUR[colour])

    line = _one_line(done.stderr)
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert "`--budget 5`" in line and "\x1b" not in line, line
    assert "usage:" not in done.stderr and "advisory" not in done.stderr, done.stderr


def test_the_same_edit_without_the_new_flag_is_judged(breach_repo):
    """Control: the repo does breach, so the exit 0 above is the guard's."""
    done = _hook(breach_repo, ["claude-hook", "--protocol", "1"], {})

    assert done.returncode == 2, done.stderr
    assert done.stderr.startswith("crapkit advisory:"), done.stderr
