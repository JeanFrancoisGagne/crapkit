"""Canonical inventory rows and exports. Pure.

Scored rows carry no timestamps and sort totally, so identical inputs produce
byte-identical exports; run metadata lives on the run row in the store, never here.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import NamedTuple

from .merge import FunctionRecord


class InventoryRow(NamedTuple):
    scope: str
    path: str
    long_name: str
    start: int
    end: int
    ccn_std: int
    ccn_mod: int
    ccn: int
    nloc: int
    params: int
    nesting: int
    cognitive: int = 0  # Sonar-spec cognitive complexity; reporting only, never gated


def build_inventory_rows(by_scope: dict[str, list[FunctionRecord]]) -> list[InventoryRow]:
    rows = [
        InventoryRow(scope, r.path, r.long_name, r.start, r.end,
                     r.ccn_std, r.ccn_mod, r.ccn, r.nloc, r.params, r.nesting,
                     r.cognitive)
        for scope, records in by_scope.items()
        for r in records
    ]
    rows.sort(key=lambda r: (r.scope, r.path, r.start, r.end, r.long_name))
    return rows


def tsv_lines(rows: Iterable[InventoryRow]) -> Iterator[str]:
    """The export document, one newline-terminated line at a time.

    A generator, not a joined string: at 140k functions the list of lines plus
    the joined document cost 44 MiB of transient copies of rows that already
    exist. The caller writes these straight to a file opened with
    newline="\\n", which is where the byte-identical guarantee is kept.
    """
    yield "\t".join(InventoryRow._fields) + "\n"
    for r in rows:
        yield "\t".join(str(v) for v in r) + "\n"
