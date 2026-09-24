"""A lane's declared output files, owned through each attempt at writing them.

crapkit used to decide whether an attempt wrote its artifact by the file's
modification time: moved means written. A command that only touched the old
report (a make rule, a cache restore that sets times) passed, and the previous
run's coverage was scored and stamped as this commit's. The refusal of a
failed attempt's leftover was keyed on the same time, so a touch, a copy that
drops times or a same-bytes rewrite handed the dead lane's numbers back to
reuse. And the flake retest judged its junit the same way, so a retest that
rewrote the file inside the old one's time tick had its passes ignored.

So the declared files move aside under .crapkit/ before an attempt starts. A
file at a declared path afterwards is one the attempt wrote, whatever its time
or bytes. When the attempt is over, a leftover goes back only where the attempt
wrote nothing, and its sha256 is what a refusal records.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from .errors import ToolError


def _aside_dir(root: Path, owner: str) -> Path:
    """One directory per lane (or per lane's retest), named so two lanes cannot
    share it whatever their names hold."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", owner)[:40]
    tag = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:8]
    return root / ".crapkit" / "aside" / f"{safe}-{tag}"


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    shutil.move(str(source), str(target))


class Outputs:
    """The declared files of one lane across one run's attempts. Use it as a
    context manager: entering moves every declared file that exists aside, and
    leaving puts each back where no attempt wrote one."""

    def __init__(self, root: Path, owner: str, names) -> None:
        self._root = root
        self._names = tuple(dict.fromkeys(names))
        self._dir = _aside_dir(root, owner)
        self._aside: dict[str, Path] = {}
        self.leftovers: dict[str, str] = {}

    def __enter__(self) -> Outputs:
        try:
            for index, name in enumerate(self._names):
                self._set_aside(index, name)
        except OSError as exc:
            self.__exit__()
            raise ToolError(f"crapkit cannot move {name} aside under .crapkit/aside before "
                            f"the lane runs ({exc}); close whatever holds it and rerun") from exc
        return self

    def _set_aside(self, index: int, name: str) -> None:
        path = self._root / name
        if path.is_file():
            _move(path, self._dir / f"{index}-{path.name}")
            self._aside[name] = self._dir / f"{index}-{path.name}"

    def clear(self) -> None:
        """Before a later attempt: drop what an earlier one of this run left at
        a declared path, so the next attempt is judged on its own."""
        for name in self._names:
            (self._root / name).unlink(missing_ok=True)

    def written(self, name: str) -> bool:
        """Whether an attempt of this run wrote the declared file."""
        return (self._root / name).is_file()

    def unwritten(self) -> list[str]:
        """The declared files a previous run left that no attempt rewrote."""
        return [name for name in self._aside if not self.written(name)]

    def __exit__(self, *_exc) -> None:
        for name, aside in self._aside.items():
            if not self.written(name):
                _move(aside, self._root / name)
                self.leftovers[name] = _sha256(self._root / name)
        shutil.rmtree(self._dir, ignore_errors=True)
        _prune_empty(self._dir.parent)


def _sha256(path: Path) -> str:
    from .lane_stamps import file_sha256

    return file_sha256(path)


def _prune_empty(directory: Path) -> None:
    try:
        os.rmdir(directory)
    except OSError:
        pass


def owned(root: Path, owner: str, names) -> Outputs:
    """The declared files `names` of `owner` (a lane's name), to hold through
    its attempts."""
    return Outputs(root, owner, names)
