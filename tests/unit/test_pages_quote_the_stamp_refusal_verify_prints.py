"""Every page that quotes verify's metric-stamp refusal, or its inferred-mode
warning, quotes the words it prints.

The refusal names the recorded stamp, the marks file, each moved scope with
each language old number to new, and the seed that clears that scope. Each
quote is rendered again from its own parts: the quoted stamp, or a per-scope
one at each scope's old number, against a running stamp at the new number.
"""
import re
from pathlib import Path

import pytest

from crapkit import invocation
from crapkit.cli.verifying import _inferred_warning
from crapkit.ratchet import MetricStamp, ScopeStamp, ScopeStamps, stamp_conflict

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES = ("README.md", "docs/handbook.html", "docs/ratchet.md", "docs/upgrading.md")
LIZARD = "1.24.0"
_QUOTE = re.compile(r"crapkit: (ratchet marks were recorded under \[([^\]]*)\] in (\S+), and the rules "
                    r"have moved since in \d+ scope\(s\): (.+?) - [^\n<]*)")
_MOVE = re.compile(r"(\w+) \((\w+) analysis (\d+) to (\d+)\)")
_PER_SCOPE = "crapkit-stamp=2 lizard="


@pytest.fixture(autouse=True)
def console_script(monkeypatch):
    """The pages run `$ crapkit verify`, so the refusal names that spelling."""
    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)


def _plain_quotes(page: str) -> list[re.Match]:
    """The quotes of the plain refusal; a pinned store's names its run instead."""
    text = (ROOT / page).read_text(encoding="utf-8")
    return [m for m in _QUOTE.finditer(text) if "re-baseline from run" not in m.group(1)]


def _stamp(moves: list, at: int) -> ScopeStamps:
    """Each named scope running its one language at its old (0) or new (1) number, cc-only."""
    return ScopeStamps(LIZARD, {name: ScopeStamp({language: int(numbers[at])}, "cc-only")
                                for name, language, *numbers in moves})


def _rendered(quote: re.Match) -> str:
    """The refusal crapkit prints for the stamp, file, scopes and numbers the quote names."""
    moves = _MOVE.findall(quote.group(4))
    label = quote.group(2)
    recorded = _stamp(moves, 0) if label.startswith(_PER_SCOPE) else MetricStamp.parse(label)
    conflict = stamp_conflict(recorded, _stamp(moves, 1), frozenset(name for name, *_ in moves))
    return conflict.refusal(quote.group(3))


@pytest.mark.parametrize("page", PAGES)
def test_the_page_quotes_the_refusal_verify_prints(page):
    quotes = _plain_quotes(page)

    assert quotes, f"{page} no longer quotes the stamp refusal"
    assert [m.group(1) for m in quotes] == [_rendered(m) for m in quotes]


# The upgrade guide quotes the warning whole; the recover skill's table cuts it after "lanes".
_WARNING = re.compile(r"warning: (\S+) records one analysis number and no coverage mode, so verify read "
                      r"scope\(s\) (.+?) as measured by[^\"\n]*")


@pytest.mark.parametrize("page", ("docs/upgrading.md", "plugin/skills/crapkit-recover/SKILL.md"))
def test_the_page_quotes_the_inferred_mode_warning_verify_prints(page):
    """A 0.8.x marks file's first verify under this release prints this
    warning; each quote is the start of the line for the file and scopes it names."""
    quotes = list(_WARNING.finditer((ROOT / page).read_text(encoding="utf-8")))

    assert quotes, f"{page} no longer quotes the inferred-mode warning"
    for quote in quotes:
        printed = _inferred_warning(tuple(quote.group(2).split(", ")), quote.group(1))
        assert printed.startswith(quote.group(0)), (quote.group(0), printed)
