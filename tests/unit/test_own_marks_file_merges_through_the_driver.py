"""crapkit's own crapkit-ratchet.tsv merges through crapkit's merge driver.

The repository tracks a marks file, and git merged it as text: no attribute named
a driver, so a merge between two worktrees, or one that lands a branch, left a
conflict for a hand to resolve. docs/ratchet.md forbids exactly that, since a
hand-resolved conflict is where a mark gets raised. .gitattributes names the
driver. git never takes a driver command from a committed file, so each of the
three setup lists (CONTRIBUTING's Setup, AGENTS.md's Setup and README's
Development block) gives the one git config line that defines it.
"""
from pathlib import Path
import re

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
DRIVER = 'git config merge.crapkit-ratchet.driver "python -P -m crapkit ratchet merge %O %A %B"'


def test_the_marks_file_names_crapkit_s_merge_driver():
    done = hang_guard.run(["git", "check-attr", "merge", "--", "crapkit-ratchet.tsv"], cwd=ROOT)

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert done.stdout.decode("utf-8") == "crapkit-ratchet.tsv: merge: crapkit-ratchet\n"


def test_contributing_setup_defines_the_driver_the_attribute_names():
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    setup = re.search(r"^## Setup\n.*?^```\n(.*?)^```", text, re.M | re.S).group(1)

    assert DRIVER in setup.splitlines()
    assert "docs/ratchet.md#the-git-merge-driver" in text


def test_agents_and_readme_setup_give_the_same_driver_line():
    """A clone set up from AGENTS.md or from README's Development block also
    merges the marks file through the driver."""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    setup = re.search(r"^## Setup\n(.*?)^## ", agents, re.M | re.S).group(1)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    development = re.search(r"^## Development\n.*?^```\n(.*?)^```", readme, re.M | re.S).group(1)

    assert f"    {DRIVER}" in setup.splitlines()
    assert DRIVER in development.splitlines()
