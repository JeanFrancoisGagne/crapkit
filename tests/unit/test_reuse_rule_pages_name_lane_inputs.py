"""Every page that states the automatic reuse rule gives both of its halves.

`--reuse-unchanged` reuses a lane at the same clean HEAD, or, for a lane that
lists its `inputs`, while nothing under those paths changed since the
artifact's commit. The packet's `refresh` field, AGENTS.md's refresh paragraph
and the handbook gave only the first half, so an agent told a lane "reruns on
any new commit" never declared the inputs that would have let it reuse one.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("AGENTS.md", "docs/agent-json.md", "docs/handbook.html", "docs/lanes.md",
         "docs/upgrading.md", "docs/configuration.md", "README.md")
_BLOCK_END = re.compile(r"\n\s*\n|\n(?=\|)|</li>|</p>")
# The config key, spelled as code: "ignored external inputs" is not it.
_INPUTS_KEY = re.compile(r"`inputs`|<code>inputs</code>")


def _blocks_stating_the_rule(page: str) -> list[str]:
    text = (ROOT / page).read_text(encoding="utf-8")
    return [block for block in _BLOCK_END.split(text) if "same clean HEAD" in block]


@pytest.mark.parametrize("page", PAGES)
def test_each_statement_of_the_rule_names_lane_inputs(page):
    unnamed = [block[:120] for block in _blocks_stating_the_rule(page)
               if not _INPUTS_KEY.search(block)]

    assert unnamed == []
