"""`doctor --plugin-root` judges the copy of the plugin Claude Code runs.

Between releases main keeps the release's version string. The README's update
lines refresh the marketplace clone, `claude plugin update` answers "already at
the latest version", and the installed copy keeps the release's files while the
clone holds main's. doctor compared version strings only and exited 0. It now
compares the install with the clone's copy when both carry one version.

A marketplace added from a local directory is the other way round: Claude Code
2.1.281 loads that plugin in place ("it loads in place from .../plugin"), so
the cache copy is not what runs, and doctor with no PATH checks the directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import admin, main
from crapkit.doctor import USER_SCOPE, stale_copy
from test_doctor_plugin_root import ON_PATH, _write, plugin

CLI = crapkit.__version__
SKILL = Path("skills") / "crapkit" / "SKILL.md"
RESOLVE = admin._spawned_cli  # the memoized probe, held before a test replaces the name


@pytest.fixture(autouse=True)
def _agreeing_path_crapkit(monkeypatch):
    RESOLVE.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: (ON_PATH, CLI))
    monkeypatch.setattr(admin, "_claude_code_version", lambda: None)
    yield
    RESOLVE.cache_clear()


def claude_home(tmp_path: Path, monkeypatch, source: dict, *, version: str = CLI,
                clone_version: str = CLI, scope: str = "user") -> tuple[Path, Path]:
    """A Claude Code config dir holding a crapkit install and the marketplace it
    came from: the clone (or local directory) at `installLocation`, its
    marketplace.json pointing at ./plugin. Returns (install root, clone plugin)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    plugins = tmp_path / "claude" / "plugins"
    clone = tmp_path / "clone" if source["source"] == "directory" else plugins / "marketplaces" / "crapkit"
    _write(clone / ".claude-plugin" / "marketplace.json",
           {"name": "crapkit", "plugins": [{"name": "crapkit", "source": "./plugin"}]})
    shipped = plugin(clone / "plugin", version=clone_version)
    _write(shipped / SKILL, "the release's skill\n")
    root = plugin(plugins / "cache" / "crapkit" / "crapkit" / version, version=version)
    _write(root / SKILL, "the release's skill\n")
    _write(root / ".in_use", "")
    _write(plugins / "known_marketplaces.json",
           {"crapkit": {"source": source, "installLocation": str(clone)}})
    _write(plugins / "installed_plugins.json",
           {"version": 2, "plugins": {"crapkit@crapkit": [
               {"scope": scope, "installPath": str(root), "version": version}]}})
    return root, shipped


GITHUB = {"source": "github", "repo": "JeanFrancoisGagne/crapkit"}


def run(capsys, *args: str) -> tuple[int, list[str]]:
    code = main(["doctor", "--plugin-root", *args])
    return code, capsys.readouterr().out.splitlines()


# --- main between releases -------------------------------------------------------------

def test_an_install_whose_files_differ_from_the_clone_at_one_version_names_the_reinstall(
        tmp_path, monkeypatch, capsys):
    root, shipped = claude_home(tmp_path, monkeypatch, GITHUB)
    _write(shipped / SKILL, "the release's skill\nA line main gained after the release.\n")

    code, lines = run(capsys)

    assert code == 1
    assert lines == [
        f"crapkit doctor: checking {root}",
        f"crapkit doctor: the plugin at {root} is version {CLI}, and so is the marketplace's copy "
        f"at {shipped}, but 1 file differs between them ({SKILL.as_posix()}); `claude plugin update` "
        "keeps an install whose version did not move, so reinstall it with `claude plugin "
        "uninstall crapkit@crapkit --scope user`, then `claude plugin install crapkit@crapkit "
        "--scope user`, and restart Claude Code's sessions."]


def test_the_reinstall_names_the_scope_the_install_was_made_in(tmp_path, monkeypatch, capsys):
    root, shipped = claude_home(tmp_path, monkeypatch, GITHUB, scope="project")
    (shipped / SKILL).unlink()
    _write(shipped / "skills" / "new" / "SKILL.md", "a skill main added\n")

    code, lines = run(capsys)

    assert code == 1
    assert "1 file differs between them (skills/new/SKILL.md)" in lines[1], lines[1]
    assert "--scope project" in lines[1]


def test_an_install_matching_its_clone_says_nothing(tmp_path, monkeypatch, capsys):
    """Files Claude Code writes beside an install (.in_use) are not the plugin's."""
    root, _ = claude_home(tmp_path, monkeypatch, GITHUB)

    assert run(capsys) == (0, [f"crapkit doctor: checking {root}"])


def test_a_clone_at_another_version_is_the_version_gap_s_business(tmp_path, monkeypatch, capsys):
    """`claude plugin update` moves an install whose version the clone moved past."""
    root, shipped = claude_home(tmp_path, monkeypatch, GITHUB, clone_version="99.0.0")
    _write(shipped / SKILL, "main's skill\n")

    assert run(capsys) == (0, [f"crapkit doctor: checking {root}"])


def test_a_root_outside_claude_code_s_cache_is_not_compared(tmp_path, capsys):
    root = plugin(tmp_path / "p")

    code, lines = run(capsys, str(root))

    assert (code, lines) == (0, [])


