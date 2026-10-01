"""Whether this machine's file system stores a file name that is not UTF-8.

ext4, XFS and tmpfs store any byte but `/` and NUL in a name. NTFS stores UTF-16,
and APFS on macOS refuses a name that is not UTF-8 with EILSEQ (`Illegal byte
sequence`), from a test's own open() and from git checking such a file out. The
tests that write one gated on `sys.platform == "win32"`, and the first macOS CI
job failed about a hundred of them. They gate on what the file system answers.

CRAPKIT_TEST_REFUSE_BYTE_NAMES=1 makes a Linux run behave like APFS inside the
test processes: the probe answers no, and creating such a name raises EILSEQ.
git and other children still write them, so it finds the tests that create a
name themselves, which is most of them, without a Mac.
"""
import errno
import os
import sys
import tempfile

import pytest

REFUSE_ENV = "CRAPKIT_TEST_REFUSE_BYTE_NAMES"
_CREATING = ("open", "os.mkdir", "os.rename", "os.replace", "os.symlink", "os.link")


def _not_utf8(path) -> bool:
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False
    try:
        os.fsencode(path).decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _refuse_like_apfs(event: str, args: tuple) -> None:
    if event in _CREATING:
        named = next((path for path in args[:2] if _not_utf8(path)), None)
        if named is not None:
            raise OSError(errno.EILSEQ, "Illegal byte sequence", named)


def stores_any_byte() -> bool:
    """Whether a file named b"caf\\xe9" can be made in this machine's temp directory.
    Windows answers no without the probe: its tests spell such a name another way."""
    if sys.platform == "win32" or os.environ.get(REFUSE_ENV) == "1":
        return False
    with tempfile.TemporaryDirectory() as directory:
        try:
            with open(os.path.join(os.fsencode(directory), b"caf\xe9"), "wb"):
                pass
        except OSError:
            return False
    return True


STORES_ANY_BYTE = stores_any_byte()
ANY_BYTE_NAMES = pytest.mark.skipif(
    not STORES_ANY_BYTE, reason="needs a file system that stores any byte in a name; NTFS and APFS "
                                "refuse a name that is not UTF-8")
# A test that spells the name as bytes on Linux and as a lone surrogate on NTFS
# runs on both; APFS refuses either spelling.
NOT_UTF8_NAMES = pytest.mark.skipif(
    sys.platform != "win32" and not STORES_ANY_BYTE,
    reason="needs a file name that is not UTF-8, which APFS refuses (EILSEQ)")

if os.environ.get(REFUSE_ENV) == "1":
    sys.addaudithook(_refuse_like_apfs)
