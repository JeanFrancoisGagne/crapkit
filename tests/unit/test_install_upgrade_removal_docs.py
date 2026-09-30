"""Every way in has its next line: the upgrade, the refusal, the removal.

A reader who installs crapkit by one route has to find, on the README and the
upgrade guide, the line that upgrades that install, what to run when the
installer refuses, and the lines that take crapkit out again. Each table and
each quoted message below was run: the upgrade lines against 0.7.6 and 0.8.0,
the lock table on Windows 11 with pip 26.2.1, pipx 1.17.6 and uv 0.12.18, and
the removal lines on a repo armed with Route 1, the merge driver and both agent
plugins. These tests keep the pages from losing a route between those runs.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
GIT_URL = "git+https://github.com/JeanFrancoisGagne/crapkit.git"
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.M | re.S)
ROW = re.compile(r"^\|\s*(?P<label>[^|`]+?)\s*\|\s*`(?P<command>[^`]+)`\s*\|\s*$", re.M)


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _section(page: str, heading: str) -> str:
    """The body under `heading`, its subsections included, down to the next
    heading of the same or a higher level."""
    text = _doc(page)
    level = heading.split(" ", 1)[0]
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    ends = [m.start() for m in re.finditer(r"^(#{1,6}) ", text[start:], re.M) if len(m[1]) <= len(level)]
    return text[start:start + ends[0]] if ends else text[start:]


def _prose(body: str) -> str:
    """The body with its fences cut out and its lines joined, as a reader reads it."""
    return " ".join(FENCE.sub("", body).split())


def _fenced_lines(body: str) -> list[str]:
    return [line.strip() for block in FENCE.findall(body) for line in block.splitlines() if line.strip()]


def _upgrade_rows() -> dict[str, str]:
    """docs/upgrading.md's upgrade table: each command, keyed by its installation."""
    table = _doc("docs/upgrading.md").split("\n## ", 1)[0]
    return {m["label"]: m["command"] for m in ROW.finditer(table)}


INSTALL = ("README.md", "## Install")
REMOVAL = ("docs/upgrading.md", "## Removing crapkit")
LOCKS = ("docs/upgrading.md", "## Windows launcher locks")


# --- the refusals an installer prints -----------------------------------------------

def test_the_install_section_names_the_pep_668_refusal_where_a_reader_reads():
    """Debian 12, Ubuntu 23.04+, Homebrew and uv-managed Pythons answer the
    Install line with `error: externally-managed-environment`. A reader scanning
    the prose for the text on their screen has to find it there, not only inside
    a transcript fence."""
    assert "error: externally-managed-environment" in _prose(_section(*INSTALL))


@pytest.mark.parametrize("line", ["pipx install crapkit", "uv tool install crapkit", "python3 -m venv .venv",
                                  "python -m venv .venv"])
def test_the_install_section_gives_each_way_past_the_refusal(line):
    assert line in _fenced_lines(_section(*INSTALL)), line


@pytest.mark.parametrize("fix", ["pipx ensurepath", "uv tool update-shell"])
def test_the_install_section_names_the_path_fix_each_tool_installer_prints(fix):
    assert f"`{fix}`" in _section(*INSTALL)


def test_the_install_section_sends_a_python_310_user_to_uvx():
    """pip on 3.10 ends with `No matching distribution found for crapkit`; the
    sentence that quotes it names the way out."""
    sentences = re.split(r"(?<=\.)\s+", _prose(_section(*INSTALL)))
    named = [s for s in sentences if "3.11" in s and "`uvx crapkit`" in s]

    assert named and "No matching distribution found for crapkit" in named[0], named


# --- where each installer puts the command -----------------------------------------

@pytest.mark.parametrize("page", ["README.md", "docs/upgrading.md"])
@pytest.mark.parametrize("fact", ["`~/.local/bin`", "`%APPDATA%\\Python\\Python312\\Scripts`",
                                  "which is not on PATH", "`~/Library/Python/3.12/bin`"])
