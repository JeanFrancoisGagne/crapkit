"""Upgrading on Windows while an MCP client holds crapkit.exe.

A user on the newest release (N-1) has an agent session whose server is
`crapkit.exe mcp`, started from the pip venv or the uv tool install. After
that server has answered initialize and one tools/call, the user runs the
upgrade line docs/upgrading.md gives for the installer. The page says the
first attempt fails with Windows error 32 and gives three steps: stop the
server, rerun the same command, check `crapkit --version`. The cell follows
them. When the first attempt succeeds instead, the cell is an xfail that
names the installer versions it ran, because the page then describes a lock
this machine did not reproduce.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kit import installers, repos, wheels
from kit.cells import cell
from kit.installers import said
from kit.mcp_client import McpClient

PACKET = "deploy-channels"
GUIDE = "docs/upgrading.md"
TABLE = "Upgrading Crapkit"
LOCKS = "Windows launcher locks"
LOCKED = ("WinError 32", "os error 32", "being used by another process")


def upgrade_line(prefix: str) -> str:
    """The upgrade command docs/upgrading.md's table gives for one installer."""
    return installers.inline(GUIDE, TABLE, prefix)


def _pip_n1(box) -> installers.Install:
    return installers.pip_venv(box, "3.12", line=f"pip install crapkit=={wheels.n_minus_1()}",
                               channel="pip venv at N-1")


def _uv_tool_n1(box) -> installers.Install:
    """`uv tool install crapkit` on the day N-1 was the newest release: the
    wheelhouse alone answers, so the install carries no version pin that
    `uv tool upgrade` would keep."""
    return installers.uv_tool(box, env={"UV_FIND_LINKS": box.toolchain["wheelhouse"]})


def _held_upgrade(box, install: installers.Install, repo: Path, line: str) -> dict:
    """The upgrade run while the client's server holds the launcher, after
    initialize and one tools/call have returned; then the server stopped."""
    with McpClient.in_box(box, [str(install.launcher), "mcp"], cwd=repo) as client:
        info = client.initialize()
        called = client.call("check_config", {})
        first = box.script(line, cwd=box.root, note="the upgrade, run while the server holds crapkit.exe")
        client.close()
    return {"info": info, "called": called, "first": first}


def _tools(box) -> str:
    pip = box.run([box.toolchain.python("3.12"), "-m", "pip", "--version"]).stdout.split(" from ")[0]
    uv = box.run(["uv", "--version"]).stdout.strip()
    return f"{pip}, {uv}"


def _guide_steps(box, install: installers.Install, line: str) -> object:
    """Steps 2 and 3 of the page, the server already stopped: rerun, check the version."""
    box.script(line, cwd=box.root, expect=0, note="step 2: rerun the same upgrade command")
    return box.run([str(install.launcher), "--version"], expect=0)


def locked_upgrade(box, templates, install: installers.Install, line: str) -> dict:
    repo = repos.checkout(box, "py-pytest", cache=templates)
    seen = _held_upgrade(box, install, repo, line)
    if seen["first"].exit == 0:
        pytest.xfail(f"the first upgrade under a live server exited 0 with {_tools(box)}")
    seen["version"] = _guide_steps(box, install, line)
    return seen


def _locked(step) -> bool:
    return any(sign in said(step) for sign in LOCKED)


@cell("win-launcher-lock", channel="pip venv", harness="spec client holding crapkit.exe",
      scenario="upgrade from N-1 after initialize and one call: error 32; the guide's steps; --version candidate",
      use_cases="launcher-lock procedure", os="windows", image=None, cadence="push")
def test_a_pip_upgrade_under_a_live_server_follows_the_lock_procedure(box, templates, candidate):
    install = _pip_n1(box)
    seen = locked_upgrade(box, templates, install, upgrade_line("python -m pip install --upgrade crapkit"))

    assert seen["info"]["serverInfo"]["version"] == wheels.n_minus_1()
    assert seen["called"]["content"]
    assert _locked(seen["first"]), said(seen["first"])
    assert f"crapkit {candidate.version}" in seen["version"].stdout


@cell("win-launcher-lock", channel="uv tool", harness="spec client holding crapkit.exe",
      scenario="upgrade from N-1 after initialize and one call: error 32; the guide's steps; --version candidate",
      use_cases="launcher-lock procedure", os="windows", image=None, cadence="push")
def test_a_uv_tool_upgrade_under_a_live_server_follows_the_lock_procedure(box, templates, candidate):
    install = _uv_tool_n1(box)
    seen = locked_upgrade(box, templates, install, upgrade_line("uv tool upgrade"))

    assert seen["info"]["serverInfo"]["version"] == wheels.n_minus_1()
    assert seen["called"]["content"]
    assert _locked(seen["first"]), said(seen["first"])
    assert f"crapkit {candidate.version}" in seen["version"].stdout


@cell("win-launcher-lock", channel="pip and uv tool", harness="none",
      scenario="the guide names the lock, its three steps and both installers' commands", use_cases="upgrade guide",
      os="windows", image=None, cadence="push")
def test_the_guide_names_the_lock_and_its_steps(box):
    steps = " ".join(installers.section_prose(GUIDE, LOCKS))

    assert "error 32" in steps
    assert all(f"{number}. " in steps for number in (1, 2, 3))
    assert upgrade_line("uv tool upgrade") == "uv tool upgrade crapkit"
