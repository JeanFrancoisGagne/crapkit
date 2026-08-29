"""Ratchet seam: committed TSV text <-> entries; updates only ever tighten. Pure."""
import pytest

from crapkit.ratchet import RatchetEntry, load_ratchet, update_ratchet, dump_ratchet
from crapkit.score import ScoredRow


def scored(path, name, ccn, cov, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "x")


def test_round_trip_sorted_and_stable():
    entries = [RatchetEntry("src/b.ts", "g( )", 12.5), RatchetEntry("src/a.ts", "f( )", 30.0)]
    text = dump_ratchet(entries)
    assert text == dump_ratchet(load_ratchet(text)), "dump-load-dump is a fixed point"
    assert text.index("src/a.ts") < text.index("src/b.ts")


def test_update_tightens_improved_entries_and_drops_fixed_ones():
    """`src/ok.ts` is MARKED and now scores under the ceiling: that is the drop.

    It used to be absent from `prior`, so the last assertion held for the wrong
    reason — an unmarked function has no entry to lose — and the drop path was
    never walked.
    """
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0), RatchetEntry("src/gone.ts", "h( )", 12.0),
             RatchetEntry("src/ok.ts", "k( )", 20.0)]
    fresh = [scored("src/a.ts", "f( )", 5, 0.5), scored("src/ok.ts", "k( )", 3, 1.0)]
    updated = update_ratchet(prior, fresh, target=6)
    by_key = {(e.path, e.long_name): e for e in updated}
    assert by_key[("src/a.ts", "f( )")].crap < 30.0, "improvement lowers the high-water mark"
    assert by_key[("src/gone.ts", "h( )")].crap == 12.0,         "absent from scored rows is not proof the code is gone; the mark survives"
    assert ("src/ok.ts", "k( )") not in by_key, "a mark that fell to the ceiling leaves the file"


def test_update_never_raises_an_entry():
    prior = [RatchetEntry("src/a.ts", "f( )", 10.0)]
    fresh = [scored("src/a.ts", "f( )", 5, 0.0)]  # crap 30, worse
    updated = update_ratchet(prior, fresh, target=6)
    (entry,) = updated
    assert entry.crap == 10.0, "a regression must never rewrite the mark upward"


def test_worsened_function_above_target_without_prior_entry_needs_override_not_silent_add():
    fresh = [scored("src/new.ts", "n( )", 9, 0.0)]
    updated = update_ratchet([], fresh, target=6)
    assert updated == [], "new debt enters the ratchet only through the audited override"


def test_malformed_ratchet_line_is_loud():
    with pytest.raises(ValueError, match="ratchet"):
        load_ratchet("src/a.ts\tonly-two-fields\n")


def test_update_compares_against_the_worst_twin():
    prior = [RatchetEntry("src/a.ts", "handlers ( )", 90.0)]
    fresh = [scored("src/a.ts", "handlers ( )", 2, 1.0), scored("src/a.ts", "handlers ( )", 9, 0.0)]
    (entry,) = update_ratchet(prior, fresh, target=6)
    assert entry.crap == 90.0, "the surviving worst twin (crap 90) keeps the mark; the clean twin cannot tighten it"


def test_write_then_verify_is_a_fixed_point_for_nonterminating_coverage():
    # cov = 2/3 gives crap 13.703703..., stored as 13.7037. An UNCHANGED tree
    # must verify clean against the mark it just wrote, or one passing verify
    # permanently wedges every later one at exit 7 (override refused).
    from crapkit.ratchet import dump_ratchet, load_ratchet, update_ratchet
    from crapkit.score import ScoredRow, crap
    from crapkit.verify import evaluate
    c = crap(10, 2 / 3)
    row = ScoredRow("src", "a.py", "f( )", 1, 9, 10, 10, 10, 8, 1, 1, 2 / 3, "measured", c, "decompose")
    marks = load_ratchet(dump_ratchet(update_ratchet(
        [__import__("crapkit.ratchet", fromlist=["RatchetEntry"]).RatchetEntry("a.py", "f( )", 20.0)],
        [row], target=6)))
    verdict = evaluate(fresh=[row], changed_ranges={}, ratchet=marks,
                       baseline_failures=set(), fresh_failures=set(), target=6)
    assert verdict.ratchet_regressions == [], \
        "the mark the ratchet itself wrote must not read as a regression"


def test_marks_survive_functions_missing_from_the_scored_rows():
    # An exclude glob (or a lane outage) removes a function from the scored
    # rows without deleting the code; dropping its entry would erase the only
    # diff-visible record of an audited override.
    from crapkit.ratchet import RatchetEntry, update_ratchet
    kept = update_ratchet([RatchetEntry("a.py", "audited( )", 110.0)], [], target=6)
    assert kept == [RatchetEntry("a.py", "audited( )", 110.0)]


def test_mark_lookup_answers_with_the_recorded_value_or_none():
    from crapkit.ratchet import mark_for
    entries = [RatchetEntry("src/a.ts", "f( )", 30.0), RatchetEntry("src/b.ts", "f( )", 12.5)]
    assert mark_for(entries, "src/b.ts", "f( )") == 12.5
    assert mark_for(entries, "src/a.ts", "g( )") is None, "the name has to match too"
    assert mark_for([], "src/a.ts", "f( )") is None
