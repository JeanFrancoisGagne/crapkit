"""GitHub Copilot: the CLI with crapkit's plugin installed, and the cloud agent.

lin-copilot-plugin-hooks installs the plugin the way Copilot CLI installs any
marketplace plugin (the GitHub URL answered by the local mirror), lets the
scripted model edit a Python function over its ceiling once, and counts the
crapkit processes the shim saw. The plugin's hooks.json is written for Claude
Code: one handler per file type, each `crapkit` plus `args` and an `if`
filter. A harness that keeps only `command` runs every handler as a bare
`crapkit` on every edit.

lin-copilot-cloud-sim does what a repository's Copilot cloud agent does: the
setup steps install crapkit with the README's pip line, the repository's MCP
config reaches Copilot CLI through --additional-mcp-config, and the session
lists crapkit's tools. A second config starts crapkit through a `cd` into
$GITHUB_WORKSPACE with no --repo, the form for a checkout path the config
cannot name.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from kit import docsnip, gitmirror, hooks_rules, profiles, shim, stub_openai, writers
from kit.cells import cell

PACKET = "deploy-harnesses"
EDITED = "calc/grade.py"
BREACH = ('    return "D"', '    if attempts > 5 and bonus:\n        return "E"\n    return "D"')
PLUGIN_LINES = ["copilot plugin marketplace add JeanFrancoisGagne/crapkit", "copilot plugin install crapkit@crapkit"]
BUG_HOOKS = ("deploy-bug deploy-harnesses-4: Copilot CLI runs the crapkit plugin's 50 hook handlers as bare `crapkit` "
             "on one edit: each prints its usage and exits 2, and the edit waits for all of them")
TOOLS = 12


def install_plugin(box, repo: Path, candidate) -> None:
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    for line in PLUGIN_LINES:
        box.script(line, cwd=repo, expect=0)


def edit_once(box, repo: Path) -> list[dict]:
    """One model turn that edits EDITED past its ceiling; the crapkit starts it caused."""
    before = len(shim.starts(box))
    old, new = BREACH
    turn = {"tool_call": {"name": "edit", "arguments": {"path": str(repo / EDITED), "old_str": old, "new_str": new}}}
    script = [turn, {"text": "done"}]
    with stub_openai.serve(script) as stub:
        _, env = profiles.session_copilot(box, repo, stub.url)
        box.run(["copilot", "-p", "Edit the grade function.", "--allow-all-tools", "--no-auto-update"], cwd=repo,
                env=env, expect=0)
    assert BREACH[1] in (repo / EDITED).read_text(encoding="utf-8"), "the edit never landed"
    return shim.starts(box)[before:]


def hook_spawns(starts: list[dict]) -> Counter:
    """argv (after the launcher) -> count, for every start that was not the MCP server."""
    return Counter(tuple(start["argv"][1:]) for start in starts if start["argv"][1:2] != ["mcp"])


@pytest.mark.xfail(strict=True, reason=BUG_HOOKS)
@cell("lin-copilot-plugin-hooks", channel="copilot plugin install", harness="Copilot CLI 1.0.88, stub BYOK",
      scenario="fresh: shim spawn count per edit", use_cases="hook portability", os="linux", image="full",
      cadence="nightly")
def test_copilot_plugin_hooks(box, templates, candidate, record_property):
    repo = profiles.real_cli_box(box, templates)
    install_plugin(box, repo, candidate)
    spawns = hook_spawns(edit_once(box, repo))
    record_property("hook_spawns_per_edit", str(sum(spawns.values())))
    box.transcript.attach("hook-spawns", {" ".join(argv) or "(bare)": count for argv, count in spawns.items()})
    wanted = tuple(hooks_rules.handlers(candidate.staged / "plugin")[0].entry["args"])
    assert set(spawns) <= {wanted} and sum(spawns.values()) <= 1, dict(spawns)


# --- the cloud agent ------------------------------------------------------------------------

def setup_steps(box) -> None:
    """copilot-setup-steps.yml: the README's install line in the runner's
    Python; the agent's MCP servers then find that crapkit (behind the shim)."""
    venv = box.root / "runner-venv"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(profiles.scripts_dir(venv))
    box.script(docsnip.commands(docsnip.fence("README.md", "The 60-second start"))[0], expect=0)
    box.prepend_path(shim.install(box, str(profiles.launcher(venv))))


def repository_config(box, text: str) -> Path:
    path = box.root / "repository-mcp.json"
    path.write_text(text, encoding="utf-8")
    return path


def cloud_session(box, repo: Path, config: Path, cwd: Path) -> profiles.ToolCall:
    """Copilot CLI as the cloud agent runs it: the repository's MCP config added for the session."""
    script = profiles.ToolCall("openai", [("get_next_item", {})])
    with stub_openai.serve(script) as stub:
        env = {"COPILOT_PROVIDER_BASE_URL": stub.url + "/v1", "COPILOT_MODEL": profiles.STUB_MODEL,
               "COPILOT_PROVIDER_API_KEY": profiles.STUB_KEY, "GITHUB_WORKSPACE": str(repo)}
        box.run(["copilot", "-p", profiles.PROMPT, "--allow-all-tools", "--no-auto-update",
                 "--additional-mcp-config", f"@{config}"], cwd=cwd, env=env, expect=0)
        results = profiles.tool_results(stub.bodies())
    assert any('"path": "calc/grade.py"' in text for text in results), results
    return script


CD_WRAPPER = {"mcpServers": {"crapkit": {"type": "local", "command": "sh", "tools": ["*"],
                                         "args": ["-c", 'cd "$GITHUB_WORKSPACE" && exec crapkit mcp']}}}


@cell("lin-copilot-cloud-sim", channel="repo MCP config + setup steps", harness="Copilot cloud agent (sim)",
      scenario="fresh: setup commands; list under Copilot CLI; cd wrapper", use_cases="MCP wiring", os="linux",
      image="full", cadence="nightly", real_cli=False)
def test_copilot_cloud_sim(box, templates):
    repo = profiles.measured_repo(box, templates)
    setup_steps(box)
    profiles.add_harnesses(box)
    profile = profiles.load("copilot-cloud-agent")
    text, source = writers.doc_config(profile)
    box.transcript.note(f"repository MCP config from {source}")
    config = repository_config(box, writers.filled(text, repo))
    assert len(cloud_session(box, repo, config, repo).crapkit_tools()) == TOOLS
    wrapper = repository_config(box, json.dumps(CD_WRAPPER))
    assert len(cloud_session(box, repo, wrapper, box.home).crapkit_tools()) == TOOLS
    assert shim.starts(box)[-1]["cwd"] == str(repo)
