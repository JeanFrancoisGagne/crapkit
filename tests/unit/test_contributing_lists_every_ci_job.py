"""CONTRIBUTING's CI table lists the jobs .github/workflows/ci.yml runs, no more and no fewer.

CI split the verdict into a `verdict-measure` job per side and a joining
`verdict`, and moved the event-base hook to `dogfood`. The table kept four rows
and the old single-job verdict command, so a contributor reading a red
`verdict-measure` check found no row for it.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
_ROW = re.compile(r"^\| `([a-z-]+)` \|", re.M)


def _workflow_jobs() -> set[str]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return set(workflow["jobs"])


def _table_jobs() -> set[str]:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = text.split("**In CI**", 1)[1].split("\n\n| Job |", 1)[1].split("\n\n", 1)[0]
    return set(_ROW.findall(section))


def test_the_table_names_every_job_the_workflow_runs():
    assert _table_jobs() == _workflow_jobs()
