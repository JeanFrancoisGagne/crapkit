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
  when a function over its ceiling needs one. `keys.resolve` says which mark
  covers it: its own key's, or the mark carried from the one key of its bare
  name that left the file. A carried pardon is the `carried` finding; a carry
  keys refuses pardons nothing and names the ambiguity on the breach.
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

from .keys import Carry, Refused, key_names, key_of, resolve

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
    diff base, None where the base holds no function under its key.
    `carried_from` names the key a carried mark was recorded under, and
    `mark_ambiguity` holds keys' refusal when no mark could carry to it."""
    path: str
    key_name: str
    function: Function
    ceiling: int
    mark: float | None = None
    base_ccn: int | None = None
    carried_from: str | None = None
    mark_ambiguity: str | None = None


class GateResult(NamedTuple):
    """The findings, each kind in the order the change listed its files and
    functions, and how many functions the change touched (`judged`).
    `carried` holds the breaches a carried mark pardons, `pardoned` those
    their own key's mark pardons."""
    unreadable_name: tuple[UnreadableName, ...] = ()
    over_ceiling: tuple[Breach, ...] = ()
    unread: tuple[Unread, ...] = ()
    pardoned: tuple[Breach, ...] = ()
    marked_rise: tuple[Breach, ...] = ()
    unproven: tuple[Breach, ...] = ()
    judged: int = 0
    unproven_rise: tuple[Breach, ...] = ()
    carried: tuple[Breach, ...] = ()


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
    when a judged function is over its ceiling, and each breach's mark is the
    one `keys.resolve` names over its file's keys. `base_ccn` answers the
    unproven functions' ccn at the diff base, called at most once and only when
    one is unproven; with none, every unproven function stays `unproven`."""
    changes = tuple(changes)
    refused = tuple(change.content for change in changes if isinstance(change.content, UnreadableName))
    if refused:
        return GateResult(unreadable_name=refused)
    files = [change for change in changes if not isinstance(change.content, Unread)]
    breaches, judged, held = _breaches(files, ceilings)
    found = _rises(_pardons(breaches, held, marks), base_ccn)
    return GateResult(unread=_unread(changes), judged=judged, **found)


def _unread(changes: tuple[ChangedFile, ...]) -> tuple[Unread, ...]:
    """The files no reader could read that the change touched."""
    return tuple(change.content for change in changes if isinstance(change.content, Unread) and change.spans)


Key = tuple[str, str]


def _breaches(files: list[ChangedFile],
              ceilings: Callable[[str], int]) -> tuple[list[Breach], int, dict[str, frozenset[Key]]]:
    """Every touched function over its ceiling, how many were touched, and the
    keys of each file that holds a breach."""
    from .score import over_ceiling

    found, judged, held = [], 0, {}
    for change in files:
        touched = [function for function in change.content if touches(function, change.spans)]
        judged += len(touched)
        over = [function for function in touched
                if over_ceiling(function.bound.low, ceilings(function.scope))]
        keyed, held[change.path] = _keyed(change, over, ceilings)
        found += keyed
    return found, judged, held


class _Located(NamedTuple):
    """A function as keys reads a row: its file, name and place."""
    path: str
    long_name: str
    start: int
    occurrence: int
    scope: str


def _located(path: str, function: Function) -> _Located:
    return _Located(path, function.long_name, function.start, function.occurrence, function.scope)


def _keyed(change: ChangedFile, over: list[Function],
           ceilings: Callable[[str], int]) -> tuple[list[Breach], frozenset[Key]]:
    """The breaches of one file, each keyed over all of the file's functions,
    and those keys; none are counted for a file with no breach."""
    if not over:
        return [], frozenset()
    names = key_names([_located(change.path, function) for function in change.content])
    breaches = [Breach(change.path, key_of(names, _located(change.path, function))[1], function,
                       ceilings(function.scope)) for function in over]
    return breaches, frozenset((change.path, name) for name in names.values())


_PARDON_KINDS = ("over_ceiling", "pardoned", "marked_rise", "unproven", "carried")


def _pardons(breaches: list[Breach], held: Mapping[str, frozenset[Key]],
             marks: Callable[[], Any]) -> dict[str, tuple[Breach, ...]]:
    """The breaches sorted by what their marks say, the marks fetched only
    when there is a breach to pardon."""
    if not breaches:
        return {}
    index = marks()
    sorted_by: dict[str, list[Breach]] = {kind: [] for kind in _PARDON_KINDS}
    for breach in breaches:
        marked = _marked(breach, held[breach.path], index)
        sorted_by[_kind(marked)].append(marked)
    return {kind: tuple(found) for kind, found in sorted_by.items()}


def _marked(breach: Breach, file_keys: frozenset[Key], index) -> Breach:
    """The breach with the mark keys.resolve names for it: its own key's, the
    one carried from the key that left its file, or none and the refusal."""
    own = (breach.path, breach.key_name)
    answer = resolve(own, file_keys, index.keys())
    if isinstance(answer, Refused):
        return breach._replace(mark_ambiguity=answer.text)
    if isinstance(answer, Carry):
        return breach._replace(mark=_value(index, answer.old_key), carried_from=answer.old_key[1])
    return breach._replace(mark=_value(index, own))


def _value(index, key: Key) -> float | None:
    """The mark recorded under `key`, None when nothing marks it."""
    entry = index.entry(key)
    return None if entry is None else entry.crap


def _kind(breach: Breach) -> str:
    """A carried mark's pardon is the `carried` finding; every other kind is
    the same whichever key holds the mark."""
    kind = _pardon(breach.function.bound, breach.mark)
    return "carried" if kind == "pardoned" and breach.carried_from is not None else kind


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
