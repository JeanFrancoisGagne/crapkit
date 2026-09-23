"""docs/agent-json.md names every store table a read command fills.

A caller that treats `worklist` or `brief` as a pure read needs to know which of
them write the store, and that a locked store only costs the cache. The section
named `run_rollup` alone. Since then `run_collisions` holds each run's same-line
collision groups, and the shingle index behind `duplication_twins` is stored
per run, while the brief page still said the shingles had no on-disk cache.
"""
from pathlib import Path

import pytest

from crapkit import store

ROOT = Path(__file__).resolve().parents[2]
HEADING = "### Read commands that write"


def _page() -> str:
    return (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")


def _section() -> str:
    page = _page()
    assert f"\n{HEADING}\n" in page, f"agent-json.md lost {HEADING!r}"
    return page.split(f"\n{HEADING}\n", 1)[1].split("\n### ", 1)[0]


@pytest.mark.parametrize("table", ["run_rollup", "run_collisions", "twin_runs"])
def test_the_section_names_each_cache_table_the_store_creates(table):
    assert f"CREATE TABLE IF NOT EXISTS {table} (" in store._SCHEMA + store._TWIN_DDL
    assert f"`{table}`" in _section()


def test_the_brief_page_no_longer_says_twins_have_no_stored_index():
    assert "per-process randomized hash" not in _page()
