"""The verdict. Pure: fresh scored rows + changed ranges + ratchet + failure sets in, Verdict out.

Three independent checks, all must hold:
- Gate: every function a change touched sits at CRAP <= target (coverage cannot
  save cc > target; that is the target's design), unless a ratchet mark carries
  it at or under the recorded value: that is the debt the repo signed for (#29).
  verify is a gate adapter: the gate module judges the touched functions with
  each one's exact CRAP, and the changed files no reader could read.
- Ratchet: no function above target scores worse than its recorded high-water
  mark, touched or not (coverage rot regresses functions nobody edited).
- Failures: the fresh failure set adds nothing over the baseline's (the suite
  is never assumed green; 98 pre-existing failures measured on day one).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any, NamedTuple

from .gate import (WHOLE, ChangedFile, CrapBound, Function, GateResult, Unread, UnreadableName, judge,
                   touches)
from .keys import MarkIndex, mark_key, rows_by_key
from .merge import UNREAD_ADVICE
from .ratchet import RatchetEntry
from .repopath import file_separators
from .score import CRAP_PLACES, ScoredRow, over_ceiling, parse_scored_tsv, scored_tsv_lines


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
    # Changed files no reader could read: the run scores each as zero
    # functions, and the gate judged none of them, so it refuses the file.
    unread_files: tuple[Unread, ...] = ()
    # Names a scope takes that are not UTF-8. The gate refuses such a name
    # before it judges anything, so verify's verdict at that stop holds these
    # and nothing else: no lane ran. The payload's `unreadable_names` lists
    # the other kind, names no scope takes.
    claimed_names: tuple[UnreadableName, ...] = ()

    @classmethod
    def passing(cls) -> Verdict:
        """A verdict holding no finding, each list its own."""
        return cls(True, [], [], [], [])


def settle_verdict(verdict: Verdict) -> Verdict:
    """Re-derive what the remaining findings decide after a grant or retry:
    the dirty subset of new_failures, and success."""
    remaining = set(verdict.new_failures)
    return verdict._replace(ok=exit_code(verdict) == 0,
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
    return settle_verdict(verdict._replace(uncovered_violations=flagged_lines(uncovered, dirty_paths)))


def flagged_lines(uncovered: Sequence, dirty_paths: Collection[str]) -> tuple[UncoveredViolation, ...]:
    """Each uncovered changed line, dirty when its file has uncommitted edits."""
    return tuple(UncoveredViolation(path, line, path in dirty_paths) for path, line in map(_spot, uncovered))


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


# --- the finding kinds -----------------------------------------------------------
#
# One row per kind of finding a verdict holds, in exit order. Every per-kind site
# reads the rows: the exit code, `ok`, the dirty split, the override's grant and
# refusal, the text lines, the JSON lists, the findings items and the SARIF
# results. A new kind is one row here and the detector that fills its field.


class Refusal(NamedTuple):
    """How a kind that refuses an override is named on the one refusal line.
    `place` orders the causes as docs/ratchet.md names them: a ratchet
    regression, a new test failure, an unread file."""
    place: int
    noun: str
    first: Callable[[Any], str]
    escape: str


class Sarif(NamedTuple):
    """A kind's SARIF form: its rule, its level, where a result anchors and what
    it says."""
    rule: str
    level: str
    place: Callable[[Any], tuple[str, int]]
    message: Callable[[Any], str]


class FindingKind(NamedTuple):
    """One kind of finding and every form it takes.

    `exit` is verify's exit code when the kind is present, None for a report
    the verdict never fails on. `granted` says an --override grants it; a
    `refusal` says an override is refused while one is present. `dirty` finds
    the dirty flag of each entry, `text` gives the lines verify prints on
    `stream`, `json` the kind's list under its 0.8.1 key `json_key` (None: the
    payload has no list for it), and `sarif` its SARIF form (None: no result).
    `rule` labels the kind's items in `findings`, the label the Action's
    comment prints, `item` gives an item's own fields, and `cap` how many
    items the kind lists at most (None: every one)."""
    kind: str
    field: str
    exit: int | None
    granted: bool
    refusal: Refusal | None
    dirty: Callable[[Verdict, Sequence], list[bool]]
    text: Callable[[Sequence, Sequence[bool]], list[str]]
    stream: str
    json_key: str | None
    json: Callable[[Sequence], list]
    sarif: Sarif | None
    rule: str
    item: Callable[[Any], dict]
    cap: int | None = None

    @property
    def fails(self) -> bool:
        return self.exit is not None


def each(line: Callable[[Any, bool], str]) -> Callable[[Sequence, Sequence[bool]], list[str]]:
    """A kind's text as one line per entry, each with its dirty flag."""
    def text(entries: Sequence, flags: Sequence[bool]) -> list[str]:
        return [line(entry, dirty) for entry, dirty in zip(entries, flags)]
    return text


