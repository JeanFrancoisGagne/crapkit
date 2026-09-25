"""The store-upgrade test's two rules, on hand-written inputs.

- model_baseline against the README's worked example ("The trusted
  baseline": coverage, a failed verify, coverage reads run 1) and the rules
  around it: a passing verify clears the taint, and partial, inventory and
  hook runs never qualify.
- upgrade_diff: a moved leaf names the calc of its nearest known key, and a
  CHANGES row declares it only when dated on or after the release.
"""
import pytest

from accuracy.corpus_goldens import model_baseline, upgrade_diff


def _runs(*rows) -> list[dict]:
    return [{"id": number, "kind": kind, "verdict_ok": ok}
            for number, (kind, ok) in enumerate(rows, start=1)]


COVERAGE, PASSED, FAILED = ("coverage", None), ("verify", 1), ("verify", 0)


@pytest.mark.parametrize(("runs", "picked"), [
    # README.md "The trusted baseline": run 3 is not the baseline; run 1 is.
    (_runs(COVERAGE, FAILED, COVERAGE), 1),
    (_runs(COVERAGE, FAILED, COVERAGE, PASSED), 4),
    (_runs(COVERAGE, FAILED, PASSED, COVERAGE), 4),
    (_runs(COVERAGE, PASSED, COVERAGE, FAILED, FAILED, COVERAGE), 3),
    (_runs(COVERAGE, ("partial", None), ("inventory", None), ("hook", None)), 1),
    (_runs(("legacy", None), ("inventory", None)), 1),
    (_runs(("inventory", None), FAILED), None),
    ([], None),
])
def test_the_baseline_model_follows_the_readme(runs, picked):
    assert model_baseline.baseline(runs) == picked


def test_a_moved_leaf_names_the_calc_of_its_nearest_key():
    old = {"active": [{"path": "src/a.py", "crap": 7.0, "risk": 3.5}], "stale": False}
    new = {"active": [{"path": "src/a.py", "crap": 7.5, "risk": 3.5}], "stale": True}

    found = upgrade_diff.differences("worklist --json", old, new)

    assert [(item.path, item.calc) for item in found] == [
        (("active", 0, "crap"), "CRAP score"),
        (("stale",), "Worklist ranking and dormant list")]
    assert found[0].line() == "worklist --json active/0/crap: 7.0 -> 7.5 (CRAP score)"


def test_a_row_only_one_side_holds_is_a_difference():
    found = upgrade_diff.differences("coupling --json", {"pairs": []},
                                     {"pairs": [{"a": "x", "support": 5}]})

    assert {item.calc for item in found} == {"Change coupling"}
    assert {item.path for item in found} == {("pairs",), ("pairs", 0, "a"), ("pairs", 0, "support")}


def _change(date: str, calcs: str) -> dict:
    return {"id": "C9", "date": date, "kind": "fix", "calcs": calcs}


def test_only_a_change_dated_since_the_release_declares_a_move():
    found = upgrade_diff.differences("worklist --json", {"crap": 7.0}, {"crap": 7.5})

    assert upgrade_diff.undeclared(found, {}, "2026-09-23") == [
        "worklist --json crap: 7.0 -> 7.5 (CRAP score)"]
    assert upgrade_diff.undeclared(found, {"C9": _change("2026-09-24", "CRAP score; nloc")},
                                   "2026-09-23") == []
    assert upgrade_diff.undeclared(found, {"C9": _change("2026-09-22", "CRAP score")},
                                   "2026-09-23") != []
