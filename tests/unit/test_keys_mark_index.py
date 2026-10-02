"""keys.MarkIndex: which mark a function is judged against, built once from the marks.

The hand table below is the calc's first check. Each row names the marks a file
holds, the key a function is looked up by, and the mark that answers. The key is
the one `keys.key_names` gives the function: its long_name, `name#N` for the Nth
twin, and `(anonymous)#N` for a callback.
"""
from __future__ import annotations

import pytest

from crapkit import keys, ratchet
from crapkit.ratchet import RatchetEntry
from crapkit.records import encode_record
from crapkit.score import ScoredRow


def row(path: str, long_name: str, start: int, crap: float, scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, path, long_name, start, start + 5, 4, 4, 4, 6, 1, 1, 0.5, "",
                     crap, "", occurrence=1)


def marks(*rows: tuple[str, str, float]) -> list[RatchetEntry]:
    return [RatchetEntry(*r) for r in rows]


LONE = marks(("src/a.py", "f( x )", 31.25))
TWINS = marks(("src/a.py", "f( x )", 31.25), ("src/a.py", "f( x )#2", 44.0))
TWICE = marks(("src/a.py", "f( x )", 31.25), ("src/a.py", "f( x )", 12.5))
ANONYMOUS = marks(("web/a.js", "(anonymous)#2", 17.0))

# (case, marks, key looked up, the mark that answers)
HAND = [
    ("a lone function", LONE, ("src/a.py", "f( x )"), 31.25),
    ("a lone function in another file", LONE, ("src/b.py", "f( x )"), None),
    ("twin #1 keeps the bare name", TWINS, ("src/a.py", "f( x )"), 31.25),
    ("twin #2", TWINS, ("src/a.py", "f( x )#2"), 44.0),
    ("a twin nothing marked", TWINS, ("src/a.py", "f( x )#3"), None),
    ("two marks under one key: the first wins", TWICE, ("src/a.py", "f( x )"), 31.25),
    ("an anonymous handle", ANONYMOUS, ("web/a.js", "(anonymous)#2"), 17.0),
    ("an anonymous handle nothing marked", ANONYMOUS, ("web/a.js", "(anonymous)"), None),
]


@pytest.mark.parametrize("case, entries, key, want", HAND, ids=[case for case, *_ in HAND])
def test_the_hand_table(case, entries, key, want):
    index = keys.MarkIndex(entries)

    assert index.mark(key) == index.get(key) == want
    assert (key in index) is (want is not None)
    assert index.mark(key) == ratchet.mark_for(entries, *key), "mark_for and the index pick one winner"


def test_entry_answers_the_first_mark_under_its_key():
    index = keys.MarkIndex(TWICE)

    assert index.entry(("src/a.py", "f( x )")) is TWICE[0]
    assert index.entry(("src/a.py", "g( )")) is None


def test_the_key_set_holds_each_key_once():
    assert set(keys.MarkIndex(TWINS + TWICE).keys()) == {("src/a.py", "f( x )"), ("src/a.py", "f( x )#2")}


def test_a_legacy_bare_mark_answers_twin_one_and_no_other():
    """A mark written before the ordinal holds the bare name, and twin #1's key is
    the bare name, so the mark reads as twin #1's and twin #2 stays unmarked."""
    rows = [row("src/a.py", "f( x )", 10, 50.0), row("src/a.py", "f( x )", 40, 60.0)]
    names = keys.key_names(rows)
    index = keys.MarkIndex(LONE)

    assert [index.mark(keys.key_of(names, r)) for r in rows] == [31.25, None]


def test_a_path_holding_a_tab_reads_back_through_the_record_line():
    path = "src/odd\tdir/a.py"
    line = encode_record((path, "f( x )", "31.2500"))
    entries = ratchet.load_ratchet(f"path\tlong_name\tcrap\n{line}\n")

    assert line.startswith("@crapkit-record-v1")
    assert keys.MarkIndex(entries).mark((path, "f( x )")) == 31.25


def test_rows_by_key_keeps_the_worst_when_two_scopes_score_one_span():
    """Two scopes claiming one path score the same span twice; the worse copy
    stands for the key whichever comes first, so a regression cannot hide
    behind a clean copy."""
    clean, worse = row("src/a.py", "f( x )", 10, 12.0, "lib"), row("src/a.py", "f( x )", 10, 30.0, "app")

    for fresh in ([clean, worse], [worse, clean]):
        assert keys.rows_by_key(fresh) == {("src/a.py", "f( x )"): worse}


def test_rows_by_key_gives_each_twin_its_own_key():
    first, second = row("src/a.py", "f( x )", 10, 12.0), row("src/a.py", "f( x )", 40, 30.0)

    assert keys.rows_by_key([second, first]) == {("src/a.py", "f( x )"): first,
                                                 ("src/a.py", "f( x )#2"): second}


# --- one winner wherever the mark is read ---------------------------------------
#
# TWICE lists f( x ) at 31.25 and then at 12.5. The index answers 31.25, so every
# reader in verify and ratchet judges, keeps and counts that mark and no other.

TOUCHED_AT_20 = [row("src/a.py", "f( x )", 10, 20.0)]


def test_the_gate_and_the_ratchet_check_judge_one_key_against_one_mark():
    """20.0 sits under the first mark and over the second. The gate pardons it
    against 31.25, so the ratchet check reads 31.25 too and finds no rise."""
    from crapkit import verify

    verdict = verify.evaluate(fresh=TOUCHED_AT_20, changed_ranges={"src/a.py": [(10, 15)]},
                              ratchet=TWICE, baseline_failures=set(), fresh_failures=set(), target=8)

    assert (verdict.ok, verdict.gate_violations, verdict.ratchet_regressions) == (True, [], [])


def test_a_rise_past_the_first_mark_is_one_regression_against_it():
    from crapkit import verify

    fresh = [row("src/a.py", "f( x )", 10, 40.0)]
    verdict = verify.evaluate(fresh=fresh, changed_ranges={}, ratchet=TWICE,
                              baseline_failures=set(), fresh_failures=set(), target=8)

    assert [(r.recorded, r.fresh_crap) for r in verdict.ratchet_regressions] == [(31.25, 40.0)]


def test_the_update_keeps_both_lines_and_counts_the_key_once():
    """The update rewrites lines, so both stay in order, and the first, now at
    20.0, still answers. The counts read the index: one key, one tighten."""
    updated = ratchet.update_ratchet(TWICE, TOUCHED_AT_20, target=8)

    assert updated == marks(("src/a.py", "f( x )", 20.0), ("src/a.py", "f( x )", 12.5))
    assert keys.MarkIndex(updated).mark(("src/a.py", "f( x )")) == 20.0
    assert ratchet.ratchet_delta(TWICE, updated) == ratchet.RatchetDelta(dropped=0, tightened=1)


def test_the_update_counts_a_dropped_key_once():
    fresh = [row("src/a.py", "f( x )", 10, 5.0)]

    assert ratchet.update_ratchet(TWICE, fresh, target=8) == []
    assert ratchet.ratchet_delta(TWICE, []) == ratchet.RatchetDelta(dropped=1, tightened=0)


def test_the_tighten_guard_refuses_a_key_once():
    refusals = ratchet.unstable_marks(TWICE, [row("src/a.py", "f( x )", 10, 90.0)],
                                      {("src/a.py", "f( x )"): 20.0}, max_jump=2.0)

    assert [(r.long_name, r.previous, r.fresh) for r in refusals] == [("f( x )", 20.0, 90.0)]
