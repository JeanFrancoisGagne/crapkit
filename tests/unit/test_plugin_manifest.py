"""The plugin is the distribution artifact: three skills, one hook, one MCP server.

Nothing in it is generated at install time, so drift between a manifest and the code
it points at ships silently to every machine that installed the plugin. Four pins
hold it: the manifest version against pyproject, the handler list against crapkit's
own language->extension map, the event set against the safety contract, and every
skill link against the blob URL, because `docs/lanes.md` resolves nowhere in the
repo an agent is actually working in.
"""
from __future__ import annotations

import json
import re
import tomllib
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN = "plugin"
PLUGIN_JSON = f"{PLUGIN}/.claude-plugin/plugin.json"
HOOKS_JSON = f"{PLUGIN}/hooks/hooks.json"
MCP_JSON = f"{PLUGIN}/.mcp.json"
CRAPKIT_SKILL = f"{PLUGIN}/skills/crapkit/SKILL.md"
RECOVER_SKILL = f"{PLUGIN}/skills/crapkit-recover/SKILL.md"
ONBOARD_SKILL = f"{PLUGIN}/skills/crapkit-onboard/SKILL.md"
SKILLS = (CRAPKIT_SKILL, RECOVER_SKILL, ONBOARD_SKILL)

BLOB = "https://github.com/JeanFrancoisGagne/crapkit/blob/main/"
# The two tools whose payload carries `tool_input.file_path`. NotebookEdit names
# `notebook_path` and is out of protocol 1 on purpose.
TOOLS = ("Edit", "Write")

# A page that lives in the crapkit repo and nowhere else: bare, it points an agent
# at a file the repo it is working in does not have.
_REPO_PAGE = re.compile(r"docs/[\w.-]+\.(?:md|html)|README\.md|AGENTS\.md")
_FLOOR = re.compile(r"crapkit (\d+\.\d+\.\d+) or newer")
_LINK = re.compile(re.escape(BLOB) + r"([\w./-]+?)(?:#([\w-]+))?\)")
_HEADING = re.compile(r"^#{1,6} +(.+?)\s*$", re.MULTILINE)
_NOT_IN_SLUG = re.compile(r"[^\w\- ]")


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    page = ROOT / name
    assert page.is_file(), f"{name} does not exist"
    return page.read_text(encoding="utf-8")


def _json(name: str) -> dict:
    return json.loads(_doc(name))


def _frontmatter(page: str) -> str:
    _, _, rest = _doc(page).partition("---\n")
    block, _, _ = rest.partition("\n---\n")
    assert block, f"{page} carries no frontmatter block"
    return block


def _field(page: str, key: str) -> str:
    (line,) = [ln for ln in _frontmatter(page).splitlines() if ln.startswith(f"{key}:")]
    return line.partition(":")[2].strip()


def _bare_repo_page_mentions(text: str) -> list[str]:
    """Every crapkit-repo page named without the blob URL in front of it."""
    return [m.group(0) for m in _REPO_PAGE.finditer(text)
            if not text[:m.start()].endswith(BLOB)]


# --- A1: the skill pages ------------------------------------------------------

def test_skill_links_absolute_and_descriptions_present():
    """A skill installs into ~/.claude or a plugin cache and is read from inside
    some other repo. `docs/ratchet.md` resolves there to nothing, so every repo
    page a skill names carries its full blob URL. And a skill with no description
    is a skill the model never reaches for."""
    bare = {(page, mention) for page in SKILLS
            for mention in _bare_repo_page_mentions(_doc(page))}
    assert bare == set(), "each names a crapkit repo page that does not exist where it runs"

    missing = [page for page in SKILLS if not _field(page, "description")]
    assert missing == [], "no description is no pointer"


def test_the_link_detector_sees_a_bare_reference_and_ignores_an_absolute_one():
    """Guards the test above, which passes on a detector that finds nothing."""
    assert _bare_repo_page_mentions("read `docs/lanes.md#timeouts` first") == ["docs/lanes.md"]
    assert _bare_repo_page_mentions(f"read [lanes]({BLOB}docs/lanes.md#timeouts)") == []
    assert _bare_repo_page_mentions("`README.md#exit-codes` owns it") == ["README.md"]


def _slug(heading: str) -> str:
    """GitHub's anchor form: lowered, punctuation dropped, spaces hyphenated."""
    return _NOT_IN_SLUG.sub("", heading.lower()).replace(" ", "-")


@lru_cache(maxsize=None)
def _anchors(page: str) -> frozenset[str]:
    return frozenset(_slug(heading) for heading in _HEADING.findall(_doc(page)))


def _link_is_broken(path: str, anchor: str) -> bool:
    """A link with no anchor only has to name a file that exists."""
    if not (ROOT / path).is_file():
        return True
    return bool(anchor) and anchor not in _anchors(path)


def test_every_skill_link_lands_on_a_page_and_heading_that_exist():
    """An absolute URL fails silently: it 404s in a browser the agent is not
    driving. Renaming a heading in docs/ has to break here instead."""
    broken = {(page, path, anchor) for page in SKILLS
              for path, anchor in _LINK.findall(_doc(page))
              if _link_is_broken(path, anchor)}
    assert broken == set(), "each link names a page or heading the repo does not hold"


