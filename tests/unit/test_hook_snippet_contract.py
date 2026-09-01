"""The Bash matcher snippet is the only hook config a consumer types by hand.

The shipped plugin registers `Edit|Write`. The `Bash` half of the advisory is a
second PostToolUse entry the consumer pastes into their own settings, and three
pages carry that JSON: README.md, docs/agent-json.md, and the 0.4.7 section of
CHANGELOG.md, which is where an upgrader reads it. `plugin/hooks/hooks.json` is
held to the code by tests/unit/test_plugin_manifest.py; these copies had no
owner, so a trailing comma, a renamed key or a drift between the three shipped
green and broke on the reader's machine, not ours.

Every fenced json block on those pages that names a matcher is parsed here and
held to the command and timeout the shipped manifest uses for its own entries.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("README.md", "docs/agent-json.md", "CHANGELOG.md")
MANIFEST = "plugin/hooks/hooks.json"

# The changelog's copy sits inside a list item, so the fence is indented and the
# closing fence carries the same indent. JSON ignores that whitespace.
_FENCE = re.compile(r"^(?P<indent>[ \t]*)```json[ \t]*\n(?P<body>.*?)\n(?P=indent)```",
                    re.MULTILINE | re.DOTALL)


def _read(name: str) -> str:
    page = ROOT / name
    assert page.is_file(), f"{name} does not exist"
    return page.read_text(encoding="utf-8")


def matcher_snippets(page: str) -> list[str]:
    """Every fenced json block on one page that registers a matcher."""
    blocks = (found.group("body") for found in _FENCE.finditer(_read(page)))
    return [block for block in blocks if '"matcher"' in block]


SNIPPETS = [(page, number, block)
            for page in PAGES
            for number, block in enumerate(matcher_snippets(page), start=1)]
IDS = [f"{page}#{number}" for page, number, _ in SNIPPETS]


def _only(items: list, what: str):
    assert len(items) == 1, f"expected one {what}, got {len(items)}"
    return items[0]


def shipped_hook() -> tuple[str, int]:
    """The command line and timeout every entry in the shipped manifest runs."""
    manifest = json.loads(_read(MANIFEST))
    entries = manifest["hooks"]["PostToolUse"]
    hooks = [hook for entry in entries for hook in entry["hooks"]]
    commands = {" ".join([hook["command"], *hook.get("args", ())]) for hook in hooks}
    timeouts = {hook["timeout"] for hook in hooks}
    return _only(sorted(commands), "command"), _only(sorted(timeouts), "timeout")


def test_every_page_that_documents_the_bash_half_carries_one():
    """An extraction that quietly matches nothing would pass every case below."""
    assert [page for page, _, _ in SNIPPETS] == list(PAGES)


@pytest.mark.parametrize(("page", "number", "block"), SNIPPETS, ids=IDS)
def test_the_snippet_registers_one_bash_posttooluse_entry(page, number, block):
    settings = json.loads(block)

    assert list(settings) == ["hooks"]
    assert list(settings["hooks"]) == ["PostToolUse"]
    entry = _only(settings["hooks"]["PostToolUse"], "PostToolUse entry")
    assert entry["matcher"] == "Bash"
    assert sorted(entry) == ["hooks", "matcher"]


@pytest.mark.parametrize(("page", "number", "block"), SNIPPETS, ids=IDS)
def test_the_snippet_runs_what_the_shipped_manifest_runs(page, number, block):
    command, timeout = shipped_hook()
    entry = _only(json.loads(block)["hooks"]["PostToolUse"], "PostToolUse entry")

    hook = _only(entry["hooks"], "hook")
    assert hook["type"] == "command"
    assert hook["command"] == command
    assert hook["timeout"] == timeout


def test_the_shipped_manifest_is_the_edit_write_half():
    """The pin above is only worth anything if the manifest it reads is the one
    that does not register Bash: that is why the snippet exists at all."""
    manifest = json.loads(_read(MANIFEST))
    matchers = {entry["matcher"] for entry in manifest["hooks"]["PostToolUse"]}

    assert matchers == {"Edit|Write"}
