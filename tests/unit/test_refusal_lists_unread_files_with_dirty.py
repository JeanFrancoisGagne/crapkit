"""Every command that refuses a file whose name is not UTF-8 lists it in its
--json error object as `unread_files`, each item {path, reason, dirty}: the
shape `rescore --gate --json` lists unread files in. verify gives such a name
a verdict instead: its payload lists the name as an `unreadable_name` finding,
with the same `path` and `dirty`.

The error object's items carried `path` and `reason` alone, so a wrapper that
reads both surfaces met two shapes for one finding. `dirty` has verify's
meaning: true when the file has uncommitted edits or git does not track it.
A refusal reads it from git on its way out; the command never judged the file.

A command refuses such a name two ways: a file argument naming one on disk
(rescore, brief, explain, mutate, claims release, ratchet move), and a scan
that meets one a scope takes in git's index (inventory, coverage, verify,
doctor). The argument rows write the file straight to disk: on POSIX a name
holding byte e9, on NTFS a lone surrogate, so they run on every OS. The scan
rows put the name into the index through `git update-index --index-info`, the
one route that works on every OS; Git for Windows cannot check such a name
out, so the committed-and-clean row runs on POSIX alone.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, git, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from crapkit.cli import main
from name_bytes import NOT_UTF8_NAMES

REASON = ("its name is not UTF-8, and crapkit reads every path as UTF-8: "
          "rename it (git mv) to a UTF-8 name")
# verify's unreadable_name item gives the scan's own sentence as its reason.
VERIFY_STOP = ("{shown} is in scope '{scope}', but git names it in bytes that are not UTF-8 and crapkit "
               "reads every path as UTF-8; a file a scope takes is refused, not left out, so no gate passes "
               "it unread: rename it (git mv) to a UTF-8 name")
NAME = "src/caf\udce9.ts"   # b"src/caf\xe9.ts" on POSIX, a lone surrogate on NTFS
SHOWN = "src/caf\\xe9.ts"
# An expression-arrow body the TypeScript reader refuses: a file no reader reads.
ARROW = "\nexport const pick = (x: number) => convert<string, number>(x);\n"
POSIX_NAME = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a POSIX file system that stores any byte in a name")


@pytest.fixture()
def measured(repo, capsys):
    """A scored run to answer from, and a mutation command for mutate."""
    toml = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        toml.replace("[crapkit]\n", '[crapkit]\nmutation_command = "python -c pass"\n', 1),
        encoding="utf-8")
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def _run(repo, capsys, *argv: str) -> tuple[int, dict, str]:
    """The exit, the printed --json payload, and stderr."""
    command, *rest = argv
    code = main([command, "--json", "--repo", str(repo), *rest])
    out, err = capsys.readouterr()
    return code, json.loads(out), err


# --- a file argument naming the file ------------------------------------------

ARGUMENT_COMMANDS = [
    pytest.param(("rescore", "--", NAME), id="rescore"),
    pytest.param(("rescore", "--gate", "--", NAME), id="rescore-gate"),
    pytest.param(("brief", NAME, "knotty"), id="brief"),
    pytest.param(("explain", NAME, "knotty"), id="explain"),
    pytest.param(("mutate", "--files", NAME), id="mutate"),
    pytest.param(("claims", "release", NAME, "knotty"), id="claims-release"),
    pytest.param(("ratchet", "move", NAME, "src/renamed.ts"), id="ratchet-move"),
]


@pytest.mark.parametrize("argv", ARGUMENT_COMMANDS)
@NOT_UTF8_NAMES
def test_an_untracked_file_argument_is_listed_dirty(measured, capsys, argv):
    (measured / NAME).write_text(KNOTTY, encoding="utf-8")

    code, printed, err = _run(measured, capsys, *argv)
    error = printed["error"]

    assert code == 3, err
    assert (error["exit"], error["kind"]) == (3, "config")
    assert error["unread_files"] == [{"path": SHOWN, "reason": REASON, "dirty": True}]


@POSIX_NAME
@pytest.mark.parametrize("argv", ARGUMENT_COMMANDS)
@NOT_UTF8_NAMES
def test_a_committed_file_argument_git_holds_as_is_is_listed_clean(measured, capsys, argv):
    (measured / NAME).write_text(KNOTTY, encoding="utf-8")
    commit_all(measured, "a Latin-1 name")

    code, printed, err = _run(measured, capsys, *argv)
    error = printed["error"]

    assert code == 3, err
    assert error["unread_files"] == [{"path": SHOWN, "reason": REASON, "dirty": False}]


@POSIX_NAME
@NOT_UTF8_NAMES
def test_a_committed_file_argument_edited_since_is_listed_dirty(measured, capsys):
    (measured / NAME).write_text(KNOTTY, encoding="utf-8")
    commit_all(measured, "a Latin-1 name")
    (measured / NAME).write_text(KNOTTY + "\n// edited\n", encoding="utf-8")

    code, printed, err = _run(measured, capsys, "brief", NAME, "knotty")
    error = printed["error"]

    assert code == 3, err
    assert error["unread_files"] == [{"path": SHOWN, "reason": REASON, "dirty": True}]


# --- a scan that meets the name in git's index ----------------------------------

SCAN_COMMANDS = [
    pytest.param(("inventory",), id="inventory"),
    pytest.param(("coverage", "--reuse-artifacts"), id="coverage"),
    pytest.param(("verify", "--reuse-artifacts"), id="verify"),
    pytest.param(("doctor",), id="doctor"),
]
LATIN1 = (b"src/caf\xe9.ts", b"src/o\x92brien.ts")


def _index(repo, names: tuple[bytes, ...]) -> None:
    """Each name in the index under its own bytes, and on disk where the OS
    can name it."""
    for name in names:
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=KNOTTY.encode(),
                              capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-index", "--add", "-z", "--index-info"], cwd=repo,
                       input=b"100644 " + blob + b"\t" + name + b"\0", capture_output=True, check=True)
        if sys.platform != "win32":
            (repo / name.decode("utf-8", "surrogateescape")).write_text(KNOTTY, encoding="utf-8")


def _listed(printed: dict) -> list[tuple[str, bool]]:
    """(path, dirty) for each refused name. verify meeting such a name prints
    its own payload, with no `error` key: it lists the names as unreadable_name
    findings, whose reason is the scan's sentence naming the file, its scope and
    the rename. Every other command lists them in its error object's
    unread_files, as {path, reason, dirty}."""
    if "findings" in printed:
        names = [item for item in printed["findings"] if item["kind"] == "unreadable_name"]
        assert all(item["reason"] == VERIFY_STOP.format(shown=item["path"], scope=item["scope"])
                   for item in names), names
        return [(item["path"], item["dirty"]) for item in names]
    error = printed["error"]
    assert {tuple(sorted(item)) for item in error["unread_files"]} == {("dirty", "path", "reason")}
    assert {item["reason"] for item in error["unread_files"]} == {REASON}
    return [(item["path"], item["dirty"]) for item in error["unread_files"]]


@pytest.mark.parametrize("argv", SCAN_COMMANDS)
@NOT_UTF8_NAMES
def test_a_staged_name_a_scope_takes_is_listed_dirty(measured, capsys, argv):
    _index(measured, LATIN1)

    code, printed, err = _run(measured, capsys, *argv)

    assert code == 3, err
    assert _listed(printed) == [(SHOWN, True), ("src/o\\x92brien.ts", True)]


@pytest.mark.parametrize("argv", SCAN_COMMANDS)
@NOT_UTF8_NAMES
def test_a_committed_name_the_working_tree_lacks_is_listed_dirty(measured, capsys, argv):
    """What every Git for Windows clone of such a commit holds: the index
    names the file, the checkout could not, so git reads it deleted."""
    _index(measured, LATIN1[:1])
    git(measured, "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "a Latin-1 name")  # the index as it stands: `add -A` drops the name
    (measured / NAME).unlink(missing_ok=True)

    code, printed, err = _run(measured, capsys, *argv)

    assert code == 3, err
    assert _listed(printed) == [(SHOWN, True)]


@POSIX_NAME
@pytest.mark.parametrize("argv", SCAN_COMMANDS)
@NOT_UTF8_NAMES
def test_a_committed_name_git_holds_as_is_is_listed_clean(measured, capsys, argv):
    _index(measured, LATIN1)
    commit_all(measured, "two Latin-1 names")
    (measured / "src" / "o\udc92brien.ts").write_text(KNOTTY + "\n// edited\n", encoding="utf-8")

    code, printed, err = _run(measured, capsys, *argv)

    assert code == 3, err
    assert _listed(printed) == [(SHOWN, False), ("src/o\\x92brien.ts", True)]


def test_a_tree_git_cannot_list_reads_every_name_untracked(tmp_path):
    """A refusal outside any repository still answers: nothing is tracked."""
    from crapkit.cli._shared import _uncommitted

    assert _uncommitted(tmp_path, [NAME]) == frozenset({NAME})


@NOT_UTF8_NAMES
def test_the_error_object_and_the_gate_verdict_share_one_item_shape(measured, capsys):
    """The gate verdict lists a changed unread file; the refusal lists a name
    it cannot read. One reader parses both."""
    (measured / NAME).write_text(KNOTTY, encoding="utf-8")
    error = _run(measured, capsys, "rescore", "--gate", "--", NAME)[1]["error"]
    (measured / NAME).unlink()
    with open(measured / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(ARROW)
    code = main(["rescore", "--gate", "--json", "--repo", str(measured), "--", "src/app.ts"])
    gate = json.loads(capsys.readouterr().out)["gate"]

    assert code == 6
    assert {tuple(sorted(item)) for item in gate["unread_files"] + error["unread_files"]} == {
        ("dirty", "path", "reason")}
    assert all(isinstance(item["dirty"], bool) for item in gate["unread_files"] + error["unread_files"])


# --- an MCP tool handed such a path --------------------------------------------

@pytest.mark.parametrize("tool", ["get_function_brief", "get_function_history"])
@NOT_UTF8_NAMES
def test_an_mcp_tool_handed_such_a_path_answers_the_clis_refusal(measured, tool):
    """On Windows the child the server started read the name off argv as
    U+FFFD and answered `no function named 'knotty' in src/caf\\ufffd.ts` at
    exit 1, with no `unread_files`. The server answers the CLI's refusal itself."""
    from crapkit import mcp_server

    (measured / NAME).write_text(KNOTTY, encoding="utf-8")

    result = mcp_server._call_tool(measured, tool, {"path": NAME, "name": "knotty"})

    assert result["isError"] is True
    error = json.loads(result["content"][0]["text"])["error"]
    assert (error["exit"], error["kind"]) == (3, "config")
    assert error["unread_files"] == [{"path": SHOWN, "reason": REASON, "dirty": True}]