def _dirty_tag(dirty: bool) -> str:
    return "  [dirty]" if dirty else ""


def gate_line(v, dirty: bool, unmeasured: bool = False) -> str:
    """One gate violation, however it was decided; verify and `rescore --gate`
    report the same finding, so they must read the same. `unmeasured` prints
    `cov -` for a cov no measurement stands behind: the GATE line said 0% where
    the rescore table under it said `-` and `coverage not measured`."""
    cov = "-" if unmeasured else f"{v.cov:.0%}"
    return (f"  GATE  crap {v.crap:8.1f}  ccn {v.ccn:>3} cov {cov}  "
            f"{v.path}:{v.start}  {v.long_name}  -> {v.remedy}{_dirty_tag(dirty)}")


def _unread_file_line(u: Unread, dirty: bool) -> str:
    """One changed file a gate refused because no reader could read it; every
    gate prints it the same way."""
    return f"  UNREAD  {u.path}: {u.reason}{_dirty_tag(dirty)}"


def _ratchet_line(r: RatchetRegression, dirty: bool) -> str:
    return f"  RATCHET  {r.path}  {r.long_name}: {r.recorded} -> {r.fresh_crap}{_dirty_tag(dirty)}"


def _failure_line(test_id: str, dirty: bool) -> str:
    return f"  NEW FAILURE  {test_id}{_dirty_tag(dirty)}"


def _spot(line) -> tuple[str, int]:
    """(path, line) of an uncovered changed line, an UncoveredViolation or the
    (path, line) pair `diff_uncovered` lists."""
    return line[0], line[1]


def _uncovered_line(line, dirty: bool) -> str:
    path, number = _spot(line)
    return f"  uncovered {path}:{number}"


def _overridden_line(v: GateViolation, dirty: bool) -> str:
    return f"  OVERRIDDEN  {v.path}:{v.start}  {v.long_name}"


def _claimed_lines(names: Sequence[UnreadableName], flags: Sequence[bool]) -> list[str]:
    """The sentence the scan refuses claimed names with: the first one named,
    the rest counted."""
    from .universe import claimed_text

    if not names:
        return []
    return [f"crapkit: {claimed_text([(n.path, n.scope) for n in names])}"]


def _shown_path(name: UnreadableName) -> str:
    from .gitpaths import shown

    return shown(name.path)


def _own_flags(verdict: Verdict, entries: Sequence) -> list[bool]:
    """Each entry's own `dirty` flag."""
    return [entry.dirty for entry in entries]


def _failure_flags(verdict: Verdict, test_ids: Sequence[str]) -> list[bool]:
    """A new failure is dirty when its test file has uncommitted edits."""
    dirty = set(verdict.dirty_failures)
    return [test_id in dirty for test_id in test_ids]


def _records(entries: Sequence) -> list[dict]:
    return [entry._asdict() for entry in entries]


# How many uncovered changed lines verify lists; `diff_uncovered_count` counts them all.
LISTED_LINES = 50


def _line_items(lines: Sequence) -> list[dict]:
    """The first 50 uncovered changed lines."""
    return [_line_item(line) for line in lines[:LISTED_LINES]]


def _line_item(line) -> dict:
    path, number = _spot(line)
    return {"path": path, "line": number}


def _own_fields(entry) -> dict:
    """An entry's fields as its findings item carries them: `dirty` is among
    the fields every item carries."""
    return {name: value for name, value in entry._asdict().items() if name != "dirty"}


def _name_item(name: UnreadableName) -> dict:
    return {"path": _shown_path(name), "scope": name.scope, "reason": _claimed_message(name)}


def _test_item(test_id: str) -> dict:
    return {"test": test_id}


