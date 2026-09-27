"""crapkit-recover quotes each line `crapkit doctor --plugin-root` prints for a
root with no version to read, down to the repair the line ends with.

A root with no `.claude-plugin/plugin.json` is no plugin root, and its line
names where to point instead or to run the bare flag. A manifest that is there
and gives no version is an install to put back, and its line names the
harness's uninstall, then its install, since a second `claude plugin install`
only answers that the plugin is already installed. The skill told the agent to
run that install line alone, and gave the no-manifest line no bare-flag route.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import crapkit
from crapkit.doctor import plugin_handshake

RECOVER = Path(__file__).resolve().parents[2] / "plugin" / "skills" / "crapkit-recover" / "SKILL.md"
PREFIX = "crapkit doctor: the plugin at PATH "


def _line(fault: str, harness: str) -> str:
    (line,) = plugin_handshake(where="PATH", version=None, cli_version=crapkit.__version__,
                               cli_where="crapkit", protocols=None, supported="1", harness=harness,
                               manifest_fault=fault)
    return line


def _skill() -> str:
    """The skill as a reader reads it, one space wherever the page wraps."""
    return " ".join(RECOVER.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("fault, harness", [("missing", "claude"), ("not-an-object", "claude"),
                                            ("unversioned", "claude"), ("not-an-object", "codex"),
                                            ("unversioned", "codex")])
def test_the_recover_skill_quotes_each_no_version_line_and_its_repair(fault, harness):
    line = _line(fault, harness)
    said = line.removeprefix(PREFIX).split(", so ", 1)[0]
    repair = line.split("; ", 1)[1].removesuffix(".")

    assert f'"{PREFIX}{said}' in _skill() or f'"{said}' in _skill(), said
    assert f'"{repair}"' in _skill(), repair
