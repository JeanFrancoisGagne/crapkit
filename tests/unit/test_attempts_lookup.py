"""Prior claims on a function, read back for the packet.

A packet says whether somebody already took this function and handed it back.
The store keeps that in `attempts`; what it lacked was a read keyed by function
rather than by "everything still open", and a read that answers a whole batch
of functions in one query instead of one per packet.
"""
from crapkit.store import SnapshotStore

FN = ("core/alpha.py", "alpha( a , b )")
OTHER = ("core/alpha.py", "helper( a )")
ELSEWHERE = ("core/beta.py", "beta( a )")


def store_with_claims(tmp_path) -> SnapshotStore:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    first = store.record_claim(path=FN[0], long_name=FN[1], commit="c0ffee1")
    store.close_claims([first])
    store.record_claim(path=FN[0], long_name=FN[1], commit="c0ffee2")
    store.record_claim(path=OTHER[0], long_name=OTHER[1], commit="c0ffee3")
    return store


def test_a_functions_prior_claims_come_back_oldest_first(tmp_path):
    rows = store_with_claims(tmp_path).attempts_for([FN])[FN]

    assert len(rows) == 2
    assert rows[0]["closed"] is not None, "the first claim was handed back"
    assert rows[1]["closed"] is None, "the second is still held"
    assert rows[0]["opened"] <= rows[1]["opened"]


def test_a_claim_on_another_function_in_the_same_file_is_not_this_ones(tmp_path):
    out = store_with_claims(tmp_path).attempts_for([FN, OTHER])

    assert len(out[FN]) == 2 and len(out[OTHER]) == 1


def test_a_function_nobody_ever_claimed_reads_as_an_empty_list(tmp_path):
    out = store_with_claims(tmp_path).attempts_for([ELSEWHERE])

    assert out == {ELSEWHERE: []}, "every requested key answers, so no caller needs .get"


def test_one_query_answers_the_whole_batch(tmp_path):
    """N packets used to mean N round trips for the same table."""
    store = store_with_claims(tmp_path)
    statements = []
    store._conn.set_trace_callback(statements.append)

    store.attempts_for([FN, OTHER, ELSEWHERE])

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects


def test_asking_about_nothing_asks_the_database_nothing(tmp_path):
    store = store_with_claims(tmp_path)
    statements = []
    store._conn.set_trace_callback(statements.append)

    assert store.attempts_for([]) == {}
    assert statements == []
