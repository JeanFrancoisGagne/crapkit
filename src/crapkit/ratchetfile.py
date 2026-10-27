"""Admitted ratchet text, the stamps it carries, and one publication rule for every writer.

Every writer renders its marks through one of three stamp rules, so no command
picks a stamp of its own:

- `kept`: the write adds no measured number (move, merge, prune, the hook's
  grant), so both recorded stamps stay.
- `measured`: the write adds numbers one metric produced (verify's tighten and
  its grant), and marks another metric recorded refuse it.
- `reseeded`: seed, the only write that replaces a recorded metric stamp, with
  the metric of the stored run it read.

Then `publish` replaces the admitted text, or refuses a change another writer made.

The file reads by `repotext.marks_text`: UTF-16 when a byte-order mark says
so, else UTF-8 with a BOM dropped, and each byte neither reads as U+FFFD. A
write keeps the encoding it read, and refuses when the read replaced a byte,
since the rewrite would save U+FFFD in place of the name that held it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

from .errors import ConfigError, ToolError
from .locks import exclusive_lock
from .ratchet import (MetricStamp, dump_ratchet, load_ratchet, read_key_version, read_stamp,
                      stamp_conflict)
from .repotext import marks_bytes, marks_codec, marks_text, unreadable_byte


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ToolError(f"cannot read ratchet {path.name}: {exc}") from exc


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _lf(text: str | None) -> str | None:
    return None if text is None else text.replace("\r\n", "\n")


def _newline(text: str | None) -> str:
    """The line ending the file on disk already uses. A Windows checkout under
    core.autocrlf=true holds the marks as CRLF, and git reads them back as LF."""
    return "\r\n" if text and "\r\n" in text else "\n"


def _replace(path: Path, data: bytes) -> None:
    handle = NamedTemporaryFile(mode="wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp",
                                delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        handle.close()
        temporary.unlink(missing_ok=True)


def _refuse_replaced(sides) -> None:
    """A rewrite of marks whose read put U+FFFD in place of a byte would save
    U+FFFD where a name was, or drop the mark as a function that is gone."""
    for side in sides:
        if side.unreadable:
            raise ConfigError(f"{side.path.name} holds {side.unreadable}, which reads as U+FFFD, and "
                              "rewriting the file would save U+FFFD in its place; save it as UTF-8 "
                              "and rerun; file left unchanged")


@dataclass(frozen=True)
class RatchetFile:
    """`codec` is the encoding the file was read in and a write keeps;
    `unreadable` names the first byte the read replaced, or is None."""
    path: Path
    text: str | None
    sha256: str | None
    codec: str = "utf-8"
    unreadable: str | None = None

    @classmethod
    def read(cls, path: Path, *, required: bool = False) -> RatchetFile:
        data = _read(path)
        if data is None and required:
            raise ConfigError(f"ratchet input {path} is missing")
        if data is None:
            return cls(path.resolve(), None, None)
        return cls(path.resolve(), marks_text(data), _digest(data), marks_codec(data),
                   unreadable_byte(data))

    @classmethod
    def committed(cls, path: Path, data: bytes) -> RatchetFile:
        """The marks one commit held, read for judging and never published. A
        past revision reads by the rule today's file does and is never refused:
        the user may have deleted the file whose bytes it holds."""
        return cls(path.resolve(), marks_text(data), _digest(data), marks_codec(data),
                   unreadable_byte(data))

    @property
    def blank(self) -> bool:
        """True when there is no file, or it holds nothing but blank lines: no
        marks, no stamp, no header. A file with a stamp and no rows is not blank."""
        return not (self.text or "").strip()

    @property
    def entries(self) -> list:
        try:
            return load_ratchet(self.text or "")
        except ValueError as exc:
            raise ConfigError(f"unreadable ratchet file {self.path.name}: {exc}") from exc

    @property
    def metric_stamp(self) -> MetricStamp:
        """The metric the recorded marks were measured under; NONE when none is recorded."""
        return read_stamp(self.text or "")

    def stamp_conflict(self, metric: MetricStamp) -> str | None:
        """Verify's refusal when these marks and numbers `metric` produced cannot be
        compared; None when they can, or when no metric is recorded."""
        return stamp_conflict(self.metric_stamp, metric)

    def kept(self, entries: list, *, keys: int | None = None,
             new_file_metric: MetricStamp = MetricStamp.NONE,
             read_with: tuple[RatchetFile, ...] = ()) -> str:
        """The text for a write that adds no measured number: both recorded stamps stay.

        A file that does not exist yet records nothing, so it takes the metric its
        first numbers came from. `keys` is a key format identity proof just
        established; without one the recorded format stays. `read_with` are the
        other files whose marks reach the text: the merge driver's THEIRS.
        """
        metric = new_file_metric if self.text is None else self.metric_stamp
        return self._dump(entries, metric, keys, read_with)

    def measured(self, entries: list, metric: MetricStamp, *, keys: int | None = None) -> str:
        """The text for a write that adds numbers `metric` produced.

        Marks another metric recorded refuse it with the refusal verify gives
        before its lanes run. A file written before stamping gains the stamp,
        which is the tighten's `restamped`.
        """
        conflict = self.stamp_conflict(self._vouched(metric))
        if conflict:
            raise ConfigError(conflict)
        return self._dump(entries, metric, keys)

    def reseeded(self, entries: list, metric: MetricStamp, *, keys: int | None = None) -> str:
        """seed's text, stamped with the metric of the run it read."""
        return self._dump(entries, self._vouched(metric), keys)

    def _vouched(self, metric: MetricStamp) -> MetricStamp:
        """A write that stamps numbers names the metric that produced them.

        NONE used to fall through to whatever stamp was there, so the
        new numbers took a label nobody had checked.
        """
        if not metric:
            raise ConfigError(f"a write that stamps numbers into {self.path.name} names no "
                              "metric, so it cannot vouch for them; the file was left unchanged")
        return metric

    def _dump(self, entries: list, metric: MetricStamp, keys: int | None, read_with=()) -> str:
        """The text a write would publish, refused here when it changes a file
        whose read replaced a byte: the override renders it before its alert
        fires, so a refusal leaves no alert and no audit row behind."""
        version = read_key_version(self.text or "") if keys is None else keys
        text = dump_ratchet(entries, stamp=metric, key_version=version)
        if _lf(text) != _lf(self.text):
            _refuse_replaced((self, *read_with))
        return text

    def publish(self, text: str) -> bool:
        """Replace the admitted text, or refuse a change another writer made."""
        lock = self.path.parent / ".crapkit" / (self.path.name + ".lock")
        try:
            with exclusive_lock(lock, label="ratchet publication"):
                return self._publish_locked(text)
        except OSError as exc:
            raise ToolError(f"cannot publish ratchet {self.path.name}: {exc}") from exc

    def _publish_locked(self, text: str) -> bool:
        """A text that differs from the admitted one only in line endings is the
        same marks, so the file is left alone; a real change keeps its endings
        and its encoding."""
        if _digest(_read(self.path)) != self.sha256:
            raise ConfigError(f"ratchet {self.path.name} changed during the command; "
                              "rerun against the current marks; file left unchanged")
        if _lf(text) == _lf(self.text):
            return False
        _replace(self.path, marks_bytes(_lf(text).replace("\n", _newline(self.text)), self.codec))
        return True
