"""The upgrade page names the scores exact arithmetic moves, at the values crapkit prints.

Cubing `1 - cov` with two products instead of `** 3` moved 21 scores at the 4 dp
a mark is stored at, 9 of them up. The CHANGELOG said so and docs/upgrading.md
did not, so an upgrader whose verify reported a 0.0001 ratchet regression on a
function nobody edited found no word of it on the page that says what an
upgrade moves.
"""
from pathlib import Path

import pytest

from crapkit.score import crap, remedy

ROOT = Path(__file__).resolve().parents[2]
UPGRADING = "docs/upgrading.md"
# (page, example as the page writes it, ccn, cov, places, what 0.8.0 printed)
MOVES = [
    (UPGRADING, "CRAP(36, 53/120)", 36, 53 / 120, 4, "261.5723"),
    (UPGRADING, "CRAP(20, 3/200)", 20, 3 / 200, 4, "402.2686"),
    (UPGRADING, "CRAP(25, 19/50)", 25, 19 / 50, 2, "173.95"),
    ("docs/releases/0.8.1.md", "CRAP(36, 53/120)", 36, 53 / 120, 4, "261.5723"),
    ("docs/releases/0.8.1.md", "CRAP(25, 19/50)", 25, 19 / 50, 2, "173.95"),
]


def _block_naming(page: str, example: str) -> str:
    """The first paragraph or list item run of `page` that names `example`, one line."""
    blocks = (ROOT / page).read_bytes().decode("utf-8").split("\n\n")
    named = [" ".join(block.split()) for block in blocks if example in " ".join(block.split())]
    assert named, f"{page} names no {example}"
    return named[0]


@pytest.mark.parametrize(("page", "example", "ccn", "cov", "places", "before"), MOVES,
                         ids=[f"{m[0]}:{m[1]}" for m in MOVES])
def test_the_page_gives_the_old_and_the_new_print(page, example, ccn, cov, places, before):
    block = _block_naming(page, example)
    now = f"{crap(ccn, cov):.{places}f}"

    assert now != before
    assert f"now prints {now}, not {before}" in block


def test_the_upgrade_page_says_a_rise_is_raised_by_hand():
    block = _block_naming(UPGRADING, "402.2686 -> 402.2687")

    assert "ratchet regression" in block
    assert "by hand" in block


def test_the_upgrade_page_says_a_crap_at_its_ceiling_reads_ok():
    value = crap(18, 2 / 3)
    block = _block_naming(UPGRADING, "CRAP(18, 2/3)")

    assert remedy(18, value, 30) == "ok"
    assert "`target = 30`" in block
    assert "`ok`" in block
