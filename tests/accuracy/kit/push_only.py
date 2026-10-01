"""The push tier sees the push lock's packages and no others, wherever it runs.

CI's accuracy-push job installs tools/accuracy/requirements-push.txt alone. The
accuracy image and a contributor's venv hold the nightly lock too, so a push
test that parsed with tree-sitter passed in both while 0.8.1's first push failed
46 push rows on CI with `No module named 'tree_sitter'`. tools/accuracy/run.py
loads this plugin (`-p accuracy.kit.push_only`) for `--tier push`: once pytest
has configured, importing a package only the nightly lock pins raises
ModuleNotFoundError, as it does on CI. A package already imported by then (a
pytest plugin the venv auto-loads) stays importable.
"""
from __future__ import annotations

import importlib.abc
from pathlib import Path
import re
import sys

LOCKS = Path(__file__).resolve().parents[3] / "tools" / "accuracy"


def pinned(lock: Path) -> set[str]:
    """The import names of the packages a requirements lock pins."""
    names = re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", lock.read_text(encoding="utf-8"), re.M)
    return {name.lower().replace("-", "_").replace(".", "_") for name in names}


def nightly_only(push: Path = LOCKS / "requirements-push.txt",
                 nightly: Path = LOCKS / "requirements-nightly.txt") -> set[str]:
    return pinned(nightly) - pinned(push)


class NightlyOnly(importlib.abc.MetaPathFinder):
    """Refuses every module under a blocked top-level package."""

    def __init__(self, blocked: frozenset[str]) -> None:
        self.blocked = blocked

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.blocked:
            raise ModuleNotFoundError(f"No module named {name!r}: only requirements-nightly.txt pins it",
                                      name=name)
        return None


def finder(loaded=None) -> NightlyOnly:
    """The finder for this venv: the nightly-only packages it has not imported yet."""
    present = {name.split(".")[0] for name in (sys.modules if loaded is None else loaded)}
    return NightlyOnly(frozenset(nightly_only() - present))


def pytest_configure(config) -> None:
    sys.meta_path.insert(0, finder())
