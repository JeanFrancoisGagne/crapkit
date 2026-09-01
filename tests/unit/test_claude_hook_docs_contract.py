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
from types import SimpleNamespace

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


# --- the advisory's own wording --------------------------------------------
# `_advisory_lines` is a pure function and its first line is the one every
# reader meets: it opens the stderr the model holds beside a nonzero exit. Three
# pages print a rendered sample of it and nothing compared them with the format
# string, so rewording the parenthetical left all three teaching the old
# sentence — AGENTS.md included, which is where an agent learns what exit 2
# means.
#
# The values differ per page (each names its own file), so the sample supplies
# them and the function supplies the wording around them. Comparing the whole
# line is then a comparison of wording alone.
#
# `plugin/skills/crapkit-recover/SKILL.md` prints the line with `N`, `C` and
# `PATH` left as placeholders rather than a rendered sample, so it is not one of
# these pages.
ADVISORY_PAGES = ("AGENTS.md", "docs/agent-json.md", "docs/handbook.html")

_VALUES = re.compile(r"(\d+) function\(s\) over ceiling (\d+) in (\S+)")

_BREACH = SimpleNamespace(ccn=9, start=41, long_name="refreshToken ( req , store )")


def advisory_samples(text: str) -> list[str]:
    """Every line a page prints as advisory output. A line that only mentions
    the advisory mid-sentence is not a sample and is not read as one."""
    stripped = (line.strip() for line in text.splitlines())
    return [line for line in stripped if line.startswith("crapkit advisory:")]


def rendered_head(sample: str) -> str:
    """The head line the hook would build for the values this sample names."""
    values = _VALUES.search(sample)
    assert values, f"no count, ceiling and path to read out of {sample!r}"
    count, ceiling, rel = int(values.group(1)), int(values.group(2)), values.group(3)
    return claude_hook._advisory_lines(rel, [_BREACH] * count, ceiling)[0]


@pytest.mark.parametrize("page", ADVISORY_PAGES)
def test_the_page_prints_the_head_line_the_hook_builds(page):
    samples = advisory_samples(_prose(page))

    assert samples, f"{page} no longer prints the advisory head line"
    for sample in samples:
        assert sample == rendered_head(sample), page


@pytest.mark.parametrize("page", ADVISORY_PAGES)
def test_the_page_prints_the_closing_line_the_hook_builds(page):
    """The third line is the other half a reader acts on: it says where the
    breach is enforced. It comes out of the same function and drifts the same way."""
    closing = claude_hook._advisory_lines("calc/grade.py", [_BREACH], 6)[-1]

    assert closing in _prose(page), f"{page} does not carry {closing!r}"


def test_a_page_that_kept_an_older_wording_would_be_caught():
    """The reader this contract exists for: a page still promising the commit
    gate's sentence on output that blocked nothing."""
    stale = ("crapkit advisory: 1 function(s) over ceiling 6 in calc/grade.py "
             "(decompose before committing)")

    assert rendered_head(stale) != stale
