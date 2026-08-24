"""One admission rule behind the worklist and next-item.

The queue used to hold two: the worklist promoted hot simple code past the ccn
floor, next-item did not, and neither admitted a function whose CRAP was over
target but whose ccn sat under the floor. A ccn-4 function at 0% coverage
scores CRAP 20 against a ceiling of 6, and the queue reported empty.
"""
import pytest

from crapkit.churn import FileChurn
from crapkit.cli import _no_lane_gap, _rankable, _skip_reason
from crapkit.score import ScoredRow, crap
from crapkit.worklist import HOT_MIN_CCN, admission, over_target_floor, sql_floor


def scored(path="util/stats.py", ccn=4, remedy="add-tests", flag="untested",
           name="dark( a , b , c )", scope="util"):
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 8, 3, 2,
                     0.0, flag, crap(ccn, 0.0), remedy)


CHURN = {"util/stats.py": FileChurn(commits=3, authors=1, weight=0.9)}


# --- the shared rule ---------------------------------------------------------

def test_over_target_debt_is_admitted_under_the_floor():
    adm = admission(CHURN, floor=5)

    assert adm.admits("util/stats.py", 4, over_target=True), \
        "CRAP 20 against a ceiling of 6 is work whatever the ccn"


def test_a_row_at_its_ceiling_still_answers_to_the_floor():
    adm = admission(CHURN, floor=5)

    assert not adm.admits("util/stats.py", 4, over_target=False)
    assert adm.admits("util/stats.py", 5, over_target=False)


def hot_churn() -> dict:
    churn = {f"src/f{i}.py": FileChurn(commits=1, authors=1, weight=0.1) for i in range(8)}
    churn["src/burning.py"] = FileChurn(commits=30, authors=4, weight=25.0)
    return churn


def test_hot_simple_code_is_promoted_for_next_item_too():
    adm = admission(hot_churn(), floor=5)

    assert adm.admits("src/burning.py", HOT_MIN_CCN, over_target=False), \
        "the worklist promoted this row; next-item counted it as below_floor"
    assert not adm.admits("src/burning.py", HOT_MIN_CCN - 1, over_target=False)


def test_the_worklist_and_the_queue_take_the_same_hot_simple_file():
    from crapkit.snapshot import InventoryRow
    from crapkit.worklist import build_worklist

    churn = hot_churn()
    rows = [InventoryRow("src", "src/burning.py", "hot( )", 1, 9, 4, 4, 4, 8, 1, 2)]
    rows += [InventoryRow("src", f"src/f{i}.py", "f( )", 1, 9, 9, 9, 9, 8, 1, 2)
             for i in range(8)]

    wl = build_worklist(rows, churn, floor=5, top=50)
    adm = admission(churn, floor=5)

    assert any(e.path == "src/burning.py" for e in wl.active)
    assert _rankable(scored(path="src/burning.py", ccn=4, remedy="ok", flag="measured",
                            name="hot( )", scope="src"), adm), \
        "one admission rule, so the two queues cannot disagree about a candidate"


# --- the worklist's half of the rule -----------------------------------------

def inventory_row(path="util/stats.py", name="dark( a , b , c )", ccn=4):
    from crapkit.snapshot import InventoryRow

    return InventoryRow("util", path, name, 1, 9, ccn, ccn, ccn, 8, 3, 2)


DEBT = {("util/stats.py", "dark( a , b , c )"): ("untested", "add-tests")}
DONE = {("util/stats.py", "dark( a , b , c )"): ("measured", "ok")}


def test_the_worklist_takes_the_sub_floor_debt_next_item_hands_out():
    from crapkit.worklist import build_worklist

    wl = build_worklist([inventory_row()], CHURN, floor=5, top=50, marks=DEBT)

    assert [e.path for e in wl.active] == ["util/stats.py"], \
        "coverage graded this repo F; the worklist printed 0 active"


def test_the_worklist_still_floors_a_row_that_sits_at_its_ceiling():
    from crapkit.worklist import build_worklist

    wl = build_worklist([inventory_row()], CHURN, floor=5, top=50, marks=DONE)

    assert wl.active == [] and wl.dormant == []


def test_a_sub_floor_debt_row_with_no_churn_is_still_recorded_as_dormant():
    """Debt is debt wherever it sleeps, but the active list means "changed
    lately" and a file with no commits in the window has not."""
    from crapkit.worklist import build_worklist

    wl = build_worklist([inventory_row()], {}, floor=5, top=50, marks=DEBT)

    assert wl.active == []
    assert [e.path for e in wl.dormant] == ["util/stats.py"]


# --- the SQL pushdown --------------------------------------------------------

def test_the_pushdown_floor_never_hides_a_row_that_could_be_over_target():
    """CRAP is worst at cov 0, where it is ccn^2 + ccn. Any ccn whose worst
    score can clear the smallest ceiling has to leave SQLite."""
    for ceiling in (6, 4, 1, 20):
        for ccn in range(1, sql_floor(99, ceiling)):
            assert crap(ccn, 0.0) <= ceiling, \
                f"ccn {ccn} can reach CRAP {crap(ccn, 0.0)} over ceiling {ceiling}"


def test_the_pushdown_floor_is_tight_at_the_configured_ceilings():
    assert over_target_floor(6) == 3, "ccn 2 tops out at CRAP 6, ccn 3 at 12"
    assert over_target_floor(4) == 2, "a per-scope ceiling of 4 reaches ccn 2"
    assert crap(over_target_floor(4), 0.0) > 4


def test_the_pushdown_floor_never_rises_above_the_rules_it_serves():
    assert sql_floor(5, 6) == min(5, HOT_MIN_CCN, over_target_floor(6))
    assert sql_floor(2, 99) == 2, "a floor under every other rule wins"


def test_the_pushdown_floor_rejects_a_ceiling_below_one():
    with pytest.raises(ValueError, match="ceiling"):
        over_target_floor(0)


# --- next-item's use of it ---------------------------------------------------

def test_next_item_ranks_the_sub_floor_debt_the_worklist_floor_hid():
    adm = admission(CHURN, floor=5)

    assert _rankable(scored(), adm) is True
    assert _skip_reason(scored(), adm, []) is None


def test_a_row_at_its_ceiling_under_the_floor_reads_as_below_floor():
    adm = admission(CHURN, floor=5)

    row = scored(remedy="ok", flag="measured")
    assert _rankable(row, adm) is False
    assert _skip_reason(row, adm, []) == "below_floor"


def test_over_target_debt_outside_the_churn_window_is_still_debt():
    adm = admission({}, floor=5)

    assert _rankable(scored(), adm) is True, "debt is debt wherever it sleeps"
    assert _skip_reason(scored(remedy="ok", flag="measured"), adm, []) == "no_churn_in_window"


def test_a_no_lane_row_stays_a_tooling_gap_not_a_queue_item():
    adm = admission(CHURN, floor=5)

    row = scored(flag="no-lane")
    assert _rankable(row, adm) is False
    assert _skip_reason(row, adm, []) == "no_lane"
    assert _no_lane_gap(row, adm) is True, \
        "skipped_no_lane has to count it, or empty:true hides a whole scope"


def test_a_no_lane_row_at_its_ceiling_under_the_floor_is_not_counted():
    adm = admission(CHURN, floor=5)

    assert _no_lane_gap(scored(flag="no-lane", remedy="ok"), adm) is False


def test_an_excluded_row_reports_the_flag_that_ate_it():
    adm = admission(CHURN, floor=5)

    assert _skip_reason(scored(), adm, ["dark"]) == "excluded_by_flag"
