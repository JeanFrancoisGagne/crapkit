"""The verdict side of crapkit, written from its docs and nothing else.

Every rule here restates a cited doc range; the header below pins each range by
sha256, so a docs edit that moves or rewrites one fails the kit contract until
this model is read against the new text again. No crapkit import: the tests
compare what crapkit prints with what these functions say it should print.

doc: README.md:879-937 sha256=1e711df8b2917237faf8b8733526e1eb1e18f3921e89ffd7b246677e6119784d
doc: README.md:939-966 sha256=c4b6125a459c4756ce5538ef5aa5cc4942ce4f3895a68fe6f32c6c8e43ca8941
doc: README.md:802-802 sha256=b468eff446265c0eeee5557ec042e40db30799e459c6011f389a9ce89ac9e1fc
doc: CONTEXT.md:26-33 sha256=f7baa578e3f82045a5588991a9c2a408ee7995faa7f12e682342b60295ef2987
doc: CONTEXT.md:58-113 sha256=6201311b450224dbd1988161248f567e6bebe17dc8ba767ef6600a1c04f2c9d3
doc: docs/ratchet.md:20-59 sha256=646283cb0999c9117af7ea2059fc95d4a36d98e470e56a17ef2fadc827c90683
doc: docs/ratchet.md:87-111 sha256=db1ea102ffed676e6466cd9b27ec3bb913919b502b87613ea06d284af02536d9
doc: docs/ratchet.md:156-212 sha256=589d1d1b2f0e3e41e3aaf1f8f98a80f549fac26b44c3c8fea0518e2acd4ed78b
doc: docs/ratchet.md:296-331 sha256=fa4437223445559ec9104f7516ce66295f9924389029beb4321809fbb8ba02b8
doc: docs/ratchet.md:405-424 sha256=ba19474ea6c621b62c620d46c5d5813683a1fb0432ad7454a9de9a199f218cbe
doc: docs/ratchet.md:485-537 sha256=29905e13557ab764061536c724a34ee9373587253d4ded117f612c33d7c9c8d0
doc: docs/ratchet.md:626-713 sha256=b66be4b259912c338e9bbabc0fe06c76610ac13ae3c92430899001eccf99ba82
doc: docs/ratchet.md:715-775 sha256=d80dce38a923b4bd4d1fb02bb8436588645d1ba671554250d9a0c3a44802e9b9
doc: docs/ratchet.md:812-814 sha256=0b0353f91de49806c816e6fbb56a8ce38494cd5d093a357a4feb9420e0c9250e
doc: docs/agent-json.md:229-247 sha256=184d66636aa2e0f750b4372ca14c787c9f83f6d817dc4abb18dd669c4f95b683
doc: docs/agent-json.md:795-812 sha256=ff22f69752a742f839d4e2edf54e985f3eab9cf30fce8a3ffd1b44978c1f7118
doc: docs/configuration.md:90-90 sha256=e6dc43339c7ab96208524957553d2995e11fc3bd1349d4f7d3b7aa6647f33d5f
doc: docs/lanes.md:1204-1239 sha256=54a0b640914153ce29428d4cd0cad50290962be3cb820f307713cc760688a978
doc: docs/lanes.md:1352-1365 sha256=16be62f9ef850d1a1a56d4146b97b184f327b4800e25ff968caf555aeef2c13f
doc: docs/ratchet.md:832-836 sha256=d595fbb2cd6d8f4d0597fa6fab845041af6c1da7ddb2aaf38e4f7884ca2bf974
doc: docs/agent-json.md:492-512 sha256=64d6cb9b71322533826e0516f0eb3a3646b001c9636575730dc41313cfd76acf
doc: docs/portable-records.md:9-24 sha256=e4e06a93b5a1fd4569a93b1673493be0fcdd6e7eb3b04c0d0bc507b5dd3867c9
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from itertools import chain

from accuracy.kit import exact

# --- runs and the trusted baseline (README.md:879-937, CONTEXT.md:58-73) -------------------

COVERAGE, VERIFY, INVENTORY, HOOK, PARTIAL = "coverage", "verify", "inventory", "hook", "partial"


@dataclass(frozen=True)
class Run:
    """One stored run. `ok` is a verify's verdict (None for a verify with no
    verdict and for every other kind)."""
    id: int
    kind: str
    commit: str = ""
    ok: bool | None = None
    metric: str = ""
    scores: dict = field(default_factory=dict, compare=False)


def refusal(run: Run) -> str | None:
    """Why a run cannot serve as a baseline, in agent-json's words, or None.
    README: 'A coverage run, or a verify that passed' qualify."""
    if run.kind == VERIFY:
        return {True: None, False: "a failed verify", None: "a verify with no verdict"}[run.ok]
    return {COVERAGE: None, INVENTORY: "an inventory run", HOOK: "a hook run",
            PARTIAL: "a partial run"}[run.kind]


def trusted(run: Run) -> bool:
    return refusal(run) is None


def _failed(run: Run) -> bool:
    return run.kind == VERIFY and run.ok is False


def _passed(run: Run) -> bool:
    return run.kind == VERIFY and run.ok is True


def standing_failure(runs: list[Run]) -> Run | None:
    """The oldest failed verify no passing verify has cleared since: the taint
    rule's anchor. Runs are in id order."""
    anchor = None
    for run in runs:
        if _failed(run) and anchor is None:
            anchor = run
        elif _passed(run):
            anchor = None
    return anchor