def test_the_slug_rule_matches_the_headings_it_is_pointed_at():
    """Guards the test above: a slug rule that mangles every heading also passes
    it, because the anchors then match nothing and the set comprehension is
    filtered by `anchor and ...`."""
    assert (_slug("a lane that wrote no artifact: seven causes")
            == "a-lane-that-wrote-no-artifact-seven-causes")
    assert "exit-codes" in _anchors("README.md")
    assert "reportonfailure" in _anchors("docs/lanes.md")


def test_the_pytest_cause_in_the_recover_table_owns_the_pytest_section():
    """One row carried both providers and linked the vitest section alone. The
    agent that got there from a pytest lane read about @vitest/coverage-v8 and
    never saw that pytest-cov belongs to the interpreter the suite runs in."""
    rows = [line for line in _doc(RECOVER_SKILL).splitlines() if "pytest-cov" in line]

    assert rows, "the recover table stopped naming the pytest cause"
    for row in rows:
        assert f"{BLOB}docs/lanes.md#pytest)" in row, \
            f"the pytest cause links elsewhere: {row}"


def test_the_skill_pages_still_link_out_at_all():
    """Guards both link tests from the other side: a page that stopped naming any
    repo page passes the absolute-link test by saying nothing, and a link
    extractor that matches nothing passes the anchor test the same way."""
    counted = {page: len(_LINK.findall(_doc(page))) for page in SKILLS}
    assert min(counted.values()) >= 3, f"a skill page dropped its links: {counted}"
    assert sum(counted.values()) >= 13


def test_the_crapkit_description_quotes_both_strings_a_refused_commit_prints():
    """A description quoting a string nothing prints is a pointer that never fires.
    The two strings come out of two different files: the shell wrapper prints one,
    the gate itself prints the other, and an agent reading the terminal sees both."""
    from crapkit.cli import verifying

    described = _field(CRAPKIT_SKILL, "description")
    for text, quoted in ((_doc("git-hooks/pre-commit"), "commit blocked by the complexity gate"),
                         (Path(verifying.__file__).read_text(encoding="utf-8"),
                          "decompose before committing")):
        assert quoted in text, f"the gate reworded {quoted!r}"
        assert quoted in described, f"the description no longer quotes {quoted!r}"


def test_the_onboarding_skill_leads_with_the_marketplace_flow():
    """Copy-install is the fallback. The two marketplace lines come first, in
    order, or a human types the fallback and never gets the hook or the MCP
    server that only the plugin carries."""
    text = _doc(ONBOARD_SKILL)
    add = text.index("claude plugin marketplace add JeanFrancoisGagne/crapkit")
    install = text.index("claude plugin install crapkit@crapkit")
    copied = text.index("plugin/skills/")
    assert add < install < copied, "the fallback is printed before the flow it falls back from"


# --- A2: the manifests --------------------------------------------------------

def test_plugin_version_matches_pyproject():
    """One artifact, one version. A plugin.json that lags pyproject tells `doctor
    --plugin-root` the CLI is ahead when nothing moved."""
    pyproject = tomllib.loads(_doc("pyproject.toml"))
    assert _json(PLUGIN_JSON)["version"] == pyproject["project"]["version"]


def test_the_plugin_manifest_names_itself_and_its_cli_floor():
    manifest = _json(PLUGIN_JSON)
    assert manifest["name"] == "crapkit"
    assert _FLOOR.search(manifest["description"]), \
        "the description names no `crapkit X.Y.Z or newer` floor"


def test_the_plugin_ships_the_three_skills_the_repo_holds():
    found = sorted(p.parent.name for p in (ROOT / PLUGIN / "skills").glob("*/SKILL.md"))
    assert found == ["crapkit", "crapkit-onboard", "crapkit-recover"]


def test_the_plugin_tree_holds_no_python():
    """`source: ./plugin` copies this tree into the plugin cache. A .py in it is a
    fifth crapkit source tree that never executes, and crapkit's own unscoped-source
    warning fires on it at every commit."""
    assert sorted((ROOT / PLUGIN).rglob("*.py")) == []


# --- the hook: generated from the language map, PostToolUse only ---------------

HANDLER_GOLDEN = Path(__file__).resolve().parent.parent / "goldens" / "plugin" / "hooks_handler_schema.json"


@lru_cache(maxsize=None)
def _recorded_handler() -> dict:
    """The handler schema, held in one file outside the code that generates it.

    Claude Code ignores a field it does not recognise instead of refusing it, so
    a misspelled `asyncRewake` ships a hook that runs, never rewakes, and says
    nothing about it. The golden is what the spelling is pinned against; the
    generator below reads it rather than repeating it, so a rename cannot land
    in both places at once and pass.
    """
    golden = json.loads(HANDLER_GOLDEN.read_text(encoding="utf-8"))
    assert golden["source"] in ("design-doc", "live-capture"), \
        "a schema golden with no provenance is a guess nobody can date"
    return golden["handler"]


def _handler(tool: str, extension: str) -> dict:
    return {**_recorded_handler(), "if": f"{tool}(*{extension})"}


