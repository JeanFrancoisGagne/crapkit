"""The verdict side of crapkit, written from its docs and nothing else.

Every rule here restates a cited doc range; the header below pins each range by
sha256, so a docs edit that moves or rewrites one fails the kit contract until
this model is read against the new text again. No crapkit import: the tests
compare what crapkit prints with what these functions say it should print.

doc: README.md:1379-1471 sha256=1c2801ea923ab793b72240c35aa05bdf750c63fa0b596aacfd8e862acadfae30
doc: README.md:1473-1500 sha256=2a0ca63518c599c9ed7c425b5b85bfc1bea28854e6aed403ccc5f0f04938601f
doc: README.md:1285-1285 sha256=b468eff446265c0eeee5557ec042e40db30799e459c6011f389a9ce89ac9e1fc
doc: CONTEXT.md:30-37 sha256=f7baa578e3f82045a5588991a9c2a408ee7995faa7f12e682342b60295ef2987
doc: CONTEXT.md:83-165 sha256=4a7f8cbaaf5c52b8cb8d05b91f7255b8a6e83eb6a228fd357fb5191ac95e22e1
doc: docs/ratchet.md:20-89 sha256=c632bda254159ea7a636a8959cac9f4eda6e27f71bb32729d05aefde1eda14c2
doc: docs/ratchet.md:118-142 sha256=8bc7df912a11fc8893505b9bc766fcbd08a2c4d0cc14f911dafb66bbe139a5cb
doc: docs/ratchet.md:189-252 sha256=e4ca95739a264be11a9e79d98b2d4b7f8792124bfc6fd2ec223f371b8ec08729
doc: docs/ratchet.md:337-395 sha256=c7885067090b5f114ae7ea9ebc2911bba938f9edfee98cb20cf84862e53df96d
doc: docs/ratchet.md:485-504 sha256=8962538fcb9660a65b4db6bbabf4066b59d1dca7806438a783db076fc1a7df9b
doc: docs/ratchet.md:596-687 sha256=61b5a7c38b473dba2247a67d3974d07c9848cebd4210166c4b224c1f2726b435
doc: docs/ratchet.md:823-959 sha256=28a98789e1c287e6ae12af9136d2ccff9513a692533c37555852c93bb1c665c9
doc: docs/ratchet.md:961-1039 sha256=7a31937dfa217b07387c20a6015e185465ff73512772c72686f85b7cefd33871
doc: docs/ratchet.md:1083-1085 sha256=0b0353f91de49806c816e6fbb56a8ce38494cd5d093a357a4feb9420e0c9250e
doc: docs/agent-json.md:306-330 sha256=46e36bc36e895f6379112955a0182505cc361f45c214960ea48d2ee35003f40e
doc: docs/agent-json.md:1010-1030 sha256=4a1e66a7653f0a869438a7e5a44b8790f192c77e2d881aae3dd4a94859e1a178
doc: docs/agent-json.md:949-1008 sha256=c0a493d50f60ea4374f0683df853d58a08f6cbdee9791909f6f8bf00787d79c8
doc: docs/configuration.md:220-220 sha256=e6dc43339c7ab96208524957553d2995e11fc3bd1349d4f7d3b7aa6647f33d5f
doc: docs/lanes.md:1540-1578 sha256=964831502d735a929aaae499fd2404f9f8d39a2827e02cc7ee3ddcca513ca08e
doc: docs/lanes.md:1714-1727 sha256=c80c08b87c893b48a67c8dc8b92f1d37f947e41bee1efafb8670fe670064689c
doc: docs/ratchet.md:1103-1107 sha256=d595fbb2cd6d8f4d0597fa6fab845041af6c1da7ddb2aaf38e4f7884ca2bf974
doc: docs/agent-json.md:593-613 sha256=64d6cb9b71322533826e0516f0eb3a3646b001c9636575730dc41313cfd76acf
doc: docs/lanes.md:1215-1372 sha256=7d28889683d759dc2dc72783fd1428ceb32951db241a9b8735352e65a8fa1c9e
doc: docs/lanes.md:1374-1435 sha256=9562d770e19872b412caf2ed5a4dbf8760c219d517f71ae90efa2d54b7f5c8a0
doc: docs/lanes.md:1972-2073 sha256=6b2e585c08bcc87c7abc8d6bd58a0038fa702579d1bac4837b7de1698f70b532
doc: README.md:1321-1328 sha256=393f9980d5d30fba8ab60f9a2945e02babf0c495c8cd2f474a0c3a20c0eab943
doc: docs/portable-records.md:9-24 sha256=e4e06a93b5a1fd4569a93b1673493be0fcdd6e7eb3b04c0d0bc507b5dd3867c9
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from itertools import chain

from accuracy.kit import exact

# --- runs and the trusted baseline (README.md:901-959, CONTEXT.md:58-73) -------------------

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
    """verify's pick: the newest trusted run in front of any standing failure.
    Only runs at or behind HEAD compete (README: a run on another branch never
    serves); callers pass the runs of HEAD's own line."""
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


