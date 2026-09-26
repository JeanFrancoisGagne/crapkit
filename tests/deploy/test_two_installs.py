"""Two crapkit installs on one machine: the old one a user forgot, the new
one they upgraded into.

Linux: 0.7.6 in the user site (`pip install --user`, past the PEP 668 marker
the way users get past it) and the candidate in a venv. Windows: 0.7.6 in a
Python's Scripts directory on the system PATH and the candidate from `uv tool
install`, whose bin directory sits on the user PATH. Each consumer resolves
`crapkit` (or `python -m crapkit`) from the PATH it is started with: a login
shell, an activated venv, the Route 1 hook's `python`, and an MCP server a GUI
client starts with Windows' merged PATH (system entries first). The cell
records which version each one runs, then asks doctor, which should name both
installs so the user can see why the hook and the editor disagree.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kit import state
from kit.cells import cell
from kit.mcp_client import McpClient
from kit.state import output

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")


def with_path(box, *directories) -> dict[str, str]:
    return {"PATH": os.pathsep.join([*(str(directory) for directory in directories), box.env["PATH"]])}


def found_on(env: dict, name: str) -> str:
    """`name` as a process started with `env` finds it on its PATH."""
    found = shutil.which(name, path=env["PATH"])
    assert found, f"{name} is not on {env['PATH']}"
    return found


def version_under(box, repo, env: dict, argv=("crapkit",)) -> str:
    step = box.run([found_on(env, argv[0]), *argv[1:], "--version"], cwd=repo, env=env, expect=0)
    return step.stdout.split()[-1]


def mcp_version(box, repo, env: dict) -> str:
    """serverInfo from `crapkit mcp` as a client started with `env` spawns it."""
    with McpClient([found_on(env, "crapkit"), "mcp"], cwd=repo, env={**box.env, **env},
                   transcript=box.transcript) as client:
        return client.initialize()["serverInfo"]["version"]


def named(text: str, directory) -> bool:
    """Whether `text` names `directory` as given, normalized or resolved."""
    forms = {str(directory), os.path.normpath(str(directory)), str(Path(directory).resolve())}
    return any(form in text for form in forms)


def doctor_names_both(box, repo, env: dict, old_dir, new_dir) -> None:
    doctor = box.run([found_on(env, "crapkit"), "doctor"], cwd=repo, env=env)
    text = output(doctor)
    assert named(text, old_dir) and named(text, new_dir), (
        f"doctor with installs in {old_dir} and {new_dir} printed:\n{text}")


@cell("lin-two-installs", channel="user-site 0.7.6 + venv candidate", harness="shell, git hook, plugin",
      scenario="upgrade one copy; which version each consumer runs; doctor names both",
      use_cases="install", os="linux", image="core", cadence="nightly")
def test_lin_two_installs(box, templates, candidate):
    source = state.build(box, OLD, cache=templates)
    repo = source.checkout(box)
    python = box.toolchain.python("3.12")
    box.run([python, "-m", "pip", "install", "-q", "--user", "--break-system-packages", f"crapkit[py]=={OLD}"],
            expect=0, note="the copy a user installed long ago, past the PEP 668 marker")
    user_bin = box.home / ".local" / "bin"
    venv = box.root / "venv"
    box.run([python, "-m", "venv", str(venv)], expect=0)
    box.run([str(state.scripts(venv) / "python"), "-m", "pip", "install", "-q", "crapkit[py]"], expect=0)

    login, activated = with_path(box, user_bin), with_path(box, state.scripts(venv), user_bin)
    seen = {"login shell": version_under(box, repo, login),
            "activated venv": version_under(box, repo, activated),
            "Route 1 hook's python, venv active": version_under(box, repo, activated, ("python", "-m", "crapkit")),
            "MCP server from a login-shell PATH": mcp_version(box, repo, login)}
    box.transcript.attach("versions-by-consumer", seen)

    assert seen == {"login shell": OLD, "activated venv": candidate.version,
                    "Route 1 hook's python, venv active": candidate.version,
                    "MCP server from a login-shell PATH": OLD}, seen
    doctor_names_both(box, repo, activated, user_bin, state.scripts(venv))


@cell("win-two-installs", channel="system Scripts 0.7.6 + uv tool candidate", harness="shell, GUI-style PATH",
      scenario="upgrade one copy; which version the terminal and a GUI-started MCP server run; doctor names both",
      use_cases="install", os="windows", image=None, cadence="nightly")
def test_win_two_installs(box, templates, candidate):
    source = state.build(box, OLD, cache=templates)
    repo = source.checkout(box)
    system = box.root / "Python312"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(system)], expect=0,
            note="stands in for a python.org install whose Scripts directory is on the system PATH")
    box.run([str(state.scripts(system) / "python.exe"), "-m", "pip", "install", "-q", f"crapkit[py]=={OLD}"],
            expect=0)
    user_bin = state.uv_tool_install(box)

    terminal = with_path(box, user_bin, state.scripts(system))
    gui = with_path(box, state.scripts(system), user_bin)
    seen = {"terminal (user PATH first)": version_under(box, repo, terminal),
            "GUI app (system PATH first)": version_under(box, repo, gui),
            "MCP server a GUI client starts": mcp_version(box, repo, gui)}
    box.transcript.attach("versions-by-consumer", seen)

    assert seen == {"terminal (user PATH first)": candidate.version, "GUI app (system PATH first)": OLD,
                    "MCP server a GUI client starts": OLD}, seen
    doctor_names_both(box, repo, terminal, state.scripts(system), user_bin)


@pytest.mark.kit
def test_a_directory_is_named_in_any_of_its_spellings(tmp_path):
    spelled = tmp_path / "share" / ".." / "bin"
    assert named(f"launcher in {tmp_path / 'bin'}", spelled)
    assert not named(f"launcher in {tmp_path / 'other'}", spelled)
