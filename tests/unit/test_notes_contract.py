"""The published note may only print numbers the committed reproduction measured.

`docs/notes/pytest-cov-7-subprocess-coverage.html` tells a reader that dropping
one config key costs them every statement of their CLI. That claim is worth
nothing unless the numbers on the page are the numbers a run produced, so
`tools/notes/pytest_cov7_repro.py` produces them and writes
`pytest_cov7_repro.json` beside itself, and this file joins the two.

Every measurement row on the page carries the pytest-cov pin, the coverage
version that resolved with it, whether `[run] patch` was present, and the
executed/total statement count. All four come from the JSON. Rewrite a number by
hand and this fails; rerun the reproduction and the page has to follow.
"""
import html
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PAGE = ROOT / "docs/notes/pytest-cov-7-subprocess-coverage.html"
REPRO_JSON = ROOT / "tools/notes/pytest_cov7_repro.json"
REPRO_PY = "tools/notes/pytest_cov7_repro.py"

# The pytest-cov 7.0.0 changelog line and its release date, quoted on the page.
DROPPED = "Dropped support for subprocesses measurement"
RELEASED = "2025-09-09"

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG = re.compile(r"<[^>]+>")


@lru_cache(maxsize=None)
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def prose() -> str:
    """The page with its markup and entities gone, for phrase assertions."""
    return " ".join(html.unescape(_TAG.sub(" ", page())).split())


@lru_cache(maxsize=None)
def repro() -> dict:
    return json.loads(REPRO_JSON.read_text(encoding="utf-8"))


def _cells(row: str) -> list[str]:
    return [" ".join(html.unescape(_TAG.sub(" ", cell)).split())
            for cell in _CELL.findall(row)]


@lru_cache(maxsize=None)
def measured_rows() -> dict:
    """(pytest-cov pin, patch state) -> the row's coverage version and count.

    A measurement row is the one shape that carries `present` or `absent` in its
    third cell, which no prose table on the page does."""
    rows = {}
    for row in _ROW.findall(page()):
        cells = _cells(row)
        if len(cells) >= 4 and cells[2] in {"present", "absent"}:
            rows[(cells[0], cells[2])] = (cells[1], cells[3])
    return rows


# --- the numbers -------------------------------------------------------------

def test_the_reproduction_measured_all_four_conditions():
    """Two pins times the key present and absent. A missing one means the run
    that wrote the JSON did not finish."""
    assert len(repro()["conditions"]) == 4
    assert len(measured_rows()) == 4


@pytest.mark.parametrize("tag", ("pytest-cov-7.1.0-patch-off", "pytest-cov-7.1.0-patch-on",
                                 "pytest-cov-6.3.0-patch-off", "pytest-cov-6.3.0-patch-on"))
def test_the_page_prints_the_count_the_reproduction_measured(tag: str):
    cond = repro()["conditions"][tag]
    state = "present" if cond["patch_subprocess"] else "absent"

    version, count = measured_rows()[(cond["pytest_cov"], state)]

    assert count == f"{cond['executed']}/{cond['total']}", tag
    assert version == repro()["environments"][cond["pytest_cov"]]["coverage"], tag


def test_the_page_names_the_module_and_the_suite_the_numbers_come_from():
    assert repro()["target"] in prose()
    assert repro()["suite"] in prose()


def test_the_page_sends_the_reader_to_the_script_that_produced_the_json():
    assert REPRO_PY in prose()
    assert REPRO_JSON.name in prose()


# --- the claim ---------------------------------------------------------------

def test_the_page_quotes_the_changelog_line_and_dates_it():
    assert DROPPED in prose()
    assert RELEASED in prose()


def test_the_page_prints_both_halves_of_the_fix():
    """One without the other is silence: the key alone warns and does nothing on
    a coverage below 7.10.6, and the floor alone patches nothing."""
    assert 'patch = ["subprocess"]' in prose()
    assert "coverage>=7.10.6" in prose()


def test_the_page_links_back_to_the_handbook():
    assert '"../handbook.html"' in page()
