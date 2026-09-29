"""The exports every OS and Python must print alike, noted for the xplat job.

Each test reads one export of a golden run on this cell (OS and Python), notes
its normalized text in the run log, where tools/accuracy/run.py puts it in the
receipt, and checks it against the committed golden under the xplat rule:
ints and labels exactly, fixed-precision floats by their text, full-precision
floats within 2 ulp (tools/accuracy/wheel_diff.py `xplat`). The xplat job then
runs `wheel_diff.py xplat` over every cell's receipt, so the cells agree with
each other and not only with the golden.

The exports: the small corpus's inventory and scored TSVs, the session's
emitted baseline and seeded ratchet file (marks at 4 dp), and the history
run's worklist (risk and weight at 4 dp, from the recency logistic) flattened
to one row per function.
"""
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

from accuracy.corpus_goldens import golden_runs, releases
from accuracy.kit import runlog

pytestmark = pytest.mark.process
REPO = Path(__file__).resolve().parents[3]
EXPORTS = (("small", "inventory.tsv"), ("small", "scored.tsv"), ("session", "baseline.tsv"),
           ("session", "ratchet.tsv"), ("history", "worklist.json"))
WORKLIST_COLUMNS = ("path", "long_name", "occurrence", "start", "ccn", "cov", "crap", "commits",
                    "authors", "weight", "risk", "remedy", "flag")


wheel_diff = releases.wheel_diff()


def worklist_tsv(text: str) -> str:
    """A worklist payload's rows, active then dormant, as a TSV keyed like an export."""
    payload = json.loads(text)
    rows = [{**row, "long_name": row["function"]}
            for row in payload["active"] + payload.get("dormant_top", [])]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, WORKLIST_COLUMNS, delimiter="\t", lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _as_export(name: str, text: str) -> str:
    return worklist_tsv(text) if name == "worklist.json" else text


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    base = golden_runs.shared_base(tmp_path_factory)
    return {name: measure(base) for name, measure in golden_runs.RUNS.items()}


@pytest.mark.parametrize("run, name", EXPORTS)
def test_the_export_matches_its_golden_under_the_xplat_rule(runs, run, name):
    now = _as_export(name, golden_runs.normalized(runs[run])[name])
    golden = _as_export(name, (golden_runs.GOLDENS / run / name).read_bytes().decode("utf-8"))
    runlog.note("digest", name=f"corpus_goldens {run}/{name}",
                value={"sha256": hashlib.sha256(now.encode("utf-8")).hexdigest(), "text": now})

    assert now.count("\n") > 1
    assert wheel_diff.export_problem(f"{run}/{name}", golden, now) is None


def test_the_worklist_export_keeps_every_row_and_the_4dp_risk():
    payload = {"active": [{"path": "a.py", "function": "f( x )", "occurrence": 1, "risk": 0.1235,
                           "weight": 0.5, "other": "dropped"}],
               "dormant_top": [{"path": "b.py", "function": "g( )", "occurrence": 1, "risk": 0.0}]}

    rows = list(csv.DictReader(io.StringIO(worklist_tsv(json.dumps(payload))), delimiter="\t"))

    assert [(row["path"], row["long_name"], row["risk"]) for row in rows] == [
        ("a.py", "f( x )", "0.1235"), ("b.py", "g( )", "0.0")]