def test_pip_user_names_its_launcher_directory_and_pips_warning(page, fact):
    """pip --user drops the launcher in a user scripts directory most PATHs lack;
    the Claude Code plugin then shows `Failed to connect` until PATH names it."""
    assert "--user" in _doc(page)
    assert fact in " ".join(_doc(page).split()), (page, fact)


# --- one upgrade line per install route ------------------------------------------------

# Each install route the README prints, and the upgrade command the guide's
# table has to give for it. A route with no row left its users on the release
# they first installed (pipx, uvx) or on the code of the first commit they
# installed from git.
ROUTES = {
    "pip install crapkit": "python -m pip install --upgrade crapkit",
    "pip install --user crapkit": "python -m pip install --user --upgrade crapkit",
    "pipx install crapkit": "pipx upgrade crapkit",
    "uv tool install crapkit": "uv tool upgrade crapkit",
    "uvx crapkit": "uvx crapkit@latest --version",
    f"pip install {GIT_URL}": f"python -m pip install --force-reinstall --no-deps {GIT_URL}",
}


@pytest.mark.parametrize("install", sorted(ROUTES))
def test_every_install_route_the_readme_prints_has_an_upgrade_row(install):
    assert install in _doc("README.md"), f"the README no longer prints {install!r}"
    assert ROUTES[install] in _upgrade_rows().values(), sorted(_upgrade_rows().values())


def test_no_other_row_on_the_guide_shadows_an_upgrade_rows_label():
    """A script that reads the guide's table by label, the way the deploy cells
    do, has to get the upgrade command and not a later table's. A removal row
    shaped `| pipx | `pipx uninstall crapkit` |` once took the pipx and uv tool
    labels over, so `uv tool` read as `uv tool uninstall crapkit`."""
    whole = {m["label"]: m["command"] for m in ROW.finditer(_doc("docs/upgrading.md"))}

    assert {label: whole[label] for label in _upgrade_rows()} == _upgrade_rows()


def test_the_readme_gives_the_line_that_refreshes_a_git_install():
    """pip keeps an installed crapkit whose version string matches, and commits
    between releases share one, so the git line run again keeps the old code."""
    assert f"pip install --force-reinstall --no-deps {GIT_URL}" in _fenced_lines(_section(*INSTALL))


def test_the_guide_says_a_cached_uvx_keeps_its_release_and_its_server_needs_a_restart():
    prose = _prose(_doc("docs/upgrading.md").split("\n## ", 1)[0])

    assert "plain `uvx crapkit` keeps running that release" in prose
    assert "Restart each client whose MCP entry runs `uvx crapkit mcp`" in prose


# --- the Windows launcher lock, as each installer meets it ----------------------------

@pytest.mark.parametrize("printed", ["Successfully installed crapkit-0.8.0", "(os error 32)",
                                     "Access is denied. (os error 5)", "ModuleNotFoundError: No module named 'crapkit'",
                                     "Nothing to upgrade"])
def test_the_lock_section_quotes_what_each_installer_printed(printed):
    assert printed in _section(*LOCKS), printed


@pytest.mark.parametrize("installation", ["pip in the active environment", "pipx", "uv tool"])
def test_every_upgrade_line_windows_can_lock_has_a_row_in_the_lock_table(installation):
    command = _upgrade_rows()[installation]
    rows = [line for line in _section(*LOCKS).splitlines() if line.startswith(f"| `{command}`")]

    assert rows, f"the lock table has no row for `{command}`"


def test_the_lock_section_keeps_its_three_steps_for_an_installer_that_failed():
    steps = _section(*LOCKS)

    assert all(f"\n{n}. " in steps for n in (1, 2, 3))
    assert "After an exit 0, restart the client" in _prose(steps)


def test_the_readme_no_longer_says_every_locked_upgrade_fails_with_error_32():
    """pip exits 0 under a live server and keeps serving old code; only uv tool
    upgrade prints error 32. A reader searching for the text on their screen
    has to find each one."""
    lock = _prose(_section("README.md", "### The exe lock on Windows"))

    assert "pip succeeds" in lock
    assert "`os error 32`" in lock and "`Access is denied. (os error 5)`" in lock
    assert "make an upgrade fail with Windows error 32" not in lock


