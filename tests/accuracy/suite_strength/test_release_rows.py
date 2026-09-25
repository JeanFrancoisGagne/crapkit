"""The release tier's suite-strength row that runs as a test: no ruling waits on JF.

A rulings row whose outside_support is `convention_only` has no source outside
crapkit for the value crapkit chose; JF rules on each before a release (the
accuracy plan's release gate, row 7). His answer replaces `convention_only`
with the anchor where the answer is written down, so a release tier that finds
any row still at `convention_only` fails, naming each.
"""
from __future__ import annotations

import pytest

from accuracy.kit import rulings


@pytest.mark.release
def test_no_convention_only_ruling_waits_for_an_answer():
    waiting = rulings.convention_only(rulings.load())

    assert waiting == [], f"answer these rulings rows before a release: {', '.join(waiting)}"
