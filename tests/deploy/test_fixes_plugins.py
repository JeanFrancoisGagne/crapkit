"""The plugin fixes, proved through the harnesses that load the plugin.

- Codex installs the plugin from the README's marketplace line and, with the
  Codex manifest, registers no hook, three skills and the twelve tools, and
  starts `crapkit mcp` in the thread's working directory. The released tree
  before the manifest registered Claude Code's 50 PostToolUse handlers.
- Codex keeps crapkit-onboard out of the model's context (agents/openai.yaml)
  and still lists the two working skills.
- Claude Code's own validator still accepts the plugin and the marketplace.
- `doctor --plugin-root` says nothing about the plugin's one shell-form hook on
  Claude Code 2.1.138, 2.1.139 or the pinned release, and names a Claude Code
  below 2.1.139 for a plugin whose hooks pass `args`, as 0.8.0's did.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path

import hang_guard

from kit import docsnip, gitmirror, hooks_rules, shim, stub_openai
from kit.cells import cell
from kit.mcp_client import McpClient

from fixes_support import (harness_package, harnesses_on_path, install_candidate, launcher,
                           measured_repo)

PACKET = "deploy-fixes"
WINDOWS = os.name == "nt"
TOOLS = 12
SKILLS = ["crapkit:crapkit", "crapkit:crapkit-onboard", "crapkit:crapkit-recover"]


# --- Codex ---------------------------------------------------------------------------

class CodexAppServer:
    """`codex app-server` over stdio, the way the Codex app drives it."""

    def __init__(self, box, cwd: Path):
        self.client = McpClient.in_box(box, ["codex", "app-server"], cwd=cwd)

    def __enter__(self) -> "CodexAppServer":
        self.client.request("initialize", {"clientInfo": {"name": "crapkit-deploy", "version": "1"}})
        self.client.notify("initialized")
        return self

    def __exit__(self, *exc) -> None:
        self.client.__exit__(*exc)

    def hooks(self, repo: Path) -> list[dict]:
        (entry,) = self.client.request("hooks/list", {"cwds": [str(repo)]})["data"]
        return entry["hooks"]

    def skills(self, repo: Path) -> list[str]:
        (entry,) = self.client.request("skills/list", {"cwds": [str(repo)], "forceReload": True})["data"]
        return sorted(skill["name"] for skill in entry["skills"] if skill.get("pluginId") == "crapkit@crapkit")

    def thread(self, repo: Path) -> str:
        started = self.client.request("thread/start", {"cwd": str(repo), "approvalPolicy": "never",
                                                       "sandbox": "read-only"})
        return started["thread"]["id"]

    def crapkit_server(self, thread: str) -> dict:
        servers = self.client.request("mcpServerStatus/list", {"threadId": thread})["data"]
        return next(server for server in servers if server["name"] == "crapkit")

    def call(self, thread: str, tool: str, arguments: dict | None = None) -> dict:
        return self.client.request("mcpServer/tool/call", {"threadId": thread, "server": "crapkit",
                                                          "tool": tool, "arguments": arguments or {}})


def codex_marketplace(box, candidate, release: str | None = None) -> None:
    """The README's Codex lines, verbatim, against the GitHub mirror."""
    mirror = gitmirror.make(box)
    mirror.publish(candidate.staged, candidate.version)
    if release:
        mirror.release_to(release)
    for line in docsnip.commands(docsnip.fence("README.md", "Codex", index=0)):
        box.script(line, shell="sh" if not WINDOWS else "cmd", expect=0)


def codex_ready(box, candidate, templates, release: str | None = None) -> tuple[Path, Path]:
    """The candidate CLI behind the shim, a measured repo, and the plugin
    installed through Codex's marketplace manager."""
    scripts = install_candidate(box)
    repo = measured_repo(box, templates)
    box.prepend_path(shim.install(box, launcher(scripts)))
    harnesses_on_path(box)
    codex_marketplace(box, candidate, release)
    return scripts, repo


def _codex_session(box, repo: Path) -> dict:
    """What a Codex session on `repo` registers and starts, and one tool call."""
    before = len(shim.starts(box))
    with CodexAppServer(box, repo) as codex:
        seen = {"hooks": codex.hooks(repo), "skills": codex.skills(repo)}
        thread = codex.thread(repo)
        # The call first: right after thread/start the status can still read
        # "starting", and a call Codex answered means the server connected.
        seen["answer"] = codex.call(thread, "get_next_item")
        seen["server"] = codex.crapkit_server(thread)
    seen["starts"] = [start for start in shim.starts(box)[before:] if start["argv"][1:] == ["mcp"]]
    box.transcript.attach("codex", seen)
    return seen


def _codex_plugin_carries_no_hook_and_starts_in_the_thread_cwd(box, candidate, templates):
    _, repo = codex_ready(box, candidate, templates)
    seen = _codex_session(box, repo)
    server, text = seen["server"], seen["answer"]["content"][0]["text"]

    assert seen["hooks"] == [], "the Codex manifest's empty hooks object leaves hooks/hooks.json out"
    assert seen["skills"] == SKILLS
    assert (server["runtimeStatus"], len(server["tools"])) == ("connected", TOOLS)
    assert server["serverInfo"]["version"] == candidate.version
    assert seen["starts"], "Codex started crapkit mcp through the shim"
    assert {Path(start["cwd"]).resolve() for start in seen["starts"]} == {repo.resolve()}
    assert not seen["answer"].get("isError"), text
    assert "calc/grade.py" in text


