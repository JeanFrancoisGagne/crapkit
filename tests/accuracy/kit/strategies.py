"""Hypothesis strategies for the accuracy packets. No crapkit: a CRAP value
comes from kit.exact, and kit.drive turns a plain tuple into a crapkit type.

Every strategy draws its ordinary values and, among them, the shapes that broke
crapkit before. Each shape has an event name: the R id of the bug it reproduces
(tests/accuracy/suite_strength/retro/bugs.tsv), or `shape:...` for an edge the
docs name without a past bug. A drawn value emits hypothesis.event() for every
shape it has, so the nightly summary can require each one to have occurred,
and the same counts reach run.py through kit.runlog. Hypothesis runs each
choice sequence once, so a shape drawn only as a sampled literal reaches a test
once a run whatever its example count; a shape the summary counts draws from a
family of values. Each shape's literal value is also returned by examples(), so
a test runs it every time:

    @examples("coverage_pair")
    @given(strategies.coverage_pair())
    @pure
    def test_...(pair): ...
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import math
import sys
import unicodedata

from hypothesis import event, example, strategies as st

from . import exact

EVENTS: Counter = Counter()
CCN_MAX = 5000
WINDOWS_INVALID = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
# Windows reserves these device names as a file's stem, in any case, with the
# superscript digits (category No, so a letters-and-digits draw can spell them).
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"{port}{n}" for port in ("COM", "LPT")
                                          for n in "0123456789¹²³")}


def _emit(names, value):
    for name in names:
        event(name)
        EVENTS[name] += 1
    return value


def _tagged(strategy, classify):
    return strategy.map(lambda value: _emit(classify(value), value))


def _checked(checks: dict, value) -> list[str]:
    """The names of the checks `value` passes, in table order."""
    return [name for name, check in checks.items() if check(value)]


# --- ccn and CRAP ------------------------------------------------------------

def ccn():
    """1 to 5000, log-distributed: small values are common, the cap is reachable."""
    raw = st.floats(0, math.log(CCN_MAX)).map(lambda x: min(CCN_MAX, max(1, round(math.exp(x)))))
    return _tagged(st.one_of(st.sampled_from([1, CCN_MAX]), raw), _ccn_shapes)


def _ccn_shapes(value: int) -> list[str]:
    return ["shape:ccn-over-1000"] if value > 1000 else []


@dataclass(frozen=True)
class CrapCase:
    ccn: int
    covered: int
    total: int

    @property
    def cov(self) -> Fraction:
        return exact.ratio(self.covered, self.total)

    @property
    def crap(self) -> Fraction:
        return exact.crap(self.ccn, self.cov)


def counts():
    """A valid (covered, total) with total >= 1."""
    return st.integers(1, 400).flatmap(lambda total: st.tuples(st.integers(0, total),
                                                               st.just(total)))


def crap_case():
    return st.builds(lambda c, pair: CrapCase(c, *pair), ccn(), counts())


# --- coverage counts a producer may write ------------------------------------

@dataclass(frozen=True)
class CoveragePair:
    """What an artifact says for one function: covered and total, possibly broken.
    None is a count the artifact left out (no branch data)."""
    covered: object
    total: object


_BAD_PAIRS = {
    "R27": (CoveragePair(math.nan, 4), CoveragePair(1, math.inf)),
    "R28": (CoveragePair(5, 4), CoveragePair(-1, 4)),
    "R89": (CoveragePair(None, None),),
    "shape:total-zero": (CoveragePair(0, 0),),
}


def _nonfinite(value) -> bool:
    return isinstance(value, float) and not math.isfinite(value)


def _ints(pair: CoveragePair) -> list[int]:
    return [value for value in (pair.covered, pair.total) if isinstance(value, int)]


def _over(pair: CoveragePair) -> bool:
    return len(_ints(pair)) == 2 and pair.covered > pair.total


_PAIR_CHECKS = {
    "R27": lambda pair: _nonfinite(pair.covered) or _nonfinite(pair.total),
    "R28": lambda pair: any(value < 0 for value in _ints(pair)) or _over(pair),
    "R89": lambda pair: pair.covered is None or pair.total is None,
    "shape:total-zero": lambda pair: pair.total == 0,
}


def _pair_shapes(pair: CoveragePair) -> list[str]:
    return _checked(_PAIR_CHECKS, pair)


_COUNT = st.integers(0, 400)


def _either_slot(odd, count):
    """A pair with `odd` as covered or as total and `count` in the other slot."""
    return st.builds(CoveragePair, odd, count) | st.builds(CoveragePair, count, odd)


def _covered_past_total():
    return st.builds(lambda total, past: CoveragePair(total + past, total), _COUNT,
                     st.integers(1, 400))


# Each broken shape's family of pairs. The six literals of _BAD_PAIRS alone
# reached each 20,000-example test once: nightly 36940595657's coverage leg
# drew R27 4 times in 40,000 examples.
_PAIR_FAMILIES = {
    "R27": _either_slot(st.sampled_from([math.nan, math.inf, -math.inf]), _COUNT),
    "R28": _either_slot(st.integers(-400, -1), _COUNT) | _covered_past_total(),
    "R89": _either_slot(st.none(), st.none() | _COUNT),
    "shape:total-zero": st.builds(CoveragePair, _COUNT, st.just(0)),
}


def coverage_pair():
    """Half the draws are counts a producer writes and half are broken: a
    literal of _BAD_PAIRS or a pair of one shape's family. The broken half is
    one flatmap so one_of keeps it one branch, where it would spread a nested
    one_of's branches beside `good`."""
    bad = [pair for pairs in _BAD_PAIRS.values() for pair in pairs]
    good = counts().map(lambda pair: CoveragePair(*pair))
    broken = st.sampled_from([st.sampled_from(bad), *_PAIR_FAMILIES.values()]).flatmap(
        lambda family: family)
    return _tagged(st.one_of(good, broken), _pair_shapes)