# --- verdict exit (README.md:961-988) ----------------------------------------------------

# README.md:1480 and 1483: a file a scope takes whose name is not UTF-8 exits 3,
# before any lane runs and so before every other finding; a changed file no reader
# could read is refused at 6 beside the gate violation.
EXIT_ORDER = ((3, "unreadable_name"), (6, "gate"), (6, "unread"), (7, "ratchet"),
              (8, "failures"), (9, "diff_uncovered"))


def exit_code(findings: frozenset) -> int:
    """verify reports the first of 6, 7, 8, 9 that fires, in that order; 0 when none.
    A claimed name that is not UTF-8 comes before all of them with 3."""
    return next((code for code, name in EXIT_ORDER if name in findings), 0)


# --- verify's findings list (docs/agent-json.md:949-1008) ---------------------------------

# The kind each finding is listed under. The kinds come in their exit order, and an
# override's grant, which fires no exit, comes last.
FINDING_KIND = {"unreadable_name": "unreadable_name", "gate": "gate_violation",
                "unread": "unread_file", "ratchet": "ratchet_regression",
                "failures": "new_failure", "diff_uncovered": "diff_uncovered"}


def findings(present: frozenset, *, uncovered_listed: bool = False,
             overridden: bool = False) -> list[tuple[str, bool, int | None]]:
    """(kind, fails, exit_code) of the item each finding in PRESENT lists, one
    finding of each, in the order verify lists them. A failing item names the exit
    its kind fires, so the first one names verify's exit. Uncovered lines under no
    ceiling (`uncovered_listed`) are listed and fail nothing, and so is a gate
    violation an override granted (`overridden`): neither names an exit."""
    items = [(FINDING_KIND[name], True, code) for code, name in EXIT_ORDER if name in present]
    if uncovered_listed and "diff_uncovered" not in present:
        items.append(("diff_uncovered", False, None))
    if overridden:
        items.append(("overridden", False, None))
    return items


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
    occurrence) (docs/ratchet.md:833-837)."""
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
    """The leading token of the long name, before its parameter list (agent-json.md:504)."""
    return long_name.split("(")[0].strip().split(" ")[0]


def _ordinal(row: Row, rows: list[Row]) -> int:
    """1 for the first function of its long name in the file, 2 for the second..."""
    same = [r for r in rows if (r.path, r.long_name) == (row.path, row.long_name)]
    return sorted(same, key=lambda r: (r.start, r.occurrence)).index(row) + 1


def _is_twin(row: Row, rows: list[Row]) -> bool:
    return sum(1 for r in rows if (r.path, r.long_name) == (row.path, row.long_name)) > 1


def handle(row: Row, rows: list[Row]) -> str:
    """The bare identifier, NAME#N for a twin (every member of the group
    numbered in file order), (anonymous)#N for a function with no name
    (CONTEXT.md:32-33). A key differs: the first twin's key keeps the bare
    long name (docs/ratchet.md:42-59)."""
    number, bare = _ordinal(row, rows), bare_name(row.long_name)
    if bare in ("(anonymous)", ""):
        return f"(anonymous)#{number}"
    return f"{bare}#{number}" if _is_twin(row, rows) else bare


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
    Physical rows split at LF with one trailing CR removed. `text` is the file
    decoded by docs/ratchet.md's rule: UTF-16 behind a UTF-16 byte-order mark,
    else UTF-8 with a UTF-8 BOM dropped, U+FFFD for a byte neither fits."""
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


