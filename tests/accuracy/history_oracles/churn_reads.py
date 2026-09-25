"""What crapkit says about churn, read the way a user or an agent reads it.

`worklist --json` is the surface: every admitted function's row carries its
file's commits, authors and weight. The per-file map every churn reader shares
is read too, from the file crapkit writes it to, because it holds every path in
the window and not only the ones with functions. No crapkit import: the CLI
runs through kit.drive.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

from accuracy.kit import drive


@dataclass(frozen=True)
class Said:
    commits: int
    authors: int
    weight: Decimal


def _said(commits, authors, weight) -> Said:
    return Said(int(commits), int(authors), Decimal(repr(float(weight))))


def churn_map(root: Path) -> dict[str, Said]:
    """{path: Said} from the per-file map crapkit stored under .crapkit/."""
    stored = sorted((Path(root) / ".crapkit").glob("churn-cache*.json"))
    assert stored, f"crapkit wrote no churn map under {root / '.crapkit'}"
    files = json.loads(stored[-1].read_text(encoding="utf-8"))["files"]
    return {path: _said(*values) for path, values in files.items()}


def worklist_rows(driver: drive.Driver) -> list[dict]:
    """Every row `worklist --json` prints, active and dormant."""
    result = driver.run("worklist", "--json")
    assert result.code == 0, result.stderr
    payload = result.json()
    return payload["active"] + payload.get("dormant_top", [])


def row_churn(rows: list[dict]) -> dict[str, Said]:
    """{path: Said} off the worklist rows; every row of one path must agree."""
    seen: dict[str, Said] = {}
    for row in rows:
        said = _said(row["commits"], row["authors"], row["weight"])
        assert seen.setdefault(row["path"], said) == said, f"two rows of {row['path']} disagree"
    return seen


def measure(root: Path, now: int) -> drive.Driver:
    """An inventory run at `root` on git's clock `now`, ready for worklist reads."""
    driver = drive.Driver(root, date_now=now)
    result = driver.run("inventory")
    assert result.code == 0, result.stderr
    return driver


def churn(root: Path, now: int) -> tuple[dict[str, Said], dict[str, Said]]:
    """(the stored map, the worklist rows' churn) after one inventory and one worklist."""
    driver = measure(root, now)
    rows = row_churn(worklist_rows(driver))
    return churn_map(root), rows
