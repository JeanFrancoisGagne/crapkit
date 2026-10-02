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

The naming rules live here too, beside the keys they reach: the bare name, the
handles, and `select`, the one resolver every command that takes a NAME runs.
`brief` resolved in the queue and `explain` in the store, and a bare twin name
picked the worst twin in one and the first in the other.

The mark index lives here as well. `MarkIndex` is built once from a marks
file's entries and answers which mark a key carries; `rows_by_key` answers which
scored row stands for a key. Every mark lookup in verify and ratchet goes
through them, `ratchet.mark_for` included, so a key a marks file lists twice has
one winner there, the first: the gate, the ratchet check, the update's counts
and the tighten guard all read it. The update itself rewrites every line, so
both lines stay and the first still answers.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import TYPE_CHECKING

from .errors import CrapkitError, ToolError

if TYPE_CHECKING:  # score imports this module
    from .score import ScoredRow

ORDINAL = "#"
# What lizard calls a function it could not name. Every anonymous function in a
# file prints the same string, which is why the handles below exist.
ANONYMOUS = "(anonymous)"
_EXPRESSION_SUFFIXES = frozenset(("js", "cjs", "mjs", "ts", "tsx", "jsx"))


def expression_group(path: str, name: str) -> bool:
    """The anonymous groups whose membership changed with expression reader 10."""
    return path.rpartition(".")[2].lower() in _EXPRESSION_SUFFIXES and name.startswith(ANONYMOUS)


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


# Right wherever the run read was chosen as the newest one: a coverage run
# replaces it. A caller whose run is pinned for another reason says why instead.
REFRESH_ADVICE = "refresh analysis before selecting or comparing these functions"


def require_unambiguous(rows, *, run_id: int | None = None, advice: str = REFRESH_ADVICE) -> None:
    """Refuse legacy same-line twins in rows; `run_id` names the stored run they came from."""
    held = () if run_id is None else (run_id,)
    refuse_ambiguous(dict.fromkeys(ambiguous_groups(rows, legacy_only=True), held), advice=advice)


def refuse_ambiguous(groups, *, advice: str = REFRESH_ADVICE) -> None:
    """The same refusal for row-backed and SQL-backed identity checks.

    `groups` holds each ambiguous (path, raw name). As a mapping it also names
    the stored runs that hold each one, so the sentence says which run it read.
    """
    if groups:
        runs = groups if isinstance(groups, dict) else {}
        names = "; ".join(_held_in(group, runs.get(group, ())) for group in sorted(groups))
        raise ToolError(f"ambiguous legacy function identity in {names}; {advice}")


def _held_in(group: tuple[str, str], runs) -> str:
    path, name = group
    if not runs:
        return f"{path}: {name}"
    label = "run" if len(runs) == 1 else "runs"
    return f"{path}: {name} in {label} {', '.join(str(run) for run in sorted(runs))}"


def key_names(rows, *, run_id: int | None = None) -> dict[tuple[str, str, int, int], str]:
    """Canonical ordinals ordered by source position, with scope copies shared.
    RUN_ID names the run the rows came from in a legacy-identity refusal."""
    rows = list(rows)
    require_unambiguous(rows, run_id=run_id)
    starts: dict[tuple[str, str], set[tuple[int, int]]] = {}
    for row in rows:
        starts.setdefault((row.path, row.long_name), set()).add(position(row))
    return {(path, name, *place): key_name(name, n)
            for (path, name), places in starts.items()
            for n, place in enumerate(sorted(places), 1)}


def key_of(keys: dict[tuple[str, str, int, int], str], row) -> tuple[str, str]:
    """One row's whole key, out of the map `key_names` built."""
    return row.path, keys[lookup(row)]


def rows_by_key(fresh: list[ScoredRow]) -> dict[tuple[str, str], ScoredRow]:
    """Scored rows by their ratchet key: `key_names` gives each twin its own.

    Twins used to share (path, long_name) and the worst of them represented the
    key, which let a repaid twin's high mark pardon a sibling's growth. The
    ordinal ends that. The worst-wins rule stays for the one collision left —
    two scopes claiming one path score the same span twice — so a regression
    still cannot hide behind a clean sibling.
    """
    names = key_names(fresh)
    worst: dict[tuple[str, str], ScoredRow] = {}
    for r in fresh:
        key = key_of(names, r)
        if key not in worst or r.crap > worst[key].crap:
            worst[key] = r
    return worst


def mark_key(entry) -> tuple[str, str]:
    """The key a mark was recorded under: its path and its key name."""
    return entry.path, entry.long_name