@cell("lin-codex-plugin-manifest", channel="Codex marketplace, README lines via the mirror",
      harness="Codex (pinned)", scenario="fresh: Codex manifest ships hooks {}: hooks/list 0, 3 skills, "
      "12 tools, crapkit mcp starts in the thread cwd, get_next_item returns calc/grade.py",
      use_cases="Codex plugin install, MCP start", os="linux", image="core", cadence="push")
def test_codex_loads_no_hook_and_starts_the_server_in_the_thread_cwd(box, candidate, templates):
    _codex_plugin_carries_no_hook_and_starts_in_the_thread_cwd(box, candidate, templates)


@cell("win-codex-plugin-manifest", channel="Codex marketplace, README lines via the mirror",
      harness="Codex (pinned)", scenario="fresh: the Linux cell on Windows, crapkit.exe behind the shim",
      use_cases="Codex plugin install, MCP start", os="windows", image=None, cadence="push")
def test_codex_on_windows_loads_no_hook_and_starts_the_server_in_the_thread_cwd(box, candidate, templates):
    _codex_plugin_carries_no_hook_and_starts_in_the_thread_cwd(box, candidate, templates)


@cell("lin-codex-plugin-hooks-0.8.0", channel="Codex marketplace at the v0.8.0 tag",
      harness="Codex (pinned)", scenario="fresh at 0.8.0: no Codex manifest, so Codex registers every "
      "hooks.json handler as a bare `crapkit` command (the finding the manifest fixes)",
      use_cases="Codex plugin install", os="linux", image="core", cadence="nightly")
def test_the_release_before_the_manifest_gave_codex_every_claude_code_hook(box, candidate, templates):
    _, repo = codex_ready(box, candidate, templates, release="0.8.0")
    count = len(hooks_rules.parse(gitmirror.at(box).git("show", "v0.8.0:plugin/hooks/hooks.json")))
    with CodexAppServer(box, repo) as codex:
        hooks = codex.hooks(repo)

    assert len(hooks) == count == 50
    assert {(hook["command"], hook["eventName"]) for hook in hooks} == {("crapkit", "postToolUse")}


# Top-level keys go above every table: `codex plugin marketplace add` already
# wrote [marketplaces.crapkit] and [plugins."crapkit@crapkit"] into the file.
STUB_MODEL = 'model = "stub-model"\nmodel_provider = "stub"\n'
STUB_PROVIDER = """
[model_providers.stub]
name = "stub"
base_url = "{url}/v1"
wire_api = "responses"
env_key = "CRAPKIT_DEPLOY_STUB_KEY"
"""


def _skills_in_the_first_request(box, repo: Path) -> str:
    """One turn against the stub model; the request body Codex sent."""
    with stub_openai.serve([{"text": "done"}]) as stub:
        config = Path(box.env["CODEX_HOME"]) / "config.toml"
        config.write_text(STUB_MODEL + config.read_text(encoding="utf-8") + STUB_PROVIDER.format(url=stub.url),
                          encoding="utf-8")
        box.env["CRAPKIT_DEPLOY_STUB_KEY"] = "stub"
        with CodexAppServer(box, repo) as codex:
            thread = codex.thread(repo)
            codex.client.request("turn/start", {"threadId": thread,
                                                "input": [{"type": "text", "text": "hello"}]})
            hang_guard.wait_until(lambda: stub.bodies("/responses"), what="Codex's first model request")
        return json.dumps(stub.bodies("/responses")[0])


@cell("lin-codex-onboard-explicit", channel="Codex marketplace, README lines via the mirror",
      harness="Codex (pinned), stub Responses API", scenario="fresh: one turn; the request lists "
      "crapkit:crapkit and crapkit:crapkit-recover, never crapkit:crapkit-onboard",
      use_cases="skills, onboarding", os="linux", image="core", cadence="push")
def test_codex_leaves_the_onboarding_skill_out_of_the_model_request(box, candidate, templates):
    _, repo = codex_ready(box, candidate, templates)
    body = _skills_in_the_first_request(box, repo)

    assert "crapkit:crapkit-recover" in body and "crapkit:crapkit:" in body
    assert "crapkit-onboard" not in body


# --- Claude Code ------------------------------------------------------------------------

def _plugin_validates(box, candidate):
    harnesses_on_path(box)
    tree = box.root / "tree"
    shutil.copytree(candidate.staged / "plugin", tree / "plugin")
    shutil.copytree(candidate.staged / ".claude-plugin", tree / ".claude-plugin")
    plugin = box.run(["claude", "plugin", "validate", "plugin", "--strict"], cwd=tree, expect=0)
    marketplace = box.run(["claude", "plugin", "validate", ".", "--strict"], cwd=tree, expect=0)

    assert "Validation passed" in plugin.stdout + plugin.stderr
    assert "Validation passed" in marketplace.stdout + marketplace.stderr
    assert (tree / "plugin" / ".codex-plugin" / "plugin.json").exists()


