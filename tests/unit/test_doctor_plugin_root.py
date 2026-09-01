"""`crapkit doctor --plugin-root PATH`: does this installed plugin match this CLI?

The plugin and the CLI ship apart. The plugin carries a `hooks.json` that spawns
`crapkit claude-hook --protocol 1`, and a machine whose CLI predates that
subcommand answers with an argparse usage dump on every edit. Nothing on either
side notices: the hook is registered, it runs, and its exit 2 is read as a
finding. This is the handshake that names the drift instead.

Two numbers are compared and nothing else: the manifest's `version` against the
running CLI's, and every `--protocol` the hook handlers name against the one
protocol this CLI answers. Agreement is silence, because a check that prints on
success is a check people stop reading.

The check never reads `crapkit.toml`. A plugin cache is not a repo, and the
question "is my plugin behind my CLI" has no repo in it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import admin, main
from crapkit.doctor import plugin_handshake

CLI = crapkit.__version__
REPO_PLUGIN = Path(__file__).resolve().parent.parent.parent / "plugin"
ON_PATH = "/usr/local/bin/crapkit"
# The memoized original, held before the autouse stub replaces the name. Its
# `__wrapped__` is the uncached function, which is what the two probe tests below
# drive: they set a PATH of their own and a cached answer would ignore it.
RESOLVE = admin._spawned_cli


@pytest.fixture(autouse=True)
def _agreeing_path_crapkit(monkeypatch):
    """The `crapkit` the plugin will spawn, stubbed to agree with this module.

    `_spawned_cli` reads the real PATH and starts what it finds, so without this
    every case here would depend on which crapkit the developer's PATH happens
    to carry. The cache is cleared on both sides so no answer crosses a test
    boundary.
    """
    RESOLVE.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: (ON_PATH, CLI))
    yield
    RESOLVE.cache_clear()


def _handler(protocol: str) -> dict:
    return {"type": "command", "command": "crapkit",
            "args": ["claude-hook", "--protocol", protocol],
            "timeout": 20, "statusMessage": "crapkit advisory",
            "async": True, "asyncRewake": True, "if": "Edit(*.py)"}


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")


def plugin(root: Path, *, version: str = CLI, protocol: str | None = "1",
           manifest: bool = True, name: str = "crapkit") -> Path:
    """A plugin tree shaped like an installed one: a manifest and a hooks file.

    `protocol=None` ships no hooks.json at all, which is a plugin that registers
    no advisory hook rather than one that registers a wrong protocol.
    """
    if manifest:
        _write(root / ".claude-plugin" / "plugin.json", {"name": name, "version": version})
    if protocol is not None:
        _write(root / "hooks" / "hooks.json",
               {"hooks": {"PostToolUse": [{"matcher": "Edit|Write",
                                           "hooks": [_handler(protocol)]}]}})
    return root


def check(root: Path, capsys) -> tuple[int, list[str], str]:
    """The subcommand at its public seam: exit code, stdout lines, stderr."""
    code = main(["doctor", "--plugin-root", str(root)])
    out = capsys.readouterr()
    return code, out.out.splitlines(), out.err


# --- agreement is silence -----------------------------------------------------

def test_a_plugin_that_matches_this_cli_prints_nothing(tmp_path, capsys):
    code, lines, err = check(plugin(tmp_path / "p"), capsys)

    assert (code, lines, err) == (0, [], "")


def test_the_plugin_this_repo_ships_matches_the_cli_it_ships_with(capsys):
    """The dogfood pin. `plugin/.claude-plugin/plugin.json` tracks pyproject and
    `plugin/hooks/hooks.json` names protocol 1; a release that moves one without
    the other is caught here rather than on somebody's machine."""
    code, lines, err = check(REPO_PLUGIN, capsys)

    assert (code, lines, err) == (0, [], "")


# --- the drift the handshake exists for ---------------------------------------

def test_a_version_gap_is_one_line_naming_both_numbers(tmp_path, capsys):
    """A reader holding one line has to be able to act on it, so the line says
    which two versions disagree and how to close the gap from either side."""
    code, lines, err = check(plugin(tmp_path / "p", version="0.1.0"), capsys)

    assert (code, err) == (1, "")
    assert len(lines) == 1, lines
    assert "0.1.0" in lines[0] and CLI in lines[0], lines[0]
    assert "claude plugin install crapkit@crapkit" in lines[0], lines[0]


