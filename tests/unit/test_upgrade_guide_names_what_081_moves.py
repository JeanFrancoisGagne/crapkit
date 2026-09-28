"""The upgrade guide and the changelog name what 0.8.1 moves for a reader of 0.8.0.

The release preflight walked docs/upgrading.md and the CHANGELOG's upgrading list
as a CI owner, an MCP client author, a team lead and a library caller would, and
found moves neither page named: the commit gate's exit under `--all-files`, a
dozen more exit codes, the MCP answers and JSON-RPC codes, values that move with
no exit code, the launcher token 0.8.0 cannot read, module names that moved with
no warning, and scores the version 12 notes left out. Each test pins one of
those to the page a reader looks at, and to the code where a claim is checkable.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = "docs/upgrading.md"


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(rel: str, heading: str) -> str:
    """The body under `heading`, its subsections included, joined as prose."""
    text = _doc(rel)
    level = heading.split(" ", 1)[0]
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    ends = [m.start() for m in re.finditer(r"^(#{1,6}) ", text[start:], re.M) if len(m[1]) <= len(level)]
    body = text[start:start + ends[0]] if ends else text[start:]
    return " ".join(body.split())


def _upgrading_list() -> str:
    return _section("CHANGELOG.md", "### Upgrading from 0.8.0")


# --- the commit gate (docs-1s2kxl1) -------------------------------------------------

def test_the_guide_gives_the_commit_gates_two_exit_moves_and_the_fix():
    gate = _section(GUIDE, "## The commit gate in 0.8.1")

    assert "`pre-commit run --all-files`" in gate
    assert re.search(r"\| `pre-commit run --all-files`[^|]*\| 0 \| 6 \|", gate), gate
    assert re.search(r"\| a commit in a repo whose `crapkit.toml` sits in a directory below[^|]*\| 3 on every commit \| 0, or 6", gate)
    assert "`crapkit ratchet seed`" in gate and "`GIT_INDEX_FILE`" in gate


def test_the_gate_section_quotes_the_line_the_hook_prints():
    printed = "crapkit gate: nothing is staged and no commit is running, so every tracked file was judged"
    source = " ".join((ROOT / "src/crapkit/cli/verifying.py").read_text(encoding="utf-8").split())

    assert printed.replace("tracked file", 'tracked " "file') in source, "the hook no longer prints it"
    assert printed in _section(GUIDE, "## The commit gate in 0.8.1")


def test_the_changelogs_upgrading_list_names_the_all_files_move():
    assert "`pre-commit run --all-files`" in _upgrading_list()
    assert "exits 6" in _upgrading_list()


# --- every exit code is listed where a CI owner looks (docs-gjm3so, docs-1erms5v) ----

def test_the_changelog_scopes_its_three_and_links_the_full_exit_code_list():
    listing = _upgrading_list()

    assert "Three config-path exit codes change." in listing
    assert "Three exit codes change." not in listing
    for anchor in ("#missing-values-that-081-names", "#text-that-is-not-utf-8",
                   "#the-commit-gate-in-081", "#other-exit-codes-that-move-in-081"):
        assert anchor in listing, anchor
    assert "Eleven changes in this release can move an exit code" not in _doc("CHANGELOG.md")


OTHER_MOVES = [
    "`git checkout main`", "a pytest lane whose coverage.py is older than",
    "uv run --with", "`pipx run`", "a user-scope plugin older than a project-scope one",
    "a flag this crapkit does not know", "Cursor, Copilot CLI or VS Code",
    "named in bytes that are not UTF-8", "`SRC\\app.ts`", "`python -m pytest Tests`",
    "8.3 name", "UTF-8 byte-order mark", "no `summary` object", "`loc.end.line`",
    "`USERPROFILE`",
]


@pytest.mark.parametrize("move", OTHER_MOVES)
def test_the_guide_has_a_row_for_each_other_exit_code_that_moves(move):
    assert move in _section(GUIDE, "## Other exit codes that move in 0.8.1"), move


def test_the_coverage_floor_row_names_the_floor_doctor_holds():
    from crapkit.coverage_py import REGIONS_FLOOR

    assert f"`coverage>={REGIONS_FLOOR}`" in _section(GUIDE, "## Other exit codes that move in 0.8.1")


# --- values that move with no exit code (docs-9176ha) ---------------------------------

@pytest.mark.parametrize("value", ["` - `", "UTC calendar", "`crap_load`", "`by_scope`", "`risk`"])
def test_the_guide_names_each_value_that_moves_without_an_exit_code(value):
    assert value in _section(GUIDE, "## Values that move without an exit code"), value


def test_the_changelogs_upgrading_list_names_the_partial_run_crap_load():
    assert "`crap_load`" in _upgrading_list()


# --- MCP answers (docs-1d9f5f9) --------------------------------------------------------

@pytest.mark.parametrize("fact", ["`2024-11-05`", "`2025-03-26`", "`structuredContent`", "`outputSchema`",
                                  "`truncated`", "`full`", "`-32602`", "`-32601`", "`-32600`",
                                  "`wait_for_previous`"])
def test_the_plugin_and_mcp_section_names_each_change_an_mcp_client_meets(fact):
    assert fact in _section(GUIDE, "### MCP answers in 0.8.1"), fact
    assert "MCP answers in 0.8.1" in _section(GUIDE, "## Plugin and MCP clients")


def test_the_truncation_bound_the_guide_gives_is_the_servers():
    from crapkit.mcp_server import ANSWER_CHARS

    assert f"{ANSWER_CHARS:,} characters" in _section(GUIDE, "### MCP answers in 0.8.1")


# --- the restart line a server prints (docs-nkr5tf) --------------------------------------

def _restart_line(monkeypatch, loaded: str, installed: str) -> str:
    from crapkit import mcp_server

    monkeypatch.setattr(mcp_server, "upgraded_to", lambda: installed)
    monkeypatch.setattr(mcp_server, "_version", lambda: loaded)
    return mcp_server._upgraded_under_us("list_runs")


@pytest.mark.parametrize("page", [GUIDE, "docs/agent-json.md"])
def test_the_quoted_restart_comes_from_a_server_that_checks(page, monkeypatch):
    """A 0.8.0 server has no such check, so the 0.8.0 -> 0.8.1 line the pages
    quoted never prints on the upgrade they describe."""
    text = " ".join(_doc(page).split())

    assert "upgraded from 0.8.0 to 0.8.1" not in text
    assert _restart_line(monkeypatch, "0.8.1", "0.8.2") in text
    assert "A 0.8.0 server does not check" in text or "a server from 0.8.0 or earlier does not check" in text


def test_the_guide_scopes_the_watch_stop_to_a_watch_that_checks():
    measure = _section(GUIDE, "## Measure before changing marks")

    assert "A `crapkit watch` from 0.8.0 can end in a Python traceback" in measure
    assert "A 0.8.1 `crapkit watch` stops at its next rescore" in measure


# --- the launcher token waits for every reader (compat-1ecd1u) -------------------------

@pytest.mark.parametrize("said", ["`{python}`", "`{python:DIR}`", "exit 5",
                                  "`/bin/sh: 1: {python:.venv}: not found`",
                                  "`The filename, directory name, or volume label syntax is incorrect.`",
                                  "the Action's `uses:` pin", "the pre-commit `rev`"])
def test_the_team_ordering_section_holds_the_token_until_every_reader_upgrades(said):
    assert said in _section(GUIDE, "## A team upgrades every reader before the re-seed lands"), said


def test_the_changelogs_token_bullet_repeats_the_ordering():
    bullet = next(b for b in _upgrading_list().split("- ") if "Swap the venv launcher" in b)

    assert "0.8.0 does not know the token" in bullet
    assert "pre-commit `rev`" in bullet and "`uses:`" in bullet


# --- library callers (compat-iar917) ---------------------------------------------------

def test_the_library_section_no_longer_promises_a_warning_for_every_name():
    assert "gets one release of warning before a name it calls moves" not in _section(GUIDE, "## Library callers")


@pytest.mark.parametrize("module, name", [("crapkit.gitio", "file_log_patches"),
                                          ("crapkit.gitpaths", "history_line"),
                                          ("crapkit.lanes", "SUITE_DROP_FRACTION")])
def test_each_name_the_library_section_lists_as_moved_is_gone(module, name):
    import importlib

    assert not hasattr(importlib.import_module(module), name), f"{module}.{name} is back"
    assert f"`{module}.{name}`" in _section(GUIDE, "## Library callers")


def test_the_library_section_names_the_new_signature_and_return_type():
    import inspect

    from crapkit import mcp_server, mutate_pool

    library = _section(GUIDE, "## Library callers")
    assert "repo" in inspect.signature(mcp_server.build_argv).parameters
    assert "`build_argv(tool, arguments, repo)`" in library
    assert bool(mutate_pool.MutantVerdict.SURVIVED) is True
    assert "`MutantVerdict`" in library and "`bool(MutantVerdict.SURVIVED)` is `True`" in library


# --- the version 12 notes (scores-10rh199) ---------------------------------------------

UNDEFINED = "\x81"
SAMPLES = {
    "Go": ("a.go", "package p\nfunc caf\x81(x int) int {\n  if x > 0 { return 1 }\n  return 2\n}\n"),
    "Java": ("A.java", "class A { int caf\x81(int x) { if (x > 0) { return 1; } return 2; } }\n"),
    "Rust": ("a.rs", "fn caf\x81(x: i32) -> i32 {\n  if x > 0 { 1 } else { 2 }\n}\n"),
    "Swift": ("a.swift", "func caf\x81(x: Int) -> Int {\n  if x > 0 { return 1 }\n  return 2\n}\n"),
    "shell": ("a.sh", "caf\x81() {\n  if [ \"$1\" ]; then echo a; fi\n}\n"),
    "PowerShell": ("a.ps1", "function caf\x81($x) {\n  if ($x) { return 1 }\n  return 2\n}\n"),
}


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_version_12_names_each_language_whose_cp1252_function_is_newly_scored(language):
    """0.8.0 scored no function there; 0.8.1 scores it under its own name."""
    from crapkit.analyze import analyze_source, decode_source

    path, text = SAMPLES[language]
    rows = analyze_source(path, decode_source(text.encode("latin-1")), note=False)

    assert [r.ccn for r in rows] == [2] and "cafƁ" in rows[0].long_name, rows
    assert language in _section(GUIDE, "### Analysis version 12"), language


def test_version_12_names_the_letter_case_joins():
    notes = _section(GUIDE, "### Analysis version 12")

    assert "`PKG/mod.py`" in notes and "`SRC/app.ts`" in notes
    assert "`untested`" in notes and "`measured`" in notes


def test_the_changelog_names_no_language_crapkit_does_not_score():
    assert "C and C#" not in _doc("CHANGELOG.md")
    assert "C#" not in _section("README.md", "## Languages")
