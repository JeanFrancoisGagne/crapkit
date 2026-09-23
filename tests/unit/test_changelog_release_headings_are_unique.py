"""A release's CHANGELOG section says each thing once.

The 0.7.6 section carried "Running it from a repo that is not Python" and "The
docs site has a new address" twice each, word for word, after a merge replayed
them. A reader skimming the headings took them for two changes.
"""
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _releases() -> list[tuple[str, str]]:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return list(zip(parts[1::2], parts[2::2]))


def test_no_release_repeats_a_heading():
    repeated = {release: sorted(h for h, n in Counter(re.findall(r"^### (.+)$", body, re.M)).items()
                                if n > 1)
                for release, body in _releases()}

    assert {r: h for r, h in repeated.items() if h} == {}
