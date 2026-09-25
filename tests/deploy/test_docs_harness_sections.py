"""docs/harnesses.md's blocks, started the way the page says an agent starts them.

A reader pastes a section's first block into their agent's config and replaces
the one placeholder. The cell reads every section of the stamped page through
tests/unit/test_harness_docs_contract.py's own readers (PyYAML, tomllib and
json, run in a sandbox venv that holds the candidate), fills the placeholder
with a repo the README start measured, and starts what each block starts: the
MCP server from a directory that is not the repo, since most agents start it
elsewhere and `--repo` is what names the repo; and Aider's lint command, whose
exit codes the Aider section spells out.

The per-harness spawn rules (working directory, environment, command lookup)
are the profile cells' job in the deploy-harnesses packet; this cell holds the
page's own claims.
"""
from __future__ import annotations

import json
from pathlib import Path

from docs_support import adopt_measured, init, venv_with
from kit import docsnip, repos
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-docs"
PLACEHOLDER = "/absolute/path/to/your/repo"
SECTIONS = 27
TOOLS = 12
# ccn 7 over the ceiling of 6: what an agent's edit adds before Aider lints the file.
BREACH = ("\n\ndef route(a, b, c, d):\n    if a and b:\n        return 1\n    if c or d:\n        return 2\n"
          "    if a and d:\n        return 3\n    return 4\n")
READ_PAGE = """
import json, sys
sys.path.insert(0, "tests/unit")
from test_harness_docs_contract import first_fence, sections, spawn_argv
print(json.dumps({name: spawn_argv(*first_fence(body)) for name, body in sections().items()}))
"""


def page_argvs(box) -> dict[str, list[str]]:
    """Each section's heading and the argv its first block starts, read from the stamped page."""
    shown = box.run(["python", "-c", READ_PAGE], cwd=docsnip.root(), expect=0)
    return json.loads(shown.stdout)


def filled(argv: list[str], repo: Path) -> list[str]:
    """The placeholder replaced the way a reader replaces it, with the repo's
    path in forward slashes, which JSON, TOML and YAML all carry unescaped."""
    return [repo.as_posix() if arg == PLACEHOLDER else arg for arg in argv]


def served(box, argv: list[str]) -> tuple[list[str], dict]:
    """Tool names and get_next_item's result, from a server started in the home directory."""
    with McpClient.in_box(box, argv, cwd=box.home) as client:
        client.initialize()
        names = [tool["name"] for tool in client.tools()]
        return names, client.call("get_next_item", {})


@cell("docs-harness-sections", channel="config from docs/harnesses.md, every section", harness="none",
      scenario="fresh: each section's first block parses in its agent's format; the MCP argv it starts, with the "
               "placeholder filled, serves the measured repo from another directory",
      use_cases="MCP client wiring", os=("linux", "windows"), image="core", cadence="push",
      real_cli=False)
def test_every_section_block_starts_a_server_that_serves_the_repo(box, templates):
    venv_with(box, "crapkit[py]", "pyyaml")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    adopt_measured(box, repo)
    argvs = page_argvs(box)
    mcp = {tuple(argv) for name, argv in argvs.items() if argv[1] == "mcp"}

    assert len(argvs) == SECTIONS, sorted(argvs)
    for argv in mcp:
        names, item = served(box, filled(list(argv), repo))
        assert len(names) == TOOLS, names
        assert not item.get("isError") and "calc/grade.py" in item["content"][0]["text"], item


def aider_lint(box, argv: list[str], repo: Path, expect: int):
    """What Aider runs after an edit: the lint command with the edited file appended."""
    return box.run([*filled(argv, repo), "calc/grade.py"], cwd=repo, expect=expect)


@cell("docs-harness-aider", channel="lint-cmd from docs/harnesses.md > Aider", harness="none (Aider's lint call)",
      scenario="fresh: before `crapkit coverage` the lint exits 1 naming no snapshot; after it an unchanged file "
               "exits 0 and an edit that adds a function over the ceiling exits 6",
      use_cases="the Aider lint route", os=("linux", "windows"), image="core", cadence="push", real_cli=False)
def test_the_aider_lint_command_exits_as_the_page_says(box, templates):
    venv_with(box, "crapkit[py]", "pyyaml")
    argv = page_argvs(box)["Aider"]
    unmeasured = repos.checkout(box, "py-pytest", cache=templates, repo_name="unmeasured")
    init(box, unmeasured)

    assert "no snapshot" in aider_lint(box, argv, unmeasured, expect=1).stderr
    repo = repos.checkout(box, "py-pytest", cache=templates)
    adopt_measured(box, repo)
    aider_lint(box, argv, repo, expect=0)
    with (repo / "calc" / "grade.py").open("a", encoding="utf-8") as source:
        source.write(BREACH)
    aider_lint(box, argv, repo, expect=6)