def _in_front(runs: list[Run], anchor: Run | None) -> list[Run]:
    return runs if anchor is None else [run for run in runs if run.id < anchor.id]


def baseline(runs: list[Run]) -> Run | None:
    """verify's pick: the newest trusted run in front of any standing failure."""
    candidates = [run for run in _in_front(runs, standing_failure(runs)) if trusted(run)]
    return candidates[-1] if candidates else None


def named_baseline(runs: list[Run], wanted: int) -> tuple[Run | None, str | None]:
    """`--baseline ID`: the run, or why it cannot serve. It steps past the taint
    rule and nothing else (CONTEXT.md, Named baseline)."""
    found = next((run for run in runs if run.id == wanted), None)
    if found is None:
        return None, f"no run {wanted} in the store"
    reason = refusal(found)
    return (None, reason) if reason else (found, None)


# --- verdict exit (README.md:939-966) ----------------------------------------------------

EXIT_ORDER = ((6, "gate"), (7, "ratchet"), (8, "failures"), (9, "diff_uncovered"))


def exit_code(findings: frozenset) -> int:
    """verify reports the first of 6, 7, 8, 9 that fires, in that order; 0 when none."""
    return next((code for code, name in EXIT_ORDER if name in findings), 0)


# --- keys, twins and handles (docs/ratchet.md:20-59, CONTEXT.md:26-33) --------------------

@dataclass(frozen=True)
class Row:
    """One scored function as a verdict reads it."""
    path: str
    long_name: str
    start: int
    crap: Fraction
    ccn: int = 1
    occurrence: int = 0
    end: int = 0


def keys(rows: list[Row]) -> dict[Row, tuple[str, str]]:
    """Each row's ratchet key: the first of a file's same-named functions keeps
    the bare name, later ones take #2, #3 counted in file order, (start,
    occurrence) (docs/ratchet.md:832-836)."""
    out = {}
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.path, row.long_name), []).append(row)
    for (path, name), members in groups.items():
        ordered = sorted(members, key=lambda r: (r.start, r.occurrence))
        for number, row in enumerate(ordered, start=1):
            out[row] = (path, name if number == 1 else f"{name}#{number}")
    return out


def bare_name(long_name: str) -> str:
    """The leading token of the long name, before its parameter list (agent-json.md:497)."""
    return long_name.split("(")[0].strip().split(" ")[0]


def _ordinal(row: Row, rows: list[Row]) -> int:
    """1 for the first function of its long name in the file, 2 for the second..."""
    same = [r for r in rows if (r.path, r.long_name) == (row.path, row.long_name)]
    return sorted(same, key=lambda r: (r.start, r.occurrence)).index(row) + 1


def handle(row: Row, rows: list[Row]) -> str:
    """The bare identifier, NAME#N for a twin past the first, (anonymous)#N for
    a function with no name (CONTEXT.md:32-33)."""
    number, bare = _ordinal(row, rows), bare_name(row.long_name)
    if bare in ("(anonymous)", ""):
        return f"(anonymous)#{number}"
    return bare if number == 1 else f"{bare}#{number}"


