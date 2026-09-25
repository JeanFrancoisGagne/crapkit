"""`doctor --plugin-root` names a Claude Code too old to pass the plugin's hook args.

The plugin's 50 PostToolUse handlers are exec-form: `command: crapkit` plus
`args: [claude-hook, --protocol, 1]`. Claude Code passes `args` from 2.1.139
on. An older release runs the bare `crapkit`, argparse exits 2 with its usage,
and asyncRewake hands that usage to the model on every matched edit. The
version and protocol handshake passed on such a machine, because the plugin
and the CLI agreed with each other. doctor now asks the `claude` on PATH for its
version and adds one line when it is below the floor.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

import crapkit
from crapkit.cli import admin, main
from crapkit.doctor import CLAUDE_CODE_ARGS_FLOOR, claude_code_floor_gap

ROOT = Path(__file__).resolve().parents[2]
REPO_PLUGIN = ROOT / "plugin"
CLAUDE = "/usr/local/bin/claude"
# The memoized probe, held before a test replaces the name, so its cache is
# cleared on both sides whatever the test put in its place.
PROBE = admin._claude_code_version


@pytest.fixture(autouse=True)
def _agreeing_cli(monkeypatch):
    """The `crapkit` on PATH agrees with the plugin, so the only line left is
    the one about Claude Code."""
    PROBE.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: ("/usr/local/bin/crapkit", crapkit.__version__))
    yield
    PROBE.cache_clear()


def _claude_answers(monkeypatch, answer):
    monkeypatch.setattr(admin, "_claude_code_version", lambda: answer)


@pytest.mark.parametrize("answer", ["2.1.138 (Claude Code)", "2.0.9 (Claude Code)", "1.9.200"])
def test_a_release_below_the_floor_is_one_line_naming_both_numbers_and_the_fix(answer):
    line = claude_code_floor_gap(CLAUDE, answer)

    found = answer.split()[0]
    assert line == (
        f"crapkit doctor: Claude Code {found} ({CLAUDE}) predates 2.1.139, the first release "
        "that passes a hook's args, so each of the plugin's hooks starts a bare `crapkit`, "
        "which exits 2 with its usage on every edit. Update Claude Code (`claude update`), "
        "then restart its sessions.")


@pytest.mark.parametrize("answer", ["2.1.139 (Claude Code)", "2.1.281 (Claude Code)", "2.2.0",
                                    "10.0.0 (Claude Code)", "", "Claude Code", "unknown option"])
def test_the_floor_itself_newer_releases_and_non_versions_say_nothing(answer):
    assert claude_code_floor_gap(CLAUDE, answer) is None


def test_the_floor_is_the_one_the_deploy_suite_pins():
    """tools/deploy/pins.toml installs the floor Claude Code beside the pinned
    one, and the lin-plugin-floors cell runs the plugin on it. One number. A
    tree without the deploy kit has no pin to hold the floor to."""
    pins_file = ROOT / "tools" / "deploy" / "pins.toml"
    if not pins_file.is_file():
        pytest.skip("this tree carries no tools/deploy/pins.toml")
    pins = tomllib.loads(pins_file.read_text(encoding="utf-8"))
    assert pins["harness"]["claude-code"]["floors"][0] == CLAUDE_CODE_ARGS_FLOOR


def test_doctor_plugin_root_prints_the_line_and_exits_1(monkeypatch, capsys):
    _claude_answers(monkeypatch, (CLAUDE, "2.1.138 (Claude Code)"))

    assert main(["doctor", "--plugin-root", str(REPO_PLUGIN)]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [claude_code_floor_gap(CLAUDE, "2.1.138 (Claude Code)")]


@pytest.mark.parametrize("answer", [(CLAUDE, "2.1.139 (Claude Code)"), None])
def test_doctor_plugin_root_stays_silent_at_the_floor_and_without_claude(monkeypatch, capsys,
                                                                          answer):
    _claude_answers(monkeypatch, answer)

    assert main(["doctor", "--plugin-root", str(REPO_PLUGIN)]) == 0
    assert capsys.readouterr().out == ""


def _fake_claude(tmp_path: Path, body: str) -> Path:
    """A `claude` on PATH that answers --version the way the real one does."""
    script = tmp_path / "fake_claude.py"
    script.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "claude.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        launcher = tmp_path / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def test_the_probe_reads_what_the_claude_on_path_prints(tmp_path, monkeypatch):
    _fake_claude(tmp_path, "import sys\nprint('2.1.138 (Claude Code)')\n")
    monkeypatch.setenv("PATH", str(tmp_path))

    where, answer = admin._claude_code_version()
    assert Path(where).parent == tmp_path
    assert answer == "2.1.138 (Claude Code)"


def test_no_claude_on_path_is_no_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    assert admin._claude_code_version() is None


def test_a_claude_that_cannot_start_is_no_answer(tmp_path, monkeypatch):
    _fake_claude(tmp_path, "")
    monkeypatch.setenv("PATH", str(tmp_path))

    def refuse(*args, **kwargs):
        raise OSError("exec format error")
    monkeypatch.setattr("subprocess.run", refuse)

    assert admin._claude_code_version() is None