# --- function spans ---------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    """A function's lines: `signature_end` is the line its body starts after."""
    start: int
    end: int
    signature_end: int


_SPAN_CHECKS = {
    "R22": lambda spans: max(Counter(span.start for span in spans).values()) > 1,
    "R46": lambda spans: any(span.start == span.end for span in spans),
    "R48": lambda spans: any(span.signature_end > span.start for span in spans),
}


def _span_shapes(spans: tuple[Span, ...]) -> list[str]:
    return _checked(_SPAN_CHECKS, spans)


def _span(start: int, length: int, signature: int) -> Span:
    return Span(start, start + length, start + min(signature, length))


def spans():
    """A file's spans, nested and on shared start lines included."""
    one = st.builds(_span, st.integers(1, 200), st.integers(0, 30), st.integers(0, 3))
    nested = one.map(lambda outer: (outer, Span(outer.start, outer.start, outer.start)))
    many = st.lists(one, min_size=1, max_size=6).map(tuple)
    return _tagged(st.one_of(many, nested), _span_shapes)


# --- run histories ---------------------------------------------------------------

RUN_KINDS = {
    "coverage": "", "inventory": "shape:inventory-run", "verify-ok": "",
    "verify-fail": "R06", "partial": "R83", "refused": "shape:refused-run",
    "hook-override": "R107", "count-less": "R98", "lane-subset": "R74",
    "version-upgrade": "R56",
}


def _run_shapes(kinds: list[str]) -> list[str]:
    return sorted({RUN_KINDS[kind] for kind in kinds} - {""})


def run_kinds():
    """A store's run history, oldest first, one kind per run."""
    return _tagged(st.lists(st.sampled_from(sorted(RUN_KINDS)), min_size=1, max_size=12),
                   _run_shapes)


# --- commit times ------------------------------------------------------------------

@dataclass(frozen=True)
class Stamp:
    author: int
    committer: int
    offset_minutes: int


AUG_31 = 1_788_220_799  # 2026-08-31T23:59:59Z, the last second of a month


def _times(stamps: tuple[Stamp, ...]) -> list[int]:
    return [stamp.committer for stamp in stamps]


_STAMP_CHECKS = {
    "R94": lambda stamps: len(set(_times(stamps))) == 1,
    "R59": lambda stamps: any(abs(time - AUG_31) <= 86_400 for time in _times(stamps)),
    "shape:tz-offset": lambda stamps: any(stamp.offset_minutes for stamp in stamps),
    "shape:child-before-parent": lambda stamps: any(
        later < earlier for earlier, later in zip(_times(stamps), _times(stamps)[1:])),
}


def _stamp_shapes(stamps: tuple[Stamp, ...]) -> list[str]:
    return _checked(_STAMP_CHECKS, stamps)


def stamps():
    """Commit times in log order (parent first); a child may predate its parent."""
    near = st.integers(AUG_31 - 86_400, AUG_31 + 86_400)
    anywhere = st.integers(1_500_000_000, 1_900_000_000)
    stamp = st.builds(Stamp, st.one_of(near, anywhere), st.one_of(near, anywhere),
                      st.sampled_from([0, 0, 60, -300, 330, 840]))
    same = stamp.flatmap(lambda one: st.integers(1, 5).map(lambda n: (one,) * n))
    return _tagged(st.one_of(st.lists(stamp, min_size=1, max_size=8).map(tuple), same),
                   _stamp_shapes)


# --- ratchet marks ------------------------------------------------------------------

def _tie(value: float) -> bool:
    return math.isfinite(value) and (Fraction(value) * 10_000 * 2) % 2 == 1


_MARK_CHECKS = {
    "R32": lambda value: not math.isfinite(value),
    "shape:more-than-4dp": lambda value: math.isfinite(value) and round(value, 4) != value,
    "shape:4dp-tie": _tie,
}


def _mark_shapes(value: float) -> list[str]:
    return _checked(_MARK_CHECKS, value)