def test_a_protocol_this_cli_does_not_answer_is_one_line(tmp_path, capsys):
    """`claude-hook --protocol 99` exits 0 silent by contract, so a plugin ahead
    of the CLI is a hook that runs on every edit and never says anything. The
    silence is correct and undiagnosable; this line is the diagnosis."""
    code, lines, err = check(plugin(tmp_path / "p", protocol="99"), capsys)

    assert (code, err) == (1, "")
    assert len(lines) == 1, lines
    assert "99" in lines[0] and "claude-hook" in lines[0], lines[0]


def test_two_faults_report_one_line_each(tmp_path, capsys):
    """Version and protocol are two different repairs. Folding them into one
    line would name neither."""
    code, lines, _ = check(plugin(tmp_path / "p", version="0.1.0", protocol="99"), capsys)

    assert code == 1
    assert len(lines) == 2, lines


def test_a_root_with_no_manifest_names_the_file_it_wanted(tmp_path, capsys):
    """Pointed at the wrong directory, doctor says which file was missing rather
    than reporting a version gap against a version nobody wrote."""
    code, lines, _ = check(plugin(tmp_path / "p", manifest=False), capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {tmp_path / 'p'} has no "
                     ".claude-plugin/plugin.json"], lines


def test_a_plugin_with_no_hooks_file_registers_no_advisory_and_says_so(tmp_path, capsys):
    code, lines, _ = check(plugin(tmp_path / "p", protocol=None), capsys)

    assert code == 1
    assert len(lines) == 1 and "hooks/hooks.json" in lines[0], lines


def test_an_unparseable_manifest_reads_as_a_missing_one(tmp_path, capsys):
    """`doctor` reports; it does not raise. A half-written plugin.json in a
    plugin cache must not end this command in a traceback."""
    root = plugin(tmp_path / "p")
    _write(root / ".claude-plugin" / "plugin.json", "{not json")

    code, lines, _ = check(root, capsys)

    assert code == 1
    assert len(lines) == 1 and ".claude-plugin/plugin.json" in lines[0], lines


# --- what it does not read ----------------------------------------------------

def test_the_handshake_runs_where_there_is_no_crapkit_toml(tmp_path, capsys, monkeypatch):
    """A plugin cache is not a repo. Loading the repo config first would exit 3
    on the config error of whatever directory the operator happened to be in."""
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    code, lines, err = check(plugin(tmp_path / "p"), capsys)

    assert (code, lines, err) == (0, [], "")


