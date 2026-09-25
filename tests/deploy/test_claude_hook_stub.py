"""The advisory hook as Claude Code fires it, with the model replaced by the
kit's Messages stub.

`claude -p` runs against stub_anthropic. The stub scripts the model's side:
one write (an Edit, a Write of a new file, or a Bash heredoc), then a Read per
turn until a request body carries the advisory, at most five of them. The
plugin's Edit|Write hook runs async and Claude Code hands its exit 2 to the
model on a later turn, so before answering each Read the stub waits until the
shim has recorded the hook's exit; the five Reads are the slack after that.

    lin-claude-hook-stub        Edit, Write of a new file, Bash heredoc of Python: one advisory each;
                                a Bash heredoc of TypeScript: none; no MultiEdit tool offered
    win-claude-hook-stub        the same script on Windows, Bash through Git Bash
    lin-up-bash-matcher-0.7.6   the README Bash entry a 0.7.6 user wrote still fires after the upgrade

If Claude Code ever refuses the stub, the cell is a strict xfail naming why;
it never passes on a recorded payload.
"""
from __future__ import annotations

import json
from pathlib import Path

import hang_guard

from kit import shim, stub_anthropic
from kit.cells import cell
from test_claude_plugin import (BREACH, CLAUDE, cli_venv, edit_settings, fence_holding, github, harness_on_path,
                                install_old_plugin, measured_repo, old_page, page_lines, run_lines, upgrade_both)

PACKET = "deploy-plugins"
READS = 5
TS_SOURCE = 'export function route(kind: string): number {\n  return kind === "a" ? 1 : 2;\n}\n'


def advisory(path: str) -> str:
    return f"crapkit advisory: 1 function(s) over ceiling 6 in {path}"


def heredoc(path: str, text: str) -> str:
    return f"cat > {path} <<'EOF'\n{text}EOF"


def write_turns(repo: Path) -> dict[str, list[dict]]:
    """Each case's scripted writes, before the Reads start."""
    grade = str(repo / "calc" / "grade.py")
    edit = {"file_path": grade, "old_string": "def curve(scores, floor):",
            "new_string": BREACH + "\n\ndef curve(scores, floor):"}
    return {
        "edit": [{"tool_use": {"name": "Read", "input": {"file_path": grade}}},
                 {"tool_use": {"name": "Edit", "input": edit}}],
        "write-new": [{"tool_use": {"name": "Write", "input": {"file_path": str(repo / "calc" / "big.py"),
                                                                "content": BREACH}}}],
        "bash-python": [{"tool_use": {"name": "Bash", "input": {"command": heredoc("calc/big.py", BREACH)}}}],
        "bash-typescript": [{"tool_use": {"name": "Bash", "input": {"command": heredoc("calc/big.ts", TS_SOURCE)}}}],
    }


EXPECTED = {"edit": advisory("calc/grade.py"), "write-new": advisory("calc/big.py"),
            "bash-python": advisory("calc/big.py"), "bash-typescript": None}


def hook_exits(box) -> int:
    """Hook processes the shim saw finish: every start that is not the MCP server
    or a version probe (a below-floor Claude Code spawns a bare `crapkit`)."""
    return sum(1 for start in shim.starts(box)
               if start["argv"][1:] not in (["mcp"], ["--version"]) and start["exit"] is not None)


class Model:
    """The stub's script for one session: the writes, then Reads until the
    advisory `marker` reaches a request, then a closing text turn."""

    def __init__(self, box, writes: list[dict], marker: str, read: str):
        self.box, self.writes, self.marker, self.read = box, writes, marker, read
        self.baseline = hook_exits(box)

    def carries(self, body: dict) -> bool:
        return self.marker in json.dumps(body.get("messages", []))

    def __call__(self, body: dict, turn: int) -> dict:
        if turn < len(self.writes):
            return self.writes[turn]
        hang_guard.wait_until(lambda: hook_exits(self.box) > self.baseline, what="the hook's exit in the shim log")
        if self.carries(body) or turn >= len(self.writes) + READS:
            return {"text": "done"}
        return {"tool_use": {"name": "Read", "input": {"file_path": self.read}}}


def session(box, repo: Path, writes: list[dict], marker: str, claude: str = "claude") -> list[dict]:
    """One `claude -p` run against the stub; every request body it sent."""
    model = Model(box, writes, marker, str(repo / "calc" / "grade.py"))
    with stub_anthropic.serve(model) as stub:
        box.run([claude, "-p", "make the change", "--permission-mode", "bypassPermissions"], cwd=repo, expect=0,
                env={"ANTHROPIC_BASE_URL": stub.url, "ANTHROPIC_API_KEY": "sk-ant-stub"})
    return stub.bodies()