def worst_twin(rows: list[Row], path: str, bare: str) -> Row:
    """A bare twin name selects the worst twin (CONTEXT.md:29)."""
    twins = [r for r in rows if r.path == path and bare_name(r.long_name) == bare]
    return max(twins, key=lambda r: r.crap)


# --- the marks file (docs/ratchet.md:20-38) ------------------------------------------------

def mark_value(crap) -> Decimal:
    """A mark is CRAP to four decimals. The docs name no tie rule; kit.exact's
    half-even of the value's exact binary expansion is the one used (ruling D5)."""
    return exact.half_even(crap, 4)


def four(value) -> str:
    return f"{mark_value(value):.4f}"


RECORD = "@crapkit-record-v1\t"
HEADER = ("path", "long_name", "crap")


@dataclass(frozen=True)
class MarksFile:
    stamp: str | None
    keys_version: str | None
    marks: dict


def _fields(line: str) -> list[str] | None:
    """A row's fields: an encoded record's JSON array, a raw row's tab split
    (a raw row starting with # is data), or None for a comment
    (docs/portable-records.md:9-24)."""
    if line.startswith(RECORD):
        import json
        return json.loads(line[len(RECORD):])
    return line.split("\t") if "\t" in line else None


def _comment(line: str, found: dict) -> None:
    text = line.removeprefix("#").strip()
    if text.startswith("crapkit-analysis="):
        found["stamp"] = text
    elif text.startswith("crapkit-keys="):
        found["keys"] = text.removeprefix("crapkit-keys=")


def parse_marks(text: str) -> MarksFile:
    """The marks file: stamp comments, a header, one `path, key, crap` row per mark.
    Physical rows split at LF with one trailing CR removed."""
    found: dict = {"marks": {}}
    for line in (raw.removesuffix("\r") for raw in text.split("\n")):
        _read_line(line, found)
    return MarksFile(found.get("stamp"), found.get("keys"), found["marks"])


def _read_line(line: str, found: dict) -> None:
    fields = _fields(line) if line else ()
    if fields is None:
        _comment(line, found)
    elif fields and tuple(fields) != HEADER:
        found["marks"][(fields[0], fields[1])] = Decimal(fields[2])