def test_an_unreadable_marketplace_record_is_no_comparison(tmp_path, monkeypatch, capsys):
    root, shipped = claude_home(tmp_path, monkeypatch, GITHUB)
    _write(shipped / SKILL, "main's skill\n")
    _write(tmp_path / "claude" / "plugins" / "known_marketplaces.json", "{not json")

    assert run(capsys) == (0, [f"crapkit doctor: checking {root}"])


def test_the_pure_rule_counts_and_names_the_first_differing_file():
    line = stale_copy(where="/c/0.8.0", version="0.8.0", source="/m/plugin", source_version="0.8.0",
                      differing=("a.md", "b.md", "c.md"), scopes=USER_SCOPE)

    assert "3 files differ between them (a.md, b.md and 1 more)" in line
    assert stale_copy(where="/c", version="0.8.0", source="/m", source_version="0.8.0",
                      differing=(), scopes=USER_SCOPE) is None
    assert stale_copy(where="/c", version="0.8.0", source="/m", source_version="0.8.1",
                      differing=("a.md",), scopes=USER_SCOPE) is None


# --- a marketplace added from a local directory ------------------------------------------

LOCAL = {"source": "directory", "path": "PLACEHOLDER"}


def test_with_no_path_a_local_directory_marketplace_is_checked_where_it_loads(tmp_path, monkeypatch,
                                                                              capsys):
    _, shipped = claude_home(tmp_path, monkeypatch, LOCAL)
    manifest = shipped / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({"name": "crapkit", "version": "99.0.0"}), encoding="utf-8")

    code, lines = run(capsys)

    assert code == 1
    assert lines[0] == (f"crapkit doctor: checking {shipped} (Claude Code loads a plugin from a "
                        "local directory marketplace in place)")
    assert lines[1].startswith(f"crapkit doctor: the plugin at {shipped} is version 99.0.0")


def test_a_local_directory_s_cache_copy_is_not_called_stale(tmp_path, monkeypatch, capsys):
    root, shipped = claude_home(tmp_path, monkeypatch, LOCAL)
    _write(shipped / SKILL, "an edit in the checkout\n")

    code, lines = run(capsys, str(root))

    assert (code, lines) == (0, [])


# --- a local directory marketplace behind the CLI ------------------------------------------
#
# Claude Code loads that plugin in place, so `claude plugin marketplace update`
# and `claude plugin update` leave its files alone: over a 0.8.0 directory and a
# 0.8.1 CLI the update answered "already at the latest version (0.8.0)" and
# doctor exited 1 again. The repair is to update the directory.

def _behind_in_place(tmp_path, monkeypatch, *, git: bool) -> tuple[Path, Path]:
    _, shipped = claude_home(tmp_path, monkeypatch, LOCAL)
    _write(shipped / ".claude-plugin" / "plugin.json", {"name": "crapkit", "version": "0.0.1"})
    if git:
        (tmp_path / "clone" / ".git").mkdir()
    return shipped, tmp_path / "clone"


_LOADS_IN_PLACE = ("(Claude Code loads it in place from the local directory marketplace at {at}, "
                   "and `claude plugin update` does not change it)")


def test_a_directory_checkout_behind_the_cli_is_updated_with_git_pull(tmp_path, monkeypatch,
                                                                      capsys):
    shipped, clone = _behind_in_place(tmp_path, monkeypatch, git=True)

    code, lines = run(capsys)

    assert code == 1
    assert lines[1].endswith(
        f"The plugin is behind; update it with `git -C {admin._shell_quote(str(clone))} pull` "
        + _LOADS_IN_PLACE.format(at=clone) + ", and restart Claude Code's sessions."), lines[1]
    assert "claude plugin update crapkit@crapkit" not in lines[1]


def test_a_directory_that_is_no_checkout_is_updated_by_copying_the_release_s_plugin(
        tmp_path, monkeypatch, capsys):
    shipped, clone = _behind_in_place(tmp_path, monkeypatch, git=False)

    code, lines = run(capsys)

    assert code == 1
    assert lines[1].endswith(
        f"The plugin is behind; update it by copying crapkit {CLI}'s plugin/ directory over "
        f"{shipped} " + _LOADS_IN_PLACE.format(at=clone)
        + ", and restart Claude Code's sessions."), lines[1]


def test_the_directory_named_as_plugin_root_gets_the_same_repair(tmp_path, monkeypatch, capsys):
    shipped, clone = _behind_in_place(tmp_path, monkeypatch, git=True)

    code, lines = run(capsys, str(shipped))

    assert code == 1
    assert f"`git -C {admin._shell_quote(str(clone))} pull`" in lines[0], lines


def test_the_pure_rule_names_the_directory_for_versions_that_do_not_order_plainly():
    from crapkit.doctor import InPlace, plugin_handshake

    (line,) = plugin_handshake(where="/m/plugin", version="0.9.0rc1", cli_version="0.8.1",
                               cli_where="/bin/crapkit", protocols=(), supported="1",
                               in_place=InPlace("/m", "git -C /m pull"))

    assert ("Update whichever is behind: the plugin with `git -C /m pull` (Claude Code loads it in "
            "place from the local directory marketplace at /m, and `claude plugin update` does not "
            "change it); the CLI with") in line, line
