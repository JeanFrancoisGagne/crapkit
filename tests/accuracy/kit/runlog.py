"""What a test tells tools/accuracy/run.py beyond pass or fail.

run.py points CRAPKIT_ACCURACY_LOG at a JSON-lines file per run. A test
appends one object per note: an oracle that was missing (an infra failure,
which run.py retries once and exits 3 on), the version of an oracle it read, a
count of files an oracle skipped, the Hypothesis events a strategy emitted, or
a digest the receipt should carry (an export the xplat job compares). Each
note names the test that wrote it, so run.py can tell an infra miss from a
real failure in the same check. Outside run.py the variable is unset and a
note goes nowhere.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path

LOG_ENV = "CRAPKIT_ACCURACY_LOG"
KINDS = ("infra", "skipped_files", "events", "oracle", "digest")
TEST_ENV = "PYTEST_CURRENT_TEST"


def note(kind: str, **fields) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown run-log kind {kind!r}; use one of {', '.join(KINDS)}")
    target = os.environ.get(LOG_ENV)
    if not target:
        return
    test = os.environ.get(TEST_ENV, "").rsplit(" (", 1)[0]
    line = json.dumps({"kind": kind, "test": test, **fields}, sort_keys=True)
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read(path: Path) -> list[dict]:
    """Every note in a log, in order. A missing log holds none."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _of(notes: list[dict], kind: str) -> list[dict]:
    return [entry for entry in notes if entry["kind"] == kind]


def _skipped(notes: list[dict]) -> dict:
    skipped: Counter = Counter()
    for entry in _of(notes, "skipped_files"):
        skipped[entry["oracle"]] += entry["count"]
    return dict(skipped)


def _events(notes: list[dict]) -> dict:
    events: Counter = Counter()
    for entry in _of(notes, "events"):
        events.update(entry["counts"])
    return dict(events)


def _by_name(notes: list[dict], kind: str, field: str) -> dict:
    return {entry["name"]: entry[field] for entry in _of(notes, kind)}


def summarize(notes: list[dict]) -> dict:
    """Infra misses, skipped-file counts per oracle and event counts summed;
    oracle versions and noted digests by name."""
    return {"infra": [entry["message"] for entry in _of(notes, "infra")],
            "skipped_files": _skipped(notes), "events": _events(notes),
            "oracles": _by_name(notes, "oracle", "version"),
            "exports": _by_name(notes, "digest", "value")}


def infra_tests(notes: list[dict]) -> set[str]:
    """The node ids of the tests that noted an infra miss."""
    return {entry["test"] for entry in _of(notes, "infra") if entry["test"]}
