"""Worklist seam: inventory rows + churn in, ranked queue and dormant list out.

The ranking itself is pure. The last section is the other half of the seam:
WHICH run gets ranked, which `worklist` and `next-item` have to answer the same
way or the two commands describe different states.
"""
import pytest

from crapkit.churn import FileChurn
from crapkit.cli._shared import _latest_scored
from crapkit.cli.queue import _worklist_run
from crapkit.errors import CrapkitError
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore
from crapkit.worklist import Marks, build_worklist


def row(path="src/a.ts", name="f( )", ccn=9, scope="src"):
    return InventoryRow(scope, path, name, 1, 9, ccn, ccn, ccn, 8, 1, 2)


CHURN = {"src/a.ts": FileChurn(commits=7, authors=2), "src/hot.ts": FileChurn(commits=12, authors=3)}


def test_active_list_is_churned_files_ranked_by_ccn_descending():
    rows = [row(path="src/a.ts", ccn=9), row(path="src/hot.ts", ccn=15), row(path="src/cold.ts", ccn=30)]
    wl = build_worklist(rows, CHURN, floor=5, top=50)
    assert [e.path for e in wl.active] == ["src/hot.ts", "src/a.ts"]
    assert wl.active[0].ccn == 15
    assert wl.active[0].commits == 12


def test_dormant_list_holds_unchurned_high_ccn_ranked_and_separate():
    rows = [row(path="src/cold.ts", ccn=30), row(path="src/hot.ts", ccn=15), row(path="src/mild.ts", ccn=8)]
    wl = build_worklist(rows, CHURN, floor=5, top=50)
    assert [e.path for e in wl.dormant] == ["src/cold.ts", "src/mild.ts"]
    assert wl.dormant[0].commits == 0


def test_floor_excludes_low_ccn_everywhere():
    rows = [row(path="src/hot.ts", ccn=4), row(path="src/cold.ts", ccn=4)]
    wl = build_worklist(rows, CHURN, floor=5, top=50)
    assert wl.active == [] and wl.dormant == []


def test_top_caps_the_active_list_only():
    rows = [row(path="src/hot.ts", ccn=10 + i, name=f"f{i}( )") for i in range(10)]
    rows += [row(path=f"src/cold{i}.ts", ccn=20) for i in range(10)]
    wl = build_worklist(rows, CHURN, floor=5, top=3)
    assert len(wl.active) == 3
    assert len(wl.dormant) == 10


def test_ties_break_by_churn_then_path_then_start_for_determinism():
    rows = [row(path="src/a.ts", ccn=9), row(path="src/hot.ts", ccn=9)]
    wl = build_worklist(rows, CHURN, floor=5, top=50)
    assert [e.path for e in wl.active] == ["src/hot.ts", "src/a.ts"]


def test_non_positive_top_is_rejected_loudly():
    import pytest
    with pytest.raises(ValueError, match="top"):
        build_worklist([], {}, floor=5, top=0)


def test_composite_rank_puts_the_hotter_file_first_at_equal_ccn():
    churn = {"src/warm.ts": FileChurn(commits=20, authors=2, weight=1.0),
             "src/blaze.ts": FileChurn(commits=3, authors=1, weight=9.0)}
    rows = [row(path="src/warm.ts", ccn=9), row(path="src/blaze.ts", ccn=9)]
    wl = build_worklist(rows, churn, floor=5, top=10)
    assert [e.path for e in wl.active] == ["src/blaze.ts", "src/warm.ts"], \
        "recency-weighted risk beats raw commit count"
    assert wl.active[0].risk > wl.active[1].risk


def test_hot_simple_code_is_promoted_past_the_floor():
    churn = {f"src/f{i}.ts": FileChurn(commits=1, authors=1, weight=0.1) for i in range(8)}
    churn["src/burning.ts"] = FileChurn(commits=30, authors=4, weight=25.0)
    rows = [row(path="src/burning.ts", name="hot( )", ccn=4)] + \
           [row(path=f"src/f{i}.ts", ccn=9) for i in range(8)]
    wl = build_worklist(rows, churn, floor=5, top=50)
    assert any(e.path == "src/burning.ts" for e in wl.active), \
        "the floor applied before churn hides hot simple code (Tornhill)"


# --- which run gets ranked ---------------------------------------------------

def _run(store: SnapshotStore, kind: str, *, ok: bool | None = None) -> int:
    run_id = store.write_run(commit="c1", tool_versions={}, rows=[row()],
                             kind=kind, lanes={"unit": {}})
    if ok is not None:
        store.set_verdict_ok(run_id, ok, findings=0 if ok else 1)
    return run_id


def test_worklist_ranks_the_run_next_item_ranks(tmp_path):
    """runs = [coverage, partial]: worklist ranked the partial run and next-item
    picked its item off the coverage run, so the two commands described
    different states while queue.py claimed they described one.

    A partial run measures a fraction of the suite and its CRAP is inflated to
    match, so a ranking off it is a ranking no other reader accepts.
    """
    store = SnapshotStore(tmp_path / "crap.sqlite")
    coverage = _run(store, "coverage")
    _run(store, "partial")

    assert _worklist_run(tmp_path, store)["id"] == coverage
    assert _latest_scored(store)["id"] == coverage


