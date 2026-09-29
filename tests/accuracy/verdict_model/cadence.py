"""Which cases of a parametrized check run on push.

The accuracy plan gives this packet 120 serial seconds on ubuntu in the push
tier. A parametrized check keeps the cases `push` names on push and runs the
others in the nightly tier. The nightly tier also runs every push case, so
each night every case runs. A test that is nightly as a whole carries
@pytest.mark.nightly instead.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence

import pytest

NIGHTLY = (pytest.mark.nightly,)


def _args(value, unpack: bool) -> tuple:
    return tuple(value) if unpack else (value,)


def _marks(value, key, push: Collection) -> tuple:
    return () if key in push or value in push else NIGHTLY


def _known(push: list, keys: list, values: list) -> list:
    """`push`, once every name in it is a case's id or value."""
    unknown = [name for name in push if name not in keys and name not in values]
    assert not unknown, f"push names no case: {unknown}"
    return push


def tiered(values: Iterable, push: Collection, ids: Sequence | None = None,
           unpack: bool = False) -> list:
    """`values` as pytest.param entries. A case whose id, or whose value, is in
    `push` stays on push; every other case runs nightly. `unpack` spreads a
    tuple value over several argument names."""
    values = list(values)
    keys = list(ids) if ids is not None else values
    push = _known(list(push), keys, values)
    return [pytest.param(*_args(value, unpack), marks=_marks(value, key, push),
                         id=None if ids is None else key)
            for value, key in zip(values, keys)]
