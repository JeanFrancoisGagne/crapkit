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
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import main
from crapkit.doctor import plugin_handshake

CLI = crapkit.__version__
REPO_PLUGIN = Path(__file__).resolve().parent.parent.parent / "plugin"


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
           manifest: bool = True) -> Path:
    """A plugin tree shaped like an installed one: a manifest and a hooks file.

    `protocol=None` ships no hooks.json at all, which is a plugin that registers
    no advisory hook rather than one that registers a wrong protocol.
    """
    if manifest:
        _write(root / ".claude-plugin" / "plugin.json", {"name": "crapkit", "version": version})
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
            "protocols": ("1",), "supported": "1"}
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