def test_a_failed_verify_is_not_the_run_worklist_ranks(tmp_path):
    """Its scores can come off a red tree. Verify refuses it as a comparison
    point and the view refuses it for the same reason."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    coverage = _run(store, "coverage")
    _run(store, "verify", ok=False)

    assert _worklist_run(tmp_path, store)["id"] == coverage


def test_an_inventory_only_repo_still_gets_a_ranking(tmp_path):
    """Why the rule is not simply "the trusted run": `inventory` writes no
    trusted run, next-item refuses that repo, and worklist still ranks
    complexity alone until the first coverage run."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    inventory = _run(store, "inventory")

    assert _worklist_run(tmp_path, store)["id"] == inventory
    assert _latest_scored(store) is None


def test_a_store_with_only_a_hook_run_names_the_command_to_run(tmp_path):
    """Hook runs carry no rows, so there is nothing to rank and the error has to
    say which command writes rows."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    _run(store, "hook")

    with pytest.raises(CrapkitError) as excinfo:
        _worklist_run(tmp_path, store)

    assert "crapkit coverage" in str(excinfo.value)


# --- the number on the row ---------------------------------------------------

def test_entries_carry_the_runs_score_and_coverage_off_the_marks():
    marks = Marks({("src/a.ts", "f( )"): ("measured", "decompose")},
                  {("src/a.ts", "f( )", 1): (45.0, 0.5)})

    wl = build_worklist([row()], CHURN, floor=5, top=50, marks=marks)

    assert (wl.active[0].flag, wl.active[0].remedy) == ("measured", "decompose")
    assert (wl.active[0].crap, wl.active[0].cov) == (45.0, 0.5)


def test_twins_carry_their_own_score_beside_the_shared_verdict():
    """The verdict is the worst twin's, so a finished sibling never marks the
    pair done; the score is each row's own, or twin #1 prints twin #2's CRAP."""
    first = InventoryRow("src", "src/a.ts", "f( )", 1, 9, 7, 7, 7, 8, 1, 2)
    second = InventoryRow("src", "src/a.ts", "f( )", 20, 28, 8, 8, 8, 8, 1, 2)
    marks = Marks({("src/a.ts", "f( )"): ("measured", "decompose")},
                  {("src/a.ts", "f( )", 1): (56.0, 0.0), ("src/a.ts", "f( )", 20): (72.0, 0.0)})

    wl = build_worklist([first, second], CHURN, floor=5, top=50, marks=marks)

    assert {e.start: (e.crap, e.remedy) for e in wl.active} ==         {1: (56.0, "decompose"), 20: (72.0, "decompose")}


def test_an_inventory_only_run_leaves_the_score_and_the_coverage_none():
    e = build_worklist([row()], CHURN, floor=5, top=50).active[0]

    assert (e.flag, e.remedy, e.crap, e.cov) == (None, None, None, None)


def test_active_total_counts_the_rows_the_cap_hid():
    rows = [row(path="src/hot.ts", ccn=10 + i, name=f"f{i}( )") for i in range(10)]

    wl = build_worklist(rows, CHURN, floor=5, top=3)

    assert (len(wl.active), wl.active_total) == (3, 10)


# --- the committed mark on the row --------------------------------------------

def test_entries_carry_the_committed_mark_under_their_own_key():
    from crapkit.worklist import RatchetMarks

    ratchet = RatchetMarks({("src/a.ts", "f( )"): 30.0}, {})

    e = build_worklist([row()], CHURN, floor=5, top=50, ratchet=ratchet).active[0]

    assert e.ratchet_mark == 30.0


def test_an_unmarked_row_and_a_repo_without_marks_read_none():
    from crapkit.worklist import RatchetMarks

    marked_elsewhere = RatchetMarks({("src/other.ts", "f( )"): 30.0}, {})

    assert build_worklist([row()], CHURN, floor=5, top=50).active[0].ratchet_mark is None
    assert build_worklist([row()], CHURN, floor=5, top=50,
                          ratchet=marked_elsewhere).active[0].ratchet_mark is None


def test_twins_never_take_each_others_mark():
    """The ratchet keys the second `f( )` in a file as `f( )#2`. Read under the
    bare name, both rows would show twin #1's mark."""
    from crapkit.snapshot import InventoryRow
    from crapkit.worklist import RatchetMarks

    first = InventoryRow("src", "src/a.ts", "f( )", 1, 9, 9, 9, 9, 8, 1, 2)
    second = InventoryRow("src", "src/a.ts", "f( )", 20, 29, 9, 9, 9, 8, 1, 2)
    ratchet = RatchetMarks({("src/a.ts", "f( )"): 30.0, ("src/a.ts", "f( )#2"): 45.0},
                           {("src/a.ts", "f( )", 1): "f( )", ("src/a.ts", "f( )", 20): "f( )#2"})

    wl = build_worklist([first, second], CHURN, floor=5, top=50, ratchet=ratchet)

    assert {e.start: e.ratchet_mark for e in wl.active} == {1: 30.0, 20: 45.0}


# --- a flat log promotes nothing ----------------------------------------------

def test_hot_promotion_is_off_when_every_file_weighs_the_same():
    """On a one-commit repo every file weighs 1.0. The 90th percentile of equal
    weights is every file, which would admit the whole repo at ccn 3."""
    flat = {f"src/f{i}.ts": FileChurn(commits=1, authors=1, weight=1.0) for i in range(8)}
    rows = [row(path=f"src/f{i}.ts", ccn=3) for i in range(8)]

    wl = build_worklist(rows, flat, floor=5, top=50)

    assert wl.active == [] and wl.dormant == []
