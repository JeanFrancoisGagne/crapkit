"""The packet's hand tables: probes/<lang>/probes.tsv and equivalence.tsv.

A probes.tsv row states one number for one function of one probe file:

    id  file  function  metric  expected  source  ruling

- `function` is the bare identifier (kit analysis_inventory.bare), or
  `name@line` when a file holds twins.
- `metric` is an inventory column (start, end, ccn_std, ccn_mod, ccn,
  cognitive, nesting, nloc, params) or `rows`, the number of rows the name has.
- `expected` was worked by hand from `source` before crapkit was run.
- `ruling` names the rulings.tsv row that records a difference, when crapkit
  counts the construct another way on purpose or gets it wrong.

No crapkit import: these tables are expected values.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from accuracy.kit import rulings

HERE = Path(__file__).resolve().parent
PROBES = HERE / "probes"
EQUIVALENCE = HERE / "equivalence.tsv"
METRICS = ("start", "end", "ccn_std", "ccn_mod", "ccn", "cognitive", "nesting", "nloc", "params",
           "rows")


def tsv(path: Path) -> list[dict]:
    """Rows of a tab-separated table with a header; blank lines skipped."""
    lines = [line.removesuffix("\r") for line in
             path.read_bytes().decode("utf-8").split("\n") if line.strip()]
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


@dataclass(frozen=True)
class Probe:
    lang: str
    id: str
    file: str
    function: str
    metric: str
    expected: str
    source: str
    ruling: str

    @property
    def key(self) -> str:
        return f"{self.lang}:{self.id}:{self.metric}"

    @property
    def path(self) -> str:
        """Where the probe file sits in the measured repo."""
        return f"{self.lang}/{self.file}"


def _probe(lang: str, row: dict) -> Probe:
    if row["metric"] not in METRICS:
        raise ValueError(f"probes/{lang}: {row['id']} names no metric {row['metric']!r}")
    return Probe(lang, row["id"], row["file"], row["function"], row["metric"], row["expected"],
                 row["source"], row.get("ruling", "").strip())


def probes(root: Path = PROBES) -> list[Probe]:
    """Every row of every probes/<lang>/probes.tsv."""
    tables = sorted(root.glob("*/probes.tsv"))
    return [_probe(table.parent.name, row) for table in tables for row in tsv(table)]


def probe_files(root: Path = PROBES) -> dict[str, bytes]:
    """{<lang>/<file>: bytes} for every probe source file, the tables left out."""
    files = (path for path in sorted(root.rglob("*")) if path.is_file())
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in files if path.suffix != ".tsv"}


def _split(function: str) -> tuple[str, int | None]:
    name, _, line = function.partition("@")
    return name, int(line) if line else None


def lookup(measured, path: str, function: str) -> list[dict]:
    """The inventory rows a probe's `function` names in `path`."""
    name, line = _split(function)
    named = measured.named(path, name)
    return [row for row in named if line is None or row["start"] == line]


def value(measured, probe: Probe) -> str:
    """crapkit's value for a probe, as a string: `missing` when no row has the
    name, `N rows` when more than one does."""
    found = lookup(measured, probe.path, probe.function)
    if probe.metric == "rows":
        return str(len(found))
    if len(found) != 1:
        return "missing" if not found else f"{len(found)} rows"
    return str(found[0][probe.metric])


def marks(ruling_id: str) -> list:
    """A strict-xfail mark while `ruling_id` is an open defect; none otherwise."""
    if not ruling_id:
        return []
    mark = rulings.applies(ruling_id)
    return [mark] if isinstance(mark, pytest.MarkDecorator) else []


def check(actual: str, expected: str, ruling_id: str) -> None:
    """The one comparison every hand and oracle test makes: equal, or the
    difference a rulings row records."""
    if ruling_id:
        rulings.pin_ruling(ruling_id, crapkit=actual, oracle=expected)
    else:
        assert actual == expected, f"crapkit says {actual}, the expected value is {expected}"
