"""What a test tells tools/accuracy/run.py beyond pass or fail.

run.py points CRAPKIT_ACCURACY_LOG at a JSON-lines file per check. A test
appends one object per note: an oracle that was missing (an infra failure,
which run.py retries once and exits 3 on), a count of files an oracle skipped,
or the Hypothesis events a strategy emitted. Outside run.py the variable is
unset and a note goes nowhere.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path

LOG_ENV = "CRAPKIT_ACCURACY_LOG"
KINDS = ("infra", "skipped_files", "events")


def note(kind: str, **fields) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown run-log kind {kind!r}; use one of {', '.join(KINDS)}")
    target = os.environ.get(LOG_ENV)
    if not target:
        return
    line = json.dumps({"kind": kind, **fields}, sort_keys=True)
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


def summarize(notes: list[dict]) -> dict:
    """Infra misses, skipped-file counts per oracle and event counts, summed."""
    skipped: Counter = Counter()
    for entry in _of(notes, "skipped_files"):
        skipped[entry["oracle"]] += entry["count"]
    events: Counter = Counter()
    for entry in _of(notes, "events"):
        events.update(entry["counts"])
    infra = [entry["message"] for entry in _of(notes, "infra")]
    return {"infra": infra, "skipped_files": dict(skipped), "events": dict(events)}
