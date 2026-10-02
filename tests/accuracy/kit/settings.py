"""Hypothesis settings for tests/accuracy. They live here and nowhere else.

Two kinds, chosen by what a test does, sized by the running tier:

- `pure` for a test that computes: push runs 200 examples derandomized, so a
  push is reproducible; nightly runs 20,000 random ones and keeps its example
  database in actions/cache; release runs 5,000 derandomized. One example may
  take PURE_DEADLINE: Hypothesis's 200 ms default failed three pure tests in one
  nightly on a loaded machine, each example at 0.1 ms on replay, so the default
  measured the scheduler, not the code.
- `process` for a test that spawns git or the CLI per example: no deadline, the
  too_slow health check off, 15 steps per state-machine run. Push runs 5
  examples; nightly runs PROCESS_NIGHTLY_EXAMPLES, sized to about 10 minutes of
  wall clock per test on a Linux runner, and WINDOWS_PROCESS_NIGHTLY_EXAMPLES
  on Windows, where the same examples run about five times as long.

A test decorates with `@pure` or `@process`. test_kit_contract refuses any
other `settings(` call under tests/accuracy.

A search too long for one xdist worker runs as `split(process)`: PARTS
parametrized parts whose examples sum to the profile's, each drawing from a
seed of its own. run.py passes the nightly tier one --hypothesis-seed, which
gives every Hypothesis test in the session the same random, so parts without
seeds of their own would all draw the same examples. kit/parts_first.py, which
run.py loads under xdist, deals the parts one to each worker before any other
test.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os
import sys

from hypothesis import HealthCheck, seed, settings
from hypothesis.database import DirectoryBasedExampleDatabase
import pytest

from . import tiers

DATABASE_ENV = "CRAPKIT_HYPOTHESIS_DB"
# One tmp-repo step (git init, a commit, a coverage run over recorded
# artifacts) takes about 2 to 3 s on a Linux runner; 200 examples of up to 15
# steps land near 10 minutes.
PROCESS_NIGHTLY_EXAMPLES = 200
# Windows starts each git and CLI process about five times slower. The ten
# process tests other than the history machine ran 20 examples each (seed 1,
# -n 4, one machine, 2026-10-01) in these seconds in all, so a Windows cell
# runs 200 / 5.07, about 40, to stay near the same wall clock. At 200 the
# Windows nightly cells ran past their 75-minute job bound with the tier at 97%
# and 99%.
LINUX_PROCESS_SECONDS = 110.3
WINDOWS_PROCESS_SECONDS = 559.3
WINDOWS_PROCESS_NIGHTLY_EXAMPLES = 40
# The nightly process examples on each sys.platform that differs from the rest.
_PROCESS_NIGHTLY_ON = {"win32": WINDOWS_PROCESS_NIGHTLY_EXAMPLES}
# Near six times the longest pause seen (0.35 s), and far under a runaway example.
PURE_DEADLINE = timedelta(seconds=2)

_PURE = {
    "push": {"max_examples": 200, "derandomize": True},
    "nightly": {"max_examples": 20_000, "derandomize": False},
    "weekly": {"max_examples": 200, "derandomize": True},
    "release": {"max_examples": 5_000, "derandomize": True},
}
_PROCESS = {
    "push": {"max_examples": 5, "derandomize": True},
    "nightly": {"max_examples": PROCESS_NIGHTLY_EXAMPLES, "derandomize": False},
    "weekly": {"max_examples": 5, "derandomize": True},
    "release": {"max_examples": 5, "derandomize": True},
}
_PROCESS_FIXED = {
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow],
    "stateful_step_count": 15,
}


def _database(derandomize: bool):
    """A random run keeps what it found; a derandomized one has nothing to keep."""
    if derandomize:
        return None
    return DirectoryBasedExampleDatabase(os.environ.get(DATABASE_ENV, ".hypothesis/examples"))


def _sized(kind: str, tier: str, platform: str) -> dict:
    values = dict({"pure": _PURE, "process": _PROCESS}[kind][tier])
    if kind == "process" and tier == "nightly":
        values["max_examples"] = _PROCESS_NIGHTLY_ON.get(platform, values["max_examples"])
    return values


def profile(kind: str, tier: str, platform: str = sys.platform) -> settings:
    values = _sized(kind, tier, platform)
    values.update(_PROCESS_FIXED if kind == "process" else {"deadline": PURE_DEADLINE})
    return settings(database=_database(values["derandomize"]), **values)


pure = profile("pure", tiers.current_tier())
process = profile("process", tiers.current_tier())

# One part per xdist worker: every oracle leg runs run.py with -n 4 (accuracy.yml).
PARTS = 4


@dataclass(frozen=True)
class Part:
    """One part of a split search: `chosen` holds its share of the examples."""
    index: int
    chosen: settings

    def seeded(self, test, config):
        """`test` (a @given test or a state machine) drawing from this part's seed.

        The seed derives from the session's --hypothesis-seed, so the receipt's
        seed reproduces every part. With none, a derandomized profile seeds the
        part by its index, and a random one leaves Hypothesis to draw a fresh
        seed for each part and keep its example database, which @seed turns off."""
        forced = config.getoption("hypothesis_seed")
        if forced is not None:
            return seed(f"{forced}/part{self.index}")(test)
        return seed(self.index)(test) if self.chosen.derandomize else test


def split(chosen: settings, count: int = PARTS) -> list:
    """`count` pytest params, the parts of chosen's search; their max_examples
    differ by at most one and sum to chosen.max_examples."""
    share, extra = divmod(chosen.max_examples, count)
    return [pytest.param(Part(index, settings(chosen, max_examples=share + (index < extra))),
                         id=f"part{index}")
            for index in range(count)]
