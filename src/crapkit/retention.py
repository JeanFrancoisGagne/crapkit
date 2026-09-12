"""Retain recognized test evidence without touching live or user-owned output."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time

from .errors import ToolError
from .locks import exclusive_lock
from .procs import own_processes


DEFAULT_TEST_RETENTION_DAYS = 7
DEFAULT_TEST_RETENTION_COUNT = 10
_RECEIPT = ".crapkit-test-run.json"


def _same_place(path: Path, resolved: Path) -> bool:
    r"""Whether `resolve()` named the directory `path` already names.

    On Windows it names the same directory two other ways while a sibling
    process is creating or deleting it: the extended-length form with the
    `\\?\` prefix, and the NTFS tombstone under `$Extend\$Deleted` for a
    directory whose last handle is still open. Two direct runners sharing a
    repository hit both, and the guard read each as a redirect and refused.
    A symlink or junction elsewhere still resolves elsewhere, and is still
    refused.
    """
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(text) == os.path.normcase(str(path)) or "$Extend" in text


def _parent(root: Path) -> Path:
    path = root / ".crapkit" / "test-runs"
    if not _same_place(path, path.resolve()):
        raise ToolError("test evidence retention refuses a redirected .crapkit/test-runs path")
    return path


def _lease(parent: Path, name: str) -> Path:
    return parent / ".leases" / (name + ".lock")


def _safe_run(path: Path, parent: Path) -> bool:
    return path.parent == parent and path.name.startswith("run-") and _same_place(path, path.resolve())


def _valid_receipt(value: dict, root: Path, path: Path) -> bool:
    expected = ("crapkit-test-run", 1, str(root), path.name)
    actual = tuple(value.get(key) for key in ("kind", "schema", "root", "name"))
    return type(value.get("schema")) is int and actual == expected


def _read_receipt(path: Path, root: Path) -> tuple[float, dict] | None:
    if not _safe_run(path, _parent(root)) or not path.is_dir():
        return None
    try:
        value = json.loads((path / _RECEIPT).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not _valid_receipt(value, root, path):
            return None
        return _receipt_time(value), value
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _receipt_time(value: dict) -> float:
    stamp = value.get("finished_at", value["created_at"])
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0:
        raise ValueError("invalid evidence timestamp")
    return float(stamp)


def _candidates(parent: Path, root: Path) -> list[tuple[float, Path]]:
    if not parent.is_dir():
        return []
    records = [(record[0], path) for path in parent.iterdir()
               if (record := _read_receipt(path, root)) is not None]
    return sorted(records, reverse=True)


def _expired(index: int, stamp: float, keep: int, cutoff: float | None) -> bool:
    return bool(keep and index >= keep) or (cutoff is not None and stamp < cutoff)


def _prune_one(path: Path, root: Path, dry_run: bool, stamp: float) -> str:
    parent = _parent(root)
    lease = _lease(parent, path.name)
    if not lease.is_file() or lease.resolve() != lease:
        return "unproven"
    try:
        with exclusive_lock(lease, label="test evidence"):
            return _remove_recognized(path, root, dry_run, stamp)
    except ToolError:
        return "active"


def _remove_recognized(path: Path, root: Path, dry_run: bool, stamp: float) -> str:
    record = _read_receipt(path, root)
    if record is None:
        return "unproven"
    if record[0] != stamp:
        return "changed"
    if dry_run:
        return "planned"
    shutil.rmtree(path)
    return "removed"


def _cutoff(keep: int, days: int) -> float | None:
    if keep < 0 or days < 0:
        raise ValueError("test evidence retention limits must be nonnegative")
    return time.time() - days * 86400 if days else None


def prune_test_runs(root: Path, *, keep: int = DEFAULT_TEST_RETENTION_COUNT,
                    days: int = DEFAULT_TEST_RETENTION_DAYS, dry_run: bool = False) -> dict:
    """Remove marked idle runs beyond either enabled retention limit.

    Active leases, redirected paths and unrecognized evidence are preserved.
    Zero disables the corresponding limit. Explicit output has no marker and
    never participates. The stable lease remains after removal for safe reuse.
    """
    root = root.resolve()
    cutoff = _cutoff(keep, days)
    result = {key: [] for key in ("removed", "planned", "active", "unproven", "changed")}
    for index, (stamp, path) in enumerate(_candidates(_parent(root), root)):
        if _expired(index, stamp, keep, cutoff):
            result[_prune_one(path, root, dry_run, stamp)].append(str(path))
    return result


def _write_receipt(path: Path, value: dict) -> None:
    temporary = path / (_RECEIPT + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path / _RECEIPT)


def _new_run(root: Path, parent: Path) -> tuple[Path, dict]:
    parent.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    receipt = {"kind": "crapkit-test-run", "schema": 1, "root": str(root),
               "name": path.name, "created_at": time.time()}
    return path, receipt


def _explicit_directory(root: Path, selected: Path) -> Path:
    output = (root / selected).resolve()
    if not output.is_relative_to(root):
        raise ValueError(f"test output must be inside {root}: {output}")
    return output


def _explicit_ownership(parent: Path, output: Path) -> tuple[Path, Path | None]:
    if output.is_relative_to(parent):
        parts = output.relative_to(parent).parts
        if parts and parts[0].startswith("run-"):
            return _lease(parent, parts[0]), parent / parts[0]
    name = "output-" + hashlib.sha256(str(output).encode("utf-8")).hexdigest()
    return _lease(parent, name), None


@contextmanager
def _explicit_run(root: Path, parent: Path, selected: Path):
    output = _explicit_directory(root, selected)
    lease, retained = _explicit_ownership(parent, output)
    with own_processes((lease,), label="test output") as owner:
        output.mkdir(parents=True, exist_ok=True)
        if retained is not None and _read_receipt(retained, root) is not None:
            (retained / _RECEIPT).unlink()
        yield output, owner


@contextmanager
def test_run_directory(root: Path, *, selected: Path | None = None,
                       keep: int = DEFAULT_TEST_RETENTION_COUNT,
                       days: int = DEFAULT_TEST_RETENTION_DAYS):
    """Own test descendants and output; mark default runs for bounded retention."""
    root = root.resolve()
    parent = _parent(root)
    if selected is not None:
        with _explicit_run(root, parent, selected) as held:
            yield held
        return
    with _default_run(root, parent, keep, days) as held:
        yield held


@contextmanager
def _default_run(root: Path, parent: Path, keep: int, days: int):
    output, receipt = _new_run(root, parent)
    with own_processes((_lease(parent, output.name),), label="test evidence") as owner:
        _write_receipt(output, receipt)
        prune_test_runs(root, keep=keep, days=days)
        try:
            yield output, owner
        finally:
            receipt["finished_at"] = time.time()
            _write_receipt(output, receipt)
