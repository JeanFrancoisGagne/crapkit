"""The README exit table and crapkit-recover's table name each exit-3 cause 0.8.1 added.

A script that reads exit 3 looks it up in one of these two tables. 0.8.1 added
four causes, and a table that names none of them sends the reader to
`crapkit.toml` for a file name, a package.json or a share. Each row is pinned
to the line the code prints, so a reworded refusal breaks this test too.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from crapkit.cli.admin import _package_object
from crapkit.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
RECOVER = ROOT / "plugin" / "skills" / "crapkit-recover" / "SKILL.md"

# (what the table says, the module that prints the refusal, a piece of that line)
CAUSES = [
    ("a scoped file whose name is not UTF-8", "crapkit.universe",
     "a file a scope takes is refused"),
    ("a path argument naming a file whose name is not UTF-8", "crapkit.cli._shared",
     "rename it (git mv) to a UTF-8 name"),
    ("a root `package.json` that `init` cannot read", "crapkit.cli.admin",
     "init wrote no file"),
    ("a root on a Windows network share", "crapkit.cli._shared",
     "is on a network share"),
]


def _exit_3_row(page: Path) -> str:
    rows = [line for line in page.read_text(encoding="utf-8").splitlines()
            if line.startswith("| 3 |")]
    assert len(rows) == 1, f"{page.name} holds {len(rows)} exit-3 rows"
    return rows[0]


def _strings(module: str) -> list[str]:
    """Every string literal in the module; the parser joins a message split
    over adjacent literals into one."""
    tree = ast.parse(Path(importlib.import_module(module).__file__).read_bytes())
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


@pytest.mark.parametrize("page", [README, RECOVER], ids=["README", "crapkit-recover"])
@pytest.mark.parametrize("cause, module, printed", CAUSES, ids=[c[0][:24] for c in CAUSES])
def test_the_exit_3_row_names_the_cause(page, cause, module, printed):
    assert any(printed in text for text in _strings(module)), f"{module} reworded the line"
    assert cause in _exit_3_row(page), f"{page.name}'s exit-3 row leaves out {cause!r}"


def test_init_refuses_a_root_package_json_that_is_not_utf8_at_exit_3(tmp_path):
    (tmp_path / "package.json").write_bytes(b'{"name": "caf\xe9"}')

    with pytest.raises(ConfigError) as raised:
        _package_object(tmp_path, "package.json")

    assert raised.value.exit_code == 3
    assert str(raised.value).startswith("init wrote no file: package.json is not UTF-8")
