"""The crapkit-recover description quotes the refusal for a file git names in
bytes that are not UTF-8 and the line a stopped measurement owner prints.

A description quoting a string the code no longer prints is a pointer that
never fires, so each quote is pinned against the module that prints it.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

RECOVER = Path(__file__).resolve().parents[2] / "plugin" / "skills" / "crapkit-recover" / "SKILL.md"


def _description() -> str:
    return RECOVER.read_text(encoding="utf-8").split("\n---", 1)[0]


def _strings(module: str) -> list[str]:
    """Every string literal in the module; the parser joins a message split
    over adjacent literals into one."""
    tree = ast.parse(Path(importlib.import_module(module).__file__).read_bytes())
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


@pytest.mark.parametrize("quote, module", [
    ("in bytes that are not UTF-8", "crapkit.gitpaths"),
    ("measurement owner stopped", "crapkit._process_owner"),
])
def test_the_recover_description_quotes_a_line_crapkit_prints(quote, module):
    assert any(quote in text for text in _strings(module)), f"{module} reworded the line"
    assert f'\\"{quote}\\"' in _description(), "the description quotes something else"
