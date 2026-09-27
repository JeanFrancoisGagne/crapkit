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
  wall clock per test.

A test decorates with `@pure` or `@process`. test_kit_contract refuses any
other `settings(` call under tests/accuracy.
"""
from __future__ import annotations

from datetime import timedelta
import os

from hypothesis import HealthCheck, settings
from hypothesis.database import DirectoryBasedExampleDatabase

from . import tiers

DATABASE_ENV = "CRAPKIT_HYPOTHESIS_DB"
# One tmp-repo step (git init, a commit, a coverage run over recorded
# artifacts) takes about 2 to 3 s on a runner; 200 examples of up to 15 steps
# land near 10 minutes.
PROCESS_NIGHTLY_EXAMPLES = 200
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


def profile(kind: str, tier: str) -> settings:
    table = {"pure": _PURE, "process": _PROCESS}[kind]
    values = dict(table[tier])
    values.update(_PROCESS_FIXED if kind == "process" else {"deadline": PURE_DEADLINE})
    return settings(database=_database(values["derandomize"]), **values)


pure = profile("pure", tiers.current_tier())
process = profile("process", tiers.current_tier())
