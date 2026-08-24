"""Merge the standard and modified lizard passes into one record per function.

Pure. Join key is (path, start, end, long_name) — spans, never bare names,
because anonymous and nested functions collide on names within a file.
"""
from __future__ import annotations

from typing import NamedTuple


class RawFn(NamedTuple):
    path: str
    long_name: str
    start: int
    end: int
    ccn: int
    nloc: int
    params: int
    nesting: int
    cognitive: int = 0


class FunctionRecord(NamedTuple):
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
    cognitive: int = 0  # Sonar-spec, from the standard pass; reporting only


def _key(fn: RawFn) -> tuple[str, int, int, str]:
    return (fn.path, fn.start, fn.end, fn.long_name)


def _group_by_key(fns: list[RawFn]) -> dict[tuple, list[RawFn]]:
    # Ordered lists, not one entry per key: two anonymous callbacks on one line
    # share a key, and a plain dict would keep only the last twin.
    grouped: dict[tuple, list[RawFn]] = {}
    for f in fns:
        grouped.setdefault(_key(f), []).append(f)
    return grouped


def _key_disagreements(std_by_key: dict[tuple, list[RawFn]],
                       mod_by_key: dict[tuple, list[RawFn]]) -> list[tuple]:
    # Keys one pass has and the other lacks; failing that, keys both have at
    # differing multiplicity. Empty means the two passes line up exactly.
    return sorted(set(std_by_key) ^ set(mod_by_key)) or sorted(
        k for k, v in std_by_key.items() if len(v) != len(mod_by_key.get(k, ()))
    )


def merge_passes(standard: list[RawFn], modified: list[RawFn]) -> list[FunctionRecord]:
    mod_by_key = _group_by_key(modified)
    diff = _key_disagreements(_group_by_key(standard), mod_by_key)
    if diff:
        raise ValueError(f"lizard pass mismatch: {len(diff)} function key(s) differ between passes: {diff[:5]}")
    # Both passes emit twins in the same parse order, so pairing within a key
    # is by position.
    taken: dict[tuple, int] = {}
    records = []
    for fn in standard:
        key = _key(fn)
        mod = mod_by_key[key][taken.get(key, 0)]
        taken[key] = taken.get(key, 0) + 1
        records.append(FunctionRecord(
            path=fn.path, long_name=fn.long_name, start=fn.start, end=fn.end,
            ccn_std=fn.ccn, ccn_mod=mod.ccn, ccn=min(fn.ccn, mod.ccn),
            nloc=fn.nloc, params=fn.params, nesting=fn.nesting, cognitive=fn.cognitive,
        ))
    return records
