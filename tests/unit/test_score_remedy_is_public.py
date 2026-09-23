"""score.remedy is the scoring rule's verdict, public for the packet that rejudges a row.

packet.rejudged judges a stored row again against today's ceiling, and it
imported the rule under its private name. The four answers, in the order the
rule asks: ccn over the ceiling, a score at or under it, then the two ways a
score stays over it.
"""
import pytest

from crapkit.score import remedy


@pytest.mark.parametrize("ccn, score, shared_span, verdict", [
    (7, 7.0, False, "decompose"),
    (4, 6.0, False, "ok"),
    (4, 6.5, False, "add-tests"),
    (4, 20.0, True, "split-lines"),
])
def test_the_rule_answers_in_its_order(ccn, score, shared_span, verdict):
    assert remedy(ccn, score, 6, shared_span) == verdict
