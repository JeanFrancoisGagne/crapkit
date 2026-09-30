"""The ratchet file's history walked version by version, and the burn-down a reader
of the docs computes from it. No crapkit.

Where crapkit replays the file's patches, this walk reads every committed
version whole (`git show <commit>:<file>`) and diffs the mark sets itself.

The file (docs/ratchet.md#what-a-mark-is): comment lines start with '#', then
a header `path<TAB>long_name<TAB>crap`, then one mark per row, keyed by
(path, key name). A row that needs the portable record encoding is refused
here: the fixtures hold none.

The report (docs/ratchet.md#reporting-the-burn-down, docs/agent-json.md
`ratchet report --json`):
- anchor_ts is the newest commit that touched the file, whatever it changed.
- A mark enters when a commit adds its key; a commit that changes its value
  keeps the entry date; a commit that removes the key repays it, and a later
  re-add enters it again.
- open is the marks on disk; a mark with no commit behind it entered at the
  anchor and is 0 days old.
- age_days is whole days from entry to the anchor (floor).
- uncommitted counts the keys the working tree and the newest commit disagree
  on, a changed value included.
- dropped_last_30d and _90d count repayments within 30 and 90 days of the anchor.
- oldest lists up to 20 open marks, oldest first, then by path and key name.
- The debt policy (docs/ratchet.md#the-debt-policy): a mark older than
  debt_max_age_months counted at 30 days a month; repayment stalled when fewer
  than repayment_min_per_30d marks were repaid in 30 days while debt is open.
- shallow is whether the checkout is a shallow clone, which holds only part of
  the history every age and repayment counts; git says so itself
  (`rev-parse --is-shallow-repository`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .history_git import git, text

DAY = 86_400
HEADER = ("path", "long_name", "crap")


class EncodedRow(ValueError):
    """A row this reader does not decode."""


def read_marks(text: str) -> dict[tuple[str, str], Decimal]:
    """{(path, key name): crap} of one version of the file."""
    rows = [line for line in text.replace("\r\n", "\n").split("\n") if _is_row(line)]
    return dict(_mark(row) for row in rows)


def _is_row(line: str) -> bool:
    """A mark row: not blank, not a comment, not the header."""
    return bool(line) and not line.startswith("#") and tuple(line.split("\t")) != HEADER


def _mark(row: str) -> tuple[tuple[str, str], Decimal]:
    cells = row.split("\t")
    if len(cells) != 3 or any(cell.startswith('"') for cell in cells):
        raise EncodedRow(row)
    return (cells[0], cells[1]), Decimal(cells[2])


@dataclass(frozen=True)
class Version:
    at: int
    marks: dict


def versions(root: Path, name: str) -> list[Version]:
    """Every committed version of the file, oldest first; a deletion reads empty."""
    log = git(root, "log", "--reverse", "--format=%H %at", "--", name).decode("utf-8")
    return [Version(int(at), _marks_at(root, sha, name))
            for sha, at in (line.split() for line in log.splitlines())]


def _marks_at(root: Path, sha: str, name: str) -> dict:
    listed = git(root, "ls-tree", "--name-only", sha, "--", name).decode("utf-8").strip()
    return read_marks(git(root, "show", f"{sha}:./{name}").decode("utf-8")) if listed else {}


def _on_disk(path: Path) -> dict:
    return read_marks(path.read_bytes().decode("utf-8")) if path.exists() else {}


@dataclass
class _Replay:
    entered: dict = field(default_factory=dict)
    dropped: list = field(default_factory=list)
    last: dict = field(default_factory=dict)
    anchor: int = 0

    def step(self, version: Version) -> None:
        for key in version.marks.keys() - self.last.keys():
            self.entered[key] = version.at
        for key in self.last.keys() - version.marks.keys():
            del self.entered[key]
            self.dropped.append(version.at)
        self.last, self.anchor = version.marks, max(self.anchor, version.at)

    def uncommitted(self, working: dict) -> int:
        keys = self.last.keys() | working.keys()
        return sum(1 for key in keys if self.last.get(key) != working.get(key))

    def within(self, days: int) -> int:
        return sum(1 for stamp in self.dropped if self.anchor - stamp <= days * DAY)


def _oldest(entered: dict, working: dict, anchor: int) -> list[dict]:
    rows = [{"path": path, "long_name": name,
             "age_days": (anchor - entered.get((path, name), anchor)) // DAY}
            for path, name in working]
    return sorted(rows, key=lambda row: (-row["age_days"], row["path"], row["long_name"]))[:20]


def report(root: Path, name: str) -> dict:
    """The burn-down of the file `name` under `root`, read as the docs define it."""
    replay = _Replay()
    for version in versions(root, name):
        replay.step(version)
    working = _on_disk(root / name)
    return {"open": len(working), "dropped_total": len(replay.dropped),
            "anchor_ts": replay.anchor, "uncommitted": replay.uncommitted(working),
            "oldest": _oldest(replay.entered, working, replay.anchor),
            "dropped_last_30d": replay.within(30), "dropped_last_90d": replay.within(90),
            "shallow": text(root, "rev-parse", "--is-shallow-repository") == "true"}


def _too_old(found: dict, max_age_months: int | None) -> list[str]:
    if max_age_months is None:
        return []
    return [f"age {row['path']} {row['long_name']}" for row in found["oldest"]
            if row["age_days"] > max_age_months * 30]


def _stalled(found: dict, min_repaid_30d: int | None) -> list[str]:
    if min_repaid_30d is None or not found["open"]:
        return []
    return ["stalled"] if found["dropped_last_30d"] < min_repaid_30d else []


def violations(found: dict, max_age_months: int | None, min_repaid_30d: int | None) -> list[str]:
    """The debt policy's findings: 'age <path> <key>' per mark past its age, then
    'stalled' when repayment falls short while debt is open."""
    return _too_old(found, max_age_months) + _stalled(found, min_repaid_30d)