def _file_start(entry) -> tuple[str, int]:
    """Line 1 anchors a finding about a whole file: no function was read, or
    the ratchet stores no line."""
    return entry.path, 1


def _gate_start(v: GateViolation) -> tuple[str, int]:
    return v.path, v.start


def _gate_message(v: GateViolation) -> str:
    return f"{v.long_name}: CRAP {v.crap:.1f} (ccn {v.ccn}, cov {v.cov:.0%}) -> {v.remedy}"


def _unread_message(u: Unread) -> str:
    return f"no reader could read this file, so the gate judged none of its functions: {u.reason}"


def _rise(r: RatchetRegression) -> str:
    return f"{r.long_name}: recorded {r.recorded} -> fresh {r.fresh_crap}"


def _uncovered_message(line) -> str:
    return "changed line has no coverage: no lane ran it"


def _claimed_message(name: UnreadableName) -> str:
    """The sentence the scan refuses this one name with."""
    from .universe import claimed_text

    return claimed_text([(name.path, name.scope)])


def _unread_cause(u: Unread) -> str:
    return f"{u.path}: {u.reason}"


def _regression_cause(r: RatchetRegression) -> str:
    return f"{r.path} {r.long_name} {r.recorded} -> {r.fresh_crap}"


# The escape for a regression is the only one there is: verify never raises a
# mark, and the override path cannot reach a marked function without also seeing
# its regression (docs/ratchet.md, Overrides and the audit trail). An unread file
# holds no function to record as debt, and granting the functions beside it would
# sign debt while the gate still refuses the file. A new failure is not debt a
# mark can carry. A breached diff-coverage ceiling is never granted and refuses
# nothing: the gate violations beside it are granted, and the verdict fails on 9.
FINDING_KINDS: tuple[FindingKind, ...] = (
    FindingKind(kind="unreadable_name", field="claimed_names", exit=3, granted=False,
                refusal=Refusal(3, "unreadable name", _shown_path,
                                "rename it (git mv) to a UTF-8 name"),
                dirty=_own_flags, text=_claimed_lines, stream="stderr", json_key=None,
                json=_records,
                sarif=Sarif("crapkit/unreadable-name", "error", _file_start, _claimed_message),
                rule="unreadable name", item=_name_item),
    FindingKind(kind="gate_violation", field="gate_violations", exit=6, granted=True,
                refusal=None, dirty=_own_flags, text=each(gate_line), stream="stdout",
                json_key="gate_violations", json=_records,
                sarif=Sarif("crapkit/gate", "error", _gate_start, _gate_message),
                rule="complexity gate", item=_own_fields),
    FindingKind(kind="unread_file", field="unread_files", exit=6, granted=False,
                refusal=Refusal(2, "unread file", _unread_cause, UNREAD_ADVICE),
                dirty=_own_flags, text=each(_unread_file_line), stream="stdout",
                json_key="unread_files", json=_records,
                sarif=Sarif("crapkit/unread", "error", _file_start, _unread_message),
                rule="complexity gate", item=_own_fields),
    FindingKind(kind="ratchet_regression", field="ratchet_regressions", exit=7, granted=False,
                refusal=Refusal(0, "ratchet regression", _regression_cause,
                                "raise the mark by hand and commit it"),
                dirty=_own_flags, text=each(_ratchet_line), stream="stdout",
                json_key="ratchet_regressions", json=_records,
                sarif=Sarif("crapkit/ratchet-regression", "error", _file_start, _rise),
                rule="ratchet regressions", item=_own_fields),
    FindingKind(kind="new_failure", field="new_failures", exit=8, granted=False,
                refusal=Refusal(1, "new test failure", str, "fix the failing test first"),
                dirty=_failure_flags, text=each(_failure_line), stream="stdout",
                json_key="new_failures", json=list, sarif=None,
                rule="new test failures", item=_test_item),
    # Present only past diff_uncovered_max; its JSON list, its findings items
    # and its SARIF results come from every uncovered changed line (`uncovered`
    # below), breach or not.
    FindingKind(kind="diff_uncovered", field="uncovered_violations", exit=9, granted=False,
                refusal=None, dirty=_own_flags, text=each(_uncovered_line), stream="stderr",
                json_key="diff_uncovered", json=_line_items,
                sarif=Sarif("crapkit/diff-uncovered", "warning", _spot, _uncovered_message),
                rule="diff-coverage ceiling", item=_line_item, cap=LISTED_LINES),
    FindingKind(kind="overridden", field="overridden", exit=None, granted=False, refusal=None,
                dirty=_own_flags, text=each(_overridden_line), stream="stdout",
                json_key="overridden", json=_records, sarif=None,
                rule="override", item=_own_fields),
)


