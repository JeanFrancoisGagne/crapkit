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

`(anonymous)` twins take the same rule. Their keys line up with the
`(anonymous)#N` handles `packet` publishes from #2 up; handle #1 is the bare
`(anonymous)`, the same function under its key.
"""
from __future__ import annotations

ORDINAL = "#"


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


def key_names(rows) -> dict[tuple[str, str, int], str]:
    """Every row's key name, looked up by (path, long_name, the line it opens on).

    Start is what separates one twin from the next: no two functions in a file
    open on the same line, which is the fact `packet.handles` already keys on. It
    is not the lookup on its own, because a synthetic row set can hand two names
    the same start and a silently overwritten entry would key a mark to the
    wrong function.

    One function scored twice in a run — two scopes claiming one path — is one
    span, so it takes one ordinal rather than becoming a phantom twin.

    Rows may arrive in any order; ordinals are read off sorted start lines,
    never off arrival.
    """
    starts: dict[tuple[str, str], set[int]] = {}
    for row in rows:
        starts.setdefault((row.path, row.long_name), set()).add(row.start)
    return {(path, long_name, start): key_name(long_name, n)
            for (path, long_name), lines in starts.items()
            for n, start in enumerate(sorted(lines), 1)}


def key_of(keys: dict[tuple[str, str, int], str], row) -> tuple[str, str]:
    """One row's whole key, out of the map `key_names` built."""
    return row.path, keys[(row.path, row.long_name, row.start)]


def stated_key(item) -> tuple[str, str]:
    """The key an item already carries, falling back to its bare long_name.

    A violation nobody keyed is not a special case: a lone function's key IS its
    long_name, and that is what every mark written before the ordinal holds.
    """
    return item.path, item.key_name or item.long_name