class MarkIndex:
    """A marks file's entries by key, built once: which mark judges a function.

    A file can list one key twice, by a hand edit or a botched merge. The first
    mark wins, as `ratchet.mark_for` answers, and every lookup in verify and
    ratchet reads the same one. Before the index, verify's gate, seed, merge and
    the update's counts each built their own dict and kept the last, while the
    ratchet check and the tighten guard walked every entry.
    """

    __slots__ = ("_by_key",)

    def __init__(self, entries) -> None:
        by_key: dict = {}
        for entry in entries:
            by_key.setdefault(mark_key(entry), entry)
        self._by_key = by_key

    def mark(self, key: tuple[str, str]) -> float | None:
        """The mark under `key`, or None when nothing marks it."""
        entry = self._by_key.get(key)
        return None if entry is None else entry.crap

    # A dict's spelling of `mark`, so a reader written against a dict of marks
    # by key reads the index unchanged.
    get = mark

    def entry(self, key: tuple[str, str]):
        """The entry under `key`, or None when nothing marks it."""
        return self._by_key.get(key)

    def keys(self):
        """Every marked key, once each."""
        return self._by_key.keys()

    def entries(self):
        """The entry that answers each key, once each, in the order the file lists them."""
        return self._by_key.values()

    def working_copy(self) -> dict:
        """The entries by key as a new dict, for a caller that adds or replaces marks."""
        return dict(self._by_key)

    def __contains__(self, key) -> bool:
        return key in self._by_key


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
    return _handle_key(claim)


def _handle_key(claim: dict) -> tuple[str, str] | None:
    """The key a claim saved before key names spells in its twin handle."""
    handle = claim.get("handle") or ""
    bare, ordinal = split_ordinal(handle)
    # Old anonymous handles count the whole file; keys count one signature.
    # Without the saved key, that ordinal cannot identify a signature's twin.
    if bare == handle or bare == ANONYMOUS:
        return None
    return claim["path"], key_name(claim["long_name"], ordinal)


def claim_holds(claim: dict, key: tuple[str, str]) -> bool:
    """Legacy ownership covers all twins until it can be resolved or released."""
    precise = claim_key(claim)
    if precise is not None:
        return precise == key
    return (claim["path"], claim["long_name"]) == (key[0], split_ordinal(key[1])[0])


# --- claims taken before analysis version 11 ------------------------------------

_CLAIM_NAMES = ("long_name", "key_name", "handle")


def respelled_nested(path: str, name: str) -> str:
    """NAME as analysis version 11 spells a nested Python def that an older
    reader named: `a.b.c( x )` for `a.a.b.c( x )`. Any other NAME comes back
    as it is.

    Before version 11 a def three or more deep repeated the names of the defs
    around it: three deep read `a.a.b.c`, four deep `a.a.b.a.a.b.c.d`. Only that
    shape is rewritten, so `a.b.c.d`, which no older reader wrote, keeps its
    name. A parameter list or a twin ordinal after the name stays as it is.
    """
    head = bare_name(name)
    chain = _def_chain(head.split(".")) if path.lower().endswith(".py") else None
    return name if chain is None else name.replace(head, ".".join(chain), 1)


def _def_chain(parts: list[str]) -> list[str] | None:
    """The defs an older reader's nested name runs through, outermost first,
    or None when PARTS is not that spelling.

    That reader spelled a def k deep as its parent's spelling minus the last
    name, then its parent's spelling, then its own name, so the spelling
    doubles with each level: `a`, `a.b`, `a.a.b.c`, `a.a.b.a.a.b.c.d`.
    """
    if len(parts) <= 2:
        return parts
    half = len(parts) // 2
    parent = parts[half - 1:-1]
    if parts[:half - 1] != parent[:-1]:
        return None
    chain = _def_chain(parent)
    return None if chain is None else [*chain, parts[-1]]


def respelled_claim(claim: dict) -> dict:
    """The claim with every name it saved spelled as analysis version 11
    spells a nested def: its long_name, its key name and its handle."""
    path = claim["path"]
    return {**claim, **{field: respelled_nested(path, claim[field])
                        for field in _CLAIM_NAMES if claim.get(field)}}


def claim_in_run(claim: dict, names_in) -> dict:
    """The claim, naming the function a run holds under the name it now has.

    A claim keeps the name it was handed out under. One taken before analysis
    version 11 on a def nested three or more deep names a function the new run
    does not hold, and matched as saved it held nothing: the queue handed the
    def to the next session. `names_in(path)` is every long_name the run holds
    in that file. The saved name wins while the run holds it, so a def nested
    in a def of its own name (`a.a.b.c` under version 11 too) keeps its claim.
    """
    moved = respelled_claim(claim)
    if moved["long_name"] == claim["long_name"]:
        return claim
    held = names_in(claim["path"])
    return claim if claim["long_name"] in held or moved["long_name"] not in held else moved


