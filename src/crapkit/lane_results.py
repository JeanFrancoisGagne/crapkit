"""Which run's record of a lane's test results a comparison reads. Pure.

A lane records `tests_total`, `tests_skipped` and `failures` only when it parsed
a junit report. It records none of them when it declares no `results_artifact`,
or when `--reuse-artifacts` found the report gone, empty, malformed or saying
the run never finished. That absence is "not recorded", never 0 tests or no
failures: reading it as either reported a lane that ran nothing as every test
short, forgave nothing a baseline had already failed, and let a trusted run
with no count hide the next run's drop.

Every reader parses a lane's record through `read_results` into `LaneResults`,
where None is "not recorded". Every comparison asks here which run holds the
lane's record: the run it compares against when that run has one, else the
newest run `behind` it that does, else none, and then it says it cannot
compare. verify's baseline and coverage's suite drop both walk it.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, NamedTuple

# 0.8.0 started naming a failure that passed its flake retry under
# `retried_passes`. A verify run an older crapkit stored kept such an id in its
# `failures` and named it nowhere else, so its list cannot say which failures
# the run really carried.
RETRIED_PASSES_SINCE = (0, 8, 0)

SUITE_DROP_FRACTION = 0.1


class LaneResults(NamedTuple):
    """One lane's test results as a run recorded them. None is "not recorded":
    no count, or no failure list a baseline may forgive from."""
    tests: int | None
    skipped: int | None
    failures: frozenset[str] | None
    retried: frozenset[str]

    @property
    def carried(self) -> frozenset[str]:
        """The failures the run counted as failing: one that passed its flake
        retry is named under `retried_passes` too and is not carried."""
        return (self.failures or frozenset()) - self.retried


def read_results(prov: dict, *, failures_trusted: bool = True) -> LaneResults:
    """A lane's provenance as a typed record. `failures_trusted` is False for a
    run whose failure list cannot be forgiven from (the 0.7.x rule)."""
    failures = prov.get("failures") if failures_trusted else None
    return LaneResults(prov.get("tests_total"), prov.get("tests_skipped"),
                       None if failures is None else frozenset(failures),
                       frozenset(prov.get("retried_passes", ())))


def recorded_failures(provenance: dict, name: str) -> frozenset[str]:
    """The failures lane `name` recorded this run: none when it recorded no list,
    or was left out of the run."""
    return read_results(provenance.get(name, {})).failures or frozenset()


def failure_ids(provenance: dict) -> frozenset[str]:
    """Every failure any lane recorded this run."""
    return frozenset().union(*(recorded_failures(provenance, name) for name in provenance))


def lists_failures(prov: dict) -> bool:
    """Whether a lane record holds a failure list at all, trusted or not."""
    return read_results(prov).failures is not None


def results_of(run: dict, name: str) -> LaneResults:
    """Lane `name`'s record in a stored run, the 0.7.x rule applied."""
    return read_results(run["lanes"].get(name, {}), failures_trusted=not retries_unrecorded(run))


def _counted(run: dict, name: str) -> bool:
    return results_of(run, name).tests is not None


def _failures_recorded(run: dict, name: str) -> bool:
    return results_of(run, name).failures is not None


def retries_unrecorded(run: dict) -> bool:
    """A verify run stored by a crapkit that kept retried passes among its failures."""
    return run.get("kind") == "verify" and _older_than(run.get("tool_versions", {}).get("crapkit"))


def _older_than(version: str | None, floor: tuple[int, ...] = RETRIED_PASSES_SINCE) -> bool:
    """A version string with no number in it is not known to be older."""
    parts = re.findall(r"\d+", version or "")[:3]
    return bool(parts) and tuple(map(int, parts)) < floor


def _record_of(baseline: dict, behind: Callable[[], Iterable[dict]], name: str,
               has: Callable[[dict, str], bool]) -> dict | None:
    """The run holding lane `name`'s record: the baseline's own, else the newest
    run `behind` yields (newest first) that has one, else None."""
    if has(baseline, name):
        return baseline
    return _newest(behind(), name, has)


def _newest(runs: Iterable[dict], name: str, has: Callable[[dict, str], bool]) -> dict | None:
    return next((run for run in runs if has(run, name)), None)


