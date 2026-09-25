"""The history packet's hand tables, the hand_*.tsv files beside this module. No crapkit.

Each row was worked by hand from a spec in repos/history_specs.py and cites
its source, and change control locks the tables, so an expected value can
only change through a declared change.
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent


def rows(name: str) -> list[dict]:
    """Every row of hand_<name>.tsv."""
    with (HERE / f"hand_{name}.tsv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))


def ranges() -> dict[str, list[tuple[int, int]]]:
    """{path: [(first, last) new-side line]} of the DIFF_CASES commit (hand_ranges.tsv)."""
    found: dict[str, list[tuple[int, int]]] = {}
    for row in rows("ranges"):
        found.setdefault(row["path"], []).append((int(row["start"]), int(row["end"])))
    return found


def gated(edit: str) -> set[str]:
    """The functions of src/m.py an edit of GATED_EDITS changes (hand_gated.tsv)."""
    return next(set(row["functions"].split(",")) for row in rows("gated") if row["edit"] == edit)


def renamed_marks() -> set[tuple[str, str]]:
    """{(path, function)} of the marks `ratchet prune` keeps after RENAME_MOVES
    (hand_renames.tsv); a new_path of '-' is a mark the docs' conditions drop."""
    return {(row["new_path"], row["function"]) for row in rows("renames") if row["new_path"] != "-"}
