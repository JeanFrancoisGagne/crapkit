"""docs/harnesses.md, the page that wires crapkit into each agent, held to the
code and to its own shape.

A reader pastes one section's first block into their agent's config file and
replaces one placeholder. So each first block has to parse in that agent's
format, carry /absolute/path/to/your/repo and no other placeholder, and start
the command crapkit's own parser reads as `crapkit mcp --repo PATH` (Aider's
as `crapkit rescore --gate --repo PATH`). Each section then answers the same
six questions in the same order. The deploy profiles under
tests/deploy/profiles/ find their block by the section heading, so the
headings are the profiles' names, and the docs-harness-sections cell in
tests/deploy/test_docs_harness_sections.py starts the argv for real.
"""
from __future__ import annotations

import json
import re
import shlex
import tomllib
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from crapkit.cli.parser import build_parser

ROOT = Path(__file__).resolve().parents[2]
PAGE = "docs/harnesses.md"
PLACEHOLDER = "/absolute/path/to/your/repo"
# One heading per agent the deploy suite carries a profile for, in page order.
HARNESSES = (
    "Claude Code", "Claude Desktop", "Claude Agent SDK", "claude-code-action", "Codex", "Cursor", "Windsurf",
    "VS Code with GitHub Copilot", "GitHub Copilot CLI", "Copilot cloud agent", "Kiro", "Gemini CLI", "Qwen Code",
    "OpenCode", "Goose", "Amp", "Crush", "oh-my-pi", "Cline", "Roo Code", "Kilo Code", "Continue", "Zed",
    "JetBrains AI Assistant", "Junie", "Aider", "Antigravity",
)
ROWS = ("Config file", "Starts in", "Environment", "Versions", "Plugin hooks", "After an upgrade")
LINTERS = {"Aider"}
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)
OTHER_PLACEHOLDERS = re.compile(r"<[a-z][a-z_ -]*>|/path/to/(?!your/repo)|YOUR_|\bREPLACE\b")
SERVER_TABLES = ("mcpServers", "servers", "context_servers", "mcp", "amp.mcpServers", "mcp_servers", "extensions")


@lru_cache(maxsize=None)
def page() -> str:
    return (ROOT / PAGE).read_text(encoding="utf-8")


