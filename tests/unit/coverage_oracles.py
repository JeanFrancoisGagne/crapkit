"""Whole-document oracles for the streaming readers.

json.loads decodes and splits the document, never covstream's framing. The
document is then written back in its canonical compact form and read through
the adapter's public file reader in one window, so no member straddles a
refill and the per-file rules are the adapter's own: the oracle imports no
private function. A streaming read at any chunk size, of any layout, has to
give what this one-window read of the canonical document gives.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from crapkit import coverage_istanbul, coverage_py
from crapkit.errors import ToolError

# Larger than any fixture: the canonical document is read in one window.
_ONE_WINDOW = 1 << 30


def _canonical(text: str, what: str) -> str:
    try:
        return json.dumps(json.loads(text))
    except ValueError as exc:
        raise ToolError(f"unparseable {what}: {exc}") from exc


def _read_whole(text: str, what: str, reader, **kwargs):
    with TemporaryDirectory() as directory:
        path = Path(directory) / "whole.json"
        path.write_text(_canonical(text, what), encoding="utf-8")
        return reader(path, chunk=_ONE_WINDOW, **kwargs)


def parse_coveragepy(text: str, *, path_prefix: str):
    return _read_whole(text, "coverage.py report", coverage_py.parse_coveragepy_both_file,
                       path_prefix=path_prefix)[0]


def parse_coveragepy_missing(text: str, *, path_prefix: str) -> dict[str, set[int]]:
    """Per measured file, the lines coverage.py reports as never run."""
    return _read_whole(text, "coverage.py report", coverage_py.parse_coveragepy_missing_file,
                       path_prefix=path_prefix)


def parse_istanbul(text: str, *, repo_root: str):
    return _read_whole(text, "istanbul artifact", coverage_istanbul.parse_istanbul_both_file,
                       repo_root=repo_root)[0]


def parse_istanbul_missing(text: str, *, repo_root: str) -> dict[str, set[int]]:
    """Per measured file, the lines whose statement never ran."""
    return _read_whole(text, "istanbul artifact", coverage_istanbul.parse_istanbul_missing_file,
                       repo_root=repo_root)
