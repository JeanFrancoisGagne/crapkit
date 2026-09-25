"""A clean-room model of crapkit's scoring rules, written from its docs alone.

Every rule below cites the doc lines it was written from. It imports no crapkit:
exact values come from kit.exact, and the rest is read off the docs. Where the
docs say nothing, the model says what it assumed, and rulings.tsv records the
gap.

doc: README.md:22-31 sha256=58a8efc378668435998df0eec0fcc96f968802e558c0729fda608abd82ff6346
doc: README.md:817-845 sha256=37c50a8de99d5249e9b6f68956332cacd268340c56525ae17eee176a1a0bd4a6
doc: README.md:846-878 sha256=a5ad841c6db8ba40f62dca771cae0b6b472691749715b4ac5652e5eccd3ad0f1
doc: README.md:800-800 sha256=f2641c8c17668dbee44078a26ce35b9f5c91431c698439fed9d6e877733c218a
doc: README.md:804-805 sha256=86556191ce5d4702e23629a9537e7c8481e04c41d7a1c7b190db74f76bba5c12
doc: docs/agent-json.md:113-137 sha256=2b7c9002c0b78366c02c30b8f85e7b409494dc1ac234c3ae8276b805acba2277
doc: docs/agent-json.md:190-227 sha256=d36083d295330ee4109e0b2bfc627f8988117f490148d067e5750dc8b1a7c685
doc: docs/agent-json.md:478-483 sha256=263d2c6d9e4cddf07af5869c4db0f7a8c421ad4b9cb9c4abf1ecbccdba1acb4f
doc: docs/agent-json.md:618-707 sha256=a2fab3c2de01100cfde062809b6970ca7587cc4f9a793792fe572de115a033c7
doc: docs/agent-json.md:709-739 sha256=20f38e8d079c1f1c1f04db43a5300f445cdb5dc33c61f0c001366fd082e75608
doc: docs/agent-json.md:920-929 sha256=7a6a4826dc919d5470141b0675da95ca3c02a442d36f39407408fa56736c3918
doc: docs/configuration.md:72-72 sha256=34f7afec831b09edb7bd1c92940727005cd92d1d398e9a416999c6965598c567
doc: docs/configuration.md:147-147 sha256=f4f4b349d64468b5ce2fe66c8f4f3b9a5fcaff4baaa2afc81c11cbfe38597e3c
doc: docs/configuration.md:386-408 sha256=14f21583464d1e2351e295daff1fcdf9779e6570fec05b47c5d0cdbecae56d58
doc: docs/lanes.md:222-238 sha256=6cedd1513e73cb392304f96d40a511cecb365168a505079857d13ac8e3aa21f0
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

from accuracy.kit import exact

FLAGS = ("measured", "untested", "no-lane", "cc-only")


# --- coverage and CRAP (README.md:22-31, docs/lanes.md:222-238) --------------------------

def coverage_ratio(branches: tuple[int, int], statements: tuple[int, int],
                   invoked: bool) -> Fraction:
    """Branch coverage; with no branch, statement coverage; with neither, 1 when
    the function was called and 0 when it was not. Each pair is (covered, total)."""
    if branches[1] > 0:
        return exact.ratio(*branches)
    if statements[1] > 0:
        return exact.ratio(*statements)
    return Fraction(1 if invoked else 0)


def crap(ccn: int, cov: Fraction | int, flag: str = "measured") -> Fraction:
    """README.md:824-826: cc-only scores crap = ccn; the three other flags carry
    their cov into the formula (untested and no-lane at cov 0)."""
    if flag == "cc-only":
        return Fraction(ccn)
    return exact.crap(ccn, cov)


# --- remedy, grade and the budget (README.md:830-844, agent-json.md:131-135) -------------

def remedy(ccn: int, score: Fraction | float, ceiling: int, shared: bool = False) -> str:
    """The README's remedy table, read top to bottom."""
    if ccn > ceiling:
        return "decompose"
    if score <= ceiling:
        return "ok"
    return "split-lines" if shared else "add-tests"


def grade(over: int, total: int) -> str:
    return exact.grade(over, total)


def est_splits(ccn: int, ceiling: int) -> int:
    """`0` when ccn <= target, else ceil(ccn / target)."""
    return 0 if ccn <= ceiling else math.ceil(Fraction(ccn, ceiling))


def est_uncovered_paths(ccn: int, cov: Fraction) -> int:
    """round((1 - cov) * ccn), ties to even (rulings D10), on the exact value."""
    return int(exact.half_even((1 - Fraction(cov)) * ccn, 0))


