"""Bound command logs while retaining the newest output and progress count."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import threading


DEFAULT_LOG_MAX_BYTES = 16 * 1024 * 1024


def _trim_existing(path: Path, limit: int) -> None:
    if not path.exists() or path.stat().st_size <= limit:
        return
    with path.open("r+b") as stream:
        stream.seek(-limit, os.SEEK_END)
        tail = stream.read()
        stream.seek(0)
        stream.write(tail)
        stream.truncate()


class _CommandLog:
    def __init__(self, path: Path, limit: int, append: bool):
        self.path, self.limit = path, limit
        self.backup = path.with_name(path.name + ".1")
        self.progress_bytes = 0
        self.parent_bytes = 0
        self.changed = threading.Condition()
        self.error = None
        self._prepare(append)
        with ExitStack() as startup:
            self.sink = startup.enter_context(path.open("ab" if append else "wb", buffering=0))
            self.position = self.sink.tell()
            read_fd, write_fd = os.pipe()
            self.reader = startup.enter_context(os.fdopen(read_fd, "rb", buffering=0))
            self.writer = startup.enter_context(os.fdopen(write_fd, "wb", buffering=0))
            self.thread = threading.Thread(target=self._drain, name="crapkit-log")
            self.thread.start()
            startup.pop_all()

    def _prepare(self, append: bool) -> None:
        if append:
            _trim_existing(self.path, self.limit)
            _trim_existing(self.backup, self.limit)
        else:
            self.backup.unlink(missing_ok=True)

    def fileno(self) -> int:
        return self.writer.fileno()

    def write(self, text: str) -> int:
        data = memoryview(text.replace("\n", os.linesep).encode("utf-8", errors="replace"))
        self.parent_bytes += len(data)
        while data:
            data = data[self.writer.write(data):]
        return len(text)

    def flush(self) -> None:
        self.writer.flush()
        with self.changed:
            self.changed.wait_for(lambda: self.progress_bytes >= self.parent_bytes or self.error is not None)
        if self.error is not None:
            raise self.error

    def _rotate(self) -> None:
        self.sink.close()
        os.replace(self.path, self.backup)
        self.sink = self.path.open("wb", buffering=0)
        self.position = 0

    def _store(self, block: bytes) -> None:
        data = memoryview(block)
        while data:
            if self.position == self.limit:
                self._rotate()
            amount = self.sink.write(data[:self.limit - self.position])
            self.position += amount
            data = data[amount:]

    def _accept(self, block: bytes) -> None:
        self._store_unless_failed(block)
        with self.changed:
            self.progress_bytes += len(block)
            self.changed.notify_all()

    def _store_unless_failed(self, block: bytes) -> None:
        if self.error is not None:
            return
        try:
            self._store(block)
        except OSError as error:
            self.error = error

    def _drain(self) -> None:
        try:
            with self.reader:
                while block := self.reader.read(64 * 1024):
                    self._accept(block)
        except OSError as error:
            with self.changed:
                self.error = error
                self.changed.notify_all()

    def close(self) -> None:
        self.writer.close()
        self.thread.join()
        self.sink.close()
        if self.error is not None:
            raise self.error


@contextmanager
def command_log(path: Path, *, max_bytes: int = DEFAULT_LOG_MAX_BYTES, append: bool = False):
    """Yield command stdout; each active/backup file stays within max_bytes.

    Callers must finish owned descendants before leaving this context. The
    reader blocks on input while commands are silent and closes after pipe EOF.
    Small logs retain their exact bytes. Zero uses a direct unlimited file.
    """
    if max_bytes < 0:
        raise ValueError("log max_bytes must be nonnegative")
    if max_bytes == 0:
        with path.open("a" if append else "w", encoding="utf-8", errors="replace") as stream:
            yield stream
        return
    stream = _CommandLog(path, max_bytes, append)
    try:
        yield stream
    finally:
        stream.close()
