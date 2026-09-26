"""The Codex plugin, installed and upgraded the way the README and
docs/upgrading.md say, then driven through `codex app-server`, the stdio
protocol the Codex IDE extension speaks.

Codex clones a git marketplace through the git binary, so the README's HTTPS
URL reaches the kit's mirror through the sandbox's insteadOf rules. Codex also
upgrades every configured git marketplace when it starts: an app-server
session held open for a few seconds reinstalls the plugin from the mirror's
new main. No config key turns that off in 0.156.1 (`codex features list` has
none), so the upgrade cells assert the state it settles in.

    lin-codex-plugin-fresh      12 tools, a real get_next_item, server cwd = thread cwd, 3 skills;
                                hooks/list and the skill texts as a Codex user meets them
    win-codex-plugin            the same tool call on Windows: crapkit.exe found through PATHEXT
    lin-up-codex-plugin-0.7.6   CLI first, the docs/upgrading.md Codex lines, then one start
    lin-codex-autoupgrade       the release lands and Codex upgrades the plugin past the CLI
    lin-up-codex-tools-0.5.1    enabled_tools and approval_mode written with 0.5.x names
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import hang_guard
import pytest

from kit import gitmirror, shim
from kit.cells import cell
from kit.mcp_client import McpClient
from test_claude_plugin import (TOOLS, cli_venv, doctor_plugin, github, harness_on_path, measured_repo, old_lines,
                                page_lines, plain_repo, rename_table, run_lines, upgrade_cli)

PACKET = "deploy-plugins"
PLUGIN = "crapkit@crapkit"
CLAUDE_COMMAND = re.compile(r"\bclaude (?:plugin|mcp)\b")
# What decision (a) ships: Codex reads this manifest before the Claude one, and
# an empty hooks table keeps it from loading plugin/hooks/hooks.json.
CODEX_MANIFEST = "plugin/.codex-plugin/plugin.json"


class CodexSession:
    """One `codex app-server` over stdio, initialized, closed on exit."""

    def __init__(self, box, codex: str = "codex"):
        self.client = McpClient.in_box(box, [codex, "app-server"], cwd=box.root)
        self.client.request("initialize", {"clientInfo": {"name": "crapkit-deploy-kit", "version": "1"}})
        self.client.notify("initialized")

    def __enter__(self) -> "CodexSession":
        return self

    def __exit__(self, *exc) -> None:
        self.client.__exit__(*exc)

    def thread(self, cwd: Path) -> str:
        return self.client.request("thread/start", {"cwd": str(cwd)})["thread"]["id"]

    def server(self, thread: str) -> dict:
        data = self.client.request("mcpServerStatus/list", {"threadId": thread, "detail": "toolsAndAuthOnly"})
        return next((entry for entry in data["data"] if entry["name"] == "crapkit"), {})

    def tools(self, thread: str) -> list[str]:
        """crapkit's tools once Codex has listed all of them."""
        found: dict = {}

        def listed() -> bool:
            found.update(self.server(thread).get("tools") or {})
            return len(found) >= TOOLS
        hang_guard.wait_until(listed, what="crapkit's tools in Codex's MCP status")
        return sorted(found)

    def call(self, thread: str, tool: str, arguments: dict | None = None) -> dict:
        return self.client.request("mcpServer/tool/call", {"threadId": thread, "server": "crapkit", "tool": tool,
                                                           "arguments": arguments or {}})

    def listing(self, method: str, cwd: Path) -> list[dict]:
        """skills/list or hooks/list for one cwd, the crapkit plugin's entries only."""
        key = method.split("/")[0]
        entries = self.client.request(method, {"cwds": [str(cwd)]})["data"][0][key]
        return [entry for entry in entries if entry.get("pluginId") == PLUGIN]


# --- what `codex plugin` reports -----------------------------------------------------

def codex_version(box, codex: str = "codex") -> str | None:
    listed = json.loads(box.run([codex, "plugin", "list", "--json"], expect=0).stdout)
    return next((entry["version"] for entry in listed["installed"] if entry["pluginId"] == PLUGIN), None)


def codex_root(box, version: str) -> Path:
    """Where docs/upgrading.md says the installed copy lives: the cache under ~/.codex."""
    return Path(box.env["CODEX_HOME"]) / "plugins" / "cache" / "crapkit" / "crapkit" / version


def settled(box) -> str | None:
    """`codex plugin list --json` read until two reads in a row agree."""
    reads = [codex_version(box)]

    def agree() -> bool:
        reads.append(codex_version(box))
        return reads[-1] == reads[-2]
    hang_guard.wait_until(agree, what="two agreeing reads of `codex plugin list --json`")
    return reads[-1]


