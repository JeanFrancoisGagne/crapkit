"""Every page that quotes verify's metric-stamp refusal quotes the words it prints.

The refusal's remedy became "run `crapkit coverage`, then re-baseline with
`crapkit ratchet seed`", because seed stamps the metric of the run it reads and
a seed right after an upgrade reads the older crapkit's run. docs/ratchet.md
moved with it; the README and the handbook still told the reader to run the
seed alone, and the handbook's next sentence says "Do what it says."
"""
import re
import sys
from pathlib import Path

import pytest

from crapkit.ratchet import stamp_conflict

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ("README.md", "docs/handbook.html", "docs/ratchet.md")
_QUOTE = re.compile(r"crapkit: (ratchet marks were recorded under \[([^\]]*)\] "
                    r"but this run measures \[([^\]]*)\][^\n<]*)")


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    """The pages run `$ crapkit verify`, so the refusal names that spelling."""
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/crapkit", "verify"])


def _plain_quotes(page: str) -> list[re.Match]:
    """The quotes of the plain refusal; a pinned store's names its run instead."""
    text = (ROOT / page).read_text(encoding="utf-8")
    return [m for m in _QUOTE.finditer(text) if "re-baseline from run" not in m.group(1)]


@pytest.mark.parametrize("page", PAGES)
def test_the_page_quotes_the_refusal_verify_prints(page):
    quotes = _plain_quotes(page)

    assert quotes, f"{page} no longer quotes the stamp refusal"
    assert [m.group(1) for m in quotes] == [stamp_conflict(m.group(2), m.group(3)) for m in quotes]
