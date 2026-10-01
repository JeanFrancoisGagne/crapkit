"""Each MCP tool description says what the call does, checked against the call.

A connected model reads the tool listing and nothing else, so a sentence there
is a promise. get_function_brief promised a miss "lists the file's functions
instead of erroring" while the miss came back isError true with the exit-1
error object. list_runs said it "spawns no git" and ran an ancestry check.
list_worklist said it read "the churn cache, not git" and asked git ls-files
and git status which scored files changed. list_coupled_files said it read git
log on every call when a warm call reads its cache. The `name` and `path`
properties named two name forms and forward slashes only, while both tools
resolve a start line, a twin handle, an absolute path and, on Windows, a
backslash one; on POSIX a backslash is a literal filename character.
get_ratchet_report said no marks file means zeros; a deleted marks file reads
as the marks its history last held.

Each of those fixes was checked in the one case it was written for. list_runs
then said it ran one ancestry check, and after a branch switch it ran two.
list_worklist said churn came from a cache, not git log, and its first call
after the first coverage run, and after each commit, walked git log.
get_ratchet_report said a repo that never committed a marks file reports
zeros, and a seeded file reports its marks before its first commit. list_runs,
get_trend and list_claims named the first coverage run as the point they stop
answering isError true, and an inventory run's store answered them first. The
server instructions named check_config and get_ratchet_report as the tools that
need no run, while list_coupled_files needs none either and an inventory run's
store answers six more.

Each test reads the description and runs the call it describes. The CLI the
server spawns runs in this process (the `exits` fixture), and GIT_TRACE, which
git inherits from this process, records every git command it ran.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, git, repo, seed_artifacts, template_repo  # noqa: F401
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


# --- list_runs: one ancestry check per commit down to the baseline -----------------

RUNS_GIT = ("Marking the baseline run costs one git ancestry check per trusted run's commit, "
            "newest first, down to it: one when it is the newest, on any branch, none with no "
            "trusted run.")


def _ancestry_checks(ran: list[str]) -> list[str]:
    return [c for c in ran if c.startswith("merge-base --is-ancestor")]


def _baseline_ids(reply: dict) -> list[int]:
    return [run["id"] for run in reply["structuredContent"]["runs"] if run["baseline"]]


def test_list_runs_asks_once_when_the_newest_run_is_the_baseline(scored, exits, monkeypatch,
                                                                  tmp_path):
    ran = _git_commands(monkeypatch, tmp_path, lambda: _call(scored, "list_runs"))

    assert [c.split()[:2] for c in ran] == [["merge-base", "--is-ancestor"]], ran
    description = _tool("list_runs")["description"]
    assert "runs one git ancestry check" not in description
    assert RUNS_GIT in description


def test_list_runs_after_a_branch_switch_asks_down_to_the_baseline(scored, exits, monkeypatch,
                                                                    tmp_path):
    git(scored, "checkout", "-q", "-b", "side")
    (scored / "src" / "extra.ts").write_text("export const a = 1;\n", encoding="utf-8")
    commit_all(scored, "side work")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(scored)]) == 0
    git(scored, "checkout", "-q", "-")
    replies = []

    ran = _git_commands(monkeypatch, tmp_path,
                        lambda: replies.append(_call(scored, "list_runs")))

    assert len(_ancestry_checks(ran)) == 2, ran
    assert _baseline_ids(replies[0]) == [1], replies[0]
    assert RUNS_GIT in _tool("list_runs")["description"]


def test_a_branch_switch_that_keeps_the_newest_run_in_head_asks_once(scored, exits,
                                                                     monkeypatch, tmp_path):
    """The description said a branch switch makes two or more checks. A new
    branch at the same commit still holds the newest trusted run's commit, so
    the first check finds the baseline."""
    git(scored, "checkout", "-q", "-b", "side")
    replies = []

    ran = _git_commands(monkeypatch, tmp_path,
                        lambda: replies.append(_call(scored, "list_runs")))

    assert len(_ancestry_checks(ran)) == 1, ran
    assert _baseline_ids(replies[0]) == [1], replies[0]
    description = _tool("list_runs")["description"]
    assert "two or more after a branch switch" not in description
    assert RUNS_GIT in description


def test_list_runs_with_no_trusted_run_asks_git_nothing(repo, exits, monkeypatch,  # noqa: F811
                                                        tmp_path):
    assert main(["inventory", "--repo", str(repo)]) == 0
    replies = []

    ran = _git_commands(monkeypatch, tmp_path, lambda: replies.append(_call(repo, "list_runs")))

    assert ran == [], ran
    assert [r["kind"] for r in replies[0]["structuredContent"]["runs"]] == ["inventory"]
    assert _baseline_ids(replies[0]) == []
    assert RUNS_GIT in _tool("list_runs")["description"]


# --- list_runs, get_trend, list_claims: the error is a missing store ----------------

NO_STORE = {
    "list_runs": "With no crapkit.toml above it, or no snapshot store yet, it answers isError "
                 "true with the command to run.",
    "get_trend": "One with no snapshot store yet answers isError true with the setup pointer.",
    "list_claims": "No crapkit.toml above it answers an init pointer, and one with no snapshot "
                   "store yet a coverage pointer, both as isError true.",
}


@pytest.mark.parametrize("tool", sorted(NO_STORE))
def test_an_inventory_runs_store_answers_before_any_coverage_run(repo, exits, tool):  # noqa: F811
    """`crapkit inventory` makes the store; the CLI refuses only when there is
    none, whatever kind of run it holds."""
    assert _call(repo, tool)["isError"] is True
    assert main(["inventory", "--repo", str(repo)]) == 0

    assert _call(repo, tool)["isError"] is False
    description = _tool(tool)["description"]
    for said in ("before the first coverage run", "An unmeasured one", "a checkout never scored"):
        assert said not in description, said
    assert NO_STORE[tool] in description


# --- list_worklist: churn walks git log once per HEAD --------------------------------

WORKLIST_CHURN = ("Churn is cached per HEAD: after a commit, or on first use, the next read "
                  "walks git log. git ls-files and git status count changed scored files "
                  "(scored_changes).")


def _words(ran: list[str]) -> set[str]:
    return {c.split()[0] for c in ran}


def test_list_worklist_walks_git_log_once_per_head(scored, exits, monkeypatch, tmp_path):
    def call():
        return _git_commands(monkeypatch, tmp_path, lambda: _call(scored, "list_worklist"))

    first, warm = call(), call()
    (scored / "src" / "extra.ts").write_text("export const a = 1;\n", encoding="utf-8")
    commit_all(scored, "one more commit")
    moved, again = call(), call()

    assert "log" in _words(first), first
    assert "log" not in _words(warm) and {"ls-files", "status"} <= _words(warm), warm
    walked = [c for c in moved if c.startswith("log ")]
    assert len(walked) == 1 and ".." in walked[0], moved  # only the commits since the cached HEAD
    assert "log" not in _words(again), again
    description = _tool("list_worklist")["description"]
    assert "not git log" not in description
    assert WORKLIST_CHURN in description


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


def _paths(root: Path) -> dict[str, str]:
    return {"backslash": "src\\app.ts", "absolute": str(root / "src" / "app.ts"),
            "absolute-posix": (root / "src" / "app.ts").as_posix()}


ON_POSIX = sys.platform != "win32"
BACKSLASH_ON_WINDOWS = pytest.param(
    "backslash", marks=pytest.mark.skipif(ON_POSIX, reason="a backslash is a separator on Windows only"))


@pytest.mark.parametrize("tool", ["get_function_brief", "get_function_history"])
@pytest.mark.parametrize("form", [BACKSLASH_ON_WINDOWS, "absolute", "absolute-posix"])
def test_every_path_form_the_description_lists_resolves(scored, exits, tool, form):
    reply = _call(scored, tool, path=_paths(scored)[form], name="plain")

    assert reply["isError"] is False, reply


@pytest.mark.skipif(not ON_POSIX, reason="Windows reads a backslash as a separator")
@pytest.mark.parametrize("tool", ["get_function_brief", "get_function_history"])
def test_a_backslash_path_names_a_literal_file_on_posix(scored, exits, tool):
    """repopath.argument: on POSIX a backslash is a filename character, so
    src\\app.ts is not src/app.ts, as the description now says."""
    reply = _call(scored, tool, path="src\\app.ts", name="plain")

    assert reply["isError"] is True, reply


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
                                            "repo; a backslash separates folders on Windows only")


# --- get_ratchet_report: the file on disk, then its history ------------------------

REPORT_OPEN = ("It reads the marks file and its git history only, and ages count from the "
               "newest commit touching that file, never the clock. Open marks are the file's "
               "rows, committed or not. A deleted or emptied file reports the marks its history "
               "last held as open, none repaid, and no file with no history reports zeros.")
REPORT_GIT = {"log", "diff-tree", "ls-tree", "cat-file"}


def _write_marks(root: Path) -> None:
    from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

    marks = [RatchetEntry("src/app.ts", "dispatch ( kind )", 60.0)]
    (root / "crapkit-ratchet.tsv").write_text(dump_ratchet(marks, stamp=metric_version()),
                                              encoding="utf-8", newline="\n")


def _report_and_git(root: Path, monkeypatch, tmp_path) -> tuple[dict, list[str]]:
    replies = []
    ran = _git_commands(monkeypatch, tmp_path,
                        lambda: replies.append(_call(root, "get_ratchet_report")))
    return replies[0]["structuredContent"], ran


def _counts(report: dict) -> tuple:
    return report["open"], report["dropped_total"], report["uncommitted"]


def test_a_deleted_marks_file_reports_what_the_description_says(scored, exits, monkeypatch,
                                                                 tmp_path):
    _write_marks(scored)
    commit_all(scored, "a mark")
    git(scored, "rm", "-q", "crapkit-ratchet.tsv")
    commit_all(scored, "delete it")

    report, ran = _report_and_git(scored, monkeypatch, tmp_path)

    assert _counts(report) == (1, 0, 0), report
    assert _words(ran) <= REPORT_GIT, ran
    description = _tool("get_ratchet_report")["description"]
    assert "no marks file means zeros" not in description
    assert "never committed a marks file reports zeros" not in description
    assert REPORT_OPEN in description


def test_a_seeded_file_never_committed_reports_its_marks_open(repo, exits, monkeypatch,  # noqa: F811
                                                              tmp_path):
    _write_marks(repo)

    report, ran = _report_and_git(repo, monkeypatch, tmp_path)

    assert _counts(report) == (1, 0, 1), report
    assert _words(ran) <= REPORT_GIT, ran
    assert REPORT_OPEN in _tool("get_ratchet_report")["description"]


def test_no_file_and_no_history_reports_zeros(repo, exits, monkeypatch, tmp_path):  # noqa: F811
    report, ran = _report_and_git(repo, monkeypatch, tmp_path)

    assert (_counts(report), report["oldest"]) == ((0, 0, 0), []), report
    assert _words(ran) <= REPORT_GIT, ran
    assert REPORT_OPEN in _tool("get_ratchet_report")["description"]


# --- the server instructions: what each tool needs before it answers ----------------
# The instructions said most tools need a coverage run, and named check_config and
# get_ratchet_report as the two that need none. list_coupled_files answers before
# any run too, and an inventory run's store answers six more. Each state below
# calls all twelve tools and holds the answers to the sentence.

NEEDS = ("Every tool needs crapkit init. check_config, get_ratchet_report and "
         "list_coupled_files need no run, get_next_item, get_function_brief and check_gate "
         "need a coverage run, and the other six need a snapshot store, which crapkit "
         "inventory or crapkit coverage makes. A tool called before what it needs answers "
         "with a one-line pointer instead of data.")
NO_RUN = {"check_config", "get_ratchet_report", "list_coupled_files"}
COVERAGE_RUN = {"get_next_item", "get_function_brief", "check_gate"}
TOOL_ARGUMENTS = {"get_function_brief": {"path": "src/app.ts", "name": "dispatch"},
                  "get_function_history": {"path": "src/app.ts", "name": "dispatch"},
                  "check_gate": {"path": "src/app.ts"}}


def _answers(root: Path) -> tuple[set[str], set[str]]:
    """The tools that answered with data, and each refusal's text."""
    answered, refusals = set(), set()
    for tool in mcp_server.TOOLS:
        reply = _call(root, tool["name"], **TOOL_ARGUMENTS.get(tool["name"], {}))
        if reply["isError"]:
            refusals.add(reply["content"][0]["text"].strip())
        else:
            answered.add(tool["name"])
    return answered, refusals


