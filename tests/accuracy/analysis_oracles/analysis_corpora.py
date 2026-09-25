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
import os
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


# The full corpus (tools/accuracy/corpus.py builds it): one directory per
# member. CRAPKIT_ACCURACY_CORPUS names it; else the local fetch cache; else
# /corpus, where the accuracy image keeps it.
CORPUS_ENV = "CRAPKIT_ACCURACY_CORPUS"


def corpus_root() -> Path:
    named = os.environ.get(CORPUS_ENV)
    if named:
        return Path(named)
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "crapkit-accuracy"
    return local / "corpus" if (local / "corpus").is_dir() else Path("/corpus")


def corpus_files(suffixes: tuple[str, ...]) -> dict[str, bytes]:
    """{<member>/<path>: bytes} for every full-corpus file with one of `suffixes`.
    A missing corpus fails the test that asked, naming where it looked."""
    root = corpus_root()
    assert root.is_dir(), (f"the full corpus is not at {root}; set {CORPUS_ENV} or run "
                           "python tools/accuracy/corpus.py fetch")
    paths = sorted(path for path in root.rglob("*") if _wanted(path, suffixes))
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}


def _wanted(path: Path, suffixes: tuple[str, ...]) -> bool:
    return path.suffix.lower() in suffixes and "node_modules" not in path.parts and path.is_file()


def unparsed(corpus: Corpus) -> Corpus:
    files = {name: (ast.unparse(ast.parse(data)) + "\n").encode("utf-8")
             for name, data in corpus.files.items()}
    return Corpus(files, corpus.rejected)


# --- the full corpus (tools/accuracy/corpus.py builds and publishes it) --------------------------

CORPUS_ENV = "CRAPKIT_ACCURACY_CORPUS"
FETCH = "python tools/accuracy/corpus.py fetch"


class CorpusMissing(LookupError):
    """No unpacked full corpus where the kit looks for one."""


def _candidates() -> list[Path]:
    named = os.environ.get(CORPUS_ENV)
    if named:
        return [Path(named)]
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    local = sorted((base / "crapkit-accuracy" / "corpus").glob("*/DIGEST"))
    return [Path("/corpus"), *(digest.parent for digest in reversed(local))]


def full_corpus_root() -> Path:
    """The unpacked full corpus: CRAPKIT_ACCURACY_CORPUS, the image's /corpus, or the
    newest fetch under LOCALAPPDATA (or ~/.cache)/crapkit-accuracy/corpus."""
    for root in _candidates():
        if (root / "DIGEST").is_file():
            return root
    raise CorpusMissing(f"no full corpus found; set {CORPUS_ENV} or run `{FETCH}`")


def member_files(root: Path, member: str, suffixes: tuple) -> dict:
    """{path: bytes} for a corpus member's files with one of `suffixes`."""
    folder = root / member
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*"))
            if path.is_file() and path.suffix.lower() in suffixes}
