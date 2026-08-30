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
from crapkit.worklist import build_worklist


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