@pytest.mark.parametrize("page, heading", [("README.md", "### The exe lock on Windows"), LOCKS])
def test_the_lock_text_says_which_pip_moves_the_exe_aside_and_what_an_older_one_does(page, heading):
    """pip 22.3.1, which a Python 3.11.2 venv ships, exits 1 with WinError 5 under
    a running `crapkit.exe mcp` and rolls back; pip 23.3 and newer move the exe
    aside. The page said pip succeeds, with no version."""
    lock = _prose(_section(page, heading))

    assert "23.3" in lock and "22.3.1" in lock, page
    assert "[WinError 5] Access is denied" in lock, page
    assert "`python -m pip install --upgrade pip`" in lock, page


# --- removal ----------------------------------------------------------------------------

# Each installer the docs name, and its own removal line.
UNINSTALL = {
    "pip": "python -m pip uninstall crapkit",
    "pipx": "pipx uninstall crapkit",
    "uv tool": "uv tool uninstall crapkit",
    "uvx": "uv cache clean crapkit",
    "the Claude Code plugin": "claude plugin uninstall crapkit@crapkit",
    "the Claude Code marketplace": "claude plugin marketplace remove crapkit",
    "the Codex plugin": "codex plugin remove crapkit@crapkit",
    "the Codex marketplace": "codex plugin marketplace remove crapkit",
    "the Copilot CLI plugin": "copilot plugin uninstall crapkit@crapkit",
    "the Copilot CLI marketplace": "copilot plugin marketplace remove crapkit",
    "the Docker image": "docker rmi crapkit",
}


# --- the channels past the Python installers ---------------------------------------------

# The deploy suite's channels that run crapkit without a pip-style installer, and
# the line that moves each one to a new release. Measured: Copilot CLI 1.0.88's
# `plugin marketplace update` and `plugin update`; the Dockerfile installs the
# clone's own source at build time; pre-commit 4.6.2's `autoupdate` in the
# lin-up-precommit-0.7.6 cell. The Docker row checks out the release tag: main's tip
# carries the last release's version string over code no release shipped.
CHANNEL_UPGRADES = {
    "the Copilot CLI plugin": ("`copilot plugin marketplace update crapkit`", "`copilot plugin update crapkit@crapkit`"),
    "the Docker image": ("`git fetch --tags`", "`git checkout v", "`docker build -t crapkit .`"),
    "the pre-commit framework (Route 3)": ("`pre-commit autoupdate`", "`rev`"),
    "the GitHub Action": ("`uses: JeanFrancoisGagne/crapkit@...`", "new tag"),
}