def _tie_value(units: int) -> float:
    """An odd multiple of 1/32. A float is an exact 4 dp tie (its fifth decimal
    a 5 and nothing after) only when it is k/20000 for odd k with a power-of-two
    denominator once reduced, and 20000 = 2^5 * 625: those are the odd
    multiples of 625/20000 = 1/32."""
    return (2 * units + 1) / 32


def marks():
    """A mark as a float: 4 dp values, longer ones, exact 4 dp ties, nonfinite ones."""
    four = st.integers(1, 10_000_000).map(lambda n: n / 10_000)
    longer = st.floats(0.0001, 1e6, allow_nan=False, allow_infinity=False)
    ties = st.integers(0, 10_000_000).map(_tie_value)
    odd = st.sampled_from([math.nan, math.inf, -math.inf])
    return _tagged(st.one_of(four, longer, ties, odd), _mark_shapes)


# --- paths ---------------------------------------------------------------------------

_PATH_SHAPES = {
    "R76": lambda text: any(ord(char) > 127 or char in '"\t\n' for char in text),
    "R66": lambda text: "\\" in text or text[1:3] == ":\\",
    "R93": lambda text: text.startswith("**/"),
    "shape:nfd": lambda text: unicodedata.normalize("NFC", text) != text,
    "shape:dotdot": lambda text: ".." in text.split("/"),
}
PATH_EXAMPLES = ("src/a\tb.py", "src/a\nb.py", 'src/"q".py', "src\\win\\a.py", "C:\\repo\\a.py",
                 "src/cafe\u0301.py", "src/café.py", "../outside.py", "**/generated/*.py")


def _path_shapes(text: str) -> list[str]:
    return _checked(_PATH_SHAPES, text)


def path_text():
    """Path strings for pure string functions: every awkward character allowed."""
    segment = st.text(st.characters(codec="utf-8", exclude_characters="/\x00"), min_size=1,
                      max_size=8)
    joined = st.lists(segment, min_size=1, max_size=4).map("/".join)
    return _tagged(st.one_of(joined, st.sampled_from(PATH_EXAMPLES)), _path_shapes)


def _unreserved(segment: str) -> str:
    """A letters-and-digits segment, with `_` appended when Windows reserves it."""
    return segment + "_" if segment.upper() in RESERVED else segment


def fs_paths(case_twins: bool = sys.platform.startswith("linux")):
    """Relative paths every OS can create: letters and digits only, so no
    Windows-invalid character, no dot or trailing space; no reserved name;
    unique under casefold. `case_twins` adds a case-only twin, which only a
    case-sensitive filesystem holds apart.

    It is built without .filter(): Hypothesis 6.168 validates a filtered
    strategy again on every draw and reads the predicate's source file each
    time, which cost 40 to 80 ms a draw on a bind-mounted checkout."""
    segment = st.text(st.characters(codec="utf-8", categories=("L", "N")), min_size=1,
                      max_size=8).map(_unreserved)
    path = st.lists(segment, min_size=1, max_size=3).map("/".join).map(lambda p: p + ".py")
    unique = st.lists(path, min_size=1, max_size=6, unique_by=str.casefold)
    if not case_twins:
        return unique
    return unique.map(lambda paths: [*paths, paths[0].swapcase()]
                      if paths[0].swapcase() != paths[0] else paths)


# --- the literal shapes every test runs ------------------------------------------------

REQUIRED = {
    "ccn": {"shape:ccn-over-1000": CCN_MAX},
    "coverage_pair": {name: pairs[0] for name, pairs in _BAD_PAIRS.items()},
    "spans": {"R22": (Span(3, 9, 3), Span(3, 3, 3)), "R46": (Span(7, 7, 7),),
              "R48": (Span(10, 20, 12),)},
    "run_kinds": {shape: [kind] for kind, shape in RUN_KINDS.items() if shape},
    "stamps": {"R94": (Stamp(AUG_31, AUG_31, 0),) * 3,
               "R59": (Stamp(AUG_31, AUG_31, 0), Stamp(AUG_31 + 1, AUG_31 + 1, 0)),
               "shape:tz-offset": (Stamp(AUG_31, AUG_31, 330),),
               "shape:child-before-parent": (Stamp(AUG_31, AUG_31, 0),
                                             Stamp(AUG_31 - 60, AUG_31 - 60, 0))},
    "marks": {"R32": math.nan, "shape:more-than-4dp": 1.23456, "shape:4dp-tie": 0.03125},
    "path_text": {"R76": "src/a\tb.py", "R66": "src\\win\\a.py", "R93": "**/generated/*.py",
                  "shape:nfd": "src/cafe\u0301.py", "shape:dotdot": "../outside.py"},
}
CLASSIFIERS = {"ccn": _ccn_shapes, "coverage_pair": _pair_shapes, "spans": _span_shapes,
               "run_kinds": _run_shapes, "stamps": _stamp_shapes, "marks": _mark_shapes,
               "path_text": _path_shapes}


def examples(name: str):
    """A decorator that runs every required shape of one strategy as an @example."""
    def apply(test):
        for value in REQUIRED[name].values():
            test = example(value)(test)
        return test
    return apply
