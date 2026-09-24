"""A Latin-1 POSIX locale a test can start a child under, built without root.

Stock Linux images list C, C.UTF-8 and POSIX in `locale -a` and nothing else,
so a test that names en_US.ISO-8859-1 in LANG gets the C locale and proves
nothing. glibc also reads a locale from the directory LOCPATH names, and
`localedef` compiles one there from the sources under /usr/share/i18n, so the
test builds the locale in its own temp directory and hands the child LOCPATH
beside LANG and LC_ALL.
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

import hang_guard

LATIN1 = "en_US.ISO-8859-1"


def latin1_env(directory: Path) -> dict[str, str]:
    """The env pairs that start a child under en_US.ISO-8859-1, or a skip that
    names what this host lacks. The locale is proved by asking a child which
    encoding it reads, so a build that silently fell back to C fails here."""
    if not sys.platform.startswith("linux"):
        pytest.skip("a POSIX locale row: Windows takes a pipe's encoding from the ANSI code page")
    localedef = shutil.which("localedef")
    if localedef is None:
        pytest.skip(f"no localedef on this host to build {LATIN1}")
    # localedef exits 1 on a portability warning and still writes the locale.
    directory.mkdir(parents=True, exist_ok=True)
    hang_guard.run([localedef, "-i", "en_US", "-f", "ISO-8859-1", str(directory / LATIN1)])
    if not (directory / LATIN1).is_dir():
        pytest.skip(f"localedef could not build {LATIN1}: the host lacks /usr/share/i18n sources")
    env = {"LOCPATH": str(directory), "LANG": LATIN1, "LC_ALL": LATIN1}
    probe = hang_guard.run([sys.executable, "-c", "import locale; print(locale.getencoding())"],
                           env={**os.environ, **env, "PYTHONUTF8": "0"})
    assert probe.stdout.strip() == b"ISO-8859-1", probe
    return env
