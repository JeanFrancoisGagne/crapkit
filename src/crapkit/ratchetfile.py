"""Admitted ratchet text and one concurrent publication rule for every writer."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

from .errors import ConfigError, ToolError
from .locks import exclusive_lock
from .ratchet import load_ratchet
from .repotext import repo_bytes_text


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ToolError(f"cannot read ratchet {path.name}: {exc}") from exc


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _replace(path: Path, text: str) -> None:
    handle = NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        handle.close()
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class RatchetFile:
    path: Path
    text: str | None
    sha256: str | None

    @classmethod
    def read(cls, path: Path, *, required: bool = False) -> RatchetFile:
        data = _read(path)
        if required and data is None:
            raise ConfigError(f"ratchet input {path} is missing")
        text = None if data is None else repo_bytes_text(data, path.name)
        return cls(path.resolve(), text, _digest(data))

    @property
    def entries(self) -> list:
        try:
            return load_ratchet(self.text or "")
        except ValueError as exc:
            raise ConfigError(f"unreadable ratchet file {self.path.name}: {exc}") from exc

    def publish(self, text: str) -> bool:
        """Replace the admitted text, or refuse a change another writer made."""
        lock = self.path.parent / ".crapkit" / (self.path.name + ".lock")
        try:
            with exclusive_lock(lock, label="ratchet publication"):
                return self._publish_locked(text)
        except OSError as exc:
            raise ToolError(f"cannot publish ratchet {self.path.name}: {exc}") from exc

    def _publish_locked(self, text: str) -> bool:
        if _digest(_read(self.path)) != self.sha256:
            raise ConfigError(f"ratchet {self.path.name} changed during the command; "
                              "rerun against the current marks; file left unchanged")
        if text == self.text:
            return False
        _replace(self.path, text)
        return True
