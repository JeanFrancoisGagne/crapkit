"""The ratchet key: which function a mark, a gate and a verdict are about. Pure.

A key is `(path, key name)`. The key name is the function's long_name when the
file gives that name to one function, and `long_name#N` for the Nth function
sharing it, counted in start order.

Before the ordinal, a file's twins shared one key. Only one of them could be
marked and only one could be gated, so the others grew past every ceiling with
no gate firing and no mark recording the debt. Python makes that ordinary: a
method's long_name carries no class, so several dataclasses in one module each
defining `__post_init__` collide by construction. C makes it ordinary too, since
both arms of an `#ifdef` fork are textually present.

Two properties earn the ordinal over the start line the key could have used:

- It survives line drift. An edit above a function moves every span below it and
  would re-key marks that nothing touched.
- It renumbers honestly. Rename one twin and the others shift by one, which is
  what happened: they really are different functions now.

The first twin keeps the BARE name. That is what makes the change free to adopt:
every mark recorded under the old two-field key already reads as twin #1, so no
committed `crapkit-ratchet.tsv` needs rewriting.

Anonymous functions take the same canonical key rule within each raw
signature. Their printed `(anonymous)#N` handles count all anonymous spans
in the file, starting at #1. Claims save the canonical key separately because
the printed ordinal does not identify an ordinal within one signature.
"""
from __future__ import annotations

from collections import Counter

from .errors import ToolError

ORDINAL = "#"
_EXPRESSION_SUFFIXES = frozenset(("js", "cjs", "mjs", "ts", "tsx", "jsx"))


def expression_group(path: str, name: str) -> bool:
    """The anonymous groups whose membership changed with expression reader 10."""
    return path.rpartition(".")[2].lower() in _EXPRESSION_SUFFIXES and name.startswith("(anonymous)")


def expression_reader_current(version) -> bool:
    """A saved reader version proves that expression callbacks were enumerated."""
    try:
        return int(version) >= 10
    except (ValueError, TypeError):
        return False


def key_name(long_name: str, ordinal: int) -> str:
    """The Nth same-named function's key name. The first of its name keeps the bare one."""
    return long_name if ordinal <= 1 else f"{long_name}{ORDINAL}{ordinal}"


def split_ordinal(name: str) -> tuple[str, int]:
    """A NAME back into (name, twin ordinal); no `#N` selects the first of its name.

    The digits have to be the whole tail and name a real position: `op#( a )` is
    a name a C++ reader can produce and `f( a )#0` selects nothing, so both stay
    whole. This is the inverse of `key_name` and the two are tested together.
    """
    head, sep, tail = name.rpartition(ORDINAL)
    if not sep or not tail.isdigit() or int(tail) < 1:
        return name, 1
    return head, int(tail)


def position(row) -> tuple[int, int]:
    """A parsed function's line and its creation order within that line."""
    return row.start, getattr(row, "occurrence", 0)


def lookup(row) -> tuple[str, str, int, int]:
    """The stored location shared by keys, marks and presentation handles."""
    return row.path, row.long_name, *position(row)


def _position_counts(rows) -> dict:
    groups: dict = {}
    for row in rows:
        key = row.path, row.long_name, row.start
        copies = groups.setdefault(key, Counter())
        copies[(getattr(row, "scope", ""), position(row)[1])] += 1
    return groups


def _ambiguous(counts, legacy_only: bool) -> bool:
    occurrences = {occ for _, occ in counts}
    unknown = _unknown_count(counts)
    if legacy_only:
        return unknown > 1 or (unknown > 0 and len(occurrences) > 1)
    return unknown > 1 or len(occurrences) > 1


def _unknown_count(counts) -> int:
    return max((n for (_, occ), n in counts.items() if occ == 0), default=0)


def ambiguous_groups(rows, *, legacy_only: bool = False) -> set[tuple[str, str]]:
    """Raw-name groups with same-line twins; scope copies count once.

    Legacy-only asks whether those twins lack a recorded within-line position.
    Rows must belong to one run; historical callers group runs separately.
    """
    return {(path, name) for (path, name, _), counts in _position_counts(rows).items()
            if _ambiguous(counts, legacy_only)}


def require_unambiguous(rows) -> None:
    refuse_ambiguous(ambiguous_groups(rows, legacy_only=True))


def refuse_ambiguous(groups) -> None:
    """The same refusal for row-backed and SQL-backed identity checks."""
    if groups:
        names = "; ".join(f"{path}: {name}" for path, name in sorted(groups))
        raise ToolError(f"ambiguous legacy function identity in {names}; "
                        "refresh analysis before selecting or comparing these functions")


def key_names(rows) -> dict[tuple[str, str, int, int], str]:
    """Canonical ordinals ordered by source position, with scope copies shared."""
    rows = list(rows)
    require_unambiguous(rows)
    starts: dict[tuple[str, str], set[tuple[int, int]]] = {}
    for row in rows:
        starts.setdefault((row.path, row.long_name), set()).add(position(row))
    return {(path, name, *place): key_name(name, n)
            for (path, name), places in starts.items()
            for n, place in enumerate(sorted(places), 1)}


def key_of(keys: dict[tuple[str, str, int, int], str], row) -> tuple[str, str]:
    """One row's whole key, out of the map `key_names` built."""
    return row.path, keys[lookup(row)]


def stated_key(item) -> tuple[str, str]:
    """The key an item already carries, falling back to its bare long_name.

    A violation nobody keyed is not a special case: a lone function's key IS its
    long_name, and that is what every mark written before the ordinal holds.
    """
    return item.path, item.key_name or item.long_name


def claim_key(claim: dict) -> tuple[str, str] | None:
    """A precise claim's key; older bare-name claims remain ambiguous."""
    if claim.get("key_version", 1) == 0:
        return None
    name = claim.get("key_name")
    if name is not None:
        return claim["path"], name
    handle = claim.get("handle") or ""
    bare, ordinal = split_ordinal(handle)
    # Old anonymous handles count the whole file; keys count one signature.
    # Without the saved key, that ordinal cannot identify a signature's twin.
    if bare == handle or bare == "(anonymous)":
        return None
    return claim["path"], key_name(claim["long_name"], ordinal)


def claim_holds(claim: dict, key: tuple[str, str]) -> bool:
    """Legacy ownership covers all twins until it can be resolved or released."""
    precise = claim_key(claim)
    if precise is not None:
        return precise == key
    return (claim["path"], claim["long_name"]) == (key[0], split_ordinal(key[1])[0])
