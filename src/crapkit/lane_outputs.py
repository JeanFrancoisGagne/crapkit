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

A kill runs no cleanup, so an attempt that never finished leaves its copies
under .crapkit/aside/. The next holder of the lane's measurement lock puts each
back where nothing was written since (`put_back`), and names the ones it must
leave: no attempt of that lane can be running while the lock is held.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from .errors import ToolError


def _aside_dir(root: Path, owner: str) -> Path:
    """One directory per lane (or per lane's retest), named so two lanes cannot
    share it whatever their names hold."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", owner)[:40]
    tag = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:8]
    return root / ".crapkit" / "aside" / f"{safe}-{tag}"


def _aside_path(directory: Path, index: int, name: str) -> Path:
    return directory / f"{index}-{Path(name).name}"


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
            self._aside[name] = _aside_path(self._dir, index, name)
            _move(path, self._aside[name])

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


def owners(lane) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Each owner that sets a lane's declared files aside, with the files it
    sets aside: the lane's attempts, and its flake retest."""
    attempts = ((lane.name, declared_files(lane)),)
    if not lane.results_artifact:
        return attempts
    return (*attempts, (retest_owner(lane), (lane.results_artifact,)))


def retest_owner(lane) -> str:
    return f"{lane.name} retest"


class Stray(NamedTuple):
    """A declared file an attempt that never finished left under
    .crapkit/aside/: `copy` is where it sits, and `back` whether it went back
    to its path, which it does only where no file was written since."""
    name: str
    copy: Path
    back: bool


def put_back(root: Path, owner: str, names) -> list[Stray]:
    """What an attempt of `owner` that never finished left aside, each copy
    moved back to its declared path where that path holds no file. A file there
    was written after the attempt set the copy aside, by the attempt itself or
    by hand, so it stays. Call it only holding the lane's measurement lock."""
    directory = _aside_dir(root, owner)
    strays = [Stray(name, _aside_path(directory, index, name), False)
              for index, name in enumerate(dict.fromkeys(names))]
    found = [_returned(root, stray) for stray in strays if stray.copy.is_file()]
    _prune_empty(directory)
    _prune_empty(directory.parent)
    return found


def _returned(root: Path, stray: Stray) -> Stray:
    if (root / stray.name).exists():
        return stray
    _move(stray.copy, root / stray.name)
    return stray._replace(back=True)


# --- which files are lane outputs --------------------------------------------------

def declared_files(lane) -> tuple[str, ...]:
    """Every path the lane says its command writes."""
    return (lane.artifact, lane.results_artifact) if lane.results_artifact else (lane.artifact,)


def normalized(name: str) -> str:
    """A lane output as the loaded config spells it (repopath's declared entry
    already put `/` between its directories), with `a/../b` resolved."""
    return posixpath.normpath(name)


def config_bytes(root: Path) -> bytes:
    """crapkit.toml's bytes, or b"" when the root holds none."""
    config = root / "crapkit.toml"
    return config.read_bytes() if config.is_file() else b""


@lru_cache(maxsize=4)
def configured_outputs(config: bytes) -> frozenset[str]:
    """Every artifact and results file the lanes of this crapkit.toml declare,
    or none when it does not load."""
    from .config import load_config_text
    from .errors import CrapkitError
    from .repotext import repo_bytes_text

    try:
        lanes = load_config_text(repo_bytes_text(config, "crapkit.toml")).lanes
    except (CrapkitError, ValueError):
        return frozenset()
    return frozenset(normalized(name) for lane in lanes for name in declared_files(lane))


def declared_outputs(root: Path, lane, config: bytes | None = None) -> frozenset[str]:
    """Every artifact and results file crapkit.toml declares, plus this lane's
    own, as root-relative paths: outputs by name, never recorded as by-products."""
    text = config_bytes(root) if config is None else config
    return frozenset(normalized(name) for name in declared_files(lane)) | configured_outputs(text)
