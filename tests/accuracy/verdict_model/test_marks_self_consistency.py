"""Properties of crapkit's own marks-file functions: not independent methods.

These call crapkit.ratchet directly and compare it with itself (merge order,
idempotence, reader and writer round trip) or with model_verdict. They sit in
a module of their own so that test_merge_driver.py and test_ratchet_file.py,
whose expected values come from git merge-file, docs/ratchet.md and
docs/portable-records.md, import no crapkit module (the kit's closure rule for
a calc's independent test).

One API-level check sits here too: the override grant's stamp rule, whose
past defect (R107) no CLI command could reach, because both CLI callers
passed the running metric.
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


class _AuditLog:
    """The store's override log, the only store method a grant calls."""

    def __init__(self):
        self.rows = []

    def write_overrides(self, run_id, rows):
        self.rows.extend(rows)


STALE = "# crapkit-analysis=7 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\na.py\tf( )\t2.0000\n"


def test_a_measured_grant_that_names_no_metric_writes_nothing(tmp_path):
    """docs/ratchet.md, Stamps: verify's override writes under the running
    metric, and marks another metric recorded are refused. A grant that names
    no metric has no running metric to write under, so a file stamped at
    analysis 7 must come out byte-identical, with no audit row. Before
    4fe65f6 an empty metric fell through to the hook's keep-the-stamp rule
    and wrote a 90.0 mark under the analysis 7 stamp."""
    from crapkit.errors import CrapkitError
    from crapkit.override import record_override
    from crapkit.verify import GateViolation

    marks = tmp_path / "crapkit-ratchet.tsv"
    marks.write_bytes(STALE.encode("utf-8"))
    log, refused = _AuditLog(), None
    try:
        record_override(store=log, run_id=1, root=tmp_path, ratchet_file=marks.name,
                        alert_command="exit 0", reason="hotfix", metric="",
                        violations=[GateViolation("a.py", "f( )", 1, 9, 0.0, 90.0, "add-tests")])
    except CrapkitError as error:
        refused = error

    assert (refused is not None, marks.read_bytes(), log.rows) == (True, STALE.encode("utf-8"), [])
