"""Properties of crapkit's own marks-file functions: not independent methods.

These call crapkit.ratchet directly and compare it with itself (merge order,
idempotence, reader and writer round trip) or with model_verdict. They sit in
a module of their own so that test_merge_driver.py and test_ratchet_file.py,
whose expected values come from git merge-file, docs/ratchet.md and
docs/portable-records.md, import no crapkit module (the kit's closure rule for
a calc's independent test).

API-level checks sit here too, for past defects no CLI command of their
before commit can reach: the override grant's stamp rule (R107, both CLI
callers passed the running metric), and verify's mark compare at four
decimals (R04) and across twin row order (R06), whose before commits had no
`ratchet seed` command to write a mark with.
"""
from __future__ import annotations

from fractions import Fraction

from hypothesis import given, strategies as st

from accuracy.kit import exact
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


def _scored(name: str, start: int, ccn: int, cov: float, crap: float, occurrence: int):
    """A scored row built from ScoredRow's own field list, so a release that
    adds or drops a field still builds it (the retro replays reach back to
    the 15-field row)."""
    from crapkit.score import ScoredRow
    values = {"scope": "app", "path": "a.py", "long_name": name, "start": start,
              "end": start + 5, "ccn_std": ccn, "ccn_mod": ccn, "ccn": ccn, "nloc": 6,
              "params": 1, "nesting": 0, "cov": cov, "flag": "measured", "crap": crap,
              "remedy": "add-tests", "cognitive": 0, "occurrence": occurrence, "inline_body": 0}
    return ScoredRow(**{field: values[field] for field in ScoredRow._fields})


def _regressions(fresh: list, marks: list) -> list:
    from crapkit.ratchet import RatchetEntry
    from crapkit.verify import evaluate
    verdict = evaluate(fresh=fresh, changed_ranges={}, target=100,
                       ratchet=[RatchetEntry("a.py", name, value) for name, value in marks],
                       baseline_failures=set(), fresh_failures=set())
    return [(r.long_name, r.recorded, r.fresh_crap) for r in verdict.ratchet_regressions]


def test_a_run_s_own_mark_is_no_regression_on_that_run():
    """docs/ratchet.md, What a mark is: a mark is the function's CRAP to four
    decimals, so the run a mark was seeded from cannot regress against it.
    ccn 3 at 2 of 3 branches: CRAP = 9 x (1/3)^3 + 3 = 10/3 (Savoia and
    Evans), stored as 3.3333. Before ed53ada verify compared the unrounded
    3.33333... with 3.3333 and reported a regression of the run's own mark."""
    crap = exact.crap(3, Fraction(2, 3))
    row = _scored("f( x )", 1, 3, 2 / 3, float(crap), 1)

    assert crap == Fraction(10, 3)
    assert _regressions([row], [("f( x )", float(exact.half_even(crap, 4)))]) == []


def test_a_twin_s_mark_does_not_depend_on_row_order():
    """A verdict reads rows, not the order the store returned them in. Twins
    A (CRAP 2) and B (CRAP 5) share one name and the mark on that name is 2:
    both row orders must give one answer. Before 8f241a6 the last row read
    owned the key, so [A, B] reported a regression and [B, A] did not."""
    twin_a = _scored("dup( x )", 1, 2, 1.0, 2.0, 1)
    twin_b = _scored("dup( x )", 7, 5, 1.0, 5.0, 2)
    marks = [("dup( x )", 2.0)]

    assert _regressions([twin_a, twin_b], marks) == _regressions([twin_b, twin_a], marks)
