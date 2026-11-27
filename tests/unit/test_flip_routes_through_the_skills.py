"""A flip refusal sends an agent to the upgrade guide's procedure through both skills.

verify refuses a scope whose coverage mode moved from `cc-only` or `no-lane`
to a reader. The refusal ends on a clause naming the scope and its readers and
on the procedure's anchor, ratchet.FLIP_PROCEDURE. crapkit-recover's
description matches the refusal's opening words, and its paragraph under
"Three exit-3 signatures worth naming" quotes the closing clause and links the
same anchor, so an agent holding the refusal finds the procedure there.
crapkit-onboard's "The repo" names the procedure for a cc-only scope that
later gains a lane. Reverting either paragraph, or moving the anchor in one
place only, fails a test here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from crapkit.ratchet import FLIP_PROCEDURE, ScopeStamp, ScopeStamps, stamp_conflict

SKILLS = Path(__file__).resolve().parents[2] / "plugin" / "skills"
# The skills link the crapkit repo on GitHub, since the repo an agent works in does not hold the page.
LINK = f"https://github.com/JeanFrancoisGagne/crapkit/blob/main/{FLIP_PROCEDURE}"
LIZARD = "1.24.0"
PY13 = {"python": 13}
CC_ONLY_TO_COVERAGEPY = ("cc-only", {"coveragepy": 1})


def flip_refusal(old, new) -> str:
    """verify's refusal for a marked scope `api` whose coverage mode moved from `old` to `new`."""
    recorded = ScopeStamps(LIZARD, {"api": ScopeStamp(PY13, old)})
    running = ScopeStamps(LIZARD, {"api": ScopeStamp(PY13, new)})
    return stamp_conflict(recorded, running, frozenset({"api"})).refusal("crapkit-ratchet.tsv")


@pytest.mark.parametrize("old, new, readers", [
    (*CC_ONLY_TO_COVERAGEPY, "coveragepy"),
    ("no-lane", {"istanbul": 1, "coveragepy": 1}, "coveragepy and istanbul"),
], ids=["one-reader", "two-readers"])
def test_the_flip_clause_names_the_readers_in_order_and_ends_on_the_procedure(old, new, readers):
    assert flip_refusal(old, new).endswith(
        f"; api was {old} and is now measured by its {readers} lane(s), "
        f"so its marks hold no coverage yet: follow {FLIP_PROCEDURE}")


def section(skill: str, heading: str) -> str:
    """The text under a skill's `## heading`, up to its next `## ` heading."""
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def linking(text: str) -> list[str]:
    """The paragraphs of `text` that link the procedure."""
    return [block for block in re.split(r"\n\s*\n", text) if f"]({LINK})" in block]


def test_crapkit_recover_sends_the_flip_refusal_to_the_procedure():
    refusal = flip_refusal(*CC_ONLY_TO_COVERAGEPY)
    description = (SKILLS / "crapkit-recover" / "SKILL.md").read_text(encoding="utf-8").split("\n---", 1)[0]
    (paragraph,) = linking(section("crapkit-recover", "Three exit-3 signatures worth naming"))
    quoted = re.findall(r"`([^`]+)`", paragraph)

    assert refusal.startswith("ratchet marks were recorded under") and '\\"were recorded under\\"' in description
    assert [clause for clause in quoted if refusal.endswith(f"{clause} {FLIP_PROCEDURE}")], (quoted, refusal)


def test_crapkit_onboard_names_the_procedure_for_a_cc_only_scope_that_gains_a_lane():
    (paragraph,) = linking(section("crapkit-onboard", "The repo"))
    (sentence,) = [part for part in re.split(r"(?<=\.)\s+", paragraph) if LINK in part]
    lead = sentence.split("[", 1)[0]

    assert "cc-only scope" in lead and "gains a lane" in lead, sentence
