r"""An MCP call names its file and its repo in whatever spelling the model
wrote, and the server answers as it answers git's spelling.

A model on Windows writes C:\, c:\, C:/ and /c/ paths, in the letter case it
remembers, and its `repo` argument the same way. check_gate answered gate.ok
true with 0 functions judged for a breach spelled SRC\app.ts, answered a /c/...
path or repo with a refusal, and get_next_item's exclude compared pkg\legacy and
PKG/Legacy with git's text and handed the excluded directory out. The server
walks the call's repo, builds the CLI command and spawns it. Here the spawn
runs that command in this process, so each spelling still crosses the
server's argument checks, its repo walk and the CLI's path reader; two calls
spawn for real, so the process boundary is held too.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
from pathlib import Path

import pytest

from cli_inproc_repo import add_knotty, commit_all, repo, seed_artifacts, template_repo  # noqa: F401
from crapkit import mcp_server
from crapkit.cli import main

from path_spellings import (SPELLINGS, admin_share, linked_checkout, lower_drive, need,
                            need_case_sensitive, only_posix, spelled)


@pytest.fixture()
def exits(monkeypatch) -> list[int]:
    """The CLI the server spawns, run in this process: the argv the server
    built goes to main(), and its stdout, stderr and exit come back as the
    child's would. Each exit is kept."""
    codes: list[int] = []

    def run(argv, **_kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv[3:])  # [python, -m, crapkit, *the command]
        codes.append(code)
        return subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())

    monkeypatch.setattr(mcp_server, "run_owned", run)
    return codes


@pytest.fixture()
def breached(repo):  # noqa: F811
    """A measured repo whose src/app.ts now holds a function over the ceiling."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    return repo


def _gate(root: Path, path: str, repo_arg: str) -> dict:
    reply = mcp_server._call_tool(root, "check_gate", {"path": path, "repo": repo_arg})
    assert reply["isError"] is False, reply
    return reply["structuredContent"]


def _verdict(payload: dict) -> tuple:
    """What a caller reads off check_gate: the gate object, keys included, and
    the path each function was scored under."""
    return payload["gate"], sorted({f["path"] for f in payload["functions"]})


@pytest.mark.parametrize("which", SPELLINGS)
def test_check_gate_answers_every_path_spelling_as_it_answers_git_s(breached, exits, which):
    control = _gate(breached, "src/app.ts", str(breached))

    answer = _gate(breached, spelled(which, breached), str(breached))

    assert control["gate"]["ok"] is False and control["gate"]["breaches"], control
    assert _verdict(answer) == _verdict(control)
    assert exits == [6, 6]


def _msys(root: Path, prefix: str) -> str:
    return prefix + root.resolve().as_posix()[2:]


# id -> (what the OS needs, the spelling of the checkout's root or a directory in it)
REPO_SPELLINGS = {
    "native": ("", lambda root: str(root)),
    "trailing-separator": ("", lambda root: str(root) + os.sep),
    "subdirectory": ("", lambda root: str(root / "src")),
    "linked-checkout": ("", lambda root: str(linked_checkout(root))),
    "forward-slashes": ("windows", lambda root: root.as_posix()),
    "lower-drive": ("windows", lower_drive),
    "msys": ("windows", lambda root: _msys(root, "/c")),
    "wsl": ("windows", lambda root: _msys(root, "/mnt/c")),
    "admin-share": ("windows", admin_share),
    "upper-cased": ("windows case", lambda root: str(root).upper()),
}


@pytest.mark.parametrize("which", REPO_SPELLINGS)
def test_check_gate_answers_every_repo_spelling_as_it_answers_the_root(breached, exits, which):
    spec, spell = REPO_SPELLINGS[which]
    need(spec, breached)
    control = _gate(breached, "src/app.ts", str(breached))

    answer = _gate(breached, "src/app.ts", spell(breached))

    assert _verdict(answer) == _verdict(control)
    assert exits == [6, 6]


@pytest.mark.parametrize("which", ["absolute", "msys"])
def test_a_spawned_check_gate_reads_the_spelling_the_model_wrote(breached, which):
    """The server's own spawn: the path and the repo cross a real process
    boundary as the argv the server built."""
    path = spelled(which, breached)
    repo_arg = _msys(breached, "/c") if which == "msys" else str(breached) + os.sep

    answer = _gate(breached, path, repo_arg)

    assert answer["gate"]["ok"] is False
    assert [b["path"] for b in answer["gate"]["breaches"]] == ["src/app.ts"]


# --- get_next_item exclude -------------------------------------------------------

@pytest.fixture()
def queued(repo):  # noqa: F811
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    return repo


def _next_paths(root: Path, exclude: list[str]) -> list[str]:
    reply = mcp_server._call_tool(root, "get_next_item", {"top": 5, "exclude": exclude})
    assert reply["isError"] is False, reply
    return [item["path"] for item in reply["structuredContent"].get("items", [])]


EXCLUDES = {
    "forward": ("", "src/app"),
    "dot-slash": ("", "./src/app"),
    "directory": ("", "src/"),
    "backslash": ("windows", "src\\app"),
    "trailing-backslash": ("windows", "src\\"),
    "dot-backslash": ("windows", ".\\src\\app"),
    "case": ("case", "SRC/App"),
    "case-backslash": ("windows case", "SRC\\App"),
}


@pytest.mark.parametrize("which", EXCLUDES)
def test_get_next_item_skips_what_the_exclude_names_in_any_spelling(queued, exits, which):
    spec, pattern = EXCLUDES[which]
    need(spec, queued)
    assert "src/app.ts" in _next_paths(queued, [])  # the control hands it out

    assert "src/app.ts" not in _next_paths(queued, [pattern])


@only_posix
@pytest.mark.parametrize("pattern", ["src\\app", "src\\app\\", "src\\"])
def test_posix_reads_an_exclude_backslash_as_part_of_a_name(queued, exits, pattern):
    """On POSIX `src\\app` names no directory: git's src/app.ts stays queued."""
    assert "src/app.ts" in _next_paths(queued, [pattern])


def test_an_exclude_in_another_case_stays_literal_on_a_case_sensitive_disk(queued, exits):
    need_case_sensitive(queued)

    assert "src/app.ts" in _next_paths(queued, ["SRC/App"])
