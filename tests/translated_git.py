"""A git that speaks French, for tests about the words git prints on stderr.

git translates the prefix it puts on a message: `error: ` becomes `erreur : `
and `fatal: ` becomes `fatal : ` under a French locale. crapkit tells a failed
read from an answer by that prefix, so a test needs a git that translates it.
Linux distributions ship git's catalogs and git-for-windows does not, so the
test writes its own: a two-entry catalog that git loads through
GIT_TEXTDOMAINDIR.

A git built without gettext, or a machine with no locale that loads it,
prints English anyway. speak_french then skips the test instead of passing it
on a git that was never asked in French.
"""
import struct
import subprocess
from pathlib import Path

import pytest

_TRANSLATIONS = {"": "Content-Type: text/plain; charset=UTF-8\n",
                 "error: ": "erreur : ", "fatal: ": "fatal : "}


def _catalog(pairs: dict[str, str]) -> bytes:
    """A GNU .mo file: a header, the two offset tables, then the strings."""
    keys = sorted(pairs)
    ids = [key.encode("utf-8") for key in keys]
    texts = [pairs[key].encode("utf-8") for key in keys]
    start = 28 + 16 * len(keys)
    table, blob = b"", b""
    for string in (*ids, *texts):
        table += struct.pack("<2I", len(string), start + len(blob))
        blob += string + b"\0"
    header = struct.pack("<7I", 0x950412DE, 0, len(keys), 28, 28 + 8 * len(keys), 0, 0)
    return header + table + blob


# glibc reads LANGUAGE only under a locale other than C, and a Linux machine may
# have no French locale built. Any other UTF-8 locale it has will do.
_LOCALES = ("fr_FR.UTF-8", "en_US.UTF-8", "C.UTF-8")


def speak_french(catalog_dir: Path, monkeypatch) -> None:
    """Every git this test starts from now on prints its prefixes in French."""
    messages = catalog_dir / "fr" / "LC_MESSAGES"
    messages.mkdir(parents=True, exist_ok=True)
    (messages / "git.mo").write_bytes(_catalog(_TRANSLATIONS))
    monkeypatch.setenv("GIT_TEXTDOMAINDIR", str(catalog_dir))
    monkeypatch.setenv("LANGUAGE", "fr")
    for locale in _LOCALES:
        monkeypatch.setenv("LC_ALL", locale)
        said = _what_git_says(catalog_dir)
        if said.startswith("fatal : "):
            return
    pytest.skip(f"this git does not load a translation catalog: it said {said.strip()!r}")


def _what_git_says(cwd: Path) -> str:
    return subprocess.run(["git", "rev-parse", "--verify", "refs/no/such/ref"], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8").stderr
