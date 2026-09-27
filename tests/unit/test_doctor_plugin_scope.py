"""`doctor --plugin-root` names a repair for each scope that holds the install.

The version-gap line named `claude plugin update crapkit@crapkit --scope user`
for every Claude Code install. Over a plugin installed with `--scope project`,
Claude Code 2.1.281 answered 'Plugin "crapkit" is not installed at scope user'
and doctor still exited 1 after the named commands ran. A project or local
install belongs to one project directory, and `claude plugin update --scope
project` run outside it moved the first project install installed_plugins.json
lists, not necessarily this one; `claude plugin install --scope project` writes
to the project the command runs in. So each repair names the scope the install
was recorded under and, for a project or local one, the directory to run it in.
"""
from __future__ import annotations

from pathlib import Path

import crapkit
from crapkit.doctor import InstallScope, plugin_handshake, stale_copy
# The copy tests' autouse stub of the crapkit on PATH and the claude it probes,
# imported so it runs here too: without it these lines depended on which
# crapkit the machine's PATH held, and a PATH with none printed the FAIL instead.
from test_doctor_plugin_copy import GITHUB, SKILL, claude_home, run
from test_doctor_plugin_copy import _agreeing_path_crapkit  # noqa: F401
from test_doctor_plugin_root import _write, plugin

CLI = crapkit.__version__
FETCH = "`claude plugin marketplace update crapkit`"


def record(tmp_path: Path, root: Path, *records: dict) -> None:
    """installed_plugins.json holding `records`, each for the install at `root`
    unless it names another installPath."""
    _write(tmp_path / "claude" / "plugins" / "installed_plugins.json",
           {"version": 2, "plugins": {"crapkit@crapkit": [
               {"installPath": str(root), "version": "0.0.1", **r} for r in records]}})


def _update(scope: str) -> str:
    return f"`claude plugin update crapkit@crapkit --scope {scope}`"


def _gap_line(tmp_path, monkeypatch, capsys, *records: dict) -> str:
    root, _ = claude_home(tmp_path, monkeypatch, GITHUB, version="0.0.1", clone_version="0.0.1")
    record(tmp_path, root, *records)
    code, lines = run(capsys)
    assert (code, len(lines)) == (1, 2), lines
    return lines[1]


# --- the version gap ---------------------------------------------------------------------

def test_a_project_install_behind_the_cli_names_its_scope_and_its_project(tmp_path, monkeypatch,
                                                                          capsys):
    project = tmp_path / "proj"

    line = _gap_line(tmp_path, monkeypatch, capsys, {"scope": "project", "projectPath": str(project)})

    assert line.endswith(f"The plugin is behind; update it with {FETCH}, then {_update('project')} "
                         f"(run in {project}), and restart Claude Code's sessions."), line
    assert "--scope user" not in line


def test_every_scope_that_holds_the_install_gets_its_own_update(tmp_path, monkeypatch, capsys):
    """Claude Code keeps one cache directory per version, so a user install and
    two project installs of one version share it, and each needs its update."""
    pa, pb = tmp_path / "pa", tmp_path / "pb"

    line = _gap_line(tmp_path, monkeypatch, capsys,
                     {"scope": "project", "projectPath": str(pa)},
                     {"scope": "local", "projectPath": str(pb)},
                     {"scope": "user"})

    assert line.endswith(f"then {_update('project')} (run in {pa}); {_update('local')} (run in "
                         f"{pb}); {_update('user')}, and restart Claude Code's sessions."), line