# The characters a writer encodes (docs/portable-records.md:68-70).
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


# --- remedy (README.md:846-853) --------------------------------------------------------------

def remedy(ccn: int, crap, ceiling: int, shares_line: bool) -> str:
    """decompose past the ceiling on ccn; ok at or under it on CRAP; else
    split-lines when another function shares the lines or a Python def's body
    starts on its signature's last line; else add-tests."""
    if ccn > ceiling:
        return "decompose"
    if crap <= ceiling:
        return "ok"
    return "split-lines" if shares_line else "add-tests"


# --- the three gates and verify (docs/ratchet.md:88-112, 627-677) --------------------------

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


# --- tighten and damping (docs/ratchet.md:627-714, configuration.md:104) --------------------

JUMP = Fraction(2)


def moved_past(before, after, jump=JUMP) -> bool:
    """configuration.md:104 calls tighten_max_jump a factor, a number >= 1: two
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


# --- seed and prune (docs/ratchet.md:157-213, 406-425) ------------------------------------

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


def follow_renames(marks: dict, present: set, renames: dict) -> dict:
    """docs/ratchet.md:418-424: a mark moves to the path git renamed its file
    to when its function is gone from the recorded path and the same key name
    exists at the destination. `renames` maps old path to new path."""
    out = {}
    for (path, name), value in marks.items():
        moved = (renames.get(path), name)
        out[moved if (path, name) not in present and moved in present else (path, name)] = value
    return out


# --- the metric stamp (docs/ratchet.md:297-332) ----------------------------------------------

STAMP_OF_WRITE = {"seed": "run", "prune": "recorded", "move": "recorded", "merge": "recorded",
                  "tighten": "running", "override": "running", "hook-override": "recorded"}


def stamp_after(write: str, recorded: str | None, running: str, run: str | None = None) -> str:
    """The stamp a write leaves. A file a recorded-stamp write creates takes
    the running metric. Seed and prune refuse marks a newer crapkit or lizard
    recorded before they write, so no write here restamps those."""
    source = STAMP_OF_WRITE[write]
    if source == "run":
        return run
    if source == "recorded":
        return recorded or running
    return running


def stamp_refused(recorded: str | None, running: str) -> bool:
    """verify refuses marks recorded under another metric; an absent stamp warns."""
    return recorded is not None and recorded != running


# --- the merge driver (docs/ratchet.md:486-538) --------------------------------------------

def merge(base: dict, ours: dict, theirs: dict) -> dict:
    """Per key, the side that changed wins over the side that did not; when both
    changed, the lower value wins, because a mark can only fall. An add/add
    merge hands the driver an empty base, and base = {} gives the union the docs
    name: each mark both sides hold at the lower value."""
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


# --- test failures (CONTEXT.md:76-88, docs/lanes.md:1310-1345) -------------------------------

@dataclass(frozen=True)
class Failures:
    new: tuple
    forgiven: tuple
    retried: tuple


def failures(fresh: dict, baseline_failed: set, baseline_retried: set,
             passed_retry: dict) -> Failures:
    """fresh: {test id: set of lanes that failed it}. A baseline's retried
    passes never count as its failures. A new failure drops out only when every
    lane that failed it reran it and it passed (passed_retry: {id: lanes}).
    baseline_failed holds each lane's list; for a lane the baseline recorded no
    list for, the newest trusted run at or behind it that did stands in
    (CONTEXT.md, Forgiven failure), and the caller resolves that first."""
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


# --- lane reuse (docs/lanes.md:1215-1372) -------------------------------------------------------

# `--reuse-unchanged` names the first condition that failed, in the order the
# docs list them. A lane without `inputs` answers for the whole tree, the
# config, the crapkit version and the inherited environment. A lane with
# `inputs` answers for its own paths, its own lane table and the crapkit
# version, and compares trees, not history: an amend or a rebase that leaves
# the inputs alone reuses it, while a stamp commit this clone does not hold
# ("commit absent") and a git read of its inputs that failed ("inputs unread")
# rerun it. A stamp commit that is no longer behind HEAD is no condition of its
# own, and the docs list none: a lane with `inputs` compares trees, and a lane
# without them reruns on HEAD's move ("head").
REUSE_ORDER = ("no artifact", "wrote none", "no proof", "uncommitted", "head", "crapkit.toml",
               "lane table", "crapkit version", "environment", "inputs", "inputs unread",
               "commit absent", "bytes")
WITH_INPUTS = frozenset({"no artifact", "wrote none", "no proof", "lane table", "crapkit version",
                         "inputs", "inputs unread", "commit absent", "bytes"})
WHOLE_TREE = frozenset(REUSE_ORDER) - {"inputs", "inputs unread", "commit absent"}


def rerun_condition(failed: set, with_inputs: bool = False) -> str | None:
    """The condition a rerun names, or None when the lane is reused."""
    counted = WITH_INPUTS if with_inputs else WHOLE_TREE
    return next((name for name in REUSE_ORDER if name in failed and name in counted), None)


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def lines_stale(path: str, scope_paths: tuple, changed: set) -> bool:
    """A file's dark lines go null once that file, under the lane's scopes,
    holds other content than the lane's run left it with, uncommitted edits
    included; every other file keeps its lines (docs/lanes.md, Reusing
    artifacts: "Moved" is about content)."""
    return path in changed and reaches([path], scope_paths)


def reaches(paths, scope_paths: tuple) -> bool:
    """Some path sits at or under some scope path."""
    return any(_under(path, scope) for path in paths for scope in scope_paths)


def reach_verdict(paths: list[str], root: str, scope_paths: tuple) -> str:
    """What a coveragepy lane whose artifact names `paths` gets (docs/lanes.md,
    An artifact that measured a different tree; an istanbul reader rebases every
    path that resolves under the checkout, so it never reads "absolute"):
    "ok" when any path reaches a scope (zero overlap is the whole test);
    otherwise "other tree" when any path is outside the root or climbs out of
    it, "absolute" when every such path is absolute under the root, and "warn"
    for in-tree relative paths."""
    if reaches(paths, scope_paths):
        return "ok"
    if any(_outside(path, root) for path in paths):
        return "other tree"
    return "absolute" if any(map(_absolute, paths)) else "warn"


def _outside(path: str, root: str) -> bool:
    """Climbing out, or absolute and not under this checkout's root."""
    return path.startswith("../") or (_absolute(path) and not _under(path, root))


