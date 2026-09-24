"""How bytes crapkit did not write become text, and OS text becomes bytes.

Stdlib only, so the advisory hook and the MCP stdio loop can import it. Three
rules, one per kind of source, and each one lives here or in the module named:

- A file the repository owns and crapkit must read exactly (crapkit.toml, the
  marks file) is UTF-8 or refused with a sentence naming the byte:
  `repotext.repo_text`.
- Text crapkit only reads, ranks or passes along (git's free text such as an
  author name or a commit message, a runner's output, an MCP frame, a
  package.json) goes through `lenient`: a UTF-8 BOM is dropped and each byte
  that is not UTF-8 reads as U+FFFD. git, Node and a browser read such bytes
  the same way, and one of them in an author name inside the churn window used
  to stop every command that reads churn.
- Text the OS hands over (argv, the environment, a host name, a directory name)
  arrives as Python decodes it: a byte that is not UTF-8 is a lone surrogate on
  POSIX. `os_bytes` turns it back into the bytes the OS meant, for a hash or a
  lock key. `os_text` makes it text a store or a file can hold.

A path git names is the fourth kind, and `gitpaths` owns it.
"""
from __future__ import annotations

import codecs
import os

_UTF16_MARKS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


def lenient(data: bytes) -> str:
    """UTF-8 with a leading BOM dropped and each undecodable byte as U+FFFD."""
    return data.decode("utf-8-sig", "replace")


def utf16_marked(data: bytes) -> bool:
    """True when the bytes open with a UTF-16 byte-order mark: what Windows
    PowerShell 5.1's `Out-File` and `>` write, and what no UTF-8 reader reads."""
    return data.startswith(_UTF16_MARKS)


def os_bytes(value: str) -> bytes:
    """The bytes an OS string stands for. A lone surrogate from a POSIX byte
    that is not UTF-8 goes back to that byte; one from broken UTF-16 on Windows
    is kept as its three bytes, so no value raises."""
    try:
        return os.fsencode(value)
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass")


def os_text(value: str) -> str:
    """An OS string as text a store or a file can hold: UTF-8, with each byte
    that is not UTF-8 read as U+FFFD."""
    return os_bytes(value).decode("utf-8", "replace")
