"""The two Hypothesis kinds, sized per tier."""
from hypothesis import HealthCheck
import pytest

from accuracy.kit import settings, tiers


@pytest.mark.parametrize("tier, examples, derandomized", [
    ("push", 200, True),
    ("nightly", 20_000, False),
    ("release", 5_000, True),
    ("weekly", 200, True),
])
def test_pure_sizes_per_tier(tier, examples, derandomized):
    chosen = settings.profile("pure", tier)

    assert (chosen.max_examples, chosen.derandomize) == (examples, derandomized)
    assert chosen.deadline == settings.PURE_DEADLINE


@pytest.mark.parametrize("tier, examples", [
    ("push", 5),
    ("nightly", settings.PROCESS_NIGHTLY_EXAMPLES),
    ("release", 5),
])
def test_process_has_no_deadline_and_fifteen_steps(tier, examples):
    chosen = settings.profile("process", tier, "linux")

    assert chosen.max_examples == examples
    assert chosen.deadline is None
    assert HealthCheck.too_slow in chosen.suppress_health_check
    assert chosen.stateful_step_count == 15


@pytest.mark.parametrize("platform, examples", [
    ("linux", settings.PROCESS_NIGHTLY_EXAMPLES),
    ("darwin", settings.PROCESS_NIGHTLY_EXAMPLES),
    ("win32", settings.WINDOWS_PROCESS_NIGHTLY_EXAMPLES),
])
def test_a_nightly_process_test_runs_the_examples_sized_for_its_os(platform, examples):
    """At 200 examples a Windows cell's process tests ran about five times as long
    as Linux's, and the cell ran past its job bound before its tier ended."""
    assert settings.profile("process", "nightly", platform).max_examples == examples
    for tier in ("push", "release", "weekly"):
        assert settings.profile("process", tier, platform).max_examples == 5
    assert settings.profile("pure", "nightly", platform).max_examples == 20_000


def test_the_windows_count_keeps_the_linux_wall_clock():
    """The Windows count is the Linux one divided by the measured slowdown."""
    slowdown = settings.WINDOWS_PROCESS_SECONDS / settings.LINUX_PROCESS_SECONDS

    assert round(settings.PROCESS_NIGHTLY_EXAMPLES / slowdown, -1) == (
        settings.WINDOWS_PROCESS_NIGHTLY_EXAMPLES)


def test_only_a_random_run_keeps_an_example_database(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.DATABASE_ENV, str(tmp_path / "db"))

    assert settings.profile("pure", "push").database is None
    assert settings.profile("pure", "nightly").database is not None


def test_the_module_settings_follow_the_running_tier():
    tier = tiers.current_tier()

    assert settings.pure.max_examples == settings.profile("pure", tier).max_examples
    assert settings.process.max_examples == settings.profile("process", tier).max_examples
