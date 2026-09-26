"""MCP clients across an upgrade: the tool names a 0.5.x client pinned, and
the server a running session keeps until its client restarts it.

lin-up-pyextra-0.5.1 upgrades a `crapkit[py]` install from 0.5.1 and calls the
candidate's server by every name the 0.6.0 rename table (CHANGELOG, read from
the stamped tree) says a 0.5.x client pinned. Each old name has to answer with
its new name, since the client's allowlist is what the user edits.

lin-up-profiles-restart and win-up-profiles-restart hold a 0.7.6 session open
across the upgrade, the way every stdio client keeps the server it started:
the session keeps reporting 0.7.6 and a fresh one reports the candidate. On
Windows the held session also holds crapkit.exe, so the pip upgrade meets it.
The per-harness restart actions (Codex's tool catalog, VS Code's server hash)
come from the harness profiles once tests/deploy/profiles/ holds them.
"""
from __future__ import annotations

import re

import pytest

from kit import docsnip, state, state_manifest
from kit.cells import cell
from kit.mcp_client import McpClient, McpError
from kit.state import output

PACKET = "deploy-upgrade"
RENAME_ROW = re.compile(r"^\|\s*`(?P<old>[a-z_]+)`\s*\|\s*`(?P<new>[a-z_]+)`\s*\|\s*$")
PIP_EXTRA = "pip with the Python coverage extra"


def renames() -> dict[str, str]:
    """CHANGELOG's 0.5.x -> 0.6.0 tool table, from the stamped tree."""
    text = (docsnip.root() / "CHANGELOG.md").read_text(encoding="utf-8")
    rows = (RENAME_ROW.match(line) for line in text.splitlines())
    table = {row["old"]: row["new"] for row in rows if row}
    assert "worklist" in table, "CHANGELOG lost the 0.6.0 rename table"
    return table


def text_of(result: dict) -> str:
    return "\n".join(part.get("text", "") for part in result.get("content", []))


def old_names_answered(box, repo) -> dict[str, str]:
    """What the candidate answers to each name a 0.5.x client pinned."""
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        return {old: text_of(client.call(old, {})) for old in renames()}


@cell("lin-up-pyextra-0.5.1", channel="pip [py]", harness="spec client, 0.5.x names",
      scenario="upgrade: `worklist` answers with its new name", use_cases="MCP renames",
      os="linux", image="core", cadence="nightly", real_cli=False)
def test_lin_up_pyextra_0_5_1(box, templates, candidate):
    source = state.build(box, state.source_version("0.5.1"), cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={source.version}")
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        assert "worklist" in {tool["name"] for tool in client.tools()}

    state.walk(box, repo, candidate, source, state.upgrade_line(PIP_EXTRA))
    answers = old_names_answered(box, repo)
    box.transcript.attach("old-tool-names", answers)
    missing = {old: new for old, new in renames().items() if new not in answers[old]}
    assert not missing, f"old names answered without their new name: {missing}"


HELD_FIRST_CALL = pytest.mark.xfail(
    strict=True, raises=state.KnownBug,
    reason="deploy-bug deploy-upgrade-8: a 0.7.6 MCP session whose first tool call comes after the upgrade "
           "answers JSON-RPC -32603 naming no restart: 'measurement owner stopped before confirming ownership' on "
           "Linux, 'TypeError: _operation() takes 2 positional arguments but 3 were given' on Windows")


def first_call_after(client, bugs) -> None:
    """The held session's first tool call after the upgrade: an answer, or an
    error that tells the user to restart the session."""
    try:
        answer = client.call("list_claims")
    except McpError as error:
        bugs.check("restart" in str(error).lower(), f"deploy-upgrade-8: the held session answered {error}")
        return
    assert not answer.get("isError") or "restart" in text_of(answer).lower(), answer


def held_session(box, templates, candidate, upgrade, after_close=state.nothing) -> None:
    """A 0.7.6 session open across `upgrade(box, repo)`: it keeps its
    serverInfo and catalog, its first call answers or names the restart, and
    once it closes (`after_close(box, repo)` runs then) a fresh session reports
    the candidate. A reported bug is raised last."""
    bugs, old = state.Bugs(), state.source_version("0.7.6")
    source = state.build(box, old, cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={old}")
    before = state_manifest.take(repo)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        assert client.initialize()["serverInfo"]["version"] == old
        catalog = [tool["name"] for tool in client.tools()]
        upgrade(box, repo)
        assert [tool["name"] for tool in client.tools()] == catalog, "the held session changed its catalog"
        first_call_after(client, bugs)
    after_close(box, repo)
    assert state.server_info(box, repo)["version"] == candidate.version, "a fresh session still runs 0.7.6"
    state.kept(box, repo, source)
    state_manifest.check(box, before, state_manifest.take(repo))
    bugs.raise_any()


def pip_extra_upgrade(box, repo, expect: int | None = 0):
    return state.run_line(box, repo, state.upgrade_line(PIP_EXTRA), expect=expect)


@HELD_FIRST_CALL
@cell("lin-up-profiles-restart", channel="per-harness config", harness="stdio clients (profiles)",
      scenario="upgrade: stale serverInfo until restart action", use_cases="MCP lifecycle",
      os="linux", image="core", cadence="nightly", real_cli=False)
def test_lin_up_profiles_restart(box, templates, candidate):
    held_session(box, templates, candidate, pip_extra_upgrade)


LOCKED = ("WinError 32", "being used by another process", "os error 32")


def rerun_if_locked(attempts: dict, candidate):
    """docs/upgrading.md "Windows launcher locks": a failed upgrade is rerun
    once the server stops, the error it failed with must be the lock, and
    step 3's `crapkit --version` then names the candidate."""
    def after_close(box, repo):
        held = attempts["held"]
        box.transcript.note(f"the upgrade under a held crapkit.exe exited {held.exit}")
        if held.exit:
            assert [word for word in LOCKED if word in output(held)], output(held)
            pip_extra_upgrade(box, repo)
        assert candidate.version in box.run(["crapkit", "--version"], expect=0).stdout
    return after_close


@HELD_FIRST_CALL
@cell("win-up-profiles-restart", channel="per-harness config", harness="stdio clients (profiles)",
      scenario="upgrade: a held session keeps crapkit.exe until its restart action", use_cases="launcher lock",
      os="windows", image=None, cadence="nightly", real_cli=False)
def test_win_up_profiles_restart(box, templates, candidate):
    attempts = {}

    def upgrade(box, repo):
        attempts["held"] = pip_extra_upgrade(box, repo, expect=None)

    held_session(box, templates, candidate, upgrade, after_close=rerun_if_locked(attempts, candidate))
