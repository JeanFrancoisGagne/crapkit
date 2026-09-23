"""The contributor pages send test evidence retention to the runner's own flags.

Retention moved from `test_retention_days` and `test_retention_count` in
crapkit.toml to tools/testing/run.py, which alone writes and prunes
.crapkit/test-runs. CONTRIBUTING still said to set the two keys and preview with
`crapkit clean`, which no longer touches test evidence. The flags a contributor
types are compared with the ones the runner registers.
"""
import argparse
import importlib.util
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("CONTRIBUTING.md", "docs/resources.md")


@lru_cache(maxsize=1)
def _runner_flags() -> tuple[str, ...]:
    """The option strings run.py's retention block adds to a parser."""
    spec = importlib.util.spec_from_file_location("_crapkit_dev_runner_flags",
                                                  ROOT / "tools/testing/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = argparse.ArgumentParser(add_help=False)
    module._retention_flags(parser)
    return tuple(flag for action in parser._actions for flag in action.option_strings)


@pytest.mark.parametrize("page", PAGES)
def test_the_page_names_every_runner_retention_flag(page):
    text = (ROOT / page).read_text(encoding="utf-8")

    assert [flag for flag in _runner_flags() if f"`{flag}" not in text] == []


@pytest.mark.parametrize("page", PAGES)
def test_the_page_does_not_send_retention_to_crapkit_toml(page):
    text = (ROOT / page).read_text(encoding="utf-8")

    assert "`test_retention_days` and `test_retention_count` in `crapkit.toml`" not in text
