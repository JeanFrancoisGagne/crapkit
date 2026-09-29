"""ratchet: every mark is written and compared at the 4 decimal places ratchet.tsv holds.

docs/ratchet.md: a mark is a CRAP score at 4 places, it never rises, a function
at or under its scope's ceiling needs none, and a mark whose measurement jumped
between two runs of one commit is held. The refusals below are quoted whole: a
session acts on the sentence, so each word is part of the contract.
"""
import pytest

from crapkit import ratchet
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow

KEY = ("src/a.py", "f( )")


def row(crap: float, scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, KEY[0], KEY[1], 1, 9, 3, 3, 3, 5, 1, 1, 0.5, "measured", crap,
                     "add-tests")


def test_a_seeded_mark_is_the_score_at_4_places():
    seeded, added, tightened = ratchet.seed_ratchet([], [row(12.300049)], target=6)

    assert (seeded, added, tightened) == ([RatchetEntry(*KEY, 12.3)], 1, 0)


def test_seeding_counts_every_mark_it_tightens():
    prior = [RatchetEntry("src/a.py", "f( )", 20.0), RatchetEntry("src/b.py", "f( )", 20.0)]
    fresh = [row(15.0), row(15.0)._replace(path="src/b.py")]

    _, added, tightened = ratchet.seed_ratchet(prior, fresh, target=6)

    assert (added, tightened) == (0, 2)


def test_an_updated_mark_is_the_lower_score_at_4_places():
    assert ratchet.update_ratchet([RatchetEntry(*KEY, 13.0)], [row(12.300049)], target=6) == [
        RatchetEntry(*KEY, 12.3)]


def test_a_mark_whose_function_sits_under_its_scope_s_ceiling_goes():
    assert ratchet.update_ratchet([RatchetEntry(*KEY, 13.0)], [row(12.3)], target=6,
                                  scope_targets={"src": 13}) == []


@pytest.mark.parametrize("fresh, refused", [
    # 15.00004 is 15.0 at 4 places: no jump past 10.0 x 1.5
    (15.00004, []),
    # 20.000049 jumped, and the refusal quotes it at 4 places
    (20.000049, [ratchet.TightenRefusal(*KEY, 10.0, 20.0)]),
])
def test_a_jump_is_judged_and_quoted_at_4_places(fresh, refused):
    assert ratchet.unstable_marks([RatchetEntry(*KEY, 9.0)], [row(fresh)], {KEY: 10.0},
                                  max_jump=1.5) == refused


def test_a_mark_moves_are_named():
    entry = RatchetEntry(*KEY, 12.0)

    assert [ratchet._mark_move(entry, fresh) for fresh in (None, 11.0, 12.0)] == [
        "dropped", "tightened", "kept"]


def test_the_stamp_is_read_with_or_without_a_space_after_the_hash():
    assert ratchet.read_stamp("#crapkit-analysis=12 lizard=1.24.0\n") == (
        "crapkit-analysis=12 lizard=1.24.0")
    assert ratchet.read_stamp("# crapkit-analysis=12 lizard=1.24.0\n") == (
        "crapkit-analysis=12 lizard=1.24.0")


def test_a_run_that_recorded_one_version_has_no_stamp():
    assert ratchet.run_stamp({"analysis_version": 12, "lizard": "1.24.0"}) == (
        "crapkit-analysis=12 lizard=1.24.0")
    assert ratchet.run_stamp({"analysis_version": 12}) == ""
    assert ratchet.run_stamp({"lizard": "1.24.0"}) == ""


def test_a_key_stamp_of_0_is_the_start_only_identity():
    assert ratchet.read_key_version("# crapkit-keys=0\n") == 0
    with pytest.raises(ValueError) as refused:
        ratchet.read_key_version("# crapkit-keys=9\n")
    assert str(refused.value) == "unreadable or unsupported ratchet key identity version"


def test_a_mark_that_is_no_number_is_refused_in_words():
    with pytest.raises(ValueError) as refused:
        ratchet._finite_mark("inf")

    assert str(refused.value) == "ratchet mark must be finite"


def test_an_ambiguous_legacy_key_names_each_mark_and_the_doc_section():
    with pytest.raises(ValueError) as refused:
        ratchet._refuse_ambiguous({("src/b.py", "g( )"), ("src/a.py", "f( )")})

    assert str(refused.value) == (
        "legacy ratchet key identity is ambiguous for src/a.py: f( ); src/b.py: g( ); "
        "preserve these marks and reconcile their function mapping as described in "
        "docs/ratchet.md#same-line-function-identity")


def test_marks_read_before_reader_10_name_every_file_whose_callbacks_moved():
    entries = [RatchetEntry("src/b.js", "(anonymous)#2", 9.0),
               RatchetEntry("src/a.ts", "(anonymous)", 9.0), RatchetEntry("src/c.py", "f( )", 9.0)]

    with pytest.raises(ValueError) as refused:
        ratchet.check_reader_version(entries, 9)

    assert str(refused.value) == (
        "expression reader 10 changed anonymous function ordinals in src/a.ts, src/b.js; refresh "
        "coverage and reconcile any saved marks with their original functions before reseeding")
    assert ratchet.check_reader_version(entries, 10) is None