def started_once(box, until_changed: bool) -> str | None:
    """Codex started once with no task: an app-server session held open while its
    startup marketplace upgrade runs, then the version the list settles on."""
    before = codex_version(box)
    with CodexSession(box):
        if until_changed:
            hang_guard.wait_until(lambda: codex_version(box) != before, what="Codex's startup marketplace upgrade")
        return settled(box)


# --- the plugin, installed ---------------------------------------------------------------

def install(box, tree: Path, version: str, cwd: Path) -> gitmirror.Mirror:
    """`tree` published as release `version`, then the README's two Codex lines."""
    mirror = gitmirror.make(box)
    mirror.publish(tree, version)
    harness_on_path(box)
    run_lines(box, page_lines("Codex"), cwd=cwd)
    return mirror


def with_codex_manifest(box, candidate) -> Path:
    """The candidate tree carrying a Codex manifest with an empty hooks table: the
    tree's own when it ships one, else the one decision (a) proposes."""
    tree = box.root / "tree-codex-manifest"
    shutil.copytree(candidate.staged, tree)
    manifest = tree / CODEX_MANIFEST
    if not manifest.exists():
        claude = json.loads((tree / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "crapkit", "version": candidate.version,
                                        "description": claude["description"], "hooks": {}}), encoding="utf-8")
    return tree


def served(box, candidate, templates, tree: Path) -> dict:
    """A measured repo, the CLI behind the shim, the plugin from `tree`, and one
    thread whose cwd is the repo: what Codex serves there."""
    real = cli_venv(box)
    repo = measured_repo(box, templates)
    box.prepend_path(shim.install(box, real))
    install(box, tree, candidate.version, repo)
    with CodexSession(box) as codex:
        thread = codex.thread(repo)
        seen = {"repo": repo, "tools": codex.tools(thread), "answer": codex.call(thread, "get_next_item"),
                "skills": codex.listing("skills/list", repo), "hooks": codex.listing("hooks/list", repo)}
    seen["server_starts"] = [start for start in shim.starts(box) if start["argv"][1:] == ["mcp"]]
    return seen


SKILLS = ["crapkit:crapkit", "crapkit:crapkit-onboard", "crapkit:crapkit-recover"]


def server_cwds(seen: dict) -> set[str]:
    return {start["cwd"] for start in seen["server_starts"]}


def assert_served(seen: dict) -> None:
    item = seen["answer"]["structuredContent"]["item"]
    assert len(seen["tools"]) == TOOLS
    assert not seen["answer"].get("isError")
    assert (item["path"], item["function"].split("(")[0]) == ("calc/grade.py", "grade")
    assert server_cwds(seen) == {str(seen["repo"])}
    assert sorted(skill["name"] for skill in seen["skills"]) == SKILLS


@cell("lin-codex-plugin-fresh", channel="Codex marketplace, README line via mirror", harness="Codex",
      scenario="fresh: 12 tools; get_next_item returns a real item; server cwd == thread cwd; skills/list 3",
      use_cases="Codex plugin install, skills", os="linux", image="core", cadence="push")
def test_codex_plugin_serves_the_thread_repo(box, candidate, templates):
    assert_served(served(box, candidate, templates, candidate.staged))


@cell("lin-codex-plugin-fresh", channel="Codex marketplace, Codex manifest with empty hooks", harness="Codex",
      scenario="fresh: with plugin/.codex-plugin/plugin.json and \"hooks\": {}: hooks/list 0, 12 tools, cwd unchanged",
      use_cases="Codex plugin install", os="linux", image="core", cadence="push")
def test_codex_manifest_keeps_tools_skills_and_cwd_and_drops_hooks(box, candidate, templates):
    seen = served(box, candidate, templates, with_codex_manifest(box, candidate))

    assert_served(seen)
    assert seen["hooks"] == []


def listed_only(box, candidate) -> dict:
    """The plugin installed from the candidate tree, listed with no CLI at all."""
    repo = plain_repo(box)
    install(box, candidate.staged, candidate.version, repo)
    with CodexSession(box) as codex:
        return {"skills": codex.listing("skills/list", repo), "hooks": codex.listing("hooks/list", repo)}


@cell("lin-codex-plugin-fresh", channel="Codex marketplace, README line via mirror", harness="Codex",
      scenario="fresh: hooks/list 0", use_cases="Codex plugin install", os="linux", image="core", cadence="push")
