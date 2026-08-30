"""The verdict. Pure: fresh scored rows + changed ranges + ratchet + failure sets in, Verdict out.

Three independent checks, all must hold:
- Gate: every function a change touched sits at CRAP <= target (coverage cannot
  save cc > target; that is the target's design), unless a ratchet mark carries
  it at or under the recorded value: that is the debt the repo signed for, and
  `rescore --gate` and the pre-commit hook already read it so (#29).
- Ratchet: no function above target scores worse than its recorded high-water
  mark, touched or not (coverage rot regresses functions nobody edited).
- Failures: the fresh failure set adds nothing over the baseline's (the suite
  is never assumed green; 98 pre-existing failures measured on day one).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from typing import NamedTuple

from .keys import key_names, key_of
from .ratchet import RatchetEntry
from .score import ScoredRow, parse_scored_tsv, scored_tsv_lines


def diff_uncovered(changed_ranges: dict, missing: dict) -> list[tuple[str, int]]:
    """Changed lines (new-file coordinates) whose statement never ran — where
    the next bug ships. Files no lane measured stay silent (absent from missing)."""
    out = []
    for path, ranges in sorted(changed_ranges.items()):
        dead = missing.get(path)
        if not dead:
            continue
        # Sort the file's dead lines ONCE: sorting (and linearly scanning) them
        # per hunk was O(hunks x dead log dead) for a file whose dead set never
        # changes. Sorted, each hunk is two bisects and a slice.
        ordered = sorted(dead)
        for start, end in ranges:
            out.extend((path, line)
                       for line in ordered[bisect_left(ordered, start):bisect_right(ordered, end)])
    return out


class PortableBaseline(NamedTuple):
    commit: str
    kind: str
    rows: list[ScoredRow]


def baseline_tsv_lines(commit: str, kind: str, rows: list[ScoredRow]) -> Iterator[str]:
    """A baseline run as a file the repo can carry: a commit stamp, then the
    run's scored export. The store lives in a gitignored .crapkit/, so a fresh
    clone has nothing else to name what it is being measured against."""
    yield f"# commit={commit} run_kind={kind}\n"
    yield from scored_tsv_lines(rows)


def _stamp_fields(stamp: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in stamp.removeprefix("# ").split() if "=" in part)


def parse_baseline_tsv(text: str) -> PortableBaseline:
    stamp, _, body = text.partition("\n")
    fields = _stamp_fields(stamp)
    if "commit" not in fields or "run_kind" not in fields:
        raise ValueError(
            f"a baseline file starts with `# commit=<sha> run_kind=<kind>`, got {stamp!r}")
    return PortableBaseline(fields["commit"], fields["run_kind"], parse_scored_tsv(body))


class GateViolation(NamedTuple):
    path: str
    long_name: str
    start: int
    ccn: int
    cov: float
    crap: float
    remedy: str
    dirty: bool = False
    # The ratchet key this function is judged under, which is its long_name plus
    # an ordinal when the file gives that name to more than one function. Empty
    # means nobody keyed it, and `keys.stated_key` reads that as the bare name.
    key_name: str = ""


class RatchetRegression(NamedTuple):
    path: str
    long_name: str
    recorded: float
    fresh_crap: float
    dirty: bool = False


class Verdict(NamedTuple):
    ok: bool
    gate_violations: list[GateViolation]
    ratchet_regressions: list[RatchetRegression]
    new_failures: list[str]
    dirty_failures: list[str]


def _id_forms(path: str) -> tuple[str, str]:
    """The two shapes a junit classname takes for one file: the repo-relative
    path (vitest, and pytest's `file` fallback) and pytest's dotted module."""
    stem = path[:-3] if path.endswith(".py") else path
    return path, stem.replace("/", ".")


def dirty_failure_ids(new_failures: list[str], dirty_paths: set[str]) -> list[str]:
    """New failures whose test id names a file with uncommitted edits."""
    forms = {form for path in dirty_paths for form in _id_forms(path)}
    return [f for f in new_failures if f.split("::")[0] in forms]


def dirty_counts(verdict: Verdict) -> tuple[int, int]:
    """(committed, dirty) over every finding kind, so one line says how much of
    a verdict belongs to the tree as committed and how much to somebody's edits."""
    dirty_ids = set(verdict.dirty_failures)
    flags = ([v.dirty for v in verdict.gate_violations]
             + [r.dirty for r in verdict.ratchet_regressions]
             + [f in dirty_ids for f in verdict.new_failures])
    dirty = sum(flags)
    return len(flags) - dirty, dirty


def _touched(row: ScoredRow, ranges: dict[str, list[tuple[int, int]]]) -> bool:
    spans = ranges.get(row.path)
    if not spans:
        return False
    return any(not (hi < row.start or lo > row.end) for lo, hi in spans)


def touched_rows(rows: list[ScoredRow],
                 changed_ranges: dict[str, list[tuple[int, int]]]) -> list[ScoredRow]:
    """The gate's selection without its policy: rows whose span a change overlaps.

    Every gate in crapkit judges touched functions only — untouched debt is the
    ratchet's business. `rescore --gate` reuses this so its verdict and the
    pre-commit hook's cannot disagree about which functions were even in scope.
    """
    return [r for r in rows if _touched(r, changed_ranges)]


def rows_by_key(fresh: list[ScoredRow]) -> dict[tuple[str, str], ScoredRow]:
    """Scored rows by their ratchet key: `keys.key_names` gives each twin its own.

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


def _ceiling(row: ScoredRow, target: int, scope_targets: dict[str, int] | None) -> int:
    return (scope_targets or {}).get(row.scope, target)


def _marks_of(ratchet: list[RatchetEntry]) -> dict[tuple[str, str], float]:
    return {(e.path, e.long_name): e.crap for e in ratchet}


def _within_mark(row: ScoredRow, key: tuple[str, str],
                 marks: dict[tuple[str, str], float]) -> bool:
    """Signed debt as `rescore --gate` reads it: a mark the fresh score sits at or
    under, compared at the 4dp the mark is stored at. Above the mark the gate
    fires, as rescore's does, and the ratchet check reports the rise beside it,
    so the three gates agree on what an edit inside marked debt may do (#29).
    The key is the twin-aware one the ratchet check looks marks up by."""
    mark = marks.get(key)
    return mark is not None and round(row.crap, 4) <= mark


def _gate_violations(fresh, changed_ranges, target, scope_targets, dirty,
                     ratchet) -> list[GateViolation]:
    names = key_names(fresh)
    marks = _marks_of(ratchet)
    gate = [
        GateViolation(r.path, r.long_name, r.start, r.ccn, r.cov, r.crap, r.remedy,
                      r.path in dirty, key_of(names, r)[1])
        for r in fresh
        if r.crap > _ceiling(r, target, scope_targets) and _touched(r, changed_ranges)
        and not _within_mark(r, key_of(names, r), marks)
    ]
    gate.sort(key=lambda v: (-v.crap, v.path, v.start))
    return gate


def _ratchet_regressions(fresh, ratchet, dirty) -> list[RatchetRegression]:
    worst_by_key = rows_by_key(fresh)
    regressions = []
    for entry in ratchet:
        row = worst_by_key.get((entry.path, entry.long_name))
        # Compare at the precision the mark is STORED at: marks live as 4dp
        # strings, and cov = covered/total makes longer decimals routine — an
        # unrounded compare wedges an unchanged tree against its own mark.
        if row is not None and round(row.crap, 4) > entry.crap:
            regressions.append(RatchetRegression(entry.path, entry.long_name, entry.crap,
                                                 round(row.crap, 4), entry.path in dirty))
    regressions.sort(key=lambda r: (-(r.fresh_crap - r.recorded), r.path))
    return regressions


def evaluate(
    *,
    fresh: list[ScoredRow],
    changed_ranges: dict[str, list[tuple[int, int]]],
    ratchet: list[RatchetEntry],
    baseline_failures: set[str],
    fresh_failures: set[str],
    target: int,
    scope_targets: dict[str, int] | None = None,
    dirty_paths: set[str] | None = None,
) -> Verdict:
    dirty = dirty_paths or set()
    gate = _gate_violations(fresh, changed_ranges, target, scope_targets, dirty, ratchet)
    regressions = _ratchet_regressions(fresh, ratchet, dirty)
    new_failures = sorted(fresh_failures - baseline_failures)

    return Verdict(
        ok=not gate and not regressions and not new_failures,
        gate_violations=gate,
        ratchet_regressions=regressions,
        new_failures=new_failures,
        dirty_failures=dirty_failure_ids(new_failures, dirty),
    )
