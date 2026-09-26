"""The pages describe the hook plugin/hooks/hooks.json ships, not an older one.

Up to 0.8.0 the plugin shipped 50 exec-form handlers whose arguments sat in
`args`. Claude Code reads `args` only from 2.1.139 on, and Codex, Cursor,
GitHub Copilot CLI and VS Code keep a handler's `command` alone, so each of
them ran a bare `crapkit` that printed its usage. The pages said so: the hook
needs Claude Code 2.1.139, this agent runs a bare `crapkit`, do not install the
plugin there. Each of those claims holds only while a shipped handler passes
`args`. A shell-form handler carries its arguments in `command`, which every
one of those agents keeps, and a page that still makes the claim sends a reader
away from a hook that works. A sentence about a plugin from 0.8.0 or earlier
holds either way, since that plugin still ships the old handlers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "plugin" / "hooks" / "hooks.json"
# The pages a user reads; the changelog records what older releases did.
PAGES = sorted({"README.md", "AGENTS.md", "CONTRIBUTING.md",
                *(path.relative_to(ROOT).as_posix() for path in (ROOT / "docs").glob("*.md")),
                *(path.relative_to(ROOT).as_posix() for path in (ROOT / "plugin").rglob("*.md"))})
EXEC_FORM_CLAIM = re.compile(r"needs (?:Claude Code )?2\.1\.139|bare `crapkit`"
                             r"|[Dd]o not (?:install|add) (?:crapkit's |the )?(?:Claude Code )?plugin")
OLD_PLUGIN = re.compile(r"\b0\.8\.0\b")
# A sentence ends at a period after a letter, a code span or a parenthesis, so a
# version number such as 2.1.139 stays inside its sentence; a table cell ends at `|`.
SENTENCE_END = re.compile(r"(?<=[A-Za-z`)]\.)\s+|\s\|\s")


def handlers(hooks: dict) -> list[dict]:
    """Every handler a hooks.json registers, across its events and matchers."""
    return [handler for groups in hooks["hooks"].values() for group in groups for handler in group["hooks"]]


def passes_args(hooks: dict) -> bool:
    """Whether a handler passes its arguments in `args`, the exec-form field only
    Claude Code 2.1.139 and later read."""
    return any("args" in handler for handler in handlers(hooks))


def exec_form_claims(text: str) -> list[str]:
    """The sentences that describe the exec-form hook's failures, bar those about
    a plugin from 0.8.0 or earlier."""
    sentences = SENTENCE_END.split(" ".join(text.split()))
    return [sentence for sentence in sentences if EXEC_FORM_CLAIM.search(sentence) and not OLD_PLUGIN.search(sentence)]


@pytest.mark.parametrize("page", PAGES)
def test_a_page_describes_exec_form_failures_only_while_a_handler_passes_args(page):
    shipped = json.loads(HOOKS.read_text(encoding="utf-8"))
    text = (ROOT / page).read_text(encoding="utf-8")

    assert passes_args(shipped) or exec_form_claims(text) == []


def test_the_pages_include_the_ones_that_name_agents():
    assert {"README.md", "docs/harnesses.md", "docs/adoption.md", "plugin/skills/crapkit-onboard/SKILL.md"} <= set(PAGES)
    assert "CHANGELOG.md" not in PAGES


def test_args_are_read_from_every_event_and_matcher():
    shell = {"type": "command", "command": "crapkit claude-hook --protocol 1"}
    exec_form = {"type": "command", "command": "crapkit", "args": ["claude-hook", "--protocol", "1"]}

    assert passes_args({"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [shell]}]}}) is False
    assert passes_args({"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [shell]},
                                                  {"matcher": "Write", "hooks": [shell, exec_form]}]}}) is True
    assert passes_args({"hooks": {}}) is False


@pytest.mark.parametrize("text", [
    "The hook needs\nClaude Code 2.1.139 or later: an older release drops the hook's arguments.",
    "| Plugin hooks | None. Cursor keeps only `command`, so crapkit's hook would run as a bare `crapkit`. |",
    "Do not install crapkit's plugin with `copilot plugin install`.",
    "none; do not add the plugin as a VS Code agent plugin",
])
def test_a_claim_about_the_exec_form_hook_is_found(text):
    assert len(exec_form_claims(text)) == 1


@pytest.mark.parametrize("text", [
    "A plugin from 0.8.0 or earlier ships no Codex manifest: Codex\n0.156.1 lists its hooks as untrusted "
    "PostToolUse hooks that run a bare `crapkit`, and they should stay untrusted.",
    "The hook is one shell command, so it runs as written in any Claude Code version with plugin support.",
    "`crapkit doctor --plugin-root` names a Claude Code below 2.1.139 when the plugin's hooks pass `args`.",
])
def test_a_sentence_about_the_old_plugin_or_the_shell_form_hook_passes(text):
    assert exec_form_claims(text) == []


def test_a_claim_is_cut_at_its_own_sentence():
    text = "Codex loads no hook. A plugin from 0.8.0 runs a bare `crapkit`. Cursor runs a bare `crapkit`."
    after_a_capital = "It needs the crapkit CLI on PATH. The hook needs Claude Code 2.1.139 or later."

    assert exec_form_claims(text) == ["Cursor runs a bare `crapkit`."]
    assert exec_form_claims(after_a_capital) == ["The hook needs Claude Code 2.1.139 or later."]
