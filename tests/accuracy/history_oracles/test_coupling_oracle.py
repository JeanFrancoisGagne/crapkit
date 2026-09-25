"""Change coupling against a pair count over git's numstat walk and a hand table.

The expected pairs come from oracles/pair_count.py over the commits
oracles/git_walk.py reads, or from hand_pairs.tsv, worked from the spec in
repos/history_specs.py (COUPLED). crapkit is read through `coupling --json`.
This file imports no crapkit module.
"""
from __future__ import annotations

import csv
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path

import pytest

from accuracy.history_oracles import coupling_reads
from accuracy.history_oracles.repos import history_specs as specs

HERE = Path(__file__).resolve().parent
NOW = specs.COUPLED_NOW
DEFAULTS = {"min_support": 5, "min_confidence": Fraction(1, 2)}
pytestmark = pytest.mark.process


def _hand(rank: str) -> list[tuple]:
    with (HERE / "hand_pairs.tsv").open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
                if row[rank] != "-"]
    rows.sort(key=lambda row: int(row[rank]))
    return [((row["file_a"], row["file_b"]), int(row["support"]), Decimal(row["confidence"]))
            for row in rows]


def test_pairs_match_the_numstat_walk(make_repo):
    built = make_repo(specs.COUPLED)

    every = coupling_reads.said(built.root, NOW, *coupling_reads.ALL)
    default = coupling_reads.said(built.root, NOW)

    assert every == coupling_reads.expected(built.root, NOW)
    assert default == coupling_reads.expected(built.root, NOW, **DEFAULTS)


def test_hand_pairs_match(make_repo):
    built = make_repo(specs.COUPLED)

    assert coupling_reads.said(built.root, NOW, *coupling_reads.ALL) == _hand("all_rank")
    assert coupling_reads.said(built.root, NOW) == _hand("default_rank")


def _paths(pairs: list[tuple]) -> set[str]:
    return {path for files, _, _ in pairs for path in files}


def test_renamed_path_never_ranks(make_repo):
    """src/x.py co-changed with src/y.py five times, then git moved it to
    src/z.py: a pair naming x names a file nobody can open (R77)."""
    built = make_repo(specs.COUPLED)

    for flags in ((), coupling_reads.ALL):
        assert "src/x.py" not in _paths(coupling_reads.said(built.root, NOW, *flags))


def test_non_ascii_paths_with_quotepath(make_repo):
    """git C-quotes a non-ASCII path under core.quotePath; a pair names the file
    the way `git ls-files` does (R76)."""
    built = make_repo(specs.COUPLED)

    said = coupling_reads.said(built.root, NOW)

    assert specs.UMLAUT in coupling_reads.tracked(built.root)
    assert ("src/b.py", specs.UMLAUT) in [files for files, _, _ in said]


def _corrupt(cache: Path) -> None:
    """The stored ranking with a pair whose first path is a number, key kept."""
    doc = json.loads(cache.read_text(encoding="utf-8"))
    doc["pairs"][0][0] = 5
    cache.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")


def test_non_string_paths_pair(make_repo):
    """A stored pair whose path is not a string reads as a cold cache, never as a
    file called '5' (R101)."""
    built = make_repo(specs.COUPLED)
    coupling_reads.said(built.root, NOW)
    _corrupt(built.root / ".crapkit" / "coupling-cache-v1.json")

    said = coupling_reads.said(built.root, NOW)

    assert said == coupling_reads.expected(built.root, NOW, **DEFAULTS)
