"""The coverage join, at the two places speed and correctness meet.

_finish builds a ScoredRow positionally, which is only safe while the inventory
row's fields ARE the scored row's first fields in the same order and cognitive
is last in both. That is a contract between two NamedTuples nothing else states,
so it is stated here.

The span join indexes each path's candidates by start line before searching,
because 91.5% of joinable rows hit a candidate declaring their exact start and
the scan behind them was 936,818 pairwise comparisons on the consumer repo. An index may
only ever be a faster route to the same winner, so the whole join is diffed
against the pre-index scan, kept verbatim below as its oracle.
"""
import random

import pytest

from crapkit.coverage_istanbul import FnCoverage
from crapkit.score import ScoredRow, score_rows
from crapkit.snapshot import InventoryRow


def _fn(name, start, end, covered=2, total=4):
    return FnCoverage(name, start, end, True, total, covered)


def _row(path="src/a.ts", start=10, end=20, scope="src", ccn=8, cognitive=0):
    return InventoryRow(scope, path, f"{name_of(start)}( )", start, end,
                        ccn, ccn, ccn, 5, 1, 1, cognitive)


def name_of(start: int) -> str:
    return f"fn{start}"


LANE = {"src"}


# --- the field-order contract the positional build rests on ----------------

def test_the_inventory_row_is_the_scored_rows_prefix():
    inventory = InventoryRow._fields
    assert ScoredRow._fields[:len(inventory) - 1] == inventory[:-1]
    assert ScoredRow._fields[-1] == inventory[-1] == "cognitive"
    assert ScoredRow._fields[len(inventory) - 1:-1] == ("cov", "flag", "crap", "remedy")


def test_every_inventory_field_lands_in_its_own_scored_slot():
    """Distinct values in every slot, so a splice that shifts one column shows.
    cognitive sits LAST in both tuples with four fields between, so splicing the
    inventory row in positionally without moving it lands it in cov."""
    row = InventoryRow("src", "p/q.ts", "name( a )", 3, 40, 5, 6, 7, 80, 9, 10, 11)

    (out,) = score_rows([row], {}, lane_scopes=LANE)

    assert out[:11] == row[:11]
    assert out.cognitive == 11
    assert (out.cov, out.flag) == (0.0, "untested")


def test_a_scope_target_still_overrides_the_repo_target():
    row = _row(ccn=7)
    (strict,) = score_rows([row], {}, lane_scopes=LANE, target=6, scope_targets={"src": 20})
    (loose,) = score_rows([row], {}, lane_scopes=LANE, target=20, scope_targets={"src": 6})
    assert strict.remedy == "add-tests"
    assert loose.remedy == "decompose"


def test_a_coverage_optional_scope_still_scores_as_its_own_complexity():
    row = _row(ccn=9)
    (out,) = score_rows([row], {}, lane_scopes=LANE, cc_only_scopes=frozenset({"src"}))
    assert (out.flag, out.crap, out.remedy) == ("cc-only", 9.0, "decompose")


# --- the join picks the same candidate an unindexed scan picked ------------

def _scan_best_match(row, candidates):
    """score._best_match as it stood before the start index, kept as the oracle."""
    def overlap(a_start, a_end, b_start, b_end):
        return max(0, min(a_end, b_end) - max(a_start, b_start) + 1)

    best, best_key = None, None
    for fn in candidates:
        o = overlap(row.start, row.end, fn.start, fn.end)
        if o <= 0:
            continue
        key = (fn.start == row.start, o, -(fn.end - fn.start), fn.coverage)
        if best_key is None or key > best_key:
            best, best_key = fn, key
    return best


def _scan_join(row, coverage_by_path):
    if row.path not in coverage_by_path:
        return 0.0, "untested"
    match = _scan_best_match(row, coverage_by_path[row.path])
    return (match.coverage, "measured") if match is not None else (0.0, "untested")


def test_an_exact_start_beats_a_wider_enclosing_span():
    """A nested function must join its own entry: joining the encloser inherits
    the parent's coverage and understates the nested function's risk."""
    row = _row(start=10, end=20)
    candidates = [_fn("outer", 1, 400, covered=4), _fn("own", 10, 20, covered=0)]

    (out,) = score_rows([row], {"src/a.ts": candidates}, lane_scopes=LANE)

    assert (out.cov, out.flag) == (0.0, "measured")
    assert (out.cov, out.flag) == _scan_join(row, {"src/a.ts": candidates})


