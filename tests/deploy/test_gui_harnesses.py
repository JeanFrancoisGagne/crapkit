"""The GUI harnesses under a virtual display: VS Code and Zed, pinned in the
:gui image, each opened on a measured repo the way a user opens it.

lin-vscode-electron writes the workspace config the profile's [doc] gives
(.vscode/mcp.json, cwd ${workspaceFolder}) and a second server in the user
profile's mcp.json with no cwd, as "MCP: Add Server > Global" writes it. A
test-only extension (kit/vscode_probe_ext) starts both servers the way the
Start code lens does and reports the language-model tools VS Code then offers
its chat. VS Code runs three times:

  from a terminal   `code --wait <repo>` inherits the terminal's PATH, so the
                    crapkit the user installed there starts; the shim shows a
                    user-profile server without a cwd starting in $HOME
  from the desktop  VS Code takes PATH from the user's login shell, so a
                    crapkit that only a venv or the terminal's PATH holds is
                    not found ("spawn crapkit ENOENT")
  from the desktop, crapkit in ~/.local/bin, where `uv tool install` and pipx
                    put it and the Debian ~/.profile adds to PATH: it starts

lin-zed-xvfb writes the profile's context_servers block into
~/.config/zed/settings.json and reads Zed.log, where Zed logs its MCP traffic
at trace level. Zed starts a context server through `sh -c` with the login
shell's environment wherever it was launched from, so it runs twice: crapkit
only on the launching PATH ("crapkit: not found"), then in ~/.local/bin.

Both cells run nightly and do not block a job until 14 green nights. VS Code
reads secrets through an OS keyring, which a container lacks, so it runs with
--password-store=basic, the switch VS Code documents for Linux without one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import hang_guard

from kit import profiles, shim
from kit.cells import cell
from kit.transcript import Step

PACKET = "deploy-harnesses"
TOOLS = 12
VSCODE = Path("/opt/vscode")
ZED = Path("/opt/zed/libexec/zed-editor")
PROBE = Path(__file__).resolve().parent / "kit" / "vscode_probe_ext"
# A server added with "MCP: Add Server > Global": no cwd, no --repo.
USER_SERVER = {"servers": {"crapkit-user": {"type": "stdio", "command": "crapkit", "args": ["mcp"]}}}
ZED_QUIET = {"telemetry": {"diagnostics": False, "metrics": False}, "auto_update": False}
# What a desktop session hands an app it starts, before the app asks the login shell.
DESKTOP_PATH = "/usr/local/bin:/usr/bin:/bin"
SKEL_PROFILE = Path("/etc/skel/.profile")


def installed(path: Path) -> str:
    assert path.exists(), f"kit: the :gui image holds no {path}"
    return str(path)


def merge_json(path: Path, extra: dict) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**data, **extra}, indent=2) + "\n", encoding="utf-8")


def start_for(box, args: list[str] | tuple[str, ...]) -> dict:
    """The shim's record of the newest start whose arguments were `args`."""
    found = [start for start in shim.starts(box) if start["argv"][1:] == list(args)]
    assert found, f"no crapkit start with {list(args)}: {[start['argv'] for start in shim.starts(box)]}"
    return found[-1]


def crapkit_in_local_bin(box) -> Path:
    """HOME as useradd leaves it (the Debian ~/.profile puts ~/.local/bin on
    PATH), with the crapkit launcher where `uv tool install` and pipx put it."""
    shutil.copy2(installed(SKEL_PROFILE), box.home / ".profile")
    local = box.home / ".local" / "bin"
    local.mkdir(parents=True, exist_ok=True)
    shutil.copy2(box.which("crapkit"), local / "crapkit")
    return local


# --- VS Code -------------------------------------------------------------------------
# Each launch gets its own user data directory: VS Code is one instance per
# directory, so a second launch on the first one's directory would hand its
# window to the instance still running with the first launch's PATH.

def data_dir(box, launch: str) -> Path:
    """The user data directory of one launch, holding the user profile's mcp.json."""
    directory = box.root / f"vscode-{launch}"
    merge_json(directory / "User" / "mcp.json", USER_SERVER)
    return directory


def vscode_flags(box, repo: Path, data: Path) -> list[str]:
    return ["--no-sandbox", "--disable-gpu", "--password-store=basic", f"--user-data-dir={data}",
            f"--extensions-dir={box.root / 'vscode-ext'}", f"--extensionDevelopmentPath={PROBE}",
            "--disable-workspace-trust", "--skip-welcome", "--skip-release-notes", str(repo)]


# Docker Desktop's kernel is a WSL one, where the `code` script asks before it
# starts a Linux build; a Linux desktop never asks.
LAUNCHES = {"terminal": {"argv": [VSCODE / "bin" / "code", "--wait"], "env": {"DONT_PROMPT_WSL_INSTALL": "1"}},
            "desktop": {"argv": [VSCODE / "code"], "env": {"PATH": DESKTOP_PATH}}}