# --- the naming rules: what a NAME can say and which function it reaches -----

# A Zig identifier spelled as a string (crapkit.lizardgolike reads it as one token),
# or a Swift raw identifier between backticks (crapkit.lizardswift reads it as one).
_QUOTED_NAME = re.compile(r'@"(?:\\.|[^"\\])*"|`[^`]*`')


def bare_name(long_name: str) -> str:
    """The identifier a long_name opens with, before its parameter list.

    Two cuts, because lizard's readers spell a parameter list two ways. Python
    and shell close the name with `(` — `classify( score , limit = 1 )`,
    `classify()` — and Rust and Go print the parameters after a space with no
    parenthesis at all: `route cmd : & Cmd`, `Classify n int`. Cutting only at
    the `(` handed those back whole, so the handle a packet published was a
    signature no command would accept back.

    The leading token settles both. It moves no parenthesised language, because
    none of those puts a space before the `(`: `n::K::m( int a)` keeps its
    namespace and an Objective-C `doThing:( int )` keeps its selector colon.

    Empty for a function lizard could not name: both `(anonymous)` and
    `(anonymous) ( z )` open with the parenthesis, so an empty prefix IS the
    test for anonymity, with no second string to keep in step.

    A Zig name written as a string, `@"weird name" x : i32`, is one token whose
    text holds a space and can hold a `(`, so it is cut whole. So is a Swift raw
    identifier, "`keeps onboarding if offline` value : Int": its name runs to its
    own closing backtick.
    """
    quoted = _QUOTED_NAME.match(long_name)
    if quoted:
        return quoted.group()
    head = long_name.split("(")[0].strip()
    return head.split()[0] if head else ""


def named_by(long_name: str, name: str) -> bool:
    """Does NAME name this function outright: its whole long_name, or its bare one?

    next-item, worklist and brief all publish `function` as the long_name, so
    the string an agent has just read has to be a string it can pass back, to
    any command and to `claims release`.
    """
    return name in (long_name, bare_name(long_name))


def exact_names(names, name: str) -> list[str]:
    """The long names `name` names outright: the whole string, or the bare one."""
    return [n for n in names if named_by(n, name)]


def matching_names(names, name: str) -> list[str]:
    """The long names one NAME resolves to, in the order `names` arrived.

    Exact first, the fragment second. `brief` matched only exactly and `explain`
    only loosely, so `route` picked one function in one command and three —
    `route`, `route_chain`, `route_num` — in the other, off the same string in
    the same payload. Nesting names is the ordinary case, so the loose command
    was wrong far more often than the strict one was unhelpful.

    The fragment survives as the fallback because a name nobody owns is usually
    a typo, and listing everything holding it is what tells a session which name
    it meant. An empty NAME resolves to nothing rather than to everything.
    """
    if not name:
        return []
    return exact_names(names, name) or [n for n in names if name in n]


def anonymous_positions(rows, *, run_id: int | None = None) -> list[tuple]:
    """Anonymous locations in source order, with scope copies shared."""
    rows = list(rows)
    require_unambiguous(rows, run_id=run_id)
    return sorted({lookup(r) for r in rows if not bare_name(r.long_name)},
                  key=lambda place: (place[2], place[3], place[1]))


def handles(rows, *, run_id: int | None = None) -> dict[tuple, str]:
    """The handle for every row in one file, keyed by its full stored location.

    Named twins carry #N, including #1; overloads retain their full signature.
    Anonymous handles count all anonymous spans in file order. Duplicate scopes
    share a span and a handle. Moving lines above a function keeps its ordinal.

    """
    rows = list(rows)
    require_unambiguous(rows, run_id=run_id)
    groups: dict[str, dict[str, set[tuple]]] = {}
    for row in rows:
        groups.setdefault(bare_name(row.long_name), {}).setdefault(row.long_name, set()).add(lookup(row))
    found = _named_handles(groups)
    found.update({place: f"{ANONYMOUS}#{n}"
                  for n, place in enumerate(anonymous_positions(rows, run_id=run_id), 1)})
    return found


def _named_handles(groups: dict) -> dict[tuple, str]:
    found = {}
    for siblings in groups.values():
        for name, starts in siblings.items():
            found.update(_numbered(name if len(siblings) > 1 else bare_name(name), starts))
    return found


