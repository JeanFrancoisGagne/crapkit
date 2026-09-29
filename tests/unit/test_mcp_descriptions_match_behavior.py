"""Each MCP tool description says what the call does, checked against the call.

A connected model reads the tool listing and nothing else, so a sentence there
is a promise. get_function_brief promised a miss "lists the file's functions
instead of erroring" while the miss came back isError true with the exit-1
error object. list_runs said it "spawns no git" and ran an ancestry check.
list_worklist said it read "the churn cache, not git" and asked git ls-files
and git status which scored files changed. list_coupled_files said it read git
log on every call when a warm call reads its cache. The `name` and `path`
properties named two name forms and forward slashes only, while both tools
resolve a start line, a twin handle, a backslash path and an absolute one.

Each test reads the description and runs the call it describes. The CLI the
server spawns runs in this process (the `exits` fixture), and GIT_TRACE, which
git inherits from this process, records every git command it ran.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
from pathlib import Path

import pytest

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401
from crapkit import mcp_server
from crapkit.cli import main


@pytest.fixture()
def exits(monkeypatch) -> list[int]:
    """The CLI the server spawns, run in this process, each exit kept."""
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
def scored(repo):  # noqa: F811
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    return repo


def _tool(name: str) -> dict:
    return next(t for t in mcp_server.TOOLS if t["name"] == name)


def _call(root: Path, tool: str, **arguments) -> dict:
    return mcp_server._call_tool(root, tool, {**arguments, "repo": str(root)})


def _git_commands(monkeypatch, tmp_path: Path, run) -> list[str]:
    """The git subcommands `run` started, from GIT_TRACE."""
    trace = tmp_path / "git-trace.txt"
    trace.unlink(missing_ok=True)
    monkeypatch.setenv("GIT_TRACE", str(trace))
    run()
    monkeypatch.delenv("GIT_TRACE")
    lines = trace.read_text(encoding="utf-8", errors="replace").splitlines() if trace.exists() else []
    marker = "trace: built-in: git "
    return [line.split(marker, 1)[1] for line in lines if marker in line]


# --- get_function_brief: a miss --------------------------------------------------

def test_a_brief_miss_is_the_tool_error_the_description_names(scored, exits):
    reply = _call(scored, "get_function_brief", path="src/app.ts", name="nope")

    assert reply["isError"] is True and "structuredContent" not in reply, reply
    text = reply["content"][0]["text"]
    assert '"exit": 1' in text and "it holds: dispatch, plain" in text, text
    description = _tool("get_function_brief")["description"]
    assert "instead of erroring" not in description
    assert "A miss is a tool error (isError true, the exit-1 error object) whose message " \
           "lists the file's functions." in description


# --- list_runs: one ancestry check ---------------------------------------------------

def test_list_runs_names_the_one_git_command_it_runs(scored, exits, monkeypatch, tmp_path):
    ran = _git_commands(monkeypatch, tmp_path,
                        lambda: _call(scored, "list_runs"))

    assert [c.split()[:2] for c in ran] == [["merge-base", "--is-ancestor"]], ran
    description = _tool("list_runs")["description"]
    assert "spawns no git" not in description
    assert "runs one git ancestry check to mark the baseline run" in description


# --- list_worklist: churn from the cache, changed files from git ---------------------

def test_list_worklist_names_the_git_reads_a_warm_call_makes(scored, exits, monkeypatch,
                                                               tmp_path):
    _call(scored, "list_worklist")  # the warm-up fills the churn cache

    ran = _git_commands(monkeypatch, tmp_path, lambda: _call(scored, "list_worklist"))

    words = {c.split()[0] for c in ran}
    assert "log" not in words, ran
    assert {"ls-files", "status"} <= words, ran
    description = _tool("list_worklist")["description"]
    assert "not git." not in description
    assert "Churn comes from a cache, not git log, and git ls-files and git status count " \
           "the scored files that changed (scored_changes)." in description


# --- list_coupled_files: git log only when the cache key moved ------------------------

def test_list_coupled_files_walks_git_log_only_on_a_cold_cache(scored, exits, monkeypatch,
                                                                 tmp_path):
    for cache in (scored / ".crapkit").glob("*"):
        if cache.name.startswith(("coupling-cache", "churn")):
            cache.unlink()

    cold = _git_commands(monkeypatch, tmp_path, lambda: _call(scored, "list_coupled_files"))
    warm = _git_commands(monkeypatch, tmp_path, lambda: _call(scored, "list_coupled_files"))

    assert "log" in {c.split()[0] for c in cold}, cold
    assert "log" not in {c.split()[0] for c in warm}, warm
    description = _tool("list_coupled_files")["description"]
    assert "once per call" not in description
    assert "It reads a cached ranking, not the scored run, and walks git log again only when " \
           "HEAD, the churn window or the clone depth moved." in description


# --- name and path: every form both tools resolve ----------------------------------

NAMES = ("plain ( x )", "plain", "pla", "13", "plain#1")


@pytest.mark.parametrize("tool", ["get_function_brief", "get_function_history"])
@pytest.mark.parametrize("name", NAMES)
def test_every_name_form_the_description_lists_resolves(scored, exits, tool, name):
    reply = _call(scored, tool, path="src/app.ts", name=name)

    assert reply["isError"] is False, reply
    assert "plain ( x )" in reply["content"][0]["text"]


def _paths(root: Path) -> list[str]:
    return ["src\\app.ts", str(root / "src" / "app.ts"), (root / "src" / "app.ts").as_posix()]


@pytest.mark.parametrize("tool", ["get_function_brief", "get_function_history"])
@pytest.mark.parametrize("which", range(3))
def test_every_path_form_the_description_lists_resolves(scored, exits, tool, which):
    reply = _call(scored, tool, path=_paths(scored)[which], name="plain")

    assert reply["isError"] is False, reply


@pytest.mark.parametrize("tool", ["get_function_brief", "get_function_history"])
def test_both_tools_describe_name_and_path_by_one_rule(tool):
    properties = _tool(tool)["properties"]

    assert properties["name"]["description"] == mcp_server._NAME_DESCRIPTION
    assert properties["path"]["description"] == mcp_server._PATH_DESCRIPTION
    for form in ("long_name get_next_item printed", "bare identifier", "fragment",
                 "the line the function starts on", "NAME#2", "(anonymous)#2",
                 "exact match first"):
        assert form in mcp_server._NAME_DESCRIPTION, form
    assert "next_item printed" not in mcp_server._NAME_DESCRIPTION.replace("get_next_item", "")
    assert mcp_server._PATH_DESCRIPTION == ("source file, repo-relative or absolute inside the "
                                            "repo; either slash works")
