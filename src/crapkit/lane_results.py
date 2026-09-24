"""Which run's record of a lane's test results a comparison reads. Pure.

A lane records `tests_total`, `tests_skipped` and `failures` only when it parsed
a junit report. It records none of them when it declares no `results_artifact`,
or when `--reuse-artifacts` found the report gone, empty, malformed or saying
the run never finished. That absence is "not recorded", never 0 tests or no
failures: reading it as either reported a lane that ran nothing as every test
short, forgave nothing a baseline had already failed, and let a trusted run
with no count hide the next run's drop.

So every comparison asks here which run holds the lane's record: the run it
compares against when that run has one, else the newest run behind it that
does, else none, and then it says it cannot compare.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, NamedTuple

# 0.8.0 started naming a failure that passed its flake retry under
# `retried_passes`. A verify run an older crapkit stored kept such an id in its
# `failures` and named it nowhere else, so its list cannot say which failures
# the run really carried.
RETRIED_PASSES_SINCE = (0, 8, 0)


def counted(run: dict, name: str) -> bool:
    """Did this run record how many tests lane `name` ran?"""
    return run["lanes"].get(name, {}).get("tests_total") is not None


def failures_recorded(run: dict, name: str) -> bool:
    """Does this run hold a failure list for lane `name` that a baseline can forgive from?"""
    return "failures" in run["lanes"].get(name, {}) and not retries_unrecorded(run)


def retries_unrecorded(run: dict) -> bool:
    """A verify run stored by a crapkit that kept retried passes among its failures."""
    return run.get("kind") == "verify" and _older_than(run.get("tool_versions", {}).get("crapkit"))


def _older_than(version: str | None, floor: tuple[int, ...] = RETRIED_PASSES_SINCE) -> bool:
    """A version string with no number in it is not known to be older."""
    parts = re.findall(r"\d+", version or "")[:3]
    return bool(parts) and tuple(map(int, parts)) < floor


def record_of(baseline: dict, behind: Callable[[], Iterable[dict]], name: str,
              has: Callable[[dict, str], bool]) -> dict | None:
    """The run holding lane `name`'s record: the baseline's own, else the newest
    run `behind` yields (newest first) that has one, else None."""
    if has(baseline, name):
        return baseline
    return next((run for run in behind() if has(run, name)), None)


def lane_failures(run: dict, name: str) -> set[str]:
    """The failures a run carries for one lane. One that passed its flake retry
    is named under `retried_passes` too and is not carried: that run never
    counted it as failing."""
    prov = run["lanes"].get(name, {})
    return set(prov.get("failures", ())) - set(prov.get("retried_passes", ()))


class BaselineFailures(NamedTuple):
    """What a verdict may forgive, and where each lane's list came from.

    `borrowed` maps a lane to the older run its list was read from, because the
    baseline held none it could vouch for. `unrecorded` names the fresh lanes
    with failures that no run at or behind the baseline recorded a list for.
    """
    carried: frozenset[str]
    borrowed: dict[str, dict]
    unrecorded: tuple[str, ...]


def baseline_failures(baseline: dict, provenance: dict,
                      behind: Callable[[], Iterable[dict]] = tuple) -> BaselineFailures:
    """The failures a baseline carries, reaching behind it only for a lane with
    failures this run whose list the baseline does not hold.

    Failures are forgiven by test id whichever lane recorded them, so a lane
    renamed since the baseline still has its old failures forgiven.
    """
    found = {name: record_of(baseline, behind, name, failures_recorded)
             for name in _failing_lanes(provenance)}
    borrowed = _borrowed(found, baseline)
    carried = _carried(baseline).union(*(lane_failures(run, name) for name, run in borrowed.items()))
    return BaselineFailures(frozenset(carried), borrowed,
                            tuple(name for name, run in found.items() if run is None))


def _borrowed(found: dict[str, dict | None], baseline: dict) -> dict[str, dict]:
    """The lanes whose list came from a run older than the baseline."""
    return {name: run for name, run in found.items() if run is not None and run is not baseline}


def _carried(baseline: dict) -> set[str]:
    """Every failure the baseline's own lists carry, across its lanes."""
    return {f for name in baseline["lanes"] if failures_recorded(baseline, name)
            for f in lane_failures(baseline, name)}


def _failing_lanes(provenance: dict) -> list[str]:
    return [name for name, prov in sorted(provenance.items()) if prov.get("failures")]


def unjudged_lanes(found: BaselineFailures, provenance: dict, new_failures) -> list[str]:
    """The unrecorded lanes that hold a failure the verdict calls new: the ones
    whose new failures may predate the change under test."""
    new = set(new_failures)
    return [name for name in found.unrecorded
            if new & set(provenance.get(name, {}).get("failures", ()))]


def without_results(provenance: dict) -> list[str]:
    """Lanes whose new-failure check could not run: they recorded no failure list."""
    return sorted(name for name, prov in provenance.items() if "failures" not in prov)


def unread_junits(lanes, provenance: dict) -> list:
    """The lanes, in declaration order, that declare a `results_artifact` and
    recorded no failure list: this run reused a junit it could not read. A lane
    that ran refuses that report itself, and a lane that declares none has no
    report to read, so neither is named here."""
    unread = set(without_results(provenance))
    return [lane for lane in lanes if lane.results_artifact and lane.name in unread]


_COUNTS = ("tests_total", "tests_skipped")
_FAILURES = ("failures", "retried_passes")


def portable_results(run: dict) -> dict:
    """The test results a baseline file carries for each lane: its counts when
    the run counted the lane, and its failure list when a baseline can forgive
    from it. Nothing else of the lane's provenance travels."""
    lanes = {name: _portable_lane(run, name) for name in run["lanes"]}
    return {name: fields for name, fields in lanes.items() if fields}


def _portable_lane(run: dict, name: str) -> dict:
    prov = run["lanes"][name]
    keys = (_COUNTS if counted(run, name) else ()) + (_FAILURES if failures_recorded(run, name) else ())
    return {key: prov[key] for key in keys if key in prov}