def _upgrade_table_row(label: str) -> str:
    table = _doc("docs/upgrading.md").split("\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith(f"| {label} |")]
    assert rows, f"the upgrade table has no row for {label}"
    return rows[0]


@pytest.mark.parametrize("channel", sorted(CHANNEL_UPGRADES))
def test_every_channel_past_pip_has_its_upgrade_row(channel):
    row = _upgrade_table_row(channel)

    assert all(part in row for part in CHANNEL_UPGRADES[channel]), row


def test_the_plugin_section_gives_the_copilot_update_and_the_path_doctor_needs():
    """doctor --plugin-root with no PATH looks in Claude Code's and Codex's
    caches only, so a Copilot install is checked by its directory."""
    lines = _fenced_lines(_section("docs/upgrading.md", "## Plugin and MCP clients"))
    copilot = "crapkit doctor --plugin-root ~/.copilot/installed-plugins/crapkit/crapkit"

    assert ["copilot plugin marketplace update crapkit", "copilot plugin update crapkit@crapkit", copilot] == \
        lines[lines.index("copilot plugin marketplace update crapkit"):][:3]
    assert "copilot plugin update crapkit@crapkit" in _doc("docs/harnesses.md")


def test_the_removal_counts_the_three_plugins_that_start_bare_crapkit():
    body = _prose(_section(*REMOVAL))

    assert "Both plugins" not in body
    assert "The Claude Code, Codex and Copilot CLI plugins" in body


def test_route_3_names_prek_beside_pre_commit():
    route = _prose(_section("README.md", "### Route 3: the pre-commit framework"))

    assert "`prek install`" in route


@pytest.mark.parametrize("installer", sorted(UNINSTALL))
def test_every_installer_has_its_removal_line(installer):
    assert f"`{UNINSTALL[installer]}`" in _section(*REMOVAL), installer


def test_removal_takes_out_what_calls_crapkit_before_the_package():
    """The hook and the driver both run crapkit: with the package gone first,
    every commit stops on `No module named crapkit` and every marks merge
    conflicts after `crapkit: not found`."""
    body = _section(*REMOVAL)
    lines = _fenced_lines(body)

    assert "rm .git/hooks/pre-commit" in lines
    assert "git config --unset merge.crapkit-ratchet.driver" in lines
    assert "git rm crapkit.toml crapkit-ratchet.tsv" in lines and "rm -rf .crapkit" in lines
    assert body.index("git config --unset merge.crapkit-ratchet.driver") < body.index(UNINSTALL["pip"])
    for said in ("No module named crapkit", "crapkit: not found", "crapkit-ratchet.tsv merge=crapkit-ratchet"):
        assert said in body, said


@pytest.mark.parametrize("route", ["Route 1", "Route 2", "Route 3", "Route 4"])
def test_every_gate_route_the_readme_prints_has_a_removal_row(route):
    assert f"### {route}:" in _doc("README.md")
    assert route in _section(*REMOVAL), route


def test_the_readme_points_at_the_removal_and_names_both_failures():
    removing = _prose(_section("README.md", "### Removing crapkit"))

    assert "upgrading.md#removing-crapkit" in removing
    assert "`No module named crapkit`" in removing and "`crapkit: not found`" in removing


# --- the handbook's Install section, the other page a team installs from ----------------

def _handbook_install() -> str:
    """The handbook's Install section as text: tags dropped, entities read."""
    import html

    page = _doc("docs/handbook.html")
    start = page.index('<h2 id="install">')
    body = page[start:page.index("<h2", start + 1)]
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())


@pytest.mark.parametrize("line", ["error: externally-managed-environment", "pipx install crapkit",
                                  "uv tool install crapkit", "No matching distribution found for crapkit",
                                  "uvx crapkit"])
def test_the_handbook_install_names_the_refusals_and_the_way_past_them(line):
    assert line in _handbook_install(), line


@pytest.mark.parametrize("line", ["os error 32", "Access is denied. (os error 5)", "pip exits 0",
                                  "uv tool install crapkit@latest"])
def test_the_handbook_windows_upgrade_says_what_each_installer_does(line):
    upgrade = _handbook_install()
    upgrade = upgrade[upgrade.index("Upgrading on Windows"):]

    assert line in upgrade, line


@pytest.mark.parametrize("link", ["docs/upgrading.md\">upgrade table", "docs/upgrading.md#removing-crapkit",
                                  "README.md#when-pip-refuses", "docs/upgrading.md#windows-launcher-locks"])
def test_the_handbook_install_links_the_upgrade_table_the_refusal_and_the_removal(link):
    page = _doc("docs/handbook.html")
    install = page[page.index('<h2 id="install">'):page.index('<h2 id="uses">')]

    assert link in install, link


def test_the_tool_install_routes_say_how_the_commit_hook_reaches_them():
    """pipx and uv tool keep crapkit out of the `python` on PATH. The hook body
    README prints runs the `crapkit` command before it tries `python`, so the
    section tells the reader the hook reaches either install as printed, and
    names no line to swap in: it told them to put `exec crapkit
    hook-precommit` in place of a line the body no longer starts with."""
    refuses = _prose(_section("README.md", "### When pip refuses"))
    body = _fenced_lines(_section("README.md", "### Route 1: `.git/hooks/pre-commit` (local, not committed)"))

    assert body[body.index("#!/bin/sh") + 1].startswith("command -v crapkit "), body
    assert "(#the-gate)" in refuses and "the `crapkit` command" in refuses, refuses
    assert "in place of" not in refuses, refuses