# --- the ceiling (configuration.md:72, :147) ------------------------------------------------

def ceiling(scope: str, repo_target: int, scope_targets: dict[str, int | None]) -> int:
    """A scope's own `target` when it sets one, else the repo's."""
    own = scope_targets.get(scope)
    return repo_target if own is None else own


# --- the rescore overlay (README.md:800) ----------------------------------------------------

@dataclass(frozen=True)
class Fresh:
    scope: str
    path: str
    name: str
    start: int
    end: int
    occurrence: int = 1


def _same_lines(row: Fresh, other: Fresh) -> bool:
    """Another function (not the same one) declared on exactly these lines."""
    return ((other.path, other.start, other.end) == (row.path, row.start, row.end)
            and (other.name, other.occurrence) != (row.name, row.occurrence))


def _shared_span(row: Fresh, rows: list[Fresh]) -> bool:
    """Another function on the same lines, or a Python def whose body is its
    def line (README.md:835)."""
    one_line_def = row.path.endswith(".py") and row.start == row.end
    return one_line_def or any(_same_lines(row, other) for other in rows)


def overlay(row: Fresh, rows: list[Fresh], baseline: dict[tuple[str, str], list[tuple[int, float]]],
            lane_scopes: set[str]) -> tuple[float, str]:
    """Fresh complexity over the baseline's coverage, joined by name: no lane is
    no-lane; a shared span is untested at 0; a name the baseline never measured
    is untested at 0; else the baseline's cov, from the same-name row nearest
    the fresh start (ASSUMED; rulings SM-OVERLAY-TWIN)."""
    if row.scope not in lane_scopes:
        return 0.0, "no-lane"
    named = baseline.get((row.path, row.name))
    if _shared_span(row, rows) or not named:
        return 0.0, "untested"
    return min(named, key=lambda pair: abs(pair[0] - row.start))[1], "measured"


# --- totals, grade per run, coverage summary (README.md:839-844, agent-json.md:905-918) ---

@dataclass(frozen=True)
class Row:
    scope: str
    path: str
    name: str
    start: int
    ccn: int
    crap: Fraction
    flag: str = "measured"


def totals(rows: list[Row], ceiling_of) -> dict:
    """functions, over their ceiling, CRAP load (2 dp) and the grade over them."""
    over = sum(1 for row in rows if row.crap > ceiling_of(row.scope))
    load = sum((row.crap for row in rows), Fraction(0))
    return {"functions": len(rows), "over_target": over,
            "crap_load": exact.half_even(load, 2),
            "grade": grade(over, len(rows)) if rows else None}


def flag_counts(rows: list[Row]) -> dict[str, int]:
    return {flag: sum(1 for row in rows if row.flag == flag) for flag in FLAGS}


def judged(rows: list[Row], unmeasured: set[str]) -> list[Row]:
    """agent-json.md:921: a partial run leaves its unmeasured scopes out of
    over_target and the grade."""
    return [row for row in rows if row.scope not in unmeasured]


# --- worklist admission and ranking (README.md:846-878, agent-json.md:618-707) -------------

def hot_weight(weights: list[float]) -> float | None:
    """The weight at the top 10%: promotion is off when every weight is equal.
    ASSUMED (the docs are silent): the lower 90th percentile of the positive
    weights, and off below five files (rulings SM-HOT-MIN)."""
    positive = sorted(weight for weight in weights if weight > 0)
    if len(positive) < 5 or positive[0] == positive[-1]:
        return None
    return positive[math.floor(Fraction(9, 10) * (len(positive) - 1))]


def admitted(ccn: int, weight: float, over_ceiling: bool, floor: int,
             hot: float | None) -> bool:
    """Over its ceiling at any ccn; hot simple code down to ccn 3; else the floor."""
    if over_ceiling:
        return True
    if hot is not None and weight >= hot and ccn >= 3:
        return True
    return ccn >= floor


def lowest_debt_ccn(ceiling_value: int) -> int:
    """The smallest ccn whose worst CRAP (cov 0: ccn^2 + ccn) passes the ceiling,
    in closed form: the largest n with n(n + 1) <= c is (isqrt(4c + 1) - 1) // 2."""
    return (math.isqrt(4 * ceiling_value + 1) - 1) // 2 + 1


def risk(ccn: int, weight: float) -> Fraction:
    """agent-json.md:696: ccn * weight, rounded to four decimals."""
    return Fraction(exact.half_even(ccn * Fraction(weight), 4))


