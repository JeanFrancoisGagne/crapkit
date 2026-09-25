"""The Python file sets the differentials read, as {repo path: bytes}.

- crapkit_sources(): every .py file of crapkit's own source tree at this
  checkout (2,145 functions at the commit the plan counted);
- stdlib_sources(): the running interpreter's standard library, one tag per
  Python, nightly only;
- unparsed(): the same files after ast.unparse, which puts every bracketed
  expression on one line. A layout difference is its own check
  (test_metamorphic_source), so the counters compare on this form.

A file ast rejects is left out and counted, never compared. No crapkit import.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import sysconfig

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "src" / "crapkit"


@dataclass(frozen=True)
class Corpus:
    files: dict
    rejected: tuple


def _parses(data: bytes) -> bool:
    try:
        ast.parse(data)
    except (SyntaxError, ValueError):
        return False
    return True


def _collect(root: Path, prefix: str, skip=()) -> Corpus:
    paths = sorted(path for path in root.rglob("*.py")
                   if not set(path.relative_to(root).parts) & set(skip))
    files, rejected = {}, []
    for path in paths:
        data, name = path.read_bytes(), f"{prefix}/{path.relative_to(root).as_posix()}"
        if _parses(data):
            files[name] = data
        else:
            rejected.append(name)
    return Corpus(files, tuple(rejected))


def crapkit_sources() -> Corpus:
    return _collect(SOURCE, "crapkit")


# Directories under Lib that are not the library itself: installed packages,
# and trees named like a test directory, which crapkit's universe excludes.
STDLIB_SKIP = ("site-packages", "test", "tests", "idle_test", "__pycache__")


def stdlib_sources() -> Corpus:
    return _collect(Path(sysconfig.get_paths()["stdlib"]), "stdlib", STDLIB_SKIP)


def unparsed(corpus: Corpus) -> Corpus:
    files = {name: (ast.unparse(ast.parse(data)) + "\n").encode("utf-8")
             for name, data in corpus.files.items()}
    return Corpus(files, corpus.rejected)
