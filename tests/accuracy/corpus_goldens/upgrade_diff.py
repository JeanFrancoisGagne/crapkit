"""Where a release's answer and the candidate's differ, and whether a change declared it.

Two answers to one read command, parsed and normalized, are compared leaf by
leaf. Each differing leaf names a calculation: the nearest key on its path
that FIELD_CALCS knows (a row's crap is "CRAP score"), else the command's
own calc. A difference is declared when a CHANGES.tsv row dated on or after
the release's upload date names its calc (change control's table: calcs
separated by `;`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from accuracy.corpus_goldens import releases
from accuracy.kit import goldens

ACCURACY = Path(__file__).resolve().parents[1]
CHANGES = ACCURACY / "change_control" / "CHANGES.tsv"
CHURN = "Churn counts and recency weight"
COMMAND_CALCS = {
    "runs list --json": "Baseline and run trust selection",
    "worklist --json": "Worklist ranking and dormant list",
    "coupling --json": "Change coupling",
    "trend --json": "Run totals and trend rollup",
}
FIELD_CALCS = {
    **releases.wheel_diff().CALCS,
    "commits": CHURN, "authors": CHURN, "weight": CHURN,
    "risk": "Worklist ranking and dormant list",
    "baseline": "Baseline and run trust selection",
    "support": "Change coupling", "confidence": "Change coupling",
    "grade": "Grade letter",
    "target": "Ceiling per row",
}
_ABSENT = object()


@dataclass(frozen=True)
class Difference:
    command: str
    path: tuple
    old: object
    new: object

    @property
    def calc(self) -> str:
        keys = [part for part in reversed(self.path) if isinstance(part, str)]
        return next((FIELD_CALCS[key] for key in keys if key in FIELD_CALCS),
                    COMMAND_CALCS.get(self.command, self.command))

    def line(self) -> str:
        where = "/".join(map(str, self.path)) or "(whole answer)"
        return f"{self.command} {where}: {self.old!r} -> {self.new!r} ({self.calc})"


def _children(value) -> list[tuple]:
    if isinstance(value, dict):
        return list(value.items())
    return list(enumerate(value)) if isinstance(value, list) else []


def leaves(value, path: tuple = ()) -> dict[tuple, object]:
    """{path: scalar} for every scalar inside a parsed JSON value."""
    children = _children(value)
    if not children:
        return {path: value}
    found: dict[tuple, object] = {}
    for key, item in children:
        found.update(leaves(item, path + (key,)))
    return found


def differences(command: str, old, new) -> list[Difference]:
    left, right = leaves(old), leaves(new)
    moved = (path for path in sorted(set(left) | set(right), key=repr)
             if left.get(path, _ABSENT) != right.get(path, _ABSENT))
    return [Difference(command, path, left.get(path), right.get(path)) for path in moved]


def _calcs(row: dict) -> set[str]:
    return {calc.strip() for calc in row["calcs"].split(";") if calc.strip()}


def declared_calcs(changes: dict[str, dict], since: str) -> set[str]:
    """Every calc a CHANGES row dated `since` or later names."""
    return set().union(*(_calcs(row) for row in changes.values() if row["date"] >= since))


def read_changes(path: Path = CHANGES) -> dict[str, dict]:
    return goldens.read_changes(path) if path.is_file() else {}


def undeclared(found: list[Difference], changes: dict[str, dict], since: str) -> list[str]:
    declared = declared_calcs(changes, since)
    return [difference.line() for difference in found if difference.calc not in declared]