def test_a_record_for_another_install_is_not_named(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"

    line = _gap_line(tmp_path, monkeypatch, capsys,
                     {"scope": "user", "installPath": str(tmp_path / "elsewhere")},
                     {"scope": "project", "projectPath": str(project)})

    assert "--scope user" not in line
    assert f"{_update('project')} (run in {project})" in line


def test_an_install_no_record_names_keeps_the_user_scope_update(tmp_path, monkeypatch, capsys):
    line = _gap_line(tmp_path, monkeypatch, capsys,
                     {"scope": "project", "projectPath": str(tmp_path / "p"),
                      "installPath": str(tmp_path / "elsewhere")})

    assert line.endswith(f"then {_update('user')}, and restart Claude Code's sessions."), line


def test_the_pure_rule_spells_each_scope_and_where_to_run_it():
    lines = plugin_handshake(where="/c/0.0.1", version="0.0.1", cli_version="0.8.1",
                             cli_where="/bin/crapkit", protocols=(), supported="1",
                             scopes=(InstallScope("local", "/w/pb"), InstallScope("user")))

    assert lines[0].endswith(f"then {_update('local')} (run in /w/pb); {_update('user')}, and "
                             "restart Claude Code's sessions."), lines


def test_versions_that_do_not_order_plainly_name_each_scope_too():
    (line,) = plugin_handshake(where="/c/p", version="0.9.0rc1", cli_version="0.8.1",
                               cli_where="/bin/crapkit", protocols=(), supported="1",
                               scopes=(InstallScope("project", "/w/pa"),))

    assert f"the plugin with {FETCH}, then {_update('project')} (run in /w/pa); the CLI" in line


# --- the reinstall a stale same-version copy needs ------------------------------------

def _reinstall(scope: str) -> str:
    return (f"`claude plugin uninstall crapkit@crapkit --scope {scope}`, then "
            f"`claude plugin install crapkit@crapkit --scope {scope}`")


def test_a_stale_project_install_is_reinstalled_in_its_project(tmp_path, monkeypatch, capsys):
    """`claude plugin install --scope project` writes to the project it runs in."""
    root, shipped = claude_home(tmp_path, monkeypatch, GITHUB)
    record(tmp_path, root, {"scope": "project", "projectPath": str(tmp_path / "proj"),
                            "version": CLI})
    _write(shipped / SKILL, "main's skill\n")

    code, lines = run(capsys)

    assert code == 1
    assert lines[1].endswith(f"so reinstall it with {_reinstall('project')} (run in "
                             f"{tmp_path / 'proj'}), and restart Claude Code's sessions."), lines[1]


def test_the_pure_stale_rule_names_every_scope():
    line = stale_copy(where="/c/0.8.0", version="0.8.0", source="/m/plugin", source_version="0.8.0",
                      differing=("a.md",),
                      scopes=(InstallScope("project", "/w/pa"), InstallScope("user")))

    assert line.endswith(f"reinstall it with {_reinstall('project')} (run in /w/pa); "
                         f"{_reinstall('user')}, and restart Claude Code's sessions."), line


# --- every install a session runs, with no PATH ----------------------------------------
#
# A user install made at 0.8.0 and a project install made later at 0.8.1 are two
# cache directories. doctor with no PATH checked only the newest and exited 0
# while every session outside that project ran the 0.8.0 plugin.

def _two_installs(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A project install at this CLI's version and a user install at 0.0.1,
    each recorded, returned (newer, older)."""
    newer, _ = claude_home(tmp_path, monkeypatch, GITHUB)
    older = plugin(newer.parent / "0.0.1", version="0.0.1")
    _write(tmp_path / "claude" / "plugins" / "installed_plugins.json",
           {"version": 2, "plugins": {"crapkit@crapkit": [
               {"scope": "user", "installPath": str(older), "version": "0.0.1"},
               {"scope": "project", "projectPath": str(tmp_path / "pa"),
                "installPath": str(newer), "version": CLI}]}})
    return newer, older


def test_with_no_path_every_recorded_install_is_checked_newest_first(tmp_path, monkeypatch, capsys):
    newer, older = _two_installs(tmp_path, monkeypatch)

    code, lines = run(capsys)

    assert code == 1
    assert lines[:2] == [f"crapkit doctor: checking {newer}", f"crapkit doctor: checking {older}"]
    (gap,) = lines[2:]
    assert gap.startswith(f"crapkit doctor: the plugin at {older} is version 0.0.1"), gap
    assert gap.endswith(f"then {_update('user')}, and restart Claude Code's sessions."), gap


def test_a_cached_version_no_record_names_is_not_checked(tmp_path, monkeypatch, capsys):
    """`claude plugin update` leaves the old version's directory beside the new
    one, and no session runs it."""
    root, _ = claude_home(tmp_path, monkeypatch, GITHUB)
    plugin(root.parent / "0.0.1", version="0.0.1")

    assert run(capsys) == (0, [f"crapkit doctor: checking {root}"])


def test_the_upgrade_guide_names_the_exit_the_every_install_check_moves():
    """0.8.0 checked only the newest install, so the two installs above passed at
    exit 0 while every session outside the project ran the older plugin. The
    check exits 1 there now, and the guide's plugin section says so."""
    guide = (Path(__file__).resolve().parents[2] / "docs" / "upgrading.md").read_text(encoding="utf-8")
    section = " ".join(guide.split("\n## Plugin and MCP clients\n", 1)[1].split("\n## ", 1)[0].split())

    assert "it checks every install `installed_plugins.json` records" in section
    assert "exited 0" in section and "exits 1" in section