def _one_line_each(refusals: set[str]) -> bool:
    return all("\n" not in text for text in refusals)


def test_every_tool_needs_init_first(tmp_path, exits):
    git(tmp_path, "init", "-q")

    answered, refusals = _answers(tmp_path)

    assert answered == set(), answered
    assert _one_line_each(refusals) and all("crapkit init" in text for text in refusals), refusals
    assert NEEDS in mcp_server._INSTRUCTIONS


def test_before_any_run_three_tools_answer(repo, exits):  # noqa: F811
    answered, refusals = _answers(repo)

    assert answered == NO_RUN, answered
    assert _one_line_each(refusals), refusals
    assert NEEDS in mcp_server._INSTRUCTIONS
    assert "check_config and get_ratchet_report need no run" not in mcp_server._INSTRUCTIONS


def test_an_inventory_store_answers_all_but_the_three_that_need_a_coverage_run(repo, exits):  # noqa: F811
    assert main(["inventory", "--repo", str(repo)]) == 0

    answered, refusals = _answers(repo)

    every = {tool["name"] for tool in mcp_server.TOOLS}
    assert answered == every - COVERAGE_RUN, answered
    assert len(every - NO_RUN - COVERAGE_RUN) == 6 and len(every) == 12
    assert _one_line_each(refusals) and all("crapkit coverage" in text for text in refusals), \
        refusals
    assert NEEDS in mcp_server._INSTRUCTIONS
