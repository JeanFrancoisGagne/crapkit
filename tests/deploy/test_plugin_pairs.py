"""The plugin beside a CLI channel or a harness version it does not expect.

    lin-pair-uvx-plugin   a uvx-only user installs the Claude plugin: no `crapkit` on PATH, so the MCP
                          server fails to connect, an edit hears nothing, and doctor names the install
                          that fixes both
    lin-plugin-floors     the README plugin lines on each harness floor pins.toml names: Claude Code
                          2.1.139 advises an edit; on 2.1.138 the exec-form args are dropped and the
                          model reads crapkit's usage error after every edit; Codex 0.121.0

A floor runs under its plain command name: a sandbox bin directory first on
PATH holds `claude` (or `codex`) pointing at the floor binary, so the README
lines run verbatim against it.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from kit import docsnip, repos, shim, wheels
from kit.cells import cell
from test_claude_hook_stub import advisory, run_case, write_turns
from test_claude_plugin import (CLAUDE, TOOLS, cli_venv, doctor_plugin, github, harness_on_path, installed,
                                mcp_line, measured_repo, page_lines, plain_repo, run_lines)
from test_codex_plugin import CodexSession, codex_version

PACKET = "deploy-plugins"
USAGE = "crapkit: error: the following arguments are required: command"
# A Go function over the ceiling of 6 (ccn 10), in the go scope `uvx crapkit init` writes.
GO_BREACH = """package main