def advisories(bodies: list[dict], marker: str) -> int:
    """How many times the advisory appears in the last request's messages."""
    return json.dumps(bodies[-1].get("messages", [])).count(marker)


def reset(box, repo: Path) -> None:
    box.run(["git", "checkout", "-q", "--", "."], cwd=repo, expect=0)
    box.run(["git", "clean", "-q", "-f", "--", "calc"], cwd=repo, expect=0)


def run_case(box, repo: Path, writes: list[dict], expected: str | None, claude: str = "claude") -> dict:
    reset(box, repo)
    marker = expected or advisory("calc/big")
    bodies = session(box, repo, writes, marker, claude)
    return {"advisories": advisories(bodies, marker), "requests": len(bodies),
            "tools": [tool["name"] for tool in bodies[0].get("tools", [])]}


def bash_entry(base: Path | None = None) -> dict:
    """The README's PostToolUse entry for the Bash matcher, as the user pastes it."""
    block = fence_holding("README.md", '"matcher": "Bash"', base=base)
    return json.loads(block.text)["hooks"]["PostToolUse"][0]


def add_bash_entry(box, entry: dict) -> None:
    edit_settings(box, lambda settings: settings.setdefault("hooks", {}).setdefault("PostToolUse", []).append(entry))


def ready(box, candidate, templates) -> Path:
    """The CLI, a measured repo, the plugin from the README lines, the README Bash
    entry in the user's settings, and the shim first on PATH."""
    real = cli_venv(box)
    repo = measured_repo(box, templates)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    github(box, candidate)
    harness_on_path(box)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    add_bash_entry(box, bash_entry())
    box.prepend_path(shim.install(box, real))
    return repo


def every_case(box, repo: Path) -> dict[str, dict]:
    return {name: run_case(box, repo, writes, EXPECTED[name]) for name, writes in write_turns(repo).items()}


def assert_cases(results: dict[str, dict]) -> None:
    counts = {name: result["advisories"] for name, result in results.items()}
    assert counts == {"edit": 1, "write-new": 1, "bash-python": 1, "bash-typescript": 0}
    assert "MultiEdit" not in results["edit"]["tools"]
    assert {"Edit", "Write", "Bash"} <= set(results["edit"]["tools"])


@cell("lin-claude-hook-stub", channel="Claude marketplace + README Bash entry", harness="Claude Code -p, stub Messages API",
      scenario="fresh: Edit, Write new file, Bash heredoc; up to 5 Read turns until the advisory is in a request; "
      "silence on a non-Python heredoc; MultiEdit is not a tool Claude Code offers",
      use_cases="PostToolUse hook (Edit|Write), Bash matcher", os="linux", image="core", cadence="nightly")
def test_claude_hook_advises_each_write(box, candidate, templates):
    results = every_case(box, ready(box, candidate, templates))
    box.transcript.attach("cases", results)

    assert_cases(results)


@cell("win-claude-hook-stub", channel="Claude marketplace + README Bash entry", harness="Claude Code, Git Bash",
      scenario="fresh: the lin-claude-hook-stub script on Windows, stub on 127.0.0.1",
      use_cases="hook, Bash matcher", os="windows", image=None, cadence="nightly")
def test_claude_hook_advises_each_write_windows(box, candidate, templates):
    results = every_case(box, ready(box, candidate, templates))
    box.transcript.attach("cases", results)

    assert_cases(results)


@cell("lin-up-bash-matcher-0.7.6", channel="user Bash entry", harness="Claude Code, stub",
      scenario="upgrade: the Bash entry a 0.7.6 user copied from that README still fires after the upgrade",
      use_cases="Bash matcher", os="linux", image="core", cadence="nightly")
def test_bash_entry_from_0_7_6_fires_after_upgrade(box, candidate, templates):
    mirror = install_old_plugin(box, "0.7.6", box.root)
    repo = measured_repo(box, templates)
    add_bash_entry(box, bash_entry(base=old_page(box, mirror, "0.7.6")))
    upgrade_both(box, mirror, candidate, repo)
    box.prepend_path(shim.install(box, box.which("crapkit")))
    result = run_case(box, repo, write_turns(repo)["bash-python"], EXPECTED["bash-python"])

    assert result["advisories"] == 1
