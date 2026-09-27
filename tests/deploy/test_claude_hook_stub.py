"""The advisory hook as Claude Code fires it, with the model replaced by the
kit's Messages stub.

`claude -p` runs against stub_anthropic. The stub scripts the model's side:
one write (an Edit, a Write of a new file, or a Bash heredoc), then a Read per
turn until a request body carries the advisory, at most five of them. The
plugin's Edit|Write hook runs async and Claude Code hands its exit 2 to the
model on a later turn, so before answering each Read the stub waits until the
shim has recorded the hook's exit; the five Reads are the slack after that.

The README's Bash entry is a plain command hook: Claude Code waits for it, or
cancels it at its 20 s timeout, before it sends the next request. So the stub
does not wait after a Bash write. When that request comes and the shim holds no
new exit, none will come: the write failed and no PostToolUse hook ran, or
Claude Code killed the hook before the shim could record one. One of the two
hit a Windows nightly run at full CPU, and the stub waited out its whole
120 s bound while Claude Code waited on the stub. The stub then
sends the same write again, up to RETRIES times; `cat >` overwrites, so the
retry needs no reset. Each miss and its cause land in the case's `misses`.

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
from types import SimpleNamespace

import hang_guard
import pytest

from kit import shim, stub_anthropic
from kit.cells import cell
from test_claude_plugin import (BREACH, CLAUDE, cli_venv, edit_settings, fence_holding, github, harness_on_path,
                                install_old_plugin, measured_repo, old_page, page_lines, run_lines, upgrade_both)

PACKET = "deploy-plugins"
READS = 5
RETRIES = 2
# Tools whose hook Claude Code runs before its next request: the README's Bash entry has no "async".
SYNC_TOOLS = {"Bash"}
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


def _text(content) -> str:
    """A tool_result's content, a string or a list of text blocks, as one string."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content)


def last_user(body: dict) -> list[dict]:
    """The content blocks of the request's last user message. Claude Code may
    send a system-role message after it."""
    users = [message["content"] for message in body.get("messages", []) if message.get("role") == "user"]
    return users[-1] if users and isinstance(users[-1], list) else []


def tool_error(body: dict) -> str:
    """The text of an errored tool_result in the request's last user message, or ''."""
    return next((_text(block.get("content", "")) for block in last_user(body) if block.get("is_error")), "")


def missed_hook(body: dict) -> str:
    """Why a hook Claude Code runs before its next request left no exit in the shim log."""
    error = tool_error(body)
    if error:
        return f"the write failed, so no PostToolUse hook ran: {error}"
    return "the hook recorded no exit: Claude Code cancelled it at its timeout"


class Model:
    """The stub's script for one session: the writes, then Reads until the
    advisory `marker` reaches a request, then a closing text turn. A Bash write
    whose hook left no exit goes again, up to RETRIES times; `misses` says why."""

    def __init__(self, box, writes: list[dict], marker: str, read: str):
        self.box, self.writes, self.marker, self.read = box, writes, marker, read
        # With no shim on PATH nothing records a hook, and the five Reads are all the wait there is.
        self.shimmed = (box.root / "shim-bin").is_dir()
        self.sync = self.shimmed and writes[-1]["tool_use"]["name"] in SYNC_TOOLS
        self.misses: list[str] = []
        self.begin(0)

    def begin(self, turn: int) -> None:
        """This attempt's writes start at `turn`; a hook exit counts past the log as it stands."""
        self.start, self.baseline = turn, hook_exits(self.box)

    def hooked(self) -> bool:
        return hook_exits(self.box) > self.baseline

    def settle(self) -> None:
        if self.shimmed and not self.sync:
            hang_guard.wait_until(self.hooked, what="the hook's exit in the shim log")

    def again(self, body: dict) -> bool:
        """Whether the write goes again: its synchronous hook left no exit, and a retry is left."""
        if not self.sync or self.hooked():
            return False
        self.misses.append(missed_hook(body))
        return len(self.misses) <= RETRIES

    def carries(self, body: dict) -> bool:
        return self.marker in json.dumps(body.get("messages", []))

    def reads(self, body: dict, step: int) -> dict:
        self.settle()
        if self.carries(body) or step >= len(self.writes) + READS:
            return {"text": "done"}
        return {"tool_use": {"name": "Read", "input": {"file_path": self.read}}}

    def __call__(self, body: dict, turn: int) -> dict:
        step = turn - self.start
        if step == len(self.writes) and self.again(body):
            self.begin(turn)
            step = 0
        if step < len(self.writes):
            return self.writes[step]
        return self.reads(body, step)


def session(box, repo: Path, writes: list[dict], marker: str, claude: str = "claude") -> tuple[list[dict], list[str]]:
    """One `claude -p` run against the stub; every request body it sent, and
    why each write that went again did."""
    model = Model(box, writes, marker, str(repo / "calc" / "grade.py"))
    with stub_anthropic.serve(model) as stub:
        box.run([claude, "-p", "make the change", "--permission-mode", "bypassPermissions"], cwd=repo, expect=0,
                env=stub_anthropic.claude_env(stub.url))
    return stub.bodies(), model.misses


