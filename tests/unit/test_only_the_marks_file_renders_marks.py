"""Only the marks-file module renders marks text.

`dump_ratchet` writes whatever stamp it is handed. Commands used to call it
directly, each picking a stamp of its own, and most took the running metric by
omission, so a write that added no numbers relabeled marks another metric
recorded. The stamp rules now live in `RatchetFile` (`kept`, `measured`,
`reseeded`), and `publish` accepts any text, so the rules hold only while no
other module renders marks. This test keeps it that way: a new writer that
calls `dump_ratchet` itself fails here instead of relabeling the file.
"""
import ast
from pathlib import Path

import crapkit

PACKAGE = Path(crapkit.__file__).parent
OWNERS = {"ratchet.py", "ratchetfile.py"}  # the definition, and its one caller


def _names_dump_ratchet(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return any(alias.name == "dump_ratchet" for alias in node.names)
    if isinstance(node, ast.Attribute):
        return node.attr == "dump_ratchet"
    return isinstance(node, ast.Name) and node.id == "dump_ratchet"


def _renderers() -> list[str]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found += [f"{rel}:{node.lineno}" for node in ast.walk(tree) if _names_dump_ratchet(node)]
    return found


def test_no_module_but_the_marks_file_renders_marks():
    assert _renderers() == []


def test_the_walk_reads_the_package_it_guards():
    """An empty result from an empty walk would pass the guard vacuously."""
    assert (PACKAGE / "ratchetfile.py").is_file()
    assert "dump_ratchet" in (PACKAGE / "ratchetfile.py").read_text(encoding="utf-8")
