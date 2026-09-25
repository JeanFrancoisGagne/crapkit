"""Properties of crapkit's own marks-file functions: not independent methods.

These call crapkit.ratchet directly and compare it with itself (merge order,
idempotence, reader and writer round trip) or with model_verdict. They sit in
a module of their own so that test_merge_driver.py and test_ratchet_file.py,
whose expected values come from git merge-file, docs/ratchet.md and
docs/portable-records.md, import no crapkit module (the kit's closure rule for
a calc's independent test).
"""
from __future__ import annotations

from hypothesis import given, strategies as st

from accuracy.kit.settings import pure
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model.test_ratchet_file import MARKS, STAMP, decoded, lines_of


def d(text: str) -> model.Decimal:
    return model.Decimal(text)


KEYS = st.sampled_from([("a.py", "f( )"), ("a.py", "f( )#2"), ("b.py", "g( )")])
SIDE = st.dictionaries(KEYS, st.sampled_from([d("10.0000"), d("20.0000"), d("30.0000")]))


def _crapkit_merge(base: dict, ours: dict, theirs: dict) -> dict:
    from crapkit.ratchet import RatchetEntry, merge_ratchets
    entries = [[RatchetEntry(p, k, float(v)) for (p, k), v in side.items()]
               for side in (base, ours, theirs)]
    return {(e.path, e.long_name): d(f"{e.crap:.4f}") for e in merge_ratchets(*entries)}


@pure
@given(base=SIDE, ours=SIDE, theirs=SIDE)
def test_crapkit_s_merge_is_commutative_and_matches_the_model(base, ours, theirs):
    result = _crapkit_merge(base, ours, theirs)

    assert result == _crapkit_merge(base, theirs, ours)
    assert result == model.merge(base, ours, theirs)


@pure
@given(base=SIDE, side=SIDE)
def test_crapkit_s_merge_is_idempotent_and_a_drop_beats_unchanged(base, side):
    assert _crapkit_merge(base, side, side) == side
    assert _crapkit_merge(base, base, side) == side


@pure
@given(marks=MARKS)
def test_round_trip_through_crapkit_s_reader_and_writer(marks):
    from crapkit.ratchet import RatchetEntry, dump_ratchet, load_ratchet
    entries = [RatchetEntry(path, key, float(value)) for (path, key), value in marks.items()]

    text = dump_ratchet(entries, stamp=STAMP, key_version=1)

    assert sorted(load_ratchet(text)) == sorted(entries)
    assert [decoded(line) for line in lines_of(text.encode("utf-8"))] == \
        model.dump_marks(model.MarksFile(STAMP, "1", marks))