def advisories(bodies: list[dict], marker: str) -> int:
    """How many times the advisory appears in the last request's messages."""
    return json.dumps(bodies[-1].get("messages", [])).count(marker)


def reset(box, repo: Path) -> None:
    box.run(["git", "checkout", "-q", "--", "."], cwd=repo, expect=0)
    box.run(["git", "clean", "-q", "-f", "--", "calc"], cwd=repo, expect=0)


def run_case(box, repo: Path, writes: list[dict], expected: str | None, claude: str = "claude") -> dict:
    reset(box, repo)
    marker = expected or advisory("calc/big")
    bodies, misses = session(box, repo, writes, marker, claude)
    return {"advisories": advisories(bodies, marker), "requests": len(bodies),
            "tools": [tool["name"] for tool in bodies[0].get("tools", [])], "misses": misses}


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
    assert counts == {"edit": 1, "write-new": 1, "bash-python": 1, "bash-typescript": 0}, results
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

    assert result["advisories"] == 1, result


# --- the scripted model, without Claude Code -----------------------------------------

BASH_WRITE = [{"tool_use": {"name": "Bash", "input": {"command": heredoc("calc/big.py", "x = 1\n")}}}]
HOOK = ["crapkit", "claude-hook", "--protocol", "1"]


def fake_box(tmp_path: Path) -> SimpleNamespace:
    (tmp_path / "shim-bin").mkdir()
    (tmp_path / "shim.log").write_text("", encoding="utf-8")
    return SimpleNamespace(root=tmp_path)


def shim_records(box, pid: int, *events: str) -> None:
    lines = [{"event": "start", "pid": pid, "argv": HOOK} if kind == "start" else {"event": "exit", "pid": pid, "code": 2}
             for kind in events]
    with open(box.root / "shim.log", "a", encoding="utf-8") as log:
        log.writelines(json.dumps(line) + "\n" for line in lines)


def after_write(error: str = "") -> dict:
    result = {"type": "tool_result", "tool_use_id": "toolu_0000", "content": error or "(Bash completed with no output)",
              "is_error": bool(error)}
    # Claude Code 2.1.281 sends a system-role message after the tool_result.
    reminder = {"role": "system", "content": [{"type": "text", "text": "<total_tokens>1 tokens left</total_tokens>"}]}
    return {"messages": [{"role": "user", "content": "make the change"}, {"role": "user", "content": [result]}, reminder]}


@pytest.mark.kit
@pytest.mark.parametrize("error, events, why", [
    ("Exit code 1\n/usr/bin/bash: line 1: calc/big.py: No such file or directory", (),
     "the write failed, so no PostToolUse hook ran"),
    ("", ("start",), "Claude Code cancelled it at its timeout"),
])
def test_a_bash_write_whose_hook_left_no_exit_goes_again_at_once(error, events, why, tmp_path, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0.5)
    box = fake_box(tmp_path)
    model = Model(box, BASH_WRITE, advisory("calc/big.py"), "calc/grade.py")
    shim_records(box, 7, *events)

    assert model(after_write(error), 0 + len(BASH_WRITE)) == BASH_WRITE[0]
    assert len(model.misses) == 1 and why in model.misses[0]
    assert (error.splitlines() or [""])[-1] in model.misses[0]


@pytest.mark.kit
def test_a_bash_write_whose_hook_exited_reads_without_going_again(tmp_path):
    box = fake_box(tmp_path)
    model = Model(box, BASH_WRITE, advisory("calc/big.py"), "calc/grade.py")
    shim_records(box, 7, "start", "exit")

    assert model(after_write(), 1)["tool_use"]["name"] == "Read"
    assert model.misses == []


@pytest.mark.kit
def test_after_its_retries_a_bash_write_reads_and_the_misses_say_why(tmp_path, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0.5)
    box = fake_box(tmp_path)
    model = Model(box, BASH_WRITE, advisory("calc/big.py"), "calc/grade.py")
    replies = [model(after_write(), turn) for turn in range(1, RETRIES + 3)]

    assert replies[:RETRIES] == [BASH_WRITE[0]] * RETRIES
    assert replies[RETRIES]["tool_use"]["name"] == "Read"
    assert len(model.misses) == RETRIES + 1


@pytest.mark.kit
def test_an_async_hook_is_still_awaited(tmp_path, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0.5)
    box = fake_box(tmp_path)
    write = [{"tool_use": {"name": "Write", "input": {"file_path": "calc/big.py", "content": "x = 1\n"}}}]
    model = Model(box, write, advisory("calc/big.py"), "calc/grade.py")

    with pytest.raises(AssertionError, match="the hook's exit in the shim log"):
        model(after_write(), 1)
