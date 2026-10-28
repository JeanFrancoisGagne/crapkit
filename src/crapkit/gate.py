"""The gate: which changed functions a gate judges, and what it finds. Pure.

Every gate in crapkit asks one question of a change, does it hold? verify is
the first adapter of this module; each adapter hands it what it knows of the
change and maps the findings to its own exit codes and lines. The rules live
here once:

- Touch. A function is judged when a span of the change overlaps its lines. A
  file git diff cannot scope (an untracked file, say) comes as `WHOLE`, and
  every function in it is judged. Untouched debt is the ratchet's business.
- The ceiling. A judged function is over its scope's ceiling when the low end
  of what its caller knows of its CRAP is (`CrapBound`), decided for the exact
  value as `score.over_ceiling` decides it. verify knows the exact CRAP; a
  staged blob has no coverage and knows only a range.
- The pardon. A function over its ceiling that a ratchet mark covers is
  pardoned when the high end sits at or under the mark, a marked rise when the
  low end sits above it, and unproven when the two ends straddle it, each end
  compared at the 4 places a mark holds. The marks are fetched once, and only
  when a function over its ceiling needs one.
- The rise. An unproven function whose ccn rose above its ccn at the diff
  base, or that the base holds no function under its key, is an unproven rise:
  the edit may have broken the mark, and only coverage can say. The base is
  asked once, and only when an unproven function exists.
- Unread. A changed file no reader could read is refused: its zero records are
  not zero functions over the ceiling. One the change never touched is not.
- An unreadable name. A file a scope takes whose name git gives in bytes that
  are not UTF-8 is refused before anything is judged.

Each function finding carries its ratchet key name, counted over the whole
file's functions: the breaching subset alone cannot say which twin a function
is.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from .keys import key_names, key_of

# The spans of a file the change takes whole: one span over every line.
WHOLE: tuple[tuple[int, int], ...] = ((0, sys.maxsize),)


class CrapBound(NamedTuple):
    """What a caller knows of one function's CRAP: at least `low`, at most
    `high`. low == high when the caller knows the exact value."""
    low: float
    high: float


class Function(NamedTuple):
    """One function of a changed file. `record` is the caller's own row for it,
    handed back on its finding; `occurrence` orders functions that start on
    one line (keys.position)."""
    long_name: str
    start: int
    end: int
    bound: CrapBound
    scope: str = ""
    occurrence: int = 0
    record: Any = None


class Unread(NamedTuple):
    """A file no reader could read, and why. A changed one is the unread
    finding; `dirty` says the file holds uncommitted edits."""
    path: str
    reason: str
    dirty: bool = False


class UnreadableName(NamedTuple):
    """A file a scope takes whose name git gives in bytes that are not UTF-8.
    `path` holds those bytes as surrogates, the way git's listings hand it on;
    every printer shows them as `\\xNN`."""
    path: str
    scope: str
    dirty: bool = False


class ChangedFile(NamedTuple):
    """One path of the change: its changed spans (`WHOLE` for a file git diff
    cannot scope, () for one the change never touched) and what the gate reads
    in it: its functions, `Unread` or `UnreadableName`."""
    path: str
    spans: Sequence[tuple[int, int]]
    content: tuple[Function, ...] | Unread | UnreadableName


class Breach(NamedTuple):
    """A judged function over its ceiling: its file, its ratchet key name, the
    function, its ceiling and the mark that covers it (None for none). An
    unproven breach the base was asked about carries `base_ccn`, its ccn at the
    diff base, None where the base holds no function under its key."""
    path: str
    key_name: str
    function: Function
    ceiling: int
    mark: float | None = None
    base_ccn: int | None = None


class GateResult(NamedTuple):
    """The findings, each kind in the order the change listed its files and
    functions, and how many functions the change touched (`judged`)."""
    unreadable_name: tuple[UnreadableName, ...] = ()
    over_ceiling: tuple[Breach, ...] = ()
    unread: tuple[Unread, ...] = ()
    pardoned: tuple[Breach, ...] = ()
    marked_rise: tuple[Breach, ...] = ()
    unproven: tuple[Breach, ...] = ()
    judged: int = 0
    unproven_rise: tuple[Breach, ...] = ()


# The base fetch `judge` takes: the (path, key name) of each unproven function,
# answered with each one's ccn at the diff base, or None where the base holds
# no function under that key.
BaseCcn = Callable[[tuple[tuple[str, str], ...]], Mapping[tuple[str, str], int | None]]


def touches(function, spans: Sequence[tuple[int, int]]) -> bool:
    """Does a span overlap the function's lines, its first and last included?
    `function` is anything with `start` and `end`."""
    return any(not (hi < function.start or lo > function.end) for lo, hi in spans)


def judge(changes: Iterable[ChangedFile], ceilings: Callable[[str], int],
          marks: Callable[[], Any], base_ccn: BaseCcn | None = None) -> GateResult:
    """The findings on a change. `ceilings` gives a scope's ceiling; `marks`
    returns the keys.MarkIndex the pardon reads, called at most once and only
    when a judged function is over its ceiling. `base_ccn` answers the unproven
    functions' ccn at the diff base, called at most once and only when one is
    unproven; with none, every unproven function stays `unproven`."""
    changes = tuple(changes)
    refused = tuple(change.content for change in changes if isinstance(change.content, UnreadableName))
    if refused:
        return GateResult(unreadable_name=refused)
    files = [change for change in changes if not isinstance(change.content, Unread)]
    breaches, judged = _breaches(files, ceilings)
    found = _rises(_pardons(breaches, marks), base_ccn)
    return GateResult(unread=_unread(changes), judged=judged, **found)


def _unread(changes: tuple[ChangedFile, ...]) -> tuple[Unread, ...]:
    """The files no reader could read that the change touched."""
    return tuple(change.content for change in changes if isinstance(change.content, Unread) and change.spans)


def _breaches(files: list[ChangedFile], ceilings: Callable[[str], int]) -> tuple[list[Breach], int]:
    """Every touched function over its ceiling, and how many were touched."""
    from .score import over_ceiling

    found, judged = [], 0
    for change in files:
        touched = [function for function in change.content if touches(function, change.spans)]
        judged += len(touched)
        over = [function for function in touched
                if over_ceiling(function.bound.low, ceilings(function.scope))]
        found += _keyed(change, over, ceilings)
    return found, judged


class _Located(NamedTuple):
    """A function as keys reads a row: its file, name and place."""
    path: str
    long_name: str
    start: int
    occurrence: int
    scope: str


def _located(path: str, function: Function) -> _Located:
    return _Located(path, function.long_name, function.start, function.occurrence, function.scope)


def _keyed(change: ChangedFile, over: list[Function], ceilings: Callable[[str], int]) -> list[Breach]:
    """The breaches of one file, each keyed over all of the file's functions."""
    if not over:
        return []
    names = key_names([_located(change.path, function) for function in change.content])
    return [Breach(change.path, key_of(names, _located(change.path, function))[1], function,
                   ceilings(function.scope)) for function in over]


