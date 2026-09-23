"""The newest analysis version the CHANGELOG names is the one crapkit runs.

A release that moves ANALYSIS_VERSION makes every repo re-seed its marks, and
the CHANGELOG is where an upgrader learns that. The reader moved to version 11
while the newest version the file named was still 10, from 0.7.4's "analysis
version 10 remain compatible".
"""
import re
from pathlib import Path

from crapkit.analyze import ANALYSIS_VERSION

ROOT = Path(__file__).resolve().parents[2]


def test_the_first_analysis_version_named_is_the_running_one():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    named = re.search(r"analysis version (\d+)", text, re.I)

    assert named, "the CHANGELOG names no analysis version"
    assert int(named.group(1)) == ANALYSIS_VERSION
