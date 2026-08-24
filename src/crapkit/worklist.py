"""Ranked worklist, dormant-risk list, session claims and batch splitting. Pure.

Active = churned files ranked by scored complexity (CRAP once coverage lanes
exist; min-CCN until then) with a floor so trivial functions never appear.
Dormant = the same scoring over files with zero churn in the window, kept
separate so sleeping hazards are recorded without clogging the queue.

`Admission` is the one rule behind both this list and next-item's queue. It
answers a single question — is this row a candidate? — and the ceiling half of
the answer outranks the floor: debt over its ceiling is admissible at any ccn.

Shared admission, different views. This list ranks every candidate by risk,
finished rows and no-lane rows included, so it never empties and never hides a
hazard. next-item ranks by CRAP descending, drops what no lane measures, and
reports empty once nothing it ranks has work left. Each entry carries the run's
`flag` and `remedy` so a row says which view it belongs to.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple

from .churn import FileChurn
from .snapshot import InventoryRow


HOT_MIN_CCN = 3  # hot promotion reaches no lower than this, whatever the floor


def over_target_floor(min_ceiling: int) -> int:
    """The lowest ccn that can possibly score over `min_ceiling`.

    CRAP is ccn^2 * (1 - cov)^3 + ccn, worst at cov 0 where it is ccn^2 + ccn.
    A ccn whose worst case lands at or under the smallest ceiling in the config
    can never be debt, whatever its coverage; every larger ccn can.
    """
    if min_ceiling < 1:
        raise ValueError(f"ceiling must be >= 1, got {min_ceiling}")
    ccn = 1
    while ccn * ccn + ccn <= min_ceiling:
        ccn += 1
    return ccn


def sql_floor(floor: int, min_ceiling: int | None = None) -> int:
    """The lowest ccn any admission rule here can let through.

    A reader may leave everything below this in SQLite: no rule can admit it, so
    fetching it is pure cost. `min_ceiling` is the smallest ceiling the caller
    can score a row against, and None only for a caller that has no scored run
    to read a remedy from. Passing it is load-bearing: a floor of 5 pushed into
    a read hid every ccn-4 function at 0% coverage, and those score CRAP 20
    against a target of 6. Both queues pass it — the worklist left it out, and
    its rows were gone before the admission rule ever saw them.
    """
    reach = [floor, HOT_MIN_CCN]
    if min_ceiling is not None:
        reach.append(over_target_floor(min_ceiling))
    return min(reach)


_NO_CHURN = FileChurn(commits=0, authors=0)


class Admission(NamedTuple):
    """What counts as a queue candidate, bound to one run's churn.

    The worklist and next-item both ask this object, so the two can never drift
    on the floor, on hot promotion, or on debt — which is what let the worklist
    rank a function next-item was reporting as `below_floor`.
    """
    churn: dict[str, FileChurn]
    floor: int
    hot: float | None

    def of(self, path: str) -> FileChurn:
        return self.churn.get(path, _NO_CHURN)

    def admits(self, path: str, ccn: int, *, over_target: bool) -> bool:
        """Over-target debt is work at any complexity: a ccn-4 function at 0%
        coverage scores CRAP 20 against a ceiling of 6. The floor and the hot
        promotion only ADD candidates the ceiling did not catch; neither may
        veto a function that is already over its ceiling.
        """
        if over_target:
            return True
        if self.hot is not None and self.of(path).weight >= self.hot and ccn >= HOT_MIN_CCN:
            return True  # hot simple code: the floor must not hide it
        return ccn >= self.floor


def admission(churn: dict[str, FileChurn], floor: int) -> Admission:
    return Admission(churn, floor, _hot_threshold(churn))


class WorklistEntry(NamedTuple):
    scope: str
    path: str
    long_name: str
    start: int
    end: int
    ccn: int
    ccn_std: int
    nloc: int
    commits: int
    authors: int
    weight: float
    risk: float  # ccn x recency-weighted churn: the composite hotspot rank
    # The scoring run's verdict on this row, so the risk map can say which of
    # its rows the burn-down queue will never hand out and which are finished.
    # Both None on an inventory-only run, which scored no verdict to report.
    flag: str | None = None
    remedy: str | None = None


class Worklist(NamedTuple):
    active: list[WorklistEntry]
    dormant: list[WorklistEntry]


def _entry(r: InventoryRow, churn: FileChurn, mark: tuple) -> WorklistEntry:
    return WorklistEntry(r.scope, r.path, r.long_name, r.start, r.end,
                         r.ccn, r.ccn_std, r.nloc, churn.commits, churn.authors,
                         churn.weight, round(r.ccn * churn.weight, 4), *mark)


def _rank_key(e: WorklistEntry):
    # Composite hotspot rank; equal risk (e.g. weightless churn data) falls
    # back to the old ccn-then-commits order.
    return (-e.risk, -e.ccn, -e.commits, e.path, e.start)


def _hot_threshold(churn: dict[str, FileChurn]) -> float | None:
    """The 90th-percentile file weight: candidacy promotion for hot simple code.

    Applying the ccn floor before churn hides a heavily-edited simple file
    (Tornhill's ordering). Needs real weights and enough files to mean anything.
    """
    weights = sorted(c.weight for c in churn.values() if c.weight > 0)
    if len(weights) < 5:
        return None
    return weights[int(0.9 * (len(weights) - 1))]


def _at_ceiling(scored, target: int, scope_targets: dict[str, int] | None) -> set:
    """(path, long_name) of every scored function at or under its own ceiling."""
    ceilings = scope_targets or {}
    return {(r.path, r.long_name) for r in scored
            if r.crap is not None and r.crap <= ceilings.get(r.scope, target)}


def closable_claims(claims: list[dict], scored: list, *, target: int,
                    scope_targets: dict[str, int] | None, stale_commits: set[str]) -> list[int]:
    """Claim ids a verify may release, sorted.

    Two ways to be done, and no third: the function now scores at or under its
    scope ceiling, or the commit the claim was taken on is no longer an ancestor
    of HEAD (an amend or rebase took the session's tree with it). A function the
    run never scored keeps its claim — absence is not evidence of a fix.
    """
    done = _at_ceiling(scored, target, scope_targets)
    return sorted(c["id"] for c in claims
                  if (c["path"], c["long_name"]) in done or c["commit"] in stale_commits)


_NO_MARK = (None, None)


def build_worklist(
    rows: list[InventoryRow],
    churn: dict[str, FileChurn],
    *,
    floor: int,
    top: int,
    marks: Mapping[tuple[str, str], tuple[str, str]] = MappingProxyType({}),
) -> Worklist:
    """The risk map: every admitted function, ranked, whatever its score.

    `marks` is (flag, remedy) per function the same run scored —
    `SnapshotStore.read_marks`. Inventory rows carry no coverage, so the ceiling
    half of the admission cannot be decided from `rows` alone, and an
    inventory-only run passes nothing here. The marks ride onto the entries as
    well: this list ranks rows the burn-down queue declines, and a row that says
    nothing about which it is sends an agent to work a wiring gap.
    """
    if top < 1:
        raise ValueError(f"worklist top must be >= 1, got {top}")
    adm = admission(churn, floor)
    active, dormant = [], []
    for r in rows:
        c, mark = adm.of(r.path), marks.get((r.path, r.long_name), _NO_MARK)
        if not adm.admits(r.path, r.ccn, over_target=mark[1] not in (None, "ok")):
            continue
        (active if c.commits > 0 else dormant).append(_entry(r, c, mark))
    active.sort(key=_rank_key)
    dormant.sort(key=_rank_key)
    return Worklist(active=active[:top], dormant=dormant)


BATCH_CONTAINMENT = 0.5  # half of one file's commits also touch the other
BATCH_PAIR_LIMIT = 1000  # coupled pairs read; far more than worklist_top files can bind


class Batch(NamedTuple):
    files: list[str]
    entries: list[WorklistEntry]


def _find(rep: dict[str, str], f: str) -> str:
    while rep.get(f, f) != f:
        f = rep[f]
    return f


def _merge(rep: dict[str, str], a: str, b: str) -> None:
    ra, rb = _find(rep, a), _find(rep, b)
    if ra == rb:
        return
    lo, hi = sorted((ra, rb))
    rep[hi] = lo  # the lexically first path represents the group, whatever order pairs arrive in


def _coupling_groups(pairs: list[dict]) -> dict[str, str]:
    """file -> group representative, merging every pair at or above the threshold.

    Coupling is transitive here: a-b and b-c put all three in one batch. That is
    deliberate, and it is why the threshold is high — a chain of weak links would
    drag the whole repo into a single unit.
    """
    rep: dict[str, str] = {}
    for p in pairs:
        if p["confidence"] >= BATCH_CONTAINMENT:
            _merge(rep, *p["files"])
    return rep


def _unit_key(item) -> tuple:
    key, entries = item
    return (-max(e.risk for e in entries), key)


def _units(active: list[WorklistEntry], rep: dict[str, str]) -> list[list[WorklistEntry]]:
    """Entries grouped by coupling group, heaviest first: what a batch takes whole."""
    groups: dict[str, list] = {}
    for e in active:
        groups.setdefault(_find(rep, e.path), []).append(e)
    return [entries for _, entries in sorted(groups.items(), key=_unit_key)]


def _risk(entries: list[WorklistEntry]) -> float:
    return sum(e.risk for e in entries)


def _bin_key(entries: list[WorklistEntry]) -> tuple:
    """Least risk wins; on a tie the emptiest batch does, so a worklist whose
    risks all round to zero still spreads instead of piling into the first."""
    return (_risk(entries), len(entries))


def _empty_bins(n: int) -> list[list]:
    return [[] for _ in range(n)]


def _as_batch(entries: list[WorklistEntry]) -> Batch:
    return Batch(files=sorted({e.path for e in entries}),
                 entries=sorted(entries, key=_rank_key))


def _batch_key(b: Batch) -> tuple:
    return (-_risk(b.entries), b.files)


def split_batches(active: list[WorklistEntry], pairs: list[dict], *,
                  batches: int) -> list[Batch]:
    """The active worklist cut into at most `batches` sets of whole files.

    Two agents editing one file collide in the worktree whatever the ranking
    says, so a file is indivisible and so is a coupled group of files: the batch
    holds every entry from every file in the group. Units go to the lightest
    batch in risk order, which is LPT scheduling, and the whole thing is a pure
    function of the worklist and the coupling pairs — same store, same history,
    same batches. Empty batches are dropped rather than handed to an agent.
    """
    if batches < 1:
        raise ValueError(f"batches must be >= 1, got {batches}")
    bins = _empty_bins(batches)
    for unit in _units(active, _coupling_groups(pairs)):
        min(bins, key=_bin_key).extend(unit)
    return sorted((_as_batch(b) for b in bins if b), key=_batch_key)