def sections(text: str | None = None) -> dict[str, str]:
    """`## Heading` -> the body under it, in page order."""
    parts = re.split(r"^## (.+)$", text if text is not None else page(), flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def first_fence(body: str) -> tuple[str, str]:
    """(language, text) of a section's first fenced block."""
    found = FENCE.search(body)
    assert found, "a section with no config block"
    return found[1], found[2]


# --- reading a block the way its agent reads it ---------------------------------------

def _servers(data: dict) -> dict:
    """The server table under whichever key this agent's format names it."""
    for key in SERVER_TABLES:
        if key in data:
            return data[key]
    raise AssertionError(f"no server table in {sorted(data)}")


def _argv(entry: dict) -> list[str]:
    """One server entry as the argv its agent spawns."""
    command = entry.get("command", entry.get("cmd"))
    if isinstance(command, list):
        return command
    return [command, *entry.get("args", [])]


def _lint_command(value) -> list[str]:
    """Aider's lint-cmd: one string, or a list whose first entry may carry a `lang: ` prefix."""
    command = value[0] if isinstance(value, list) else value
    return shlex.split(re.sub(r"^[a-z]+: ", "", command))


def _from_config(data: dict) -> list[str]:
    if isinstance(data.get("mcpServers"), list):
        (entry,) = [entry for entry in data["mcpServers"] if entry.get("name") == "crapkit"]
        return _argv(entry)
    if "lint-cmd" in data:
        return _lint_command(data["lint-cmd"])
    return _argv(_servers(data)["crapkit"])


READERS = {"json": lambda text: _from_config(json.loads(text)),
           "toml": lambda text: _from_config(tomllib.loads(text)),
           "yaml": lambda text: _from_config(yaml.safe_load(text))}


def spawn_argv(lang: str, text: str) -> list[str]:
    """The argv an agent starts from this block, parsed in the block's format."""
    assert lang in READERS, f"a config block in {lang!r}, a format no reader here parses"
    return READERS[lang](text)


# --- the page ---------------------------------------------------------------------------

def test_there_is_one_section_per_harness_in_page_order():
    assert tuple(sections()) == HARNESSES


def profile_headings(root: Path) -> set[str]:
    """The [doc] heading of every deploy profile under `root`."""
    return {tomllib.loads(path.read_text(encoding="utf-8"))["doc"]["heading"] for path in root.glob("*.toml")}


def test_the_headings_are_the_deploy_profiles_names_when_the_profiles_exist():
    """tests/deploy/profiles/*.toml name the page's heading in [doc] heading;
    the profile cells find their block by it."""
    headings = profile_headings(ROOT / "tests" / "deploy" / "profiles")
    assert not headings or headings == set(HARNESSES)


def test_the_profile_reader_takes_each_doc_heading(tmp_path):
    (tmp_path / "vscode.toml").write_text('name = "x"\n[doc]\nheading = "VS Code with GitHub Copilot"\n',
                                          encoding="utf-8")
    assert profile_headings(tmp_path) == {"VS Code with GitHub Copilot"}
    assert profile_headings(tmp_path / "none") == set()


@pytest.mark.parametrize("harness", HARNESSES)
def test_the_first_block_parses_and_starts_crapkit_on_the_placeholder_repo(harness):
    lang, text = first_fence(sections()[harness])
    argv = spawn_argv(lang, text)
    subcommand = ["rescore", "--gate"] if harness in LINTERS else ["mcp"]
    appended = ["app/edited.py"] if harness in LINTERS else []  # a linter gets the edited files appended

    assert argv == ["crapkit", *subcommand, "--repo", PLACEHOLDER]
    assert build_parser().parse_args([*argv[1:], *appended]).repo == PLACEHOLDER, "an argv crapkit's parser reads"


@pytest.mark.parametrize("harness", HARNESSES)
def test_the_first_block_carries_the_one_placeholder_and_no_other(harness):
    _, text = first_fence(sections()[harness])

    assert PLACEHOLDER in text
    assert OTHER_PLACEHOLDERS.findall(text) == []


@pytest.mark.parametrize("harness", HARNESSES)
def test_each_section_answers_the_six_questions_in_order(harness):
    body = sections()[harness]
    rows = re.findall(r"^\| ([A-Z][a-z]+(?: [a-z]+)*) \|", body, re.M)

    assert tuple(rows) == ROWS
    assert f"| | {harness} |" in body, "the table names the agent it answers for"


def anchor(heading: str) -> str:
    """GitHub's anchor for a heading."""
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")


def test_the_summary_table_links_every_section():
    for harness in HARNESSES:
        assert f"[{harness}](#{anchor(harness)})" in page(), harness


def test_the_floors_the_page_names_are_the_ones_the_deploy_pins_hold():
    """The releases a reader is told the install tests run are the ones they run:
    the pinned Claude Code and Codex, and the oldest Claude Code floor, where the
    hook cells run the plugin's one shell-form hook."""
    pins = tomllib.loads((ROOT / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))["harness"]
    claude, codex = sections()["Claude Code"], sections()["Codex"]
    oldest = min(pins["claude-code"]["floors"], key=lambda release: tuple(map(int, release.split("."))))

    assert f"run it on Claude Code {oldest} as well" in claude
    assert f"The deploy suite runs {pins['claude-code']['version']}" in claude
    assert f"The deploy suite runs {pins['codex']['version']}" in codex


def test_the_codex_upgrade_row_moves_a_pinned_marketplace_to_the_new_tag():
    """Every page prints the Codex install line with `--ref vX.Y.Z`, and a
    marketplace added at a tag stays there, so a new thread alone keeps the old
    plugin. The row said Codex upgrades git marketplaces when it starts."""
    row = next(line for line in sections()["Codex"].splitlines() if line.startswith("| After an upgrade |"))

    assert "`codex plugin marketplace remove crapkit`" in row
    assert "`codex plugin add crapkit@crapkit`" in row and "at the new `--ref`" in row
    assert "upgrades configured git marketplaces when it starts" not in row


@pytest.mark.parametrize("linking", ["README.md", "docs/adoption.md", "docs/agent-json.md",
                                     "plugin/skills/crapkit-onboard/SKILL.md"])
def test_the_pages_a_reader_starts_from_link_the_harness_page(linking):
    assert "harnesses.md" in (ROOT / linking).read_text(encoding="utf-8")


# --- the readers themselves ------------------------------------------------------------

def test_the_readers_follow_each_format_to_the_argv():
    toml = '[mcp_servers.crapkit]\ncommand = "crapkit"\nargs = ["mcp"]\n'
    opencode = '{"mcp": {"crapkit": {"type": "local", "command": ["crapkit", "mcp"]}}}'
    goose = "extensions:\n  crapkit:\n    cmd: crapkit\n    args: [mcp]\n"
    cont = "mcpServers:\n  - name: other\n    command: x\n  - name: crapkit\n    command: crapkit\n    args: [mcp]\n"

    for lang, text in (("toml", toml), ("json", opencode), ("yaml", goose), ("yaml", cont)):
        assert spawn_argv(lang, text) == ["crapkit", "mcp"], (lang, text)
    for lint in ("lint-cmd: crapkit rescore --gate\n", 'lint-cmd:\n  - "python: crapkit rescore --gate"\n'):
        assert spawn_argv("yaml", lint) == ["crapkit", "rescore", "--gate"], lint
    with pytest.raises(AssertionError, match="no reader"):
        spawn_argv("ts", "")
    with pytest.raises(AssertionError, match="no server table"):
        spawn_argv("json", "{}")


def test_the_placeholder_check_catches_a_second_placeholder():
    assert OTHER_PLACEHOLDERS.findall('"command": "/path/to/crapkit", "args": ["<repo>"]') == ["/path/to/", "<repo>"]
    assert OTHER_PLACEHOLDERS.findall(f'"args": ["{PLACEHOLDER}"]') == []


def test_the_anchor_is_githubs():
    assert anchor("VS Code with GitHub Copilot") == "vs-code-with-github-copilot"
    assert anchor("oh-my-pi") == "oh-my-pi"
