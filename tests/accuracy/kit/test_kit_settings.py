"""The two Hypothesis kinds, sized per tier, and a search split across workers."""
from hypothesis import HealthCheck, given, strategies as st
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


def _kept(chosen) -> tuple:
    return (chosen.derandomize, chosen.deadline, tuple(chosen.suppress_health_check),
            chosen.stateful_step_count, chosen.database)


def _parts(chosen) -> list:
    return [param.values[0] for param in settings.split(chosen)]


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("tier", tiers.TIERS)
@pytest.mark.parametrize("kind", ["pure", "process"])
def test_the_parts_of_a_split_search_hold_its_examples(kind, tier, platform):
    """A split search runs as many examples as the whole one did, in every tier
    and on every OS, and each part keeps the profile's other settings."""
    chosen = settings.profile(kind, tier, platform)
    parts = _parts(chosen)
    shares = sorted(part.chosen.max_examples for part in parts)

    assert [part.index for part in parts] == list(range(settings.PARTS))
    assert (sum(shares), shares[-1] - shares[0]) in {(chosen.max_examples, 0), (chosen.max_examples, 1)}
    assert [_kept(part.chosen) for part in parts] == [_kept(chosen)] * settings.PARTS


class _Session:
    """A pytest config as Part.seeded reads it: the --hypothesis-seed it was given."""
    def __init__(self, forced: str | None):
        self.forced = forced

    def getoption(self, name: str):
        assert name == "hypothesis_seed"
        return self.forced


def _drawn(part, forced: str | None) -> list[int]:
    seen = []

    @given(st.integers())
    def draw(number):
        seen.append(number)

    part.seeded(part.chosen(draw), _Session(forced))()
    return seen


def test_each_part_draws_from_a_seed_of_its_own():
    """run.py hands the nightly tier one --hypothesis-seed, and a forced seed gives
    every test the same random: without seeds of their own the parts drew the
    same examples. Each part's seed derives from the forced one, so the seed a
    receipt records reproduces every part; a derandomized profile seeds a part
    by its index, and a random one leaves the part to Hypothesis's own draw."""
    first, second = _parts(settings.profile("pure", "push"))[:2]
    random = _parts(settings.profile("pure", "nightly"))[0]
    test = object()

    assert _drawn(first, "7") == _drawn(first, "7") != _drawn(second, "7")
    assert _drawn(first, "7") != _drawn(first, "8")
    assert _drawn(first, None) == _drawn(first, None) != _drawn(second, None)
    assert random.seeded(test, _Session(None)) is test
