"""The doc ranges a clean-room model is written from, pinned by sha256.

A model_*.py is written from cited lines of crapkit's docs, never from its
code. Its module docstring names each range on a line of its own:

    doc: README.md:819-828 sha256=<64 hex>

The digest covers those lines, joined with LF and without the final newline.
test_kit_contract recomputes it, so a docs edit that moves or rewrites a
cited range fails until the model is re-read against the new text and its
header updated. `python tools/accuracy/run.py doc-range README.md 819 828`
prints the header line for a range.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[3]
LINE = re.compile(r"^\s*doc: (?P<path>\S+):(?P<start>\d+)-(?P<end>\d+) sha256=(?P<sha>[0-9a-f]{64})\s*$",
                  re.M)


def sha(path: str, start: int, end: int, repo: Path = REPO) -> str:
    lines = (repo / path).read_bytes().decode("utf-8").split("\n")
    cited = [line.removesuffix("\r") for line in lines[start - 1:end]]
    return hashlib.sha256("\n".join(cited).encode("utf-8")).hexdigest()


def header(path: str, start: int, end: int, repo: Path = REPO) -> str:
    """The docstring line that cites lines start..end of path."""
    return f"doc: {path}:{start}-{end} sha256={sha(path, start, end, repo)}"


def cited(model: Path) -> list[tuple[str, int, int, str]]:
    """(path, start, end, sha256) for every range a model's docstring cites."""
    docstring = ast.get_docstring(ast.parse(model.read_bytes())) or ""
    return [(m["path"], int(m["start"]), int(m["end"]), m["sha"]) for m in LINE.finditer(docstring)]


def stale(model: Path, repo: Path = REPO) -> list[str]:
    """One line per cited range whose text no longer hashes to its pin."""
    return [f"{model.name} cites {path}:{start}-{end}, which changed; re-read it and update the "
            f"header to `{header(path, start, end, repo)}`"
            for path, start, end, pinned in cited(model) if sha(path, start, end, repo) != pinned]
