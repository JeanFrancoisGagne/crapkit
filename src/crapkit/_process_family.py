"""POSIX nested guardians retain ancestor completion until their writers stop.

Command groups cannot kill a nested guardian before it finishes cleanup. Each
guardian runs in a separate session and holds a lease in every ancestor family.
Closing admission before killing a group makes late guardian starts refuse.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .errors import ToolError

_ENV = 'CRAPKIT_COMMAND_FAMILIES'


def command_environment(environment):
    """Name a family before spawn; only successful registration creates it."""
    family = str(Path(tempfile.gettempdir()) / ('crapkit-family-' + uuid.uuid4().hex))
    return family, {**(os.environ if environment is None else environment),
                    _ENV: json.dumps([*_inherited(), family])}


def _inherited():
    """The ancestor families crapkit handed this process. The variable is
    crapkit's own hand-off, so any other value is refused by name: parsed
    unchecked, it stopped the measurement owner with no word of the cause."""
    text = os.environ.get(_ENV, '[]')
    try:
        families = json.loads(text)
    except ValueError:
        families = None
    if not (isinstance(families, list) and all(map(_is_family, families))):
        raise ToolError(f'{_ENV} holds {text!r}, not the JSON list of absolute paths crapkit '
                        f'hands a nested command; unset {_ENV} and run the command again')
    return families


def _is_family(value):
    return isinstance(value, str) and os.path.isabs(value) and '\0' not in value


@contextmanager
def _locked(path, mode='r+b'):
    import fcntl
    with path.open(mode) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield stream


def _register(path, stack):
    with _locked(path / 'admission'):
        if (path / 'closed').exists():
            raise ToolError('ancestor command has stopped; no new work can start')
        lease = path / (uuid.uuid4().hex + '.lease')
        stack.enter_context(_locked(lease, 'x+b'))


@contextmanager
def ancestor_leases():
    """Keep all ancestor commands incomplete until this guardian exits."""
    families = _inherited()
    with ExitStack() as stack:
        try:
            for path in families:
                _register(Path(path), stack)
        except OSError as error:
            raise ToolError('ancestor command has stopped; cannot retain ownership') from error
        yield


class Family:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(mode=0o700)
        try:
            (self.path / 'admission').touch()
        except OSError:
            self.path.rmdir()
            raise

    def close_admission(self):
        with _locked(self.path / 'admission'):
            (self.path / 'closed').touch()
            return list(self.path.glob('*.lease'))

    def finish(self, leases):
        for lease in leases:
            with _locked(lease):
                pass
        shutil.rmtree(self.path)

    @contextmanager
    def stopping(self):
        leases = self.close_admission()
        try:
            yield
        finally:
            self.finish(leases)