def kind(name: str) -> FindingKind:
    return next(row for row in FINDING_KINDS if row.kind == name)


def _entries(verdict: Verdict, row: FindingKind) -> Sequence:
    return getattr(verdict, row.field)


def exit_code(verdict: Verdict) -> int:
    """README's exit table: the first row present that fails decides; 0 when none does."""
    return next((row.exit for row in FINDING_KINDS if row.fails and _entries(verdict, row)), 0)


def dirty_counts(verdict: Verdict) -> tuple[int, int]:
    """(committed, dirty) over every finding that fails the verdict, so one line
    says how much of a verdict belongs to the tree as committed and how much to
    somebody's edits."""
    flags = [flag for row in FINDING_KINDS if row.fails
             for flag in row.dirty(verdict, _entries(verdict, row))]
    dirty = sum(flags)
    return len(flags) - dirty, dirty


def text_lines(verdict: Verdict, stream: str = "stdout") -> list[str]:
    """The lines verify prints for its findings on `stream`, kind by kind."""
    return [line for row in FINDING_KINDS if row.stream == stream
            for line in _lines(verdict, row)]


def _lines(verdict: Verdict, row: FindingKind) -> list[str]:
    entries = _entries(verdict, row)
    return row.text(entries, row.dirty(verdict, entries))


def lines_of(name: str, entries: Sequence) -> list[str]:
    """One kind's lines for entries that carry no dirty flag."""
    return kind(name).text(entries, [False] * len(entries))


def _refusing(verdict: Verdict) -> list[tuple[Refusal, Sequence]]:
    found = [(row.refusal, _entries(verdict, row)) for row in FINDING_KINDS if row.refusal]
    return sorted(((refusal, entries) for refusal, entries in found if entries),
                  key=lambda pair: pair[0].place)


def _cause(refusal: Refusal, entries: Sequence) -> str:
    """`1 ratchet regression (app/m.py pick( a ) 10.75 -> 20.0)`: the count, and
    the first one named so the line stands on its own in a CI log."""
    plural = "" if len(entries) == 1 else "s"
    return f"{len(entries)} {refusal.noun}{plural} ({refusal.first(entries[0])})"


def override_refusal(verdict: Verdict) -> str | None:
    """Why an --override grants nothing on this verdict, or None when no kind
    present refuses one. Every cause on one line, each with its own escape: a
    run holding a regression and a new failure is refused once, not twice."""
    refusing = _refusing(verdict)
    if not refusing:
        return None
    verb = "qualifies" if sum(len(entries) for _, entries in refusing) == 1 else "qualify"
    causes = " and ".join(_cause(refusal, entries) for refusal, entries in refusing)
    escapes = "; ".join(refusal.escape for refusal, _ in refusing)
    return f"override refused: {causes} never {verb} for an override; {escapes}"


def override_grants(verdict: Verdict) -> tuple:
    """What an --override grants on this verdict: every entry of the kinds it
    may grant, or nothing while a kind that refuses an override is present."""
    if _refusing(verdict):
        return ()
    return tuple(entry for row in FINDING_KINDS if row.granted for entry in _entries(verdict, row))


def grant(verdict: Verdict) -> Verdict:
    """The verdict once an override granted what `override_grants` names: those
    entries move to `overridden`, and the rest decides the verdict."""
    granted = override_grants(verdict)
    if not granted:
        return verdict
    cleared = {row.field: [] for row in FINDING_KINDS if row.granted}
    return settle_verdict(verdict._replace(**cleared, overridden=verdict.overridden + granted))


def _reported(verdict: Verdict, uncovered: Sequence | None) -> Verdict:
    """The verdict as its reports read it: diff_uncovered holds every changed
    line no lane ran, breach or not, when `uncovered` lists them."""
    if uncovered is None:
        return verdict
    return verdict._replace(uncovered_violations=tuple(uncovered))


