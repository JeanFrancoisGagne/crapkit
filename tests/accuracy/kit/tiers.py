"""Which accuracy tests a run selects, and the guard on the `process` marker.

A tier is a cadence. A test with no tier marker is a push test, and push tests
also run in the nightly and release tiers; weekly is the mutation run and runs
only its own tests. The tier comes from CRAPKIT_ACCURACY_TIER, which
tools/accuracy/run.py sets, so a bare `pytest` selects push.

A test that is not in the running tier is deselected, never skipped: the only
skip or xfail under tests/accuracy is a rulings row's strict xfail.

A test that spawns git, node, pwsh or the crapkit CLI carries `process`, which
the mutation killer command deselects. The kit's own spawners call
require_process(), so a test that forgot the marker fails instead of slowing
every mutant down.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
import sys

TIERS = ("push", "nightly", "weekly", "release")
TIER_ENV = "CRAPKIT_ACCURACY_TIER"
# Set by the contract's collect-only run: every test of every tier, platform
# and Python is collected, so a node id a table names can be checked to exist.
COLLECT_ALL_ENV = "CRAPKIT_ACCURACY_COLLECT_ALL"
# Set by test_kit_guards: the guard probes, deselected otherwise, run.
GUARD_PROBES_ENV = "CRAPKIT_ACCURACY_GUARD_PROBES"
RUNS = {
    "push": frozenset({"push"}),
    "nightly": frozenset({"push", "nightly"}),
    "weekly": frozenset({"weekly"}),
    "release": frozenset({"push", "release"}),
}
MARKERS = {
    "push": "runs on every push and pull request, and in the nightly and release tiers",
    "nightly": "runs in the nightly tier only",
    "weekly": "runs in the weekly mutation tier only",
    "release": "runs in the release tier only",
    "process": "spawns git, node, pwsh or the crapkit CLI; the mutation killer deselects it",
    "golden": "compares crapkit to its own recorded output; not an independent method",
    "change_control": "judges a diff against the change-control rules; not an independent method",
    "cross_surface": "compares two crapkit surfaces; not an independent method",
    "platform(name)": "runs only where sys.platform starts with name (win32, linux, darwin)",
    "python(major, minor)": "runs only on that Python and later (an input older ones cannot parse)",
    "guard_probe": "breaks a session guard on purpose; runs only under test_kit_guards",
}


class TierError(ValueError):
    """CRAPKIT_ACCURACY_TIER names no tier."""


def current_tier(environ: Mapping[str, str] = os.environ) -> str:
    tier = environ.get(TIER_ENV) or "push"
    if tier not in RUNS:
        raise TierError(f"{TIER_ENV}={tier!r} names no tier; use one of {', '.join(TIERS)}")
    return tier


def tiers_of(marker_names: Iterable[str]) -> frozenset[str]:
    """The tiers a test belongs to. No tier marker means push."""
    named = frozenset(marker_names) & frozenset(TIERS)
    return named or frozenset({"push"})


def runs_on_platform(platforms: Iterable[str], platform: str = sys.platform) -> bool:
    wanted = list(platforms)
    return not wanted or any(platform.startswith(name) for name in wanted)


def runs_on_python(pythons: Iterable[tuple], version: tuple = sys.version_info[:2]) -> bool:
    """Whether the running Python is at least every (major, minor) a python marker names."""
    return all(tuple(version) >= tuple(minimum) for minimum in pythons)


def selected(marker_names: Iterable[str], tier: str, platforms: Iterable[str] = (),
             platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
             pythons: Iterable[tuple] = (), version: tuple = sys.version_info[:2]) -> bool:
    """Whether a test with these markers runs in this tier, on this platform and Python."""
    names = frozenset(marker_names)
    if environ.get(COLLECT_ALL_ENV):
        return True
    if _probe_held(names, environ):
        return False
    here = runs_on_platform(platforms, platform) and runs_on_python(pythons, version)
    return bool(tiers_of(names) & RUNS[tier]) and here


def _probe_held(names: frozenset[str], environ: Mapping[str, str]) -> bool:
    """A guard probe runs only under test_kit_guards, which sets GUARD_PROBES_ENV."""
    return "guard_probe" in names and not environ.get(GUARD_PROBES_ENV)


_RUNNING: dict[str, frozenset[str] | None] = {"markers": None}


def enter(marker_names: Iterable[str]) -> None:
    """Called as a test starts, so the kit's spawners can read its markers."""
    _RUNNING["markers"] = frozenset(marker_names)


def leave() -> None:
    _RUNNING["markers"] = None


def require_process(what: str) -> None:
    """Fail a running test that spawns `what` without the `process` marker.
    Outside a test (run.py, retro.py) there is nothing to check."""
    markers = _RUNNING["markers"]
    if markers is not None and "process" not in markers:
        raise AssertionError(f"this test spawns {what}: mark it @pytest.mark.process, "
                             "so the mutation killer command can deselect it")