@cell("lin-plugin-validate-strict", channel="plugin tree of the candidate",
      harness="Claude Code (pinned)", scenario="fresh: `claude plugin validate plugin --strict` and the "
      "marketplace pass with the Codex manifest and agents/openai.yaml in the tree",
      use_cases="plugin install", os="linux", image="core", cadence="push")
def test_claude_code_still_validates_the_plugin_strictly(box, candidate):
    _plugin_validates(box, candidate)


@cell("win-plugin-validate-strict", channel="plugin tree of the candidate",
      harness="Claude Code (pinned)", scenario="fresh: the Linux cell on Windows",
      use_cases="plugin install", os="windows", image=None, cadence="push")
def test_claude_code_on_windows_still_validates_the_plugin_strictly(box, candidate):
    _plugin_validates(box, candidate)


def _claude_at(box, package: str) -> Path:
    """A directory holding only `claude`, the given Claude Code release; the
    same directory for a second call with that release."""
    binary = harness_package(box, package) / "bin" / "claude.exe"
    directory = box.root / f"{package}-bin"
    if directory.exists():
        return directory
    directory.mkdir()
    if WINDOWS:
        (directory / "claude.cmd").write_text(f'@"{binary}" %*\r\n', encoding="utf-8")
    else:
        (directory / "claude").symlink_to(binary)
    return directory


def _plugin_doctor(box, candidate, package: str | None = None, plugin: Path | None = None) -> tuple[int, str]:
    """doctor --plugin-root on the candidate's plugin tree, or `plugin`, with the
    Claude Code release `package` first on PATH, or the pinned one."""
    claude = _claude_at(box, package) if package else None
    env = {"PATH": os.pathsep.join([str(claude), box.env["PATH"]])} if claude else None
    box.run([box.which("claude") if claude is None else str(next(claude.iterdir())), "--version"],
            expect=0)
    step = box.run(["crapkit", "doctor", "--plugin-root", str(plugin or candidate.staged / "plugin")], env=env)
    return step.exit, step.stdout + step.stderr


def _candidate_and_claude(box) -> None:
    install_candidate(box, "crapkit")
    harnesses_on_path(box)


def _exec_form_plugin(box, candidate) -> Path:
    """The candidate's plugin with its hook written the way 0.8.0 wrote it: the
    command's first word in `command` and the rest in `args`, the field
    Claude Code below 2.1.139 drops."""
    plugin = box.root / "plugin-exec-form"
    shutil.copytree(candidate.staged / "plugin", plugin)
    hooks = plugin / "hooks" / "hooks.json"
    declared = json.loads(hooks.read_text(encoding="utf-8"))
    for group in declared["hooks"]["PostToolUse"]:
        for handler in group["hooks"]:
            handler["command"], *handler["args"] = shlex.split(handler["command"])
    hooks.write_text(json.dumps(declared, indent=2) + "\n", encoding="utf-8")
    return plugin


def _floor_cells(box, candidate):
    _candidate_and_claude(box)
    shipped = _plugin_doctor(box, candidate, "claude-code-2.1.138")
    below_exit, below = _plugin_doctor(box, candidate, "claude-code-2.1.138", _exec_form_plugin(box, candidate))

    assert shipped == (0, ""), shipped
    assert below_exit == 1, below
    assert "Claude Code 2.1.138 (" in below and "predates 2.1.139" in below
    assert "`claude update`" in below


@cell("lin-plugin-floor-doctor", channel="pip venv + the plugin tree", harness="Claude Code 2.1.138",
      scenario="fresh: doctor --plugin-root is silent on Claude Code 2.1.138 for the shipped shell-form hook, "
               "and names 2.1.139 and exits 1 for a plugin whose hooks pass args",
      use_cases="doctor --plugin-root", os="linux", image="core", cadence="push")
def test_doctor_names_a_claude_code_below_the_args_floor(box, candidate):
    _floor_cells(box, candidate)


@cell("win-plugin-floor-doctor", channel="pip venv + the plugin tree", harness="Claude Code 2.1.138",
      scenario="fresh: the Linux cell on Windows, claude.exe behind a cmd shim",
      use_cases="doctor --plugin-root", os="windows", image=None, cadence="nightly")
def test_doctor_on_windows_names_a_claude_code_below_the_args_floor(box, candidate):
    _floor_cells(box, candidate)


@cell("lin-plugin-floor-doctor-silent", channel="pip venv + the plugin tree",
      harness="Claude Code 2.1.139 and the pinned release",
      scenario="fresh: at the floor and at the pinned release doctor --plugin-root prints nothing, exit 0",
      use_cases="doctor --plugin-root", os="linux", image="core", cadence="push")
def test_doctor_is_silent_at_the_floor(box, candidate):
    _candidate_and_claude(box)

    assert _plugin_doctor(box, candidate, "claude-code-2.1.139") == (0, "")
    assert _plugin_doctor(box, candidate) == (0, "")