def json_lists(verdict: Verdict, uncovered: Sequence | None = None) -> dict[str, list]:
    """Each kind's list in `verify --json`, under its 0.8.1 key."""
    reported = _reported(verdict, uncovered)
    return {row.json_key: row.json(_entries(reported, row))
            for row in FINDING_KINDS if row.json_key}


def finding_items(verdict: Verdict, uncovered: Sequence | None = None,
                  dirty_paths: Collection[str] = ()) -> list[dict]:
    """`verify --json`'s findings: one item per entry of each kind, the kinds in
    exit order and each kind's entries in the order its 0.8.1 list gives them.
    An item fails when its kind fails and the verdict holds the kind, so the
    lines `uncovered` lists fail only past the ceiling, and a failing item
    names the exit code its kind fires; `dirty_paths` flags those lines."""
    reported = _reported(verdict, None if uncovered is None else flagged_lines(uncovered, dirty_paths))
    return [item for row in FINDING_KINDS for item in _items(row, verdict, reported)]


def _items(row: FindingKind, verdict: Verdict, reported: Verdict) -> list[dict]:
    entries = _entries(reported, row)[:row.cap]
    fails = row.fails and bool(_entries(verdict, row))
    common = {"kind": row.kind, "fails": fails, "exit_code": row.exit if fails else None,
              "overridable": row.granted, "rule": row.rule}
    return [{**common, "dirty": dirty, **row.item(entry)}
            for entry, dirty in zip(entries, row.dirty(reported, entries))]


def sarif_results(verdict: Verdict, uncovered: Sequence | None = None) -> list[dict]:
    """One SARIF result per entry of each kind that has a SARIF form, kind by kind."""
    from .sarif import finding_result

    reported = _reported(verdict, uncovered)
    return [finding_result(row.sarif, entry) for row in FINDING_KINDS if row.sarif
            for entry in _entries(reported, row)]


def touched_rows(rows: list[ScoredRow],
                 changed_ranges: dict[str, list[tuple[int, int]]]) -> list[ScoredRow]:
    """The gate's selection without its policy: rows whose span a change overlaps.

    Every gate in crapkit judges touched functions only — untouched debt is the
    ratchet's business. `rescore --gate` reuses this so its verdict and the
    pre-commit hook's cannot disagree about which functions were even in scope.
    The touch rule is the gate module's.
    """
    return [r for r in rows if touches(r, changed_ranges.get(r.path, ()))]


def _ceiling(row: ScoredRow, target: int, scope_targets: dict[str, int] | None) -> int:
    return (scope_targets or {}).get(row.scope, target)


def _function(row: ScoredRow) -> Function:
    """A scored row as the gate judges it: verify knows its exact CRAP."""
    return Function(row.long_name, row.start, row.end, CrapBound(row.crap, row.crap), row.scope,
                    row.occurrence, row)


def _changed_functions(fresh: list[ScoredRow],
                       changed_ranges: dict[str, list[tuple[int, int]]]) -> list[ChangedFile]:
    """Each changed file with every row the run scored in it, so the gate keys
    each function over the whole file."""
    functions: dict[str, list[Function]] = {}
    for row in fresh:
        if row.path in changed_ranges:
            functions.setdefault(row.path, []).append(_function(row))
    return [ChangedFile(path, changed_ranges[path], tuple(found)) for path, found in functions.items()]


def _unread_files(unread: Mapping[str, str], changed_ranges: dict[str, list[tuple[int, int]]],
                  dirty: set[str]) -> list[ChangedFile]:
    """Each file no reader could read: taken whole when the change or an
    uncommitted edit touched it, which the gate refuses, else with no span."""
    return [ChangedFile(path, WHOLE if path in changed_ranges or path in dirty else (),
                        Unread(path, why, path in dirty)) for path, why in sorted(unread.items())]


def _judged(fresh, changed_ranges, unread, dirty, ratchet, target, scope_targets) -> GateResult:
    """The gate module's findings on the change: the marks are read only when a
    touched function is over its ceiling."""
    targets = scope_targets or {}
    return judge(_changed_functions(fresh, changed_ranges) + _unread_files(unread, changed_ranges, dirty),
                 lambda scope: targets.get(scope, target), lambda: MarkIndex(ratchet))


