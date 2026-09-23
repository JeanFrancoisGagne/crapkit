"""AGENTS.md describes the fixture lanes and the hang bound the suite has now.

mini_repo's py lane moved to `-n 0` and the one xdist test got
mini_repo_xdist with `-n 2`, while the Setup section still said mini_repo spells
`-n 2`. Every test-side wait on a child went through tests/hang_guard.py, and
the Tests section never named it, so a contributor wrote a numeric bound and
met test_one_hang_bound's refusal with no page that says why.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
_PAGE_CLAIM = re.compile(r"`tests/fixtures/(mini_repo\w*)`[^`]*`pytest \.\.\. -n (\d+)`")
_LANE_WORKERS = re.compile(r"-n (\d+)")


def _section(heading: str) -> str:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    return " ".join(text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0].split())


def _fixture_workers(name: str) -> str:
    config = (FIXTURES / name / "crapkit.toml").read_text(encoding="utf-8")
    return _LANE_WORKERS.search(config).group(1)


def test_setup_quotes_each_fixture_lanes_worker_count():
    claims = dict(_PAGE_CLAIM.findall(_section("## Setup")))

    assert claims == {name: _fixture_workers(name) for name in ("mini_repo", "mini_repo_xdist")}


@pytest.mark.parametrize("name", ["HANG_SECONDS", "CHILD_WAIT", "CHILD_HOLD"])
def test_tests_names_the_hang_guard_and_what_a_child_spells(name):
    guard = (ROOT / "tests" / "hang_guard.py").read_text(encoding="utf-8")
    section = _section("## Tests")

    assert re.search(rf"^{name} = ", guard, re.M), f"hang_guard.py no longer defines {name}"
    assert "`tests/hang_guard.py`" in section and f"`{name}`" in section