# The characters a writer encodes (docs/portable-records.md:37-39).
ENCODED = frozenset("\t\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def needs_record(fields: list[str]) -> bool:
    """A row holding an encoded character, or whose first field starts with #."""
    return fields[0].startswith("#") or any(ENCODED.intersection(field) for field in fields)


def mark_line(path: str, key: str, value) -> str | list[str]:
    """One mark's line: the raw row, or the fields an encoded record must hold
    (its JSON spelling is the writer's, so a test compares the decoded array)."""
    fields = [path, key, four(value)]
    return fields if needs_record(fields) else "\t".join(fields)


def dump_marks(marks: MarksFile) -> list:
    """The file a writer leaves: the stamp comments, the header, then one line
    per mark sorted by (path, key name), CRAP to four decimals."""
    head = [f"# {marks.stamp}"] if marks.stamp else []
    head += [f"# crapkit-keys={marks.keys_version}"] if marks.keys_version else []
    rows = [mark_line(path, key, marks.marks[(path, key)]) for path, key in sorted(marks.marks)]
    return head + ["\t".join(HEADER)] + rows


# --- the three gates and verify (docs/ratchet.md:87-111, 626-676) --------------------------

def within_mark(crap, mark: Decimal | None) -> bool:
    """rescore --gate and verify pardon a function at or under its mark,
    compared at the four decimals the mark is stored at."""
    return mark is not None and mark_value(crap) <= mark


def verify_gate(row: Row, touched: bool, ceiling: int, mark: Decimal | None) -> bool:
    """A touched function over its ceiling and past any mark it carries."""
    return touched and row.crap > ceiling and not within_mark(row.crap, mark)


def hook_gate(ccn: int, ceiling: int, marked: bool) -> bool:
    """The pre-commit hook and the advisory see a staged blob: ccn, no
    coverage. They skip a function on its mark's existence."""
    return ccn > ceiling and not marked


def regression(crap, mark: Decimal | None) -> bool:
    """A marked function whose CRAP rose above its mark, touched or not."""
    return mark is not None and mark_value(crap) > mark


def unmarked_debt(rows: list[Row], ceiling: int, marks: dict) -> int:
    """Functions over their ceiling carrying no mark: the worst row per key
    (agent-json.md: unmarked_over_target)."""
    worst = worst_per_key(rows)
    return sum(1 for key, row in worst.items() if row.crap > ceiling and key not in marks)


def worst_per_key(rows: list[Row]) -> dict:
    """The worst row under each ratchet key (two scopes can score one span twice)."""
    worst: dict = {}
    for row, key in sorted(keys(rows).items(), key=lambda item: item[0].crap):
        worst[key] = row
    return worst


# --- tighten and damping (docs/ratchet.md:626-713, configuration.md:90) --------------------

JUMP = Fraction(2)


def moved_past(before, after, jump=JUMP) -> bool:
    """configuration.md:90 calls tighten_max_jump a factor, a number >= 1: two
    measurements further apart than that ratio are the measurement talking."""
    low, high = sorted((Fraction(before), Fraction(after)))
    return low <= 0 or high / low > jump


def tighten(marks: dict, fresh: dict, ceiling: int, previous: dict | None = None,
            jump=JUMP) -> dict:
    """The marks after a green verify: at or under the ceiling drops the mark,
    over it keeps the lower of mark and fresh score, absent keeps the mark. A
    key the same commit's previous trusted run measured further than `jump`
    away is held."""
    out = {}
    for key, mark in marks.items():
        out.update(_tightened(key, mark, fresh, ceiling, previous or {}, jump))
    return out


def _tightened(key, mark, fresh, ceiling, previous, jump) -> dict:
    if key not in fresh:
        return {key: mark}
    crap = fresh[key]
    if key in previous and moved_past(previous[key], crap, jump):
        return {key: mark}
    if crap <= ceiling:
        return {}
    return {key: min(mark, mark_value(crap))}


# --- seed and prune (docs/ratchet.md:156-212, 405-424) ------------------------------------

def seed(marks: dict, fresh: dict, ceiling: int) -> tuple[dict, int, int]:
    """Mark every function over its ceiling at its score; a mark only lowers.
    Returns the marks, how many were added, how many tightened."""
    out, added, lowered = dict(marks), 0, 0
    for key, crap in fresh.items():
        if crap <= ceiling:
            continue
        value = mark_value(crap)
        if key not in out:
            out[key], added = value, added + 1
        elif value < out[key]:
            out[key], lowered = value, lowered + 1
    return out, added, lowered


def prune(marks: dict, present: set) -> dict:
    """Drop the marks whose function the run does not hold."""
    return {key: value for key, value in marks.items() if key in present}


# --- the metric stamp (docs/ratchet.md:296-331) ----------------------------------------------

STAMP_OF_WRITE = {"seed": "run", "prune": "recorded", "move": "recorded", "merge": "recorded",
                  "tighten": "running", "override": "running", "hook-override": "recorded"}


def stamp_after(write: str, recorded: str | None, running: str, run: str | None = None) -> str:
    """The stamp a write leaves. A file a recorded-stamp write creates takes
    the running metric."""
    source = STAMP_OF_WRITE[write]
    if source == "run":
        return run
    if source == "recorded":
        return recorded or running
    return running


def stamp_refused(recorded: str | None, running: str) -> bool:
    """verify refuses marks recorded under another metric; an absent stamp warns."""
    return recorded is not None and recorded != running


# --- the merge driver (docs/ratchet.md:485-537) --------------------------------------------

def merge(base: dict, ours: dict, theirs: dict) -> dict:
    """Per key, the side that changed wins over the side that did not; when both
    changed, the lower value wins, because a mark can only fall."""
    out = {}
    for key in set(base) | set(ours) | set(theirs):
        value = _merged(base.get(key), ours.get(key), theirs.get(key))
        if value is not None:
            out[key] = value
    return out


def _merged(base, ours, theirs):
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    return _lower(ours, theirs)


def _lower(ours, theirs):
    """Both sides changed the key: the lower surviving value."""
    present = [value for value in (ours, theirs) if value is not None]
    return min(present) if present else None


# --- test failures (CONTEXT.md:76-88, docs/lanes.md:1204-1239) -------------------------------

@dataclass(frozen=True)
class Failures:
    new: tuple
    forgiven: tuple
    retried: tuple


def failures(fresh: dict, baseline_failed: set, baseline_retried: set,
             passed_retry: dict) -> Failures:
    """fresh: {test id: set of lanes that failed it}. A baseline's retried
    passes never count as its failures. A new failure drops out only when every
    lane that failed it reran it and it passed (passed_retry: {id: lanes})."""
    base = baseline_failed - baseline_retried
    new = sorted(set(fresh) - base)
    retried = [test for test in new if _passed_everywhere(fresh[test], passed_retry.get(test))]
    return Failures(tuple(t for t in new if t not in retried),
                    tuple(sorted(set(fresh) & base)), tuple(retried))


def _passed_everywhere(failed_in: set, passed_in: set | None) -> bool:
    return bool(failed_in) and failed_in <= (passed_in or set())


def dirty_failures(new: list[str], dirty_paths: set[str]) -> list[str]:
    """New failures whose test id names a file with uncommitted edits, in the
    repo-path form or pytest's dotted-module form (agent-json.md, Dirty attribution)."""
    forms = set(dirty_paths) | {path.removesuffix(".py").replace("/", ".") for path in dirty_paths}
    return [test for test in new if test.split("::")[0] in forms]


def suite_dropped(before: int | None, now: int | None) -> bool:
    """coverage warns when a lane's test total fell more than 10% below the last
    trusted run's; an absent count compares nothing."""
    if before is None or now is None:
        return False
    return Fraction(before - now, 1) > Fraction(before, 10)


# --- overrides (docs/ratchet.md:715-775, 812-814) --------------------------------------------

def override_refusal(reason: str, alert: bool, regressions: int, new_failures: int) -> str | None:
    """Why `verify --override` grants nothing, or None when it may grant."""
    if not reason.strip():
        return "blank reason"
    if not alert:
        return "no alert_command"
    if regressions or new_failures:
        return "regression or new failure"
    return None


# --- retention (README.md:802) -----------------------------------------------------------------

def keep_set(runs: list[Run], keep: int, override_runs: set[int], digest_pair: set[int]) -> set[int]:
    """`runs prune --keep N` keeps the newest N trusted runs, the digest pair,
    every passing verify baseline, every run an override names, and the newest
    non-hook run. It is a floor, not a cap. "Every passing verify baseline" is
    read as every passing verify, the run kind that can serve as a baseline;
    the older reading, every run some passing verify measured against, keeps
    runs no reader picks again. The taint rule's runs are kept too (ruling V1)."""
    passing = [run.id for run in runs if _passed(run)]
    non_hook = [run.id for run in runs if run.kind != HOOK][-1:]
    return set(chain(_newest_trusted(runs, keep), digest_pair, passing, override_runs, non_hook,
                     taint_runs(runs)))


def taint_runs(runs: list[Run]) -> set[int]:
    """The runs verify's pick and its taint warning name (README.md, The taint
    rule): the baseline, the newest trusted run the rule passed over, and every
    failed verify after the baseline. README.md:802 does not list them; a prune
    that dropped them would move the baseline past the findings the rule
    protects, so they are the doc gap ruling V1 records."""
    picked = baseline(runs)
    after = runs[runs.index(picked) + 1:] if picked else runs
    named = chain([picked] if picked else [], list(filter(trusted, after))[-1:], filter(_failed, after))
    return {run.id for run in named}


def _newest_trusted(runs: list[Run], keep: int) -> list[int]:
    ids = [run.id for run in runs if trusted(run)]
    return ids[len(ids) - keep:] if keep > 0 else []


# --- claims (agent-json.md:229-247) ----------------------------------------------------------

def claim_closes(crap, ceiling: int, commit_in_history: bool) -> bool:
    """verify releases a claim once the function sits at its ceiling or its
    commit leaves the history."""
    return crap <= ceiling or not commit_in_history
