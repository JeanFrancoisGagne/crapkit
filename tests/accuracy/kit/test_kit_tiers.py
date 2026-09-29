"""Tier selection: the table, and the conftest that applies it to a real session."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

import hang_guard
from accuracy.kit import tiers

HERE = Path(__file__).resolve()
TESTS = HERE.parents[2]


@pytest.mark.parametrize("markers, tier, runs", [
    ((), "push", True),
    ((), "nightly", True),
    ((), "release", True),
    ((), "weekly", False),
    (("nightly",), "push", False),
    (("nightly",), "nightly", True),
    (("nightly",), "release", False),
    (("weekly",), "weekly", True),
    (("weekly",), "nightly", False),
    (("release",), "release", True),
    (("release",), "push", False),
    (("nightly", "release"), "release", True),
    (("process", "golden"), "push", True),
])
def test_a_tier_runs_its_own_tests_and_push_tests_run_outside_weekly(markers, tier, runs):
    assert tiers.selected(markers, tier) is runs


@pytest.mark.parametrize("platforms, platform, runs", [
    ((), "linux", True),
    (("win32",), "win32", True),
    (("win32",), "linux", False),
    (("linux", "darwin"), "darwin", True),
])
def test_a_platform_marker_limits_where_a_test_runs(platforms, platform, runs):
    assert tiers.selected((), "push", platforms, platform) is runs


@pytest.mark.parametrize("pythons, version, runs", [
    ((), (3, 11), True),
    (((3, 12),), (3, 11), False),
    (((3, 12),), (3, 12), True),
    (((3, 12),), (3, 14), True),
    (((3, 12), (3, 14)), (3, 13), False),
])
def test_a_python_marker_limits_which_interpreters_run_a_test(pythons, version, runs):
    """python(3, 12) marks a test whose input only 3.12 and later can parse, such
    as a PEP 695 def: an older Python deselects it, and the contract's collect-all
    run still sees it."""
    assert tiers.selected((), "push", pythons=pythons, version=version) is runs
    assert tiers.selected((), "push", pythons=pythons, version=version,
                          environ={tiers.COLLECT_ALL_ENV: "1"}) is True


def test_an_unset_or_empty_tier_is_push():
    assert tiers.current_tier({}) == "push"
    assert tiers.current_tier({tiers.TIER_ENV: ""}) == "push"


def test_an_unknown_tier_names_the_tiers():
    with pytest.raises(tiers.TierError, match="'hourly' names no tier; use one of push, nightly"):
        tiers.current_tier({tiers.TIER_ENV: "hourly"})


@pytest.mark.process
def test_a_spawning_test_with_the_marker_passes_the_guard():
    tiers.require_process("git")


def test_a_spawning_test_without_the_marker_fails_the_guard():
    with pytest.raises(AssertionError, match="spawns git: mark it @pytest.mark.process"):
        tiers.require_process("git")


def test_outside_a_test_the_guard_checks_nothing():
    held = tiers._RUNNING["markers"]
    tiers.leave()
    try:
        tiers.require_process("git")
    finally:
        tiers._RUNNING["markers"] = held


@pytest.mark.nightly
def test_probe_selected_in_nightly_only():
    """Exists to be selected; test_the_conftest_deselects_by_tier counts it."""


@pytest.mark.platform("no-such-platform")
def test_probe_selected_on_no_platform():
    """Exists to be deselected on every platform."""


@pytest.mark.python(99, 0)
def test_probe_selected_on_no_python():
    """Exists to be deselected on every Python this suite runs on."""


def _collected(tier: str) -> str:
    env = {**os.environ, tiers.TIER_ENV: tier, "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:randomly",
            "-p", "no:cacheprovider", str(HERE)]
    done = hang_guard.run(argv, cwd=TESTS.parent, env=env, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


@pytest.mark.process
def test_the_conftest_deselects_by_tier():
    push, nightly = _collected("push"), _collected("nightly")

    assert "test_probe_selected_in_nightly_only" not in push
    assert "test_probe_selected_in_nightly_only" in nightly
    assert "test_probe_selected_on_no_platform" not in push + nightly
    assert "test_probe_selected_on_no_python" not in push + nightly
    assert "test_an_unset_or_empty_tier_is_push" in push