def test_the_recorded_schema_spells_both_async_fields_the_way_the_harness_reads_them():
    """Sync handlers run strictly serially, so a snake_case guess here is a
    3.6 s pause on a 19-edit batch, and a hook that never rewakes is a breach
    written to a stream nobody reads. Both failures are silent."""
    handler = _recorded_handler()

    assert {k for k in handler if "async" in k.lower()} == {"async", "asyncRewake"}
    assert (handler["async"], handler["asyncRewake"]) == (True, True)


def _generated_handlers() -> list[dict]:
    """One handler per (tool, extension) pair, extensions in map order.

    The `if` filter skips the spawn itself, so an extension crapkit measures but
    the map forgot here is an extension the advisory never sees.
    """
    from crapkit.universe import LANGUAGE_EXTENSIONS

    return [_handler(tool, extension)
            for extensions in LANGUAGE_EXTENSIONS.values()
            for extension in extensions
            for tool in TOOLS]


def _generated_hooks() -> dict:
    return {"hooks": {"PostToolUse": [{"matcher": "|".join(TOOLS),
                                       "hooks": _generated_handlers()}]}}


def test_hooks_json_is_what_the_language_extension_map_generates():
    """The committed file is generated output. Regenerate it here and diff, so a
    new language in `LANGUAGE_EXTENSIONS` cannot land without its handlers."""
    assert _json(HOOKS_JSON) == _generated_hooks()


def test_the_generator_covers_every_extension_crapkit_measures():
    """Guards the diff above: two empty dicts also compare equal."""
    from crapkit.universe import LANGUAGE_EXTENSIONS

    extensions = [e for exts in LANGUAGE_EXTENSIONS.values() for e in exts]
    filters = {h["if"] for h in _generated_handlers()}
    assert len(filters) == len(extensions) * len(TOOLS) >= 20
    assert {"Edit(*.py)", "Write(*.py)", "Edit(*.rs)", "Write(*.tsx)"} <= filters


def _committed_handlers() -> list[dict]:
    """Every handler the committed hooks.json registers, flat."""
    return [handler for event in _json(HOOKS_JSON)["hooks"].values()
            for matcher in event for handler in matcher["hooks"]]


def _spawned_subcommand(handler: dict) -> str:
    """What `crapkit <this>` a handler runs: the first arg that is not a flag."""
    return next(arg for arg in handler["args"] if not arg.startswith("-"))


def test_hooks_json_subcommands_exist_in_parser():
    """The pin that would have caught `claude-hook` shipping ahead of the CLI.

    The plugin and the CLI are released apart, and a handler naming a subcommand
    argparse does not have exits 2 with a usage dump, on every matching edit, on
    every machine that installed the plugin. Nothing on either side notices: the
    hook is registered, it runs, and PostToolUse reads its exit 2 as a finding.
    """
    import argparse

    from crapkit.cli import build_parser

    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    spawned = {_spawned_subcommand(h) for h in _committed_handlers()}

    assert spawned - set(subs[0].choices) == set(), \
        "hooks.json spawns a subcommand this CLI has no parser for"


def test_the_subcommand_reader_finds_the_one_the_hook_actually_spawns():
    """Guards the test above: an empty set is a subset of everything, and a
    reader that returned the `--protocol` value instead would pass it too."""
    assert {_spawned_subcommand(h) for h in _committed_handlers()} == {"claude-hook"}
    assert _spawned_subcommand({"args": ["claude-hook", "--protocol", "1"]}) == "claude-hook"


def test_the_plugin_registers_post_tool_use_and_nothing_else():
    """Safety contract #2. Exit 2 on Stop blocks the stop, and the gate reads the
    filesystem rather than the conversation, so every non-actionable verdict loops
    forever."""
    assert list(_json(HOOKS_JSON)["hooks"]) == ["PostToolUse"]


@pytest.mark.parametrize("field,value", [("async", True), ("asyncRewake", True),
                                         ("timeout", 20), ("command", "crapkit")])
def test_every_handler_stays_async_bare_exe_and_bounded(field: str, value: object):
    """Sync hooks run serially: a 19-edit batch becomes a 3.6 s pause. `python`
    resolves to the WindowsApps stub and to six venvs without crapkit; the console
    script is the real exe Windows exec form needs."""
    assert {h[field] for h in _committed_handlers()} == {value}


# --- the MCP server -----------------------------------------------------------

def test_the_mcp_manifest_runs_the_subcommand_the_parser_defines():
    """`.mcp.json` lives under plugin/ so it never lands in project scope, where
    it would raise a scope conflict for anyone developing crapkit itself. It spawns
    the console script for the same reason the hook handlers do: bare `python` on
    Windows resolves to the WindowsApps stub and to venvs without crapkit, which
    makes a dead MCP server with no visible error."""
    import argparse

    from crapkit.cli import build_parser

    servers = _json(MCP_JSON)["mcpServers"]
    assert servers == {"crapkit": {"command": "crapkit", "args": ["mcp"]}}

    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    assert "mcp" in subs[0].choices, "the manifest launches a subcommand argparse dropped"