func route(kind string, size int, fast bool, retry bool, user string) int {
	if kind == "a" && size > 1 {
		return 1
	}
	if kind == "b" || fast {
		return 2
	}
	if retry && user != "" {
		return 3
	}
	if size > 10 {
		return 4
	}
	if size > 20 && !fast {
		return 5
	}
	return 7
}
"""


# --- uvx only ----------------------------------------------------------------------

def readme_code(text: str) -> str:
    """An inline command the README prints, checked to be there verbatim."""
    readme = (docsnip.root() / "README.md").read_text(encoding="utf-8")
    assert f"`{text}`" in readme, f"README.md no longer prints `{text}`"
    return text


def uvx_user(box, candidate, templates) -> Path:
    """The README's uvx start in a repo that is not Python, then the README plugin
    lines: the plugin installed, and nothing named `crapkit` on PATH."""
    repo = repos.checkout(box, "go-rust-shell", cache=templates)
    start = docsnip.commands(docsnip.fence("README.md", "A repo that is not Python"))[0]
    box.script(start, cwd=repo, expect=0)
    github(box, candidate)
    harness_on_path(box)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    assert box.which("crapkit") is None
    return repo


@cell("lin-pair-uvx-plugin", channel="uvx + Claude plugin", harness="Claude Code",
      scenario="fresh: plugin MCP and hook with no crapkit on PATH; the README's pipx line puts it there; Connected",
      use_cases="plugin", os="linux", image="core", cadence="nightly")
def test_uvx_only_user_gets_the_plugin_working(box, candidate, templates):
    repo = uvx_user(box, candidate, templates)
    write = [{"tool_use": {"name": "Write", "input": {"file_path": str(repo / "go" / "extra.go"), "content": GO_BREACH}}}]
    marker = advisory("go/extra.go")
    before = (mcp_line(box, repo), run_case(box, repo, write, marker))
    box.script(readme_code("pipx install crapkit"), cwd=repo, expect=0)
    box.prepend_path(box.home / ".local" / "bin")
    box.prepend_path(shim.install(box, box.which("crapkit")))
    after = (mcp_line(box, repo), run_case(box, repo, write, marker))

    assert "Failed to connect" in before[0] and before[1]["advisories"] == 0
    assert "Connected" in after[0] and after[1]["advisories"] == 1
    assert doctor_plugin(box, cwd=repo).exit == 0


@cell("lin-pair-uvx-plugin", channel="uvx + Claude plugin", harness="Claude Code",
      scenario="fresh: `uvx crapkit doctor --plugin-root` names the missing launcher the plugin cannot start",
      use_cases="plugin, doctor --plugin-root", os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-plugins-6: under uvx, doctor --plugin-root finds the "
                   "crapkit uvx put on its own PATH and exits 0, while the plugin's `crapkit` spawns fail with ENOENT")
def test_uvx_doctor_names_the_missing_launcher(box, candidate, templates):
    repo = uvx_user(box, candidate, templates)
    doctor = box.run(["uvx", "crapkit", "doctor", "--plugin-root"], cwd=repo)

    assert doctor.exit == 1 and "crapkit doctor: FAIL no `crapkit` on PATH" in doctor.stdout


# --- harness floors ------------------------------------------------------------------

def floors(harness: str) -> list[str]:
    pins = tomllib.loads((wheels.SRC / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))
    return pins["harness"][harness].get("floors", [])


def floor_first(box, command: str, version: str) -> None:
    """`command` on PATH answers as the pinned floor binary `<command>-<version>`."""
    harness_on_path(box)
    target = box.which(f"{command}-{version}")
    assert target, f"the image holds no {command}-{version}"
    directory = box.root / f"floor-{command}"
    directory.mkdir()
    os.symlink(target, directory / command)
    box.prepend_path(directory)


def claude_on_floor(box, candidate, templates, version: str) -> Path:
    """A measured repo, the CLI behind the shim, and the README plugin lines run by
    Claude Code `version`."""
    real = cli_venv(box)
    repo = measured_repo(box, templates)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    github(box, candidate)
    floor_first(box, "claude", version)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    box.prepend_path(shim.install(box, real))
    assert box.run(["claude", "--version"], expect=0).stdout.startswith(version)
    return repo


FLOOR = floors("claude-code")[0]
BELOW = floors("claude-code")[-1]


@cell("lin-plugin-floors", channel="plugins", harness=f"Claude Code {FLOOR}",
      scenario=f"fresh: README plugin lines on Claude Code {FLOOR}; Connected; one advisory after a Write",
      use_cases="plugin install", os="linux", image="core", cadence="nightly")
def test_claude_code_floor_advises_an_edit(box, candidate, templates):
    repo = claude_on_floor(box, candidate, templates, FLOOR)
    result = run_case(box, repo, write_turns(repo)["write-new"], advisory("calc/big.py"))

    assert installed(box)["version"] == candidate.version
    assert "Connected" in mcp_line(box, repo)
    assert result["advisories"] == 1


@cell("lin-plugin-floors", channel="plugins", harness=f"Claude Code {BELOW}",
      scenario=f"fresh: what a Claude Code {BELOW} user sees after an edit: crapkit's usage error, once per edit",
      use_cases="plugin install", os="linux", image="core", cadence="nightly")
def test_below_the_floor_the_model_reads_a_usage_error(box, candidate, templates):
    repo = claude_on_floor(box, candidate, templates, BELOW)
    result = run_case(box, repo, write_turns(repo)["write-new"], USAGE)
    bare = [start["argv"][1:] for start in shim.starts(box) if start["argv"][1:] not in (["mcp"], ["--version"])]

    assert bare == [[]]
    assert result["advisories"] == 1


@cell("lin-plugin-floors", channel="plugins", harness=f"Claude Code {BELOW}",
      scenario=f"fresh: doctor --plugin-root warns on Claude Code {BELOW}, below the {FLOOR} the hooks need",
      use_cases="doctor --plugin-root", os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason=f"deploy-bug deploy-plugins-4: on Claude Code {BELOW} the plugin's hooks spawn a "
                   f"bare `crapkit` after every edit, and doctor --plugin-root exits 0 without naming the {FLOOR} floor")
def test_doctor_warns_below_the_claude_code_floor(box, candidate, templates):
    repo = claude_on_floor(box, candidate, templates, BELOW)
    doctor = doctor_plugin(box, cwd=repo)

    assert doctor.exit == 1 and FLOOR in doctor.stdout


CODEX_FLOOR = floors("codex")[0]


@cell("lin-plugin-floors", channel="plugins", harness=f"Codex {CODEX_FLOOR}",
      scenario=f"fresh: the README Codex lines on Codex {CODEX_FLOOR}; 12 tools", use_cases="Codex plugin install",
      os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason=f"deploy-bug deploy-plugins-5: the README's Codex lines fail on Codex "
                   f"{CODEX_FLOOR} (marketplace add wants .agents/plugins/marketplace.json, `codex plugin add` does "
                   "not exist) and the README names no minimum Codex version")
def test_codex_floor_installs_the_plugin(box, candidate):
    cli_venv(box)
    repo = plain_repo(box)
    github(box, candidate)
    floor_first(box, "codex", CODEX_FLOOR)
    steps = run_lines(box, page_lines("Codex"), cwd=repo, expect=None)
    assert [step.exit for step in steps] == [0, 0]
    with CodexSession(box) as codex:
        tools = codex.tools(codex.thread(repo))

    assert codex_version(box) == candidate.version and len(tools) == TOOLS
