"""The two numbers the Bash fallback runs on, pinned to every page that prints them.

`crapkit claude-hook` judges a Bash write off the working tree: the changed
*.py files whose mtime sits inside `_FRESH_WINDOW_SECONDS`, at most
`_MAX_COMMAND_FILES` of them. Seven pages print those two numbers in prose, so a
tuning change to either constant would leave all seven wrong and green. This
reads the numbers back out of each page and compares them with the module.

A page that names the window has to name the cap too: the reader who learns
"12 seconds" and not "25 files" is told half the rule.
"""
import re
from pathlib import Path

import pytest

from crapkit.cli import claude_hook

ROOT = Path(__file__).resolve().parents[2]
PAGES = (
    "README.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "docs/agent-json.md",
    "docs/handbook.html",
    "plugin/skills/crapkit/SKILL.md",
    "plugin/skills/crapkit-onboard/SKILL.md",
)

# "12-second window", "in the last 12 seconds, 25 at most": the number before
# the unit is the window when the same sentence goes on to name the window or
# the cap. The 60-second start and the plugin's 20-second timeout do not, and
# the churn window ("12 months") never matches the unit.
_WINDOW = re.compile(r"\b(\d+)\s*-?\s*seconds?\b(?=[^.]{0,80}\b(?:window|at most|capped at))")
# "at most 25", "capped at 25 files", "25 at most", "cap of 25": the number
# beside the cap words is the cap.
_CAP = re.compile(r"(?:at most|capped at|cap of)\s+(\d+)\b|\b(\d+)\s+at most\b")


def _prose(page: str) -> str:
    text = (ROOT / page).read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", text) if page.endswith(".html") else text
    return text.replace("**", "")


def numbers_stated(text: str) -> tuple[set[int], set[int]]:
    """Every window and every cap a page states, as the reader would read them."""
    windows = {int(m.group(1)) for m in _WINDOW.finditer(text)}
    caps = {int(m.group(1) or m.group(2)) for m in _CAP.finditer(text)}
    return windows, caps


@pytest.mark.parametrize("page", PAGES)
def test_the_page_states_the_window_and_the_cap_the_hook_runs_on(page):
    windows, caps = numbers_stated(_prose(page))

    assert windows == {claude_hook._FRESH_WINDOW_SECONDS}, f"{page} window(s) {windows}"
    assert caps == {claude_hook._MAX_COMMAND_FILES}, f"{page} cap(s) {caps}"


def test_a_page_with_a_different_number_would_be_caught():
    """The reader this contract exists for: a page that kept the old window."""
    windows, caps = numbers_stated("inside a 30-second window, at most 25 of them")

    assert windows == {30}
    assert caps == {25}