def test_codex_loads_no_crapkit_hooks(box, candidate):
    hooks = listed_only(box, candidate)["hooks"]
    box.transcript.attach("codex-hooks", hooks)

    assert hooks == []


def claude_lines(path: Path) -> list[str]:
    """The lines of a skill that tell the reader to run `claude plugin` or `claude mcp`."""
    text = path.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if CLAUDE_COMMAND.search(line)]


def claude_commands(skills: list[dict]) -> dict[str, list[str]]:
    found = {skill["name"]: claude_lines(Path(skill["path"])) for skill in skills}
    return {name: lines for name, lines in found.items() if lines}


@cell("lin-codex-plugin-fresh", channel="Codex marketplace, README line via mirror", harness="Codex",
      scenario="fresh: no loaded skill names a `claude` command", use_cases="skills", os="linux", image="core",
      cadence="push")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-plugins-2: the crapkit-onboard and crapkit-recover skills "
                   "Codex loads tell a Codex user to run `claude plugin ...`")
def test_codex_skills_name_no_claude_command(box, candidate):
    found = claude_commands(listed_only(box, candidate)["skills"])
    box.transcript.attach("claude-commands", found)

    assert found == {}


@cell("win-codex-plugin", channel="Codex marketplace", harness="Codex",
      scenario="fresh: tool call under Windows allowlist; PATHEXT resolution in shim", use_cases="Codex plugin",
      os="windows", image=None, cadence="push")
def test_codex_plugin_windows(box, candidate, templates):
    seen = served(box, candidate, templates, candidate.staged)
    shim_bin = box.root / "shim-bin"

    assert_served(seen)
    # shim-bin holds crapkit.exe alone, so a server that ran as shim-bin\crapkit
    # is the bare name `crapkit` resolved to the .exe.
    assert [path.name for path in shim_bin.iterdir()] == ["crapkit.exe"]
    assert {ran_as(start) for start in seen["server_starts"]} == {(shim_bin, "crapkit")}


def ran_as(start: dict) -> tuple[Path, str]:
    """The directory and extensionless name of the program a start ran as.
    argv[0] carries `.exe` only when the spawning process wrote it there."""
    program = Path(start["argv"][0])
    return program.parent, program.stem.lower()


# --- upgrades ------------------------------------------------------------------------------

def install_old(box, version: str, cwd: Path) -> gitmirror.Mirror:
    """CLI and Codex plugin at release `version`, from the lines that release printed."""
    cli_venv(box, spec=f"crapkit=={version}")
    mirror = github(box, at=version)
    harness_on_path(box)
    run_lines(box, old_lines(box, mirror, version, "codex plugin marketplace add"), cwd=cwd)
    assert codex_version(box) == version
    return mirror


def guide_lines(box) -> list[str]:
    """docs/upgrading.md's Codex lines, PATH filled with the installed copy the
    listing names, as the page says to."""
    lines = page_lines("Plugin and MCP clients", index=1, page="docs/upgrading.md")
    root = codex_root(box, codex_version(box) or "")
    return [line.replace(" PATH", f' "{root}"') if line.endswith(" PATH") else line for line in lines]


def run_guide(box, cwd: Path, expect: int | None = 0) -> list:
    """The Codex lines in page order; the doctor line is built after the refresh."""
    lines = page_lines("Plugin and MCP clients", index=1, page="docs/upgrading.md")
    steps = run_lines(box, lines[:-1], cwd=cwd, expect=expect)
    return steps + run_lines(box, guide_lines(box)[-1:], cwd=cwd, expect=expect)


@cell("lin-up-codex-plugin-0.7.6", channel="Codex marketplace", harness="Codex",
      scenario="upgrade: docs steps after the CLI; one start, list read until stable; list --json candidate; 12 tools",
      use_cases="Codex plugin upgrade", os="linux", image="core", cadence="push")
def test_codex_plugin_upgrade_from_0_7_6(box, candidate):
    repo = plain_repo(box)
    mirror = install_old(box, "0.7.6", repo)
    mirror.publish(candidate.staged, candidate.version)
    upgrade_cli(box)
    steps = run_guide(box, repo)
    version = started_once(box, until_changed=False)
    with CodexSession(box) as codex:
        tools = codex.tools(codex.thread(repo))

    assert all(step.exit == 0 for step in steps)
    assert version == candidate.version and codex_root(box, version).is_dir()
    assert len(tools) == TOOLS


