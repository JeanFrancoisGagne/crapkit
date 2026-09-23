"""`crapkit clean` recovers abandoned mutation checkouts only. Test evidence
retention lives in crapkit's own development runner, tools/testing/run.py, and
src/crapkit/retention.py is gone. The pages that describe clean, doctor's
retention fields or .crapkit/test-runs say so."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLAG = "--retention-days"
STALE = ("configured retention", "configured test retention", "test_retention_days = 7",
         "configured age and count retention", "`retention.py`")
PAGES = ("AGENTS.md", "README.md", "CONTRIBUTING.md", "docs/resources.md",
         "docs/agent-json.md", "docs/lanes.md")


def _page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _row(name: str, start: str) -> str:
    return next(line for line in _page(name).splitlines() if line.startswith(start))


def _after(name: str, marker: str) -> str:
    """The paragraph or table that follows `marker`, up to the next blank line."""
    return _page(name).split(marker, 1)[1].strip().split("\n\n", 1)[0]


def _section(name: str, heading: str) -> str:
    """The body under `heading`, up to the next heading of the same depth."""
    level = heading.split(" ", 1)[0]
    body = _page(name).split(heading + "\n", 1)[1]
    return re.split(rf"^{level} ", body, maxsplit=1, flags=re.M)[0]


def test_the_shared_rules_table_names_only_modules_that_exist():
    table = _after("AGENTS.md", "Shared rules belong to these modules:")
    names = re.findall(r"^\| `(\w+\.py)` \|", table, re.M)

    assert len(names) > 5, table
    assert [name for name in names if not (ROOT / "src/crapkit" / name).is_file()] == []


@pytest.mark.parametrize("name", PAGES)
def test_no_page_says_crapkit_applies_test_retention(name):
    page = _page(name)

    assert [phrase for phrase in STALE if phrase in page] == []


@pytest.mark.parametrize("text", [
    lambda: _row("README.md", "| `clean "),
    lambda: _row("docs/lanes.md", "| `test-runs/` |"),
    lambda: _section("docs/resources.md", "## Logs and retained evidence"),
    lambda: _section("docs/agent-json.md", "### `clean --json`"),
    lambda: _after("CONTRIBUTING.md", "Add `--coverage` to the shared runner"),
], ids=["readme-clean", "lanes-test-runs", "resources", "agent-json-clean", "contributing"])
def test_each_page_names_the_runner_flag_that_sets_retention(text):
    assert FLAG in text(), text()


def test_agent_json_says_the_retention_fields_are_always_empty_or_zero():
    clean = _after("docs/agent-json.md", "| `test_runs` |")
    doctor = _after("docs/agent-json.md", "The additive `resources` object")

    assert "always empty" in clean, clean
    assert "always `0`" in doctor, doctor