def vscode_probe(box, repo: Path, launch: str, run: str, seconds: int = 60) -> tuple[dict, Path]:
    """VS Code opened on the repo until the probe saw both servers' tools or
    `seconds` passed; the probe's report and the run's user data directory."""
    data, report = data_dir(box, run), box.root / f"probe-{run}.json"
    env = {**LAUNCHES[launch]["env"], "CRAPKIT_PROBE_OUT": str(report), "CRAPKIT_PROBE_EXPECT": str(2 * TOOLS),
           "CRAPKIT_PROBE_SECONDS": str(seconds)}
    binary, *flags = LAUNCHES[launch]["argv"]
    box.run(["xvfb-run", "-a", installed(binary), *flags, *vscode_flags(box, repo, data)], cwd=repo, env=env,
            note=f"VS Code under xvfb, started from the {launch}")
    assert report.is_file(), "the probe extension wrote no report: VS Code never finished starting"
    return json.loads(report.read_text(encoding="utf-8")), data


def server_log(data: Path) -> str:
    """VS Code's log of the workspace crapkit server in one user data directory."""
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in data.glob("logs/*/window1/mcpServer.mcp.config.ws0.crapkit.log"))


def assert_terminal_launch(box, repo: Path, workspace) -> None:
    report, _ = vscode_probe(box, repo, "terminal", "terminal")
    box.transcript.attach("vscode-lm-tools", report)
    assert len(report["crapkit"]) == 2 * TOOLS, report
    started = start_for(box, workspace.args)
    assert started["cwd"] == str(repo)
    assert profiles.initialize_params(started)["protocolVersion"] == profiles.load("vscode-copilot").initialize["protocol"]
    assert start_for(box, ["mcp"])["cwd"] == str(box.home)


@cell("lin-vscode-electron", channel=".vscode/mcp.json + user mcp.json", harness="VS Code (test-electron, xvfb)",
      scenario="fresh: shim cwd per scope; vscode.lm.tools; non-blocking", use_cases="MCP wiring", os="linux",
      image="gui", cadence="nightly", nonblocking=True)
def test_vscode_electron(box, templates):
    repo = profiles.real_cli_box(box, templates)
    workspace = profiles.doc_server(box, repo, "vscode-copilot")
    assert_terminal_launch(box, repo, workspace)

    report, data = vscode_probe(box, repo, "desktop", "desktop-venv", seconds=20)
    assert report["crapkit"] == [] and "spawn crapkit ENOENT" in server_log(data), report
    crapkit_in_local_bin(box)
    report, _ = vscode_probe(box, repo, "desktop", "desktop-local-bin")
    assert len(report["crapkit"]) == 2 * TOOLS, report


# --- Zed -------------------------------------------------------------------------------

TOOLS_ANSWER = re.compile(r'recv: (\{"jsonrpc": "2\.0", "id": 1, "result": \{"tools".*)$', re.M)
LISTED = 'to receive response to "tools/list"'
NOT_FOUND = "crapkit: not found"


def zed_log(box) -> Path:
    return Path(box.env["XDG_DATA_HOME"]) / "zed" / "logs" / "Zed.log"


def listed_tools(log: str) -> list[str]:
    """The tool names in the tools/list answer Zed logged."""
    match = TOOLS_ANSWER.search(log)
    assert match, "Zed.log holds no tools/list answer from crapkit"
    answer = json.JSONDecoder().raw_decode(match[1])[0]
    return [tool["name"] for tool in answer["result"]["tools"]]


def _stop(process: subprocess.Popen) -> int:
    """Zed runs until its window closes: end xvfb-run, Xvfb and Zed together."""
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    return hang_guard.exited(process)


def _logged(log: Path, text: str) -> bool:
    return log.is_file() and text in log.read_text(encoding="utf-8")


def zed_session(box, repo: Path, until: str) -> str:
    """Zed opened on the repo until Zed.log holds `until`; the log's text."""
    argv, log = ["xvfb-run", "-a", installed(ZED), str(repo)], zed_log(box)
    log.unlink(missing_ok=True)
    started = time.monotonic()
    process = subprocess.Popen([box.resolve(argv[0]), *argv[1:]], cwd=repo, start_new_session=True,
                               env={**box.env, "RUST_LOG": "info,context_server=trace"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        hang_guard.wait_until(lambda: _logged(log, until), process, log=log, what=f"{until!r} in Zed.log")
    finally:
        code = _stop(process)
        box.transcript.add(Step(argv, str(repo), code, "", "", round(time.monotonic() - started, 2),
                                f"Zed under xvfb, stopped once Zed.log held {until!r}"))
    return log.read_text(encoding="utf-8")


def assert_zed_start(box, repo: Path) -> None:
    started = profiles.last_start(box)
    assert started["cwd"] == str(repo)
    offered, profile = profiles.initialize_params(started), profiles.load("zed")
    assert offered["protocolVersion"] == profile.initialize["protocol"]
    assert offered["clientInfo"]["name"] == profile.initialize["client_name"]


@cell("lin-zed-xvfb", channel="context_servers", harness="Zed (xvfb)", scenario="fresh: Zed.log and shim; non-blocking",
      use_cases="MCP wiring", os="linux", image="gui", cadence="nightly", nonblocking=True)
def test_zed_xvfb(box, templates):
    repo = profiles.real_cli_box(box, templates)
    profiles.doc_server(box, repo, "zed")
    merge_json(Path(box.env["XDG_CONFIG_HOME"]) / "zed" / "settings.json", ZED_QUIET)
    assert NOT_FOUND in zed_session(box, repo, until=NOT_FOUND)
    assert shim.starts(box) == []

    crapkit_in_local_bin(box)
    assert len(listed_tools(zed_session(box, repo, until=LISTED))) == TOOLS
    assert_zed_start(box, repo)
