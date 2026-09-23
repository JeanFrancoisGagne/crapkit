"""Both contributor pages show how to run one test session, as each Windows CI job does.

CI gives each Windows suite a job of its own, and that job calls
`tools/testing/run.py --suite unit` or `--suite e2e`. AGENTS.md and CONTRIBUTING.md
never named the flag, so a contributor reproducing one red Windows job ran both
sessions. The suites the pages name are compared with the ones the runner accepts.
"""
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITES = runpy.run_path(str(ROOT / "tools/testing/run.py"))["SUITES"]


@pytest.mark.parametrize("page", ("AGENTS.md", "CONTRIBUTING.md"))
def test_the_page_names_the_flag_for_each_suite(page):
    text = (ROOT / page).read_text(encoding="utf-8")

    assert [suite for suite in SUITES if f"`--suite {suite}`" not in text] == []
