"""CHANGELOG.md and docs/upgrading.md give each 0.9.0 build slot its own block.

0.9.0 is built in sixteen slots that land on one integration branch. In 0.8.1
each of the six class trees wrote its own unreleased heading, and the
integrator merged the copies by hand. Now one 0.9.0 heading in the CHANGELOG,
and one 0.9.0 section in the upgrade guide, hold a pair of markers per slot,
and a slot writes only between its own pair, so two slots never add adjacent
lines and git merges them clean. The collapse that writes
docs/releases/0.9.0.md removes every marker before the release is dated.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASH = chr(0x2014)
SLOTS = (
    "gate-group", "m1-foundations", "mission-3", "metric-stamp", "mission-4", "m1-readers",
    "m5-comment-fixture", "mission-9", "mission-2", "lanes-visible", "schema-2", "criterion",
    "m6-step-coverage", "release-bytes", "protocol-2-step", "hash-cost",
)
HOMES = {"CHANGELOG.md": f"## 0.9.0 {DASH} unreleased", "docs/upgrading.md": "## Upgrading to 0.9.0"}
OLDER = {"CHANGELOG.md": f"## 0.8.1 {DASH} 2026-10-01", "docs/upgrading.md": "## 0.8.0 to 0.8.1, in order"}
COLLAPSED = ROOT / "docs" / "releases" / "0.9.0.md"
MARKER = re.compile(r"<!-- */?0\.9\.0:")
BUILD_MAP = os.environ.get("CRAPKIT_090_BUILD_MAP", "")


def _pairs(slots) -> list[str]:
    return [row for slot in slots for row in (f"<!-- 0.9.0:{slot} -->", f"<!-- /0.9.0:{slot} -->")]


SEEDED = _pairs(SLOTS)


def _read(rel: str) -> str:
    """The file as git checked it out, CRLF or LF."""
    return (ROOT / rel).read_bytes().decode("utf-8")


def _rows(text: str) -> list[str]:
    return [row.strip() for row in text.splitlines()]


def _expected() -> list[str]:
    """Every slot's markers until the collapse writes the detail page, then none."""
    return [] if COLLAPSED.exists() else SEEDED


def placement_problems(text: str, home: str) -> list[str]:
    """Markers that sit under any level-2 heading but `home`."""
    problems = []
    heading = "no heading"
    for number, row in enumerate(_rows(text), 1):
        if row.startswith("## "):
            heading = row
        elif MARKER.search(row) and heading != home:
            problems.append(f"line {number}: {row} sits under {heading!r}, not {home!r}")
    return problems


def block_problems(text: str, expected: list[str]) -> list[str]:
    """The first marker that differs from `expected`: a block opened twice, out of
    order, nested in another, never closed, or a marker sharing its line."""
    found = [(number, row) for number, row in enumerate(_rows(text), 1) if MARKER.search(row)]
    for (number, row), want in zip(found, expected):
        if row != want:
            return [f"line {number}: {row} where {want} belongs"]
    if len(found) > len(expected):
        number, row = found[len(expected)]
        return [f"line {number}: {row} after the last block closed"]
    if len(found) < len(expected):
        return [f"the file ends where {expected[len(found)]} belongs"]
    return []


def _page(heading: str, markers: list[str], newline: str = "\n") -> str:
    """A fixture page: a title, `heading` with the markers under it, then an older section."""
    rows = ["# Page", "", heading, ""] + [row for marker in markers for row in (marker, "")]
    return newline.join(rows + ["## Older", "", "Text.", ""])


# --- the files in the tree ------------------------------------------------------------

@pytest.mark.parametrize("rel", sorted(HOMES))
def test_each_slot_block_opens_and_closes_once(rel):
    assert block_problems(_read(rel), _expected()) == []


@pytest.mark.parametrize("rel", sorted(HOMES))
def test_a_dated_release_holds_no_slot_marker(rel):
    assert placement_problems(_read(rel), HOMES[rel]) == []


@pytest.mark.skipif(not BUILD_MAP, reason="CRAPKIT_090_BUILD_MAP names no 0.9.0 ownership map")
def test_the_blocks_follow_the_build_maps_slots_and_lands_markers():
    path = Path(BUILD_MAP)
    plan = json.loads(path.read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("_land_090", path.parent / "tools" / "land.py")
    land = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = land
    spec.loader.exec_module(land)

    assert SLOTS == tuple(slot for slot in plan["slots"] if slot != "build-map")
    assert sorted(plan["blocks"]) == sorted(HOMES)
    for rel, prefix in plan["blocks"].items():
        lands = [marker for slot in SLOTS for marker in land._markers(prefix, slot)]
        assert lands == SEEDED
        assert block_problems(_read(rel), _expected()) == []


# --- fixtures: what the checks refuse ------------------------------------------------

DEFECTS = {
    "a block opened twice": SEEDED[:1] + SEEDED,
    "blocks out of order": SEEDED[2:4] + SEEDED[:2] + SEEDED[4:],
    "nested blocks": [SEEDED[0], SEEDED[2], SEEDED[3], SEEDED[1]] + SEEDED[4:],
    "a block never closed": SEEDED[:-1],
    "a marker sharing its line": [SEEDED[0] + " - a line"] + SEEDED[1:],
}


@pytest.mark.parametrize("defect", sorted(DEFECTS))
@pytest.mark.parametrize("rel", sorted(HOMES))
def test_a_misshapen_block_is_refused_in_either_file(rel, defect):
    assert block_problems(_page(HOMES[rel], DEFECTS[defect]), SEEDED)


@pytest.mark.parametrize("rel", sorted(HOMES))
def test_a_marker_under_a_released_heading_is_refused(rel):
    dated = _page(f"## 0.9.0 {DASH} 2026-11-02", SEEDED)
    older = _page(HOMES[rel], []).replace("## Older", f"{OLDER[rel]}\n\n{SEEDED[0]}\n{SEEDED[1]}")

    assert placement_problems(dated, HOMES["CHANGELOG.md"])
    assert placement_problems(older, HOMES[rel]) == [
        f"line 7: {SEEDED[0]} sits under {OLDER[rel]!r}, not {HOMES[rel]!r}",
        f"line 8: {SEEDED[1]} sits under {OLDER[rel]!r}, not {HOMES[rel]!r}",
    ]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("rel", sorted(HOMES))
def test_empty_and_filled_blocks_pass_with_either_line_ending(rel, newline):
    filled = SEEDED[:1] + ["- A line a user sees."] + SEEDED[1:]

    for markers in (SEEDED, filled):
        page = _page(HOMES[rel], markers, newline)
        assert block_problems(page, SEEDED) == []
        assert placement_problems(page, HOMES[rel]) == []
