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
from collections.abc import Iterable, Iterator, Mapping
from typing import NamedTuple

from .keys import key_names, key_of
from .ratchet import RatchetEntry
from .repopath import file_separators
from .score import ScoredRow, parse_scored_tsv, scored_tsv_lines


def diff_uncovered(changed_ranges: dict, missing: dict,
                   rows: Iterable[ScoredRow] = ()) -> list[tuple[str, int]]:
    """Changed lines (new-file coordinates) whose statement never ran — where
    the next bug ships.

    A file no lane artifact mentions is one nothing imported, so none of it ran:
    every line of its `untested` functions counts. Its module-level lines do not,
    since no artifact says which of them are statements."""
    dead_by_path = {**_silent_file_lines(rows, missing, changed_ranges), **missing}
    out = []
    for path, ranges in sorted(changed_ranges.items()):
        dead = dead_by_path.get(path)
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


def _silent_file_lines(rows: Iterable[ScoredRow], missing: dict,
                       changed_ranges: dict) -> dict[str, set[int]]:
    """The lines of `untested` functions in changed files no artifact mentions.

    The flag alone is not enough: a measured file's one-line def floors to
    untested although the artifact saw its line run."""
    out: dict[str, set[int]] = {}
    for row in rows:
        if row.flag == "untested" and row.path in changed_ranges and row.path not in missing:
            out.setdefault(row.path, set()).update(range(row.start, row.end + 1))
    return out


class PortableBaseline(NamedTuple):
    commit: str
    kind: str
    rows: list[ScoredRow]
    # Each lane's test results, as the run recorded them; {} for a file written
    # before the stamp carried them, which forgives no failure.
    lanes: dict = {}


# JSON punctuation stays readable on the stamp line; a space, `=`, `%` or any
# other character a test id can hold is percent-encoded, so the stamp stays one
# line of space-separated fields an older reader still splits correctly.
_RESULTS_SAFE = '":,{}[]/'


def baseline_tsv_lines(commit: str, kind: str, rows: list[ScoredRow],
                       results: dict | None = None) -> Iterator[str]:
    """A baseline run as a file the repo can carry: a commit stamp, then the
    run's scored export. The store lives in a gitignored .crapkit/, so a fresh
    clone has nothing else to name what it is being measured against.

    `results` is each lane's test results. They ride on the stamp line because
    a reader older than the field reads the stamp's fields by name and ignores
    the rest, where a line of its own would read as a malformed row."""
    yield f"# commit={commit} run_kind={kind}{_results_field(results)}\n"
    yield from scored_tsv_lines(rows)


def _results_field(results: dict | None) -> str:
    from urllib.parse import quote
    import json

    if not results:
        return ""
    text = json.dumps(results, sort_keys=True, separators=(",", ":"))
    return f" results={quote(text, safe=_RESULTS_SAFE)}"


def _stamp_fields(stamp: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in stamp.removeprefix("# ").split() if "=" in part)


def _results_of(fields: dict[str, str]) -> dict:
    from urllib.parse import unquote
    import json

    return json.loads(unquote(fields["results"])) if "results" in fields else {}


def parse_baseline_tsv(text: str) -> PortableBaseline:
    stamp, _, body = text.partition("\n")
    fields = _stamp_fields(stamp)
    if "commit" not in fields or "run_kind" not in fields:
        raise ValueError(
            f"a baseline file starts with `# commit=<sha> run_kind=<kind>`, got {stamp!r}")
    return PortableBaseline(fields["commit"], fields["run_kind"], parse_scored_tsv(body),
                            _results_of(fields))


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


class UncoveredViolation(NamedTuple):
    path: str
    line: int
    dirty: bool = False


class UnreadFile(NamedTuple):
    """A changed file no reader could read. The run scores it as zero
    functions; the gate judged none of them, so it refuses the file."""
    path: str
    reason: str
    dirty: bool = False


class Verdict(NamedTuple):
    ok: bool
    gate_violations: list[GateViolation]
    ratchet_regressions: list[RatchetRegression]
    new_failures: list[str]
    dirty_failures: list[str]
    uncovered_violations: tuple[UncoveredViolation, ...] = ()
    # What the verdict settled without failing on it: fresh failures the
    # baseline carries too, new failures that passed their flake retry, and
    # gate violations an --override granted.
    forgiven_failures: tuple[str, ...] = ()
    retried_passes: tuple[str, ...] = ()
    overridden: tuple[GateViolation, ...] = ()
    unread_files: tuple[UnreadFile, ...] = ()


def _any_finding(verdict: Verdict) -> bool:
    return bool(verdict.gate_violations or verdict.unread_files or verdict.ratchet_regressions
                or verdict.new_failures or verdict.uncovered_violations)


