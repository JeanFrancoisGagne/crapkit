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
    assert chosen.deadline is not None


@pytest.mark.parametrize("tier, examples", [
    ("push", 5),
    ("nightly", settings.PROCESS_NIGHTLY_EXAMPLES),
    ("release", 5),
])
def test_process_has_no_deadline_and_fifteen_steps(tier, examples):
    chosen = settings.profile("process", tier)

    assert chosen.max_examples == examples
    assert chosen.deadline is None
    assert HealthCheck.too_slow in chosen.suppress_health_check
    assert chosen.stateful_step_count == 15


def test_only_a_random_run_keeps_an_example_database(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.DATABASE_ENV, str(tmp_path / "db"))

    assert settings.profile("pure", "push").database is None
    assert settings.profile("pure", "nightly").database is not None


def test_the_module_settings_follow_the_running_tier():
    tier = tiers.current_tier()

    assert settings.pure.max_examples == settings.profile("pure", tier).max_examples
    assert settings.process.max_examples == settings.profile("process", tier).max_examples
