"""The worklist's entries, batches, hot threshold, floor and claims, hand-counted.

worklist.py: an entry carries its row's scope, span, ccn columns and nloc; a
batch lists its entries in rank order, risk first; the hot threshold is the
90th-percentile file weight once five files weigh something and not all the
same; the floor is the lowest ccn whose worst CRAP, ccn^2 + ccn, is over the
smallest ceiling; and a claim that names its twin closes when that twin is
done, whatever its sibling does.
"""
from crapkit.churn import FileChurn
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.worklist import (WorklistEntry, admission, build_worklist, closable_claims,
                              over_target_floor, split_batches)


def test_an_entry_carries_its_row_s_scope_span_ccn_columns_and_nloc():
    row = InventoryRow("web", "a.py", "f", 3, 9, 4, 6, 5, 7, 1, 0)

    [entry] = build_worklist([row], {"a.py": FileChurn(2, 1, 2.0)}, floor=1, top=5).active

    assert entry[:12] == ("web", "a.py", "f", 3, 9, 5, 4, 7, 2, 1, 2.0, 10.0)


def entry(scope: str, name: str, ccn: int, risk: float) -> WorklistEntry:
    return WorklistEntry(scope, "x.py", name, ccn, ccn + 1, ccn, ccn, 2, 1, 1, 1.0, risk)


def test_a_batch_lists_its_entries_by_risk():
    low, high = entry("a", "g", 2, 1.0), entry("b", "f", 9, 9.0)

    [batch] = split_batches([low, high], [], batches=1)

    assert batch.entries == [high, low]


def test_the_hot_threshold_needs_only_the_lightest_file_to_differ():
    weights = {f"{n}.py": FileChurn(1, 1, w) for n, w in enumerate((1.0, 5.0, 5.0, 5.0, 5.0))}

    assert admission(weights, 3).hot == 5.0


def test_the_floor_is_the_first_ccn_whose_worst_score_is_over_the_ceiling():
    """2^2 + 2 = 6 is not over 6; 3^2 + 3 = 12 is."""
    assert over_target_floor(6) == 3


def test_a_claim_on_the_finished_twin_closes_while_its_sibling_is_over():
    """Twins keep `f` and `f#2` by start line: f scores 3, at or under 6; f#2 scores 40."""
    scored = [ScoredRow("web", "a.py", "f", start, start + 5, 2, 2, 2, 6, 1, 1, 0.0, "measured",
                        crap, "ok") for start, crap in ((1, 3.0), (10, 40.0))]
    claim = {"id": 7, "path": "a.py", "long_name": "f", "key_name": "f", "commit": "c1"}

    assert closable_claims([claim], scored, target=6, scope_targets=None,
                           stale_commits=set()) == [7]