@dataclass(frozen=True)
class Entry:
    path: str
    start: int
    occurrence: int
    ccn: int
    commits: int
    weight: float


def rank_key(entry: Entry) -> tuple:
    """Risk first, then ccn, then commits (README.md:858-866 ranks bucket at 5.4
    over curve at 4.5), then the file and its position as the stable tail.
    ASSUMED past commits: path, start, occurrence (rulings SM-RANK-TAIL)."""
    return (-risk(entry.ccn, entry.weight), -entry.ccn, -entry.commits, entry.path,
            entry.start, entry.occurrence)


def split_active(entries: list[Entry]) -> tuple[list[Entry], list[Entry]]:
    """Active: files with commits in the window; dormant: zero churn."""
    active = sorted((e for e in entries if e.commits > 0), key=rank_key)
    dormant = sorted((e for e in entries if e.commits == 0), key=rank_key)
    return active, dormant


# --- next-item (agent-json.md:113-227) -----------------------------------------------------

def queue_takes(flag: str, remedy_now: str, has_churn: bool, admits: bool) -> bool:
    """A row next-item ranks: never no-lane; over its ceiling at any churn
    (agent-json.md:210: an over-target row is queued whatever its ccn); else it
    needs churn in the window and the admission rule."""
    if flag == "no-lane":
        return False
    return admits and (has_churn or remedy_now != "ok")


def skip_bucket(flag: str, excluded: bool, taken: bool, has_churn: bool) -> str | None:
    """agent-json.md:208-216: the reasons bucket of a row the queue did not take."""
    if flag == "no-lane":
        return "no_lane"
    if excluded:
        return "excluded_by_flag"
    if taken:
        return None
    return "below_floor" if has_churn else "no_churn_in_window"


def next_item_order(rows: list[Row], commits: dict[str, int]) -> list[Row]:
    """The queue by crap descending. ASSUMED ties: more commits first, then path
    and start (rulings SM-NEXT-TAIL)."""
    return sorted(rows, key=lambda row: (-row.crap, -commits.get(row.path, 0), row.path,
                                         row.start))


# --- batches (agent-json.md:709-740) -------------------------------------------------------

def _root(parent: dict[str, str], name: str) -> str:
    while parent[name] != name:
        name = parent[name]
    return name


def _strong(pairs, threshold: float, files: set[str]) -> list[tuple[str, str]]:
    return [(a, b) for a, b, confidence in pairs if confidence >= threshold and {a, b} <= files]


def _joined(files: list[str], pairs, threshold: float) -> dict[str, str]:
    parent = {name: name for name in files}
    for a, b in _strong(pairs, threshold, set(files)):
        parent[_root(parent, a)] = _root(parent, b)
    return parent


def groups(files: list[str], pairs: list[tuple[str, str, float]],
           threshold: float = 0.5) -> list[frozenset[str]]:
    """Files joined by any coupled pair at or over the threshold, transitively.
    ASSUMED threshold 0.5 (the docs say only 'co-changing'; rulings SM-BATCH-50)."""
    parent = _joined(files, pairs, threshold)
    found: dict[str, set[str]] = {}
    for name in files:
        found.setdefault(_root(parent, name), set()).add(name)
    return sorted((frozenset(group) for group in found.values()), key=sorted)


# --- regrowth (agent-json.md:478-483) --------------------------------------------------------

def regrown(ccns: list[int]) -> bool:
    """ccn fell between two runs and rose again at any later point."""
    fell_at = next((i for i in range(1, len(ccns)) if ccns[i] < ccns[i - 1]), None)
    if fell_at is None:
        return False
    return any(ccns[i] > ccns[i - 1] for i in range(fell_at + 1, len(ccns)))


# --- digest (README.md:805) ------------------------------------------------------------------

def comparable_pair(lane_sets: list[frozenset[str]]) -> tuple[int, int] | None:
    """The two newest runs with identical lane sets, as indexes, newest last.
    ASSUMED: the newest run pairs with its newest earlier twin first."""
    for newer in range(len(lane_sets) - 1, 0, -1):
        for older in range(newer - 1, -1, -1):
            if lane_sets[older] == lane_sets[newer]:
                return older, newer
    return None


# --- doctor --tune (configuration.md:386-405) ------------------------------------------------

def lane_slots_ok(slots: int, lanes: int, shared: bool) -> bool:
    """Never more slots than lanes, at least one, and one while lanes share data."""
    return 1 <= slots <= max(1, lanes) and (not shared or slots == 1)