def _numbered(label: str, starts: set) -> dict[tuple, str]:
    """One label's handles: the label alone for one span, `label#N` in file
    order for several."""
    if len(starts) == 1:
        return dict.fromkeys(starts, label)
    return {start: f"{label}#{n}" for n, start in enumerate(sorted(starts), 1)}


def handle_names(rows) -> list[str]:
    """Every anonymous handle this file offers, in order.

    What an out-of-range ordinal is reported against: a session that guessed #5
    needs the two that exist, the same way a wrong bare name gets the file's
    real names back.
    """
    return [f"{ANONYMOUS}#{n}" for n in range(1, len(anonymous_positions(rows)) + 1)]


def handle_ordinal(name: str) -> int | None:
    """The N in `(anonymous)#N`, or None when `name` is some other name form.

    None rather than an error: this is the question "is that string a handle",
    asked before the other name forms get their turn.
    """
    head, sep, tail = name.partition("#")
    if not sep or head.strip() != ANONYMOUS or not tail.isdigit():
        return None
    return int(tail)


# --- the one resolver ---------------------------------------------------------

def select(rows, name: str, names=None, *, run_id: int | None = None) -> list[tuple[str, str]]:
    """The functions NAME selects in one run's rows of one file, as
    (long name, key name) pairs.

    `brief` and `explain` both resolve here, so one string names one function in
    each. `brief` passes the rows it packets. `explain` passes the same run's
    positions and, as `names`, every long name a stored run scored in the file,
    so a function that run no longer holds still has a trajectory to show.

    - A start line selects the function that opens on it. A line several
      functions open on is refused with their handles: the line cannot say which.
    - `(anonymous)#N` selects the file's Nth anonymous function.
    - Any other NAME matches long names exact first and by fragment second, and
      selects one twin of each: the Nth in file order for `NAME#N`, the worst
      for a bare NAME, which is the one the queue ranks. A long name no row
      holds keeps the key its ordinal spells.

    Nothing selected is an empty list. Each command words its own miss. A
    legacy-identity refusal names RUN_ID, the run the rows came from.
    """
    rows = list(rows)
    if name.isdigit():
        return _at_line(rows, key_names(rows, run_id=run_id), name)
    if handle_ordinal(name) is not None:
        found = handles(rows, run_id=run_id)
        return _pairs(key_names(rows, run_id=run_id), [r for r in rows if found[lookup(r)] == name])
    return _by_name(rows, name, names, run_id)


def _pairs(keys: dict, rows) -> list[tuple[str, str]]:
    """Each selected function once: scope copies of one span share its key."""
    return list(dict.fromkeys((r.long_name, keys[lookup(r)]) for r in rows))


def _at_line(rows: list, keys: dict, line: str) -> list[tuple[str, str]]:
    at = [r for r in rows if r.start == int(line)]
    if len({lookup(r) for r in at}) > 1:
        raise CrapkitError(_ambiguous_line(rows, at, line))
    return _pairs(keys, at)


def _ambiguous_line(rows: list, at: list, line: str) -> str:
    found = handles(rows)
    choices = ", ".join(sorted({found[lookup(r)] for r in at}))
    return f"line {line} in {at[0].path} is ambiguous; use a handle: {choices}"


def _by_name(rows: list, name: str, names, run_id: int | None = None) -> list[tuple[str, str]]:
    wanted, ordinal = split_ordinal(name)
    known = list(dict.fromkeys(r.long_name for r in rows)) if names is None else names
    picked = None if wanted == name else ordinal
    return [(long_name, _twin_key(rows, long_name, picked, run_id))
            for long_name in matching_names(known, wanted)]


def _twin_key(rows: list, long_name: str, ordinal: int | None, run_id: int | None = None) -> str:
    """The key of the twin NAME picked: the Nth in file order, or the worst of
    them when NAME gave no ordinal.

    Only the twins are keyed. Ordinals count within one long name, so their keys
    match the whole file's, and a legacy collision under another name does not
    refuse this one."""
    if ordinal is not None:
        return key_name(long_name, ordinal)
    twins = [r for r in rows if r.long_name == long_name]
    return key_names(twins, run_id=run_id)[lookup(max(twins, key=_severity))] if twins else long_name


def _severity(row) -> tuple:
    """Worst first, and the first in the file among equals. A row with no score,
    as on an inventory run, ranks with every other unscored one.

    Scores compare at the 4 places a mark holds (score.CRAP_PLACES; score imports
    this module), so equal ones tie: ccn 25 at 80% coverage and ccn 5 at none
    both score 30, which the floats read as 29.999999999999996 and 30.0."""
    return round(row.crap or 0.0, 4), -row.start, -position(row)[1]