def test_identical_span_twins_keep_the_better_measurement():
    """Two lanes measuring the same file must not let declaration order move a
    score, so the higher of the two branch numbers wins either way round."""
    row = _row(start=10, end=20)
    poor, rich = _fn("f", 10, 20, covered=0), _fn("f", 10, 20, covered=4)
    for candidates in ([poor, rich], [rich, poor]):
        (out,) = score_rows([row], {"src/a.ts": candidates}, lane_scopes=LANE)
        assert out.cov == 1.0


def test_the_tightest_span_wins_when_the_overlap_ties():
    row = _row(start=10, end=20)
    tight, loose = _fn("tight", 10, 25, covered=0), _fn("loose", 10, 30, covered=4)
    candidates = [loose, tight]

    (out,) = score_rows([row], {"src/a.ts": candidates}, lane_scopes=LANE)

    assert out.cov == 0.0, "same overlap, so the tighter span owns the row"
    assert (out.cov, out.flag) == _scan_join(row, {"src/a.ts": candidates})


def test_a_dead_even_tie_keeps_the_first_candidate():
    row = _row(start=10, end=20)
    first, second = _fn("first", 10, 20, covered=2), _fn("second", 10, 20, covered=2)
    (out,) = score_rows([row], {"src/a.ts": [first, second]}, lane_scopes=LANE)
    assert out.cov == _scan_best_match(row, [first, second]).coverage


def test_a_candidate_declaring_the_rows_start_but_ending_before_it_is_no_match():
    """An artifact can report an end line above its start line. Such a candidate
    shares the row's start and still overlaps nothing, so the search must fall
    back rather than hand it the row."""
    row = _row(start=10, end=20)
    broken, real = _fn("broken", 10, 4, covered=4), _fn("real", 8, 30, covered=0)
    candidates = [broken, real]

    (out,) = score_rows([row], {"src/a.ts": candidates}, lane_scopes=LANE)

    assert (out.cov, out.flag) == _scan_join(row, {"src/a.ts": candidates})
    assert out.cov == 0.0


def test_a_row_no_candidate_overlaps_reads_as_untested():
    row = _row(start=100, end=110)
    candidates = [_fn("elsewhere", 1, 20, covered=4)]
    (out,) = score_rows([row], {"src/a.ts": candidates}, lane_scopes=LANE)
    assert (out.cov, out.flag) == (0.0, "untested")


# --- and the same thing at corpus scale ------------------------------------

def _corpus(seed=20260823, n_paths=40, per_path=25, rows_per_path=25):
    rnd = random.Random(seed)
    coverage, rows = {}, []
    for p in range(n_paths):
        path = f"src/m{p}.ts"
        spans = [(rnd.randint(1, 200), rnd.choice([0, 1, 5, 5, 40, 200])) for _ in range(per_path)]
        coverage[path] = [_fn(f"c{i}", s, s + length, covered=i % 5, total=4)
                          for i, (s, length) in enumerate(spans)]
        for i in range(rows_per_path):
            # half the rows land on a candidate's exact start, half do not
            start = spans[i % len(spans)][0] if i % 2 else rnd.randint(1, 260)
            rows.append(_row(path=path, start=start, end=start + rnd.choice([0, 3, 30, 300])))
    return coverage, rows


def test_the_indexed_join_agrees_with_the_scan_on_every_row():
    coverage, rows = _corpus()
    scored = score_rows(rows, coverage, lane_scopes=LANE)
    expected = [_scan_join(r, coverage) for r in rows]
    disagreed = [(r.path, r.start) for r, s, e in zip(rows, scored, expected)
                 if (s.cov, s.flag) != e]
    assert disagreed == [], f"{len(disagreed)} rows joined differently, first {disagreed[:5]}"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_indexed_join_agrees_with_the_scan_on_other_corpora(seed):
    coverage, rows = _corpus(seed=seed)
    scored = score_rows(rows, coverage, lane_scopes=LANE)
    assert [(s.cov, s.flag) for s in scored] == [_scan_join(r, coverage) for r in rows]