def counted_record(baseline: dict, behind: Callable[[], Iterable[dict]], name: str) -> dict | None:
    """The run whose test count for lane `name` a suite-size comparison reads:
    the baseline's, else the newest run behind it that counted the lane."""
    return _record_of(baseline, behind, name, _counted)


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
    found = {name: _record_of(baseline, behind, name, _failures_recorded)
             for name in _failing_lanes(provenance)}
    borrowed = _borrowed(found, baseline)
    carried = _carried(baseline).union(*(results_of(run, name).carried
                                         for name, run in borrowed.items()))
    return BaselineFailures(frozenset(carried), borrowed,
                            tuple(name for name, run in found.items() if run is None))


def _borrowed(found: dict[str, dict | None], baseline: dict) -> dict[str, dict]:
    """The lanes whose list came from a run older than the baseline."""
    return {name: run for name, run in found.items() if run is not None and run is not baseline}


def _carried(baseline: dict) -> set[str]:
    """Every failure the baseline's own lists carry, across its lanes."""
    return set().union(*(results_of(baseline, name).carried for name in baseline["lanes"]))


def _failing_lanes(provenance: dict) -> list[str]:
    return [name for name, prov in sorted(provenance.items()) if read_results(prov).failures]


def unjudged_lanes(found: BaselineFailures, provenance: dict, new_failures) -> list[str]:
    """The unrecorded lanes that hold a failure the verdict calls new: the ones
    whose new failures may predate the change under test."""
    new = set(new_failures)
    return [name for name in found.unrecorded if new & recorded_failures(provenance, name)]


def without_results(provenance: dict) -> list[str]:
    """Lanes whose new-failure check could not run: they recorded no failure list."""
    return sorted(name for name, prov in provenance.items() if not lists_failures(prov))


def suite_drops(behind: Callable[[], Iterable[dict]], current: dict, *,
                fraction: float = SUITE_DROP_FRACTION) -> list[str]:
    """Lanes whose junit counted far fewer tests than the newest run `behind`
    (newest first) that counted them.

    The cheap half of the crashed-worker check, for the runner that dies without
    writing the crash into its own report: then the count is the only signature
    left. The lane this came from wrote 10,674 of 15,300 collected tests after
    one xdist worker died, and reported success.

    A tenth is wide enough that deleting a test file does not cry wolf, and
    narrow enough that a dead worker's whole queue cannot hide under it.

    A lane with no count on either side compares nothing. This run's lane has
    none when it declares no `results_artifact` or `--reuse-artifacts` could not
    read one, and the reuse warning already says so; reading that absence as
    zero once reported a lane that ran nothing as every test short. A run that
    counted nothing for a lane is passed over, not compared with: as the only
    comparison point it left the run after it free to lose any number of tests.
    `behind` is read only when some lane counted tests this run.
    """
    counted_now = _counts(current)
    runs = list(behind()) if counted_now else []
    notes = (_drop_note(name, tests, runs, fraction) for name, tests in counted_now.items())
    return [note for note in notes if note]


def _counts(current: dict) -> dict[str, int]:
    """{lane: its test count} for the lanes this run counted, in name order."""
    tests = {name: read_results(prov).tests for name, prov in sorted(current.items())}
    return {name: count for name, count in tests.items() if count is not None}


def _drop_note(name: str, now: int, runs: list[dict], fraction: float) -> str | None:
    """The warning for lane `name`, when its count fell past `fraction` of the
    newest count in `runs` (newest first)."""
    source = _newest(runs, name, _counted)
    before = results_of(source, name).tests if source else None
    if not before or now >= before * (1 - fraction):
        return None
    whose, why = _drop_source(source, runs)
    return (f"lane {name!r} ran {now} tests, {before - now} fewer than {whose}'s {before}{why} "
            "- check the runner's log for a worker that died without reporting it")


def _drop_source(source: dict, runs: list[dict]) -> tuple[str, str]:
    """Whose count a drop line compares with: the last trusted run, or the older
    run that counted the lane when the last one recorded no count, named with
    why, as verify names the run it read in place of its baseline."""
    if source is runs[0]:
        return "the last trusted run", ""
    return (f"run {source['id']}",
            f" (the last trusted run, run {runs[0]['id']}, recorded no test count for it)")


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
    keys = ((_COUNTS if _counted(run, name) else ())
            + (_FAILURES if _failures_recorded(run, name) else ()))
    return {key: prov[key] for key in keys if key in prov}
