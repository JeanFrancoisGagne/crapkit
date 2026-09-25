"""The commit-gate hook the docs print, armed under each install channel.

README Route 1 and Route 2 and the handbook's printf line write the same hook.
It has to reach crapkit however the reader installed it: a pipx or uv tool
launcher on PATH, or uvx with nothing on PATH at all. Each cell installs
through the README's own line, arms the hook with the page's own block, and
then commits twice the way the reader does: a staged function over the
ceiling is refused with the README's refusal text, and a clean one lands.

Cells of the deploy-docs packet: the doc half of lin-pair-* (decision f).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from kit import docsnip, repos
from kit.cells import cell

PACKET = "deploy-docs"
WINDOWS = os.name == "nt"
ROUTE_1 = "Route 1: `.git/hooks/pre-commit` (local, not committed)"
ROUTE_2 = "Route 2: a committed hooks directory"
HANDBOOK = "Enforcement: seed the ratchet, then arm the hook"
# ccn 7: three ifs and three boolean operators over a ceiling of 6.
BREACH = ("def route(a, b, c, d):\n    if a and b:\n        return 1\n    if c or d:\n        return 2\n"
          "    if a and d:\n        return 3\n    return 4\n")
CLEAN = "def route(a, b, c, d):\n    return a\n"


# --- installing and arming, each through the page's own lines --------------------------

def install(box, channel: str, repo: Path) -> None:
    """The README's line for the channel; uvx installs nothing up front."""
    if channel != "uvx":
        block = docsnip.fence("README.md", "When pip refuses", contains=f"{channel} install crapkit")
        line = next(line for line in docsnip.commands(block) if line.startswith(channel))
        box.script(line, cwd=repo, expect=0)
        box.prepend_path(box.home / ".local" / "bin")
    assert (box.which("crapkit") is None) == (channel == "uvx"), "only uvx leaves PATH without a crapkit"


def launcher(channel: str) -> list[str]:
    return ["uvx", "crapkit"] if channel == "uvx" else ["crapkit"]


SH_ROUTES = {"route1": (ROUTE_1, "cat >"), "route2": (ROUTE_2, "core.hooksPath")}


def handbook_lines() -> str:
    """The handbook block's hook lines, without the seed that needs a scored run."""
    block = docsnip.fence("docs/handbook.html", HANDBOOK, contains=".git/hooks/pre-commit")
    return "\n".join(line for line in docsnip.commands(block) if "pre-commit" in line)


def route_block(route: str) -> tuple[str, str]:
    """(shell, text) of the block that arms the hook."""
    if route in ("powershell", "pwsh"):
        return route, docsnip.fence("README.md", ROUTE_1, contains="Set-Content").text
    if route == "handbook":
        return "sh", handbook_lines()
    heading, marker = SH_ROUTES[route]
    return "sh", docsnip.fence("README.md", heading, contains=marker).text


def adopt(box, repo: Path, channel: str) -> None:
    box.run([*launcher(channel), "init"], cwd=repo, expect=0)
    commit(box, repo, "adopt crapkit", expect=0)


def commit(box, repo: Path, message: str, expect: int):
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    return box.run(["git", "commit", "-m", message], cwd=repo, env=box.commit_env(), expect=expect)


def refusal_line() -> str:
    """The first line README's 'What a refusal looks like' prints under git commit."""
    (_, printed), = docsnip.outputs(docsnip.fence("README.md", "What a refusal looks like"))
    return printed.splitlines()[0]


PAIRS = [("pipx", "route1"), ("uv tool", "route2"), ("uvx", "route1"), ("uv tool", "handbook")]
PAIRS += [("uv tool", "powershell"), ("pipx", "pwsh")] if WINDOWS else []


@cell("docs-hook-line", channel="pipx, uv tool, uvx x Route 1, Route 2, the handbook printf hook",
      harness="git", scenario="fresh: install through the README line, arm the hook with the page's block, "
                              "commit a ccn-7 function (refused with the README's text), then a clean one",
      use_cases="commit gate", os=("linux", "windows"), image="core", cadence="push")
@pytest.mark.parametrize(("channel", "route"), PAIRS, ids=[f"{c.replace(' ', '')}-{r}" for c, r in PAIRS])
def test_the_printed_hook_reaches_crapkit_under_each_install_channel(box, templates, channel, route):
    repo = repos.checkout(box, "py-pytest", cache=templates)
    install(box, channel, repo)
    adopt(box, repo, channel)
    shell, text = route_block(route)
    box.script(text, shell=shell, cwd=repo, expect=0)

    (repo / "calc" / "route.py").write_text(BREACH, encoding="utf-8")
    refused = commit(box, repo, "add route", expect=1)
    (repo / "calc" / "route.py").write_text(CLEAN, encoding="utf-8")
    commit(box, repo, "add a clean route", expect=0)

    assert refusal_line() in refused.stderr, "git shows the README's refusal"