def _absolute(path: str) -> bool:
    """/x, or a drive letter and a slash (C:/x)."""
    return path.startswith("/") or path[1:3] == ":/"


# --- overrides (docs/ratchet.md:716-776, 813-815) --------------------------------------------

def override_refusal(reason: str, alert: bool, regressions: int, new_failures: int,
                     unread: int = 0) -> str | None:
    """Why `verify --override` grants nothing, or None when it may grant. A
    ratchet regression, a new test failure or an unread file in the same run
    refuses it: none of them is debt a mark can carry."""
    if not reason.strip():
        return "blank reason"
    if not alert:
        return "no alert_command"
    if regressions + new_failures + unread:
        return "regression, new failure or unread file"
    return None


# --- retention (README.md:816) -----------------------------------------------------------------

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
    failed verify after the baseline. README.md:816 does not list them; a prune
    that dropped them would move the baseline past the findings the rule
    protects, so they are the doc gap ruling V1 records."""
    picked = baseline(runs)
    after = runs[runs.index(picked) + 1:] if picked else runs
    named = chain([picked] if picked else [], list(filter(trusted, after))[-1:], filter(_failed, after))
    return {run.id for run in named}


def _newest_trusted(runs: list[Run], keep: int) -> list[int]:
    ids = [run.id for run in runs if trusted(run)]
    return ids[len(ids) - keep:] if keep > 0 else []


# --- claims (agent-json.md:236-254) ----------------------------------------------------------

def claim_closes(crap, ceiling: int, commit_in_history: bool) -> bool:
    """verify releases a claim once the function sits at its ceiling or its
    commit leaves the history."""
    return crap <= ceiling or not commit_in_history
