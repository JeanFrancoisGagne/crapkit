"""Every page that names the current analysis version names the one crapkit runs.

The reader moved to analysis version 11 while five pages still said 10: the
README's upgrade note, the doctor field, the marks-file example and its stamp
row, and the ratchet page's pointer to the current reader. A reader comparing
a stamp against the docs then took a current marks file for a stale one. The
ratchet page's seed and verify sessions keep their own crapkit, one that
measures analysis 10 (test_verify_reads_its_baseline_before_the_stamp_guard pins
it): they show an upgrade, not the current number.
docs/upgrading.md also owes the running version a section of its own, since an
upgrade across it re-seeds every marks file.
"""
import re
from pathlib import Path

import pytest

from crapkit.analyze import ANALYSIS_VERSION

ROOT = Path(__file__).resolve().parent.parent.parent
CLAIMS = [
    ("README.md", r"The current reader is\s+analysis version (\d+)"),
    ("docs/agent-json.md", r"A current `doctor` reports\s+version (\d+)"),
    ("docs/agent-json.md", r"The analysis semantics version, currently `(\d+)`"),
    ("docs/ratchet.md", r"## What a mark is\s+```\s+# crapkit-analysis=(\d+) "),
    ("docs/ratchet.md", r"the current reader uses version (\d+)"),
    ("docs/ratchet.md", r"\| `# crapkit-analysis=(\d+) lizard=[^`]*` \| The reader"),
    ("docs/upgrading.md", r"^###? Analysis version (\d+)"),
]


@pytest.mark.parametrize(("page", "claim"), CLAIMS, ids=[f"{p}:{c[:24]}" for p, c in CLAIMS])
def test_the_page_names_the_running_analysis_version(page, claim):
    text = (ROOT / page).read_text(encoding="utf-8")
    found = re.search(claim, text, re.M)

    assert found, f"{page} lost the sentence {claim!r}"
    assert int(found.group(1)) == ANALYSIS_VERSION, (
        f"{page} names analysis version {found.group(1)}; crapkit runs {ANALYSIS_VERSION}")
