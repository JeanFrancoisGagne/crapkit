"""Session guards the accuracy conftest applies to every test under tests/accuracy.

- A test never writes under tests/accuracy: fixtures, probes, recordings and
  goldens are expected values, and a test that rewrites one grades itself.
  snapshot() records every file's size and mtime when the session starts, and
  the session fails naming each file that appeared, vanished or changed.
- A test never skips. A missing oracle fails (kit.oracles), and a test outside
  the running tier is deselected (kit.tiers), so a skip can only hide a check.
- The only xfail is a rulings row's: strict, and raising RulingDefect
  (kit.rulings.applies).
"""
from __future__ import annotations

from pathlib import Path

SKIPPED = ("__pycache__",)


def _kept(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return path.is_file() and not any(part in SKIPPED for part in parts)


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """{posix path under root: (size, mtime_ns)} for every file but bytecode caches."""
    files = (path for path in root.rglob("*") if _kept(path, root))
    return {path.relative_to(root).as_posix(): _stamp(path) for path in files}


def _stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def changed(before: dict, after: dict) -> list[str]:
    """Every path added, removed or rewritten between two snapshots."""
    return sorted(path for path in {*before, *after} if before.get(path) != after.get(path))


def skip_problem(skipped: bool, wasxfail: bool) -> str | None:
    if skipped and not wasxfail:
        return ("tests/accuracy never skips: a missing tool fails through kit.oracles and a "
                "test outside the tier is deselected by its marker")
    return None


def _is_ruling(mark) -> bool:
    from .rulings import RulingDefect
    return mark.kwargs.get("strict") is True and mark.kwargs.get("raises") is RulingDefect


def xfail_problem(marks) -> str | None:
    """A problem when any xfail mark did not come from kit.rulings.applies()."""
    if all(map(_is_ruling, marks)):
        return None
    return ("the only xfail under tests/accuracy is a rulings row's: decorate the test with "
            "kit.rulings.applies(<ruling id>)")