_PARDON_KINDS = ("over_ceiling", "pardoned", "marked_rise", "unproven")


def _pardons(breaches: list[Breach], marks: Callable[[], Any]) -> dict[str, tuple[Breach, ...]]:
    """The breaches sorted by what their marks say, the marks fetched only
    when there is a breach to pardon."""
    if not breaches:
        return {}
    index = marks()
    sorted_by: dict[str, list[Breach]] = {kind: [] for kind in _PARDON_KINDS}
    for breach in breaches:
        marked = breach._replace(mark=index.mark((breach.path, breach.key_name)))
        sorted_by[_pardon(marked.function.bound, marked.mark)].append(marked)
    return {kind: tuple(found) for kind, found in sorted_by.items()}


def _rises(found: dict[str, tuple[Breach, ...]], base_ccn: BaseCcn | None) -> dict[str, tuple[Breach, ...]]:
    """The unproven breaches split by whether their ccn rose since the diff
    base, the base asked once, with every unproven key, and only when one is
    unproven."""
    unproven = found.get("unproven", ())
    if base_ccn is None or not unproven:
        return found
    return {**found, **_split(_asked(unproven, base_ccn))}


def _asked(unproven: tuple[Breach, ...], base_ccn: BaseCcn) -> list[Breach]:
    """Each unproven breach with its ccn at the base, one call for every key."""
    answers = base_ccn(tuple(dict.fromkeys((breach.path, breach.key_name) for breach in unproven)))
    return [breach._replace(base_ccn=answers[(breach.path, breach.key_name)]) for breach in unproven]


def _split(asked: list[Breach]) -> dict[str, tuple[Breach, ...]]:
    """The asked breaches as the unproven ones and the unproven rises."""
    kinds: dict[str, list[Breach]] = {"unproven": [], "unproven_rise": []}
    for breach in asked:
        kinds["unproven_rise" if _rose(breach) else "unproven"].append(breach)
    return {kind: tuple(found) for kind, found in kinds.items()}


def _rose(breach: Breach) -> bool:
    """An unproven bound runs from the CRAP with every branch run, which is the
    ccn, so its low end is the staged ccn the base's is compared with."""
    return breach.base_ccn is None or breach.function.bound.low > breach.base_ccn


def _pardon(bound: CrapBound, mark: float | None) -> str:
    """Which kind of finding a breach is under its mark, each end of the bound
    compared at the places the mark is stored at."""
    from .score import CRAP_PLACES

    if mark is None:
        return "over_ceiling"
    if round(bound.high, CRAP_PLACES) <= mark:
        return "pardoned"
    if round(bound.low, CRAP_PLACES) > mark:
        return "marked_rise"
    return "unproven"