def settle_verdict(verdict: Verdict) -> Verdict:
    """Re-derive what the remaining findings decide after a grant or retry:
    the dirty subset of new_failures, and success."""
    remaining = set(verdict.new_failures)
    return verdict._replace(ok=not _any_finding(verdict),
                            dirty_failures=[f for f in verdict.dirty_failures if f in remaining])


def settle_flake_retry(verdict: Verdict, survivors: set[str]) -> Verdict:
    """The verdict after a flake retry: the new failures missing from
    `survivors` passed their rerun and become retried passes."""
    passed = tuple(sorted(set(verdict.new_failures) - survivors))
    return settle_verdict(verdict._replace(
        new_failures=[f for f in verdict.new_failures if f in survivors],
        retried_passes=verdict.retried_passes + passed))


def with_diff_coverage(verdict: Verdict, uncovered: list[tuple[str, int]],
                       maximum: int | None, dirty_paths: set[str]) -> Verdict:
    """Keep a breached changed-line ceiling in the verdict's findings."""
    if maximum is None or len(uncovered) <= maximum:
        return verdict
    findings = tuple(UncoveredViolation(path, line, path in dirty_paths) for path, line in uncovered)
    return settle_verdict(verdict._replace(uncovered_violations=findings))


def with_unread(verdict: Verdict, unread: dict[str, str], changed: set[str],
                dirty_paths: set[str]) -> Verdict:
    """Fail the gate on every changed file no reader could read: its zero
    records are not zero functions over the ceiling. An unread file the
    change never touched holds no changed function, so it is left alone."""
    found = tuple(UnreadFile(path, why, path in dirty_paths)
                  for path, why in sorted(unread.items()) if path in changed)
    return settle_verdict(verdict._replace(unread_files=found)) if found else verdict


def _id_forms(path: str) -> tuple[str, str]:
    """The two shapes a junit id's file part takes for one file once it is read
    as a path: the repo-relative path (vitest, and pytest's `file` fallback) and
    pytest's dotted module. `path` is git's, so `/` is its only separator."""
    stem = path[:-3] if path.endswith(".py") else path
    return path, stem.replace("/", ".")


def file_part(test_id: str) -> str:
    """The file part of a junit id, as the runner wrote it."""
    return test_id.split("::")[0]


def _id_file(test_id: str, test_files: Mapping[str, str] | None) -> str:
    """The file part of a junit id, spelled the way git spells the file.

    A runner names the test file however it was started: bun on Windows writes
    `src\\deep\\keep.test.ts`, a runner handed `./web/...` keeps the dot, and
    jest-junit's `{filepath}` is absolute. Compared as text with git's path,
    each one read a failure in the file under edit as committed. `test_files`
    holds the file part as the disk placed it (repopath.Reported); a part it
    lacks is read as text."""
    part = file_part(test_id)
    placed = test_files.get(part) if test_files else None
    return placed or file_separators(part).removeprefix("./")


def dirty_failure_ids(new_failures: list[str], dirty_paths: set[str],
                      test_files: Mapping[str, str] | None = None) -> list[str]:
    """New failures whose test id names a file with uncommitted edits.
    `test_files` maps an id's file part to git's path for it, so an absolute
    part or one in another letter case can match; the ids stay as written."""
    forms = {form for path in dirty_paths for form in _id_forms(path)}
    return [f for f in new_failures if _id_file(f, test_files) in forms]


# The finding kinds that carry their own `dirty` flag; new_failures carries ids.
_FLAGGED_FINDINGS = ("gate_violations", "unread_files", "ratchet_regressions",
                     "uncovered_violations")


def dirty_counts(verdict: Verdict) -> tuple[int, int]:
    """(committed, dirty) over every finding kind, so one line says how much of
    a verdict belongs to the tree as committed and how much to somebody's edits."""
    dirty_ids = set(verdict.dirty_failures)
    flags = ([f.dirty for kind in _FLAGGED_FINDINGS for f in getattr(verdict, kind)]
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


def unmarked_over_ceiling(fresh: list[ScoredRow], ratchet: list[RatchetEntry], target: int,
                          scope_targets: dict[str, int] | None = None) -> list[ScoredRow]:
    """Standing debt nothing guards: the worst row per ratchet key that sits over
    its ceiling with no mark under that key, worst first.

    The gate judges touched functions only and the ratchet check compares marks
    only, so a rise on one of these (coverage loss included) reaches no finding.
    A marked function that rose is a regression already; this is the debt
    `ratchet seed` was never run on.
    """
    marks = _marks_of(ratchet)
    rows = [row for key, row in rows_by_key(fresh).items()
            if row.crap > _ceiling(row, target, scope_targets) and key not in marks]
    rows.sort(key=lambda r: (-r.crap, r.path, r.start))
    return rows


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
    test_files: Mapping[str, str] | None = None,
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
        dirty_failures=dirty_failure_ids(new_failures, dirty, test_files),
        forgiven_failures=tuple(sorted(fresh_failures & baseline_failures)),
    )
