"""Where the unpacked full corpus is: one answer for every packet.

CRAPKIT_ACCURACY_CORPUS names it when set. Otherwise the accuracy image's
/corpus, then the per-user cache `python tools/accuracy/corpus.py fetch` fills:
corpus-<digest> under %LOCALAPPDATA%/crapkit-accuracy/corpus (under
~/.cache/crapkit-accuracy/corpus where LOCALAPPDATA is unset), at the digest
corpus.toml pins today. A candidate counts only when it holds the DIGEST file
corpus.py writes, so an image built without the release asset, whose /corpus
is empty, falls through to the cache.

A check that needs the corpus calls require(): a missing corpus is an infra
miss, noted in the run log and named with every place looked in, the way a
missing oracle is.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

from . import runlog

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools" / "accuracy" / "corpus.py"
ENV = "CRAPKIT_ACCURACY_CORPUS"
IMAGE = Path("/corpus")
FETCH = "python tools/accuracy/corpus.py fetch"
_MODULE = "accuracy_corpus_tool"


def corpus_tool():
    """tools/accuracy/corpus.py, loaded once by path."""
    if _MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE, TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE]


def candidates() -> list[Path]:
    """The places looked in, in order."""
    named = os.environ.get(ENV)
    if named:
        return [Path(named)]
    tool = corpus_tool()
    return [IMAGE, tool.default_dest() / tool.tag(tool.load())]


def locate() -> Path | None:
    """The first candidate that holds a built corpus, or None."""
    return next((path for path in candidates() if (path / "DIGEST").is_file()), None)


def missing() -> str:
    return (f"no full corpus: set {ENV}, run in the accuracy image, or run `{FETCH}`; "
            f"looked in {', '.join(map(str, candidates()))}")


def require() -> Path:
    """The full corpus; without one, the calling check ends as an infra miss."""
    found = locate()
    if found is None:
        runlog.note("infra", message=missing())
        pytest.fail(missing(), pytrace=False)
    return found