def test_a_broken_repo_config_does_not_reach_the_handshake(tmp_path, capsys, monkeypatch):
    """The stronger form: a `crapkit.toml` that cannot parse sits in cwd, and the
    plugin check still answers about the plugin."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "crapkit.toml").write_text("[crapkit\n", encoding="utf-8")
    monkeypatch.chdir(broken)

    code, lines, _ = check(plugin(tmp_path / "p", version="0.1.0"), capsys)

    assert code == 1
    assert len(lines) == 1 and "0.1.0" in lines[0], lines


# --- the comparison itself ----------------------------------------------------

def _lines(**kw) -> list[str]:
    args = {"where": "/plugins/crapkit", "version": "0.4.0", "cli_version": "0.4.0",
            "cli_where": ON_PATH, "protocols": ("1",), "supported": "1"}
    return plugin_handshake(**{**args, **kw})


def test_matching_input_produces_no_lines():
    assert _lines() == []


def test_a_handler_that_names_no_protocol_is_not_a_mismatch():
    """argparse defaults `--protocol` to 1, so a handler that omits the flag
    asks for exactly what this CLI answers. Reading the omission as a mismatch
    would print a line about a hook that works."""
    assert _lines(protocols=()) == []


def test_every_odd_protocol_appears_in_the_one_protocol_line():
    (line,) = _lines(protocols=("1", "2", "99", "2"))

    assert "2, 99" in line, line
    assert "99, 2" not in line, "the odd protocols are sorted, so the line never moves"


def test_a_missing_manifest_stops_the_comparison_it_cannot_make():
    """No manifest means no version to compare. Reporting a protocol gap under it
    would bury the one fact that explains both."""
    assert len(_lines(version=None, protocols=("99",))) == 1


@pytest.mark.parametrize("kw", [{"version": "0.1.0"}, {"protocols": None},
                                {"protocols": ("99",)}, {"version": None}])
def test_each_fault_alone_produces_exactly_one_line(kw):
    assert len(_lines(**kw)) == 1


# --- finding the plugin (#28) --------------------------------------------------
#
# Claude Code caches an install at cache/<marketplace>/<plugin>/<version>/ and
# keeps the old version beside the new one after an update. The newest is the
# one it runs, so that is the one checked, and the operator may point at any
# directory above it, or at nothing at all.

def test_a_directory_above_the_install_resolves_to_the_newest_version(tmp_path, capsys):
    cache = tmp_path / "cache" / "crapkit" / "crapkit"
    plugin(cache / "0.4.2", version="0.4.2")
    plugin(cache / CLI)

    code, lines, err = check(tmp_path / "cache", capsys)

    assert (code, err) == (0, "")
    assert lines == [f"crapkit doctor: checking {cache / CLI}"], lines


def test_a_root_the_operator_typed_is_judged_without_a_line_about_itself(tmp_path, capsys):
    """The search under a named directory reaches three levels, so a source
    checkout can win over an install and the reader would never know which tree
    the verdict came from. Naming the chosen root costs one line — but only when
    it was found rather than typed, or agreement stops being silence."""
    exact = plugin(tmp_path / "p")

    code, lines, err = check(exact, capsys)

    assert (code, lines, err) == (0, [], ""), "a typed root names itself"


def test_the_newest_is_decided_by_version_not_by_name_order(tmp_path, capsys):
    cache = tmp_path / "cache" / "m" / "crapkit"
    plugin(cache / "0.10.0", version="0.10.0")
    plugin(cache / "0.9.0", version="0.9.0")

    code, lines, _ = check(cache, capsys)

    assert code == 1 and len(lines) == 2, lines
    assert lines[0] == f"crapkit doctor: checking {cache / '0.10.0'}", lines[0]
    assert "0.10.0" in lines[1], lines[1]


def test_no_path_reads_claude_code_s_plugin_cache(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    root = plugin(tmp_path / "plugins" / "cache" / "crapkit" / "crapkit" / CLI)

    code = main(["doctor", "--plugin-root"])
    out = capsys.readouterr()

    assert (code, out.err) == (0, "")
    assert out.out.splitlines() == [f"crapkit doctor: checking {root}"], out.out


def test_no_path_honours_the_installer_s_record(tmp_path, capsys, monkeypatch):
    """installed_plugins.json names the install directory, so a record pointing
    outside the cache layout still resolves."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    root = plugin(tmp_path / "elsewhere" / "crapkit-plugin", version="0.1.0")
    _write(tmp_path / "plugins" / "installed_plugins.json",
           {"plugins": {"crapkit@crapkit": [{"installPath": str(root), "version": "0.1.0"}]}})

    code = main(["doctor", "--plugin-root"])
    lines = capsys.readouterr().out.splitlines()

    assert code == 1 and len(lines) == 2, lines
    assert lines[0] == f"crapkit doctor: checking {root}", lines[0]
    assert "0.1.0" in lines[1], lines[1]