def gap_repairs(line: str) -> list[str]:
    """The commands a doctor version-gap line names, in its order."""
    return re.findall(r"`([^`]+)`", line.split("Reinstall whichever is behind:", 1)[1])


def drifted(box, candidate) -> tuple[Path, object]:
    """CLI and plugin at 0.7.6, the release lands, Codex starts once. Returns the
    plugin root and what doctor says about it."""
    repo = plain_repo(box)
    mirror = install_old(box, "0.7.6", repo)
    mirror.publish(candidate.staged, candidate.version)
    version = started_once(box, until_changed=True)
    root = codex_root(box, version)
    return root, doctor_plugin(box, str(root), cwd=repo)


@cell("lin-codex-autoupgrade", channel="Codex marketplace", harness="Codex",
      scenario="drift: CLI 0.7.6, mirror moves to candidate, Codex started once; poll until stable; doctor "
      "--plugin-root prints the gap; the repair it names for the CLI", use_cases="plugin/CLI drift", os="linux",
      image="core", cadence="push")
def test_codex_upgrades_the_plugin_past_the_cli_at_startup(box, candidate):
    root, gap = drifted(box, candidate)
    pip_repair = [command for command in gap_repairs(gap.stdout) if command.startswith("pip ")]
    run_lines(box, pip_repair, cwd=box.root)
    after = doctor_plugin(box, str(root))

    assert root.name == candidate.version
    assert gap.exit == 1
    assert f"is version {candidate.version}, and the crapkit its hooks spawn ({box.which('crapkit')}) is 0.7.6" in gap.stdout
    assert pip_repair == ["pip install -U crapkit"]
    assert after.exit == 0 and after.stdout == ""


@cell("lin-codex-autoupgrade", channel="Codex marketplace", harness="Codex",
      scenario="drift: the repair doctor names for a Codex plugin root is a Codex command",
      use_cases="plugin/CLI drift, doctor --plugin-root", os="linux", image="core", cadence="push")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-plugins-3: doctor --plugin-root on a Codex plugin root "
                   "names `claude plugin install crapkit@crapkit` as the plugin repair")
def test_codex_drift_repair_is_a_codex_command(box, candidate):
    _, gap = drifted(box, candidate)
    plugin_repair = [command for command in gap_repairs(gap.stdout) if not command.startswith("pip ")]

    assert plugin_repair and all(command.startswith("codex ") for command in plugin_repair)


def pin_tools(box, names: list[str]) -> None:
    """A user's Codex entry for crapkit that pins tools by name: an allowlist and
    one approval rule, as a 0.5.x user wrote them."""
    config = Path(box.env["CODEX_HOME"]) / "config.toml"
    rules = (f'\n[mcp_servers.crapkit]\ncommand = "crapkit"\nargs = ["mcp"]\nenabled_tools = {json.dumps(names)}\n'
             f'\n[mcp_servers.crapkit.tools.{names[0]}]\napproval_mode = "approve"\n')
    config.write_text(config.read_text(encoding="utf-8") + rules, encoding="utf-8")


def repin(box, old: list[str], table: dict[str, str]) -> None:
    """The CHANGELOG's rename table applied to the pinned names."""
    config = Path(box.env["CODEX_HOME"]) / "config.toml"
    text = config.read_text(encoding="utf-8")
    for name in old:
        text = re.sub(rf'(?<=["\.]){name}(?=["\]\s])', table[name], text)
    config.write_text(text, encoding="utf-8")


def visible(box, cwd: Path) -> dict:
    with CodexSession(box) as codex:
        return codex.server(codex.thread(cwd))


@cell("lin-up-codex-tools-0.5.1", channel="Codex TOML tool policy", harness="Codex",
      scenario="upgrade: approval_mode and enabled_tools with 0.5.x names; what Codex reports; the rename table "
      "restores them", use_cases="MCP renames", os="linux", image="core", cadence="nightly")
def test_codex_tool_policy_with_0_5_x_names(box, candidate):
    repo = plain_repo(box)
    cli_venv(box, spec="crapkit==0.5.1")
    harness_on_path(box)
    old = ["worklist", "next_item"]
    pin_tools(box, old)
    before = visible(box, repo)
    upgrade_cli(box)
    stale = visible(box, repo)
    repin(box, old, rename_table())
    after = visible(box, repo)

    assert sorted(before["tools"]) == sorted(old)
    assert stale["serverInfo"]["version"] == candidate.version and stale["tools"] == {}
    assert sorted(after["tools"]) == sorted(rename_table()[name] for name in old)