def _gate_violations(judged: GateResult, fresh: list[ScoredRow], dirty: set[str]) -> list[GateViolation]:
    """The gate's findings verify fails on, worst first: every touched function
    over its ceiling that no mark pardons. Above its mark the gate fires, and
    the ratchet check reports the rise beside it (#29). verify knows each exact
    CRAP, so no finding is unproven.

    The gate lists its findings kind by kind, so they go back into the order
    the run listed their rows before the sort: two functions on one start line
    with one CRAP list in row order, whichever is the marked rise."""
    place = {id(row): n for n, row in enumerate(fresh)}
    breaches = sorted((*judged.over_ceiling, *judged.marked_rise, *judged.unproven),
                      key=lambda breach: place[id(breach.function.record)])
    gate = [_violation(breach, dirty) for breach in breaches]
    gate.sort(key=_worst_first)
    return gate


def _violation(breach, dirty: set[str]) -> GateViolation:
    r = breach.function.record
    return GateViolation(r.path, r.long_name, r.start, r.ccn, r.cov, r.crap, r.remedy,
                         r.path in dirty, breach.key_name)


def _worst_first(row: ScoredRow | GateViolation) -> tuple:
    """Highest CRAP first, compared at the 4 places a mark holds, then path and
    start line. ccn 25 at 80% coverage and ccn 5 at none both score 30, which
    the floats read as 29.999999999999996 and 30.0: compared unrounded, the
    second listed first whatever the paths said."""
    return -round(row.crap, CRAP_PLACES), row.path, row.start


def _ratchet_regressions(fresh, ratchet, dirty) -> list[RatchetRegression]:
    worst_by_key = rows_by_key(fresh)
    regressions = []
    for entry in MarkIndex(ratchet).entries():
        row = worst_by_key.get(mark_key(entry))
        # Compare at the precision the mark is STORED at: marks live as 4dp
        # strings, and cov = covered/total makes longer decimals routine — an
        # unrounded compare wedges an unchanged tree against its own mark.
        if row is not None and round(row.crap, 4) > entry.crap:
            regressions.append(RatchetRegression(entry.path, entry.long_name, entry.crap,
                                                 round(row.crap, 4), entry.path in dirty))
    regressions.sort(key=_largest_rise_first)
    return regressions


def _largest_rise_first(r: RatchetRegression) -> tuple:
    """The rise is rounded to the 4 places both scores hold, so equal rises tie
    and list by path. In binary floating point 10.3 - 10.1 is 0.20000000000000107
    and 20.3 - 20.1 is 0.1999999999999993: two marks that rose by 0.2 listed by
    that noise, and the refusal line names whichever came first."""
    return -round(r.fresh_crap - r.recorded, CRAP_PLACES), r.path


def unmarked_over_ceiling(fresh: list[ScoredRow], ratchet: list[RatchetEntry], target: int,
                          scope_targets: dict[str, int] | None = None) -> list[ScoredRow]:
    """Standing debt nothing guards: the worst row per ratchet key that sits over
    its ceiling with no mark under that key, worst first.

    The gate judges touched functions only and the ratchet check compares marks
    only, so a rise on one of these (coverage loss included) reaches no finding.
    A marked function that rose is a regression already; this is the debt
    `ratchet seed` was never run on.
    """
    marks = MarkIndex(ratchet)
    rows = [row for key, row in rows_by_key(fresh).items()
            if over_ceiling(row.crap, _ceiling(row, target, scope_targets)) and key not in marks]
    rows.sort(key=_worst_first)
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
    unread: Mapping[str, str] | None = None,
) -> Verdict:
    """`unread` is {path: why} for each file no reader could read; the gate
    refuses the ones the change or an uncommitted edit touched."""
    dirty = dirty_paths or set()
    judged = _judged(fresh, changed_ranges, unread or {}, dirty, ratchet, target, scope_targets)
    regressions = _ratchet_regressions(fresh, ratchet, dirty)
    new_failures = sorted(fresh_failures - baseline_failures)

    return settle_verdict(Verdict(
        ok=True,
        gate_violations=_gate_violations(judged, fresh, dirty),
        ratchet_regressions=regressions,
        new_failures=new_failures,
        dirty_failures=dirty_failure_ids(new_failures, dirty, test_files),
        forgiven_failures=tuple(sorted(fresh_failures & baseline_failures)),
        unread_files=judged.unread,
    ))