def test_no_path_and_no_install_is_one_line_naming_where_it_looked(tmp_path, capsys,
                                                                   monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    code = main(["doctor", "--plugin-root"])
    lines = capsys.readouterr().out.splitlines()

    assert code == 1 and len(lines) == 1, lines
    assert str(tmp_path / "plugins") in lines[0], lines[0]
    assert "claude plugin install crapkit@crapkit" in lines[0], lines[0]


def test_a_cache_shared_with_other_plugins_yields_crapkit_not_the_highest_version(tmp_path,
                                                                                    capsys):
    """A real cache holds every vendor's plugins. Picking the highest version
    across all of them checked security-guidance 2.0.6 against the crapkit CLI
    and prescribed reinstalling crapkit."""
    cache = tmp_path / "cache"
    plugin(cache / "official" / "security-guidance" / "2.0.6", name="security-guidance",
           version="2.0.6")
    root = plugin(cache / "crapkit" / "crapkit" / CLI)

    code, lines, err = check(cache, capsys)

    assert (code, err) == (0, "")
    assert lines == [f"crapkit doctor: checking {root}"], lines


@pytest.mark.parametrize("above", ["", "plugins", "plugins/cache", "plugins/cache/crapkit"])
def test_every_directory_above_the_install_in_claude_code_s_layout_reaches_it(tmp_path, capsys,
                                                                             above):
    """`~/.claude` and `~/.claude/plugins` sit five and four levels above the
    manifest; the search steps into `plugins` and `cache` first, which also
    keeps the marketplace clone beside the cache out of the answer."""
    root = plugin(tmp_path / "plugins" / "cache" / "crapkit" / "crapkit" / CLI)
    plugin(tmp_path / "plugins" / "marketplaces" / "crapkit" / "plugin", version="0.1.0")

    code, lines, err = check(tmp_path / above, capsys)

    assert (code, err) == (0, "")
    assert lines == [f"crapkit doctor: checking {root}"], lines


def test_a_path_with_no_manifest_anywhere_under_it_still_names_the_file(tmp_path, capsys):
    """The line for a wrong directory survives the search under it."""
    (tmp_path / "empty").mkdir()

    code, lines, _ = check(tmp_path / "empty", capsys)

    assert code == 1
    assert lines == [f"crapkit doctor: the plugin at {tmp_path / 'empty'} has no "
                     ".claude-plugin/plugin.json"], lines


# --- the CLI the hook will spawn, not the one this check runs in ---------------
#
# plugin/hooks/hooks.json names a bare `crapkit` on every PostToolUse entry and
# plugin/.mcp.json names it for the MCP server, so what the plugin starts is
# PATH's answer. Compared against this module's __version__ the handshake
# described its own process: run from a venv holding 0.4.11 beside a pipx copy
# at 0.1.0, it called the two versions equal while the hook spawned the older
# one, and run where PATH carries no crapkit at all it printed nothing.

def _crapkit_shim(directory: Path, version: str) -> None:
    """A `crapkit` on PATH that answers `--version` and nothing else, spelled
    the way this platform's shell can start one."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (directory / "crapkit.bat").write_text(f"@echo crapkit {version}\n", encoding="utf-8")
        return
    shim = directory / "crapkit"
    shim.write_text(f'#!/bin/sh\necho "crapkit {version}"\n', encoding="utf-8")
    shim.chmod(0o755)


def test_the_version_compared_is_the_one_the_path_crapkit_answers(tmp_path, capsys,
                                                                  monkeypatch):
    """A venv crapkit beside an older pipx copy: the plugin matches the module
    running this check and still disagrees with the CLI the hook spawns."""
    monkeypatch.setattr(admin, "_spawned_cli", lambda: ("/opt/pipx/bin/crapkit", "0.1.0"))

    code, lines, _ = check(plugin(tmp_path / "p", version=CLI), capsys)

    assert code == 1 and len(lines) == 1, lines
    assert "0.1.0" in lines[0] and CLI in lines[0], lines[0]
    assert "/opt/pipx/bin/crapkit" in lines[0], "the line names which executable answered"


def test_no_crapkit_on_path_is_a_fail_naming_what_cannot_start(tmp_path, capsys,
                                                               monkeypatch):
    """pip into a project .venv is the usual install, and it puts the console
    script only in that venv's Scripts. Every edit then fires a command that
    cannot start and the MCP server never comes up, and this was the one command
    written to check the plugin against the CLI it will call."""
    monkeypatch.setattr(admin, "_spawned_cli", lambda: None)

    code, lines, _ = check(plugin(tmp_path / "p"), capsys)

    assert code == 1 and len(lines) == 1, lines
    assert "hooks/hooks.json" in lines[0] and ".mcp.json" in lines[0], lines[0]
    assert "PATH" in lines[0], lines[0]


def test_the_probe_reads_the_version_off_the_executable_it_found(tmp_path, monkeypatch):
    """The resolution itself, against a real shim: which() picks it up and the
    number comes back off its own `--version`, not out of this process."""
    _crapkit_shim(tmp_path / "bin", "9.9.9")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    executable, version = RESOLVE.__wrapped__()

    assert Path(executable).parent == tmp_path / "bin", executable
    assert version == "9.9.9", version


def test_an_empty_path_resolves_no_crapkit_at_all(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert RESOLVE.__wrapped__() is None


def test_an_executable_that_answers_nothing_falls_back_to_this_module(tmp_path):
    """The probe is best effort. A console script that cannot run says nothing
    about versions, and a handshake that reported '' would be worse than one
    that reports the number it does hold."""
    assert admin._probed_cli_version(str(tmp_path / "no-such-executable")) == CLI
