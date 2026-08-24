"""The payload rules an agent loop runs on: what a silent artifact reports, when
the queue is finished, which name strings resolve, what a batched worklist adds,
and how a claim gets handed back.

Every seam here is pure — values in, a dict or a list out — so the shape an agent
parses can be argued about without a repo, a store or a lane.
"""
from types import SimpleNamespace

import pytest
from crapkit.churn import FileChurn
from crapkit.cli import (_actionable, _claims_to_release, _matching_rows, _name_matches,
                         _next_reasons, _policy_findings, _uncovered_fields, _worklist_payload)
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.uncovered import MissingLines
from crapkit.worklist import Batch, Worklist, WorklistEntry, admission


def row(name: str = "alpha( a , b )", *, remedy: str = "decompose", ccn: int = 8,
        path: str = "core/alpha.py", flag: str = "measured") -> ScoredRow:
    return ScoredRow("core", path, name, 1, 20, ccn, ccn, ccn, 18, 2, 1, 0.5, flag,
                     float(ccn * ccn), remedy)


def entry(path: str = "core/alpha.py") -> WorklistEntry:
    return WorklistEntry("core", path, "alpha( a , b )", 1, 20, 8, 8, 18, 3, 1, 1.0, 8.0)


def claim(cid: int, path: str, name: str) -> dict:
    return {"id": cid, "path": path, "long_name": name, "commit": "c0ffee",
            "created_at": "2026-01-01T00:00:00Z"}


# --- uncovered lines: absent is not covered ----------------------------------

def test_lines_are_null_when_no_artifact_spoke_about_the_file():
    # [] is what a fully covered function reports; a file nothing measured must
    # not borrow that answer
    fields = _uncovered_fields(MissingLines({}, ""), row(path="core/unseen.py"))

    assert fields["uncovered_lines"] is None
    assert "core/unseen.py" in fields["uncovered_lines_note"]


def test_lines_are_null_when_the_artifacts_are_stale():
    fields = _uncovered_fields(MissingLines({}, "lane 'py': rerun coverage"), row())

    assert fields["uncovered_lines"] is None
    assert fields["uncovered_lines_note"] == "lane 'py': rerun coverage"


def test_an_empty_list_means_the_artifacts_answered_and_found_nothing_dark():
    fields = _uncovered_fields(MissingLines({"core/alpha.py": set()}, ""), row())

    assert fields["uncovered_lines"] == []
    assert "uncovered_lines_note" not in fields, "no note: the artifact did answer"


def test_dark_lines_inside_the_span_come_back_sorted():
    src = MissingLines({"core/alpha.py": {21, 4, 6}}, "")

    assert _uncovered_fields(src, row())["uncovered_lines"] == [4, 6], "21 is past the span"


# --- termination: a queue with nothing to do says so -------------------------

class _StubStore:
    def count_scored_below(self, run_id, floor, scopes) -> int:
        return 0


CFG = SimpleNamespace(worklist_floor=5, churn_window_months=6, target=6, scope_targets={})
ADMISSION = admission({"core/alpha.py": FileChurn(commits=3, authors=1, weight=1.0)},
                      CFG.worklist_floor)


def reasons(ranked: list, scored: list) -> dict:
    return _next_reasons(_StubStore(), 1, ranked, scored, ADMISSION, CFG, [], [])


def test_a_row_at_its_ceiling_is_not_actionable():
    assert _actionable([row(remedy="ok")]) == []


def test_add_tests_and_decompose_stay_actionable():
    ranked = [row(remedy="add-tests"), row(remedy="decompose")]

    assert _actionable(ranked) == ranked


def test_an_all_ok_queue_reports_how_many_sit_at_target():
    assert reasons([row(remedy="ok"), row(remedy="ok")], [])["all_remaining_at_or_under_target"] == 2


def test_a_queue_that_was_never_ranked_reports_no_target_count():
    # nothing reached the ranking, so "all remaining are fine" would be a lie
    assert "all_remaining_at_or_under_target" not in reasons([], [])


def test_the_filter_counts_survive_the_new_key():
    out = reasons([row(remedy="ok")], [row(flag="no-lane")])

    assert out["no_lane"] == 1
    assert out["below_floor"] == 0 and out["churn_window_months"] == 6


def test_a_no_lane_row_over_its_ceiling_is_counted_on_its_own():
    """`all_remaining_at_or_under_target` reads as "stop looping". A no-lane row
    over target is debt the queue never offered, so the stop condition needs its
    own count to hang off."""
    out = reasons([row(remedy="ok")], [row(flag="no-lane", remedy="decompose")])

    assert out["no_lane"] == 1
    assert out["no_lane_over_target"] == 1


def test_a_no_lane_row_at_its_ceiling_is_not_counted_as_debt():
    out = reasons([row(remedy="ok")], [row(flag="no-lane", remedy="ok")])

    assert out["no_lane"] == 1
    assert out["no_lane_over_target"] == 0, "a wiring gap with nothing over target"


# --- names: the string next-item prints is the string brief takes ------------

def test_the_bare_identifier_and_the_whole_long_name_both_match():
    assert _name_matches("classify( score , late )", "classify")
    assert _name_matches("classify( score , late )", "classify( score , late )")


def test_a_fragment_or_another_function_does_not_match():
    assert not _name_matches("classify( score , late )", "class")
    assert not _name_matches("classify( score , late )", "summarize")


def test_matching_rows_accepts_the_long_name_next_item_published():
    rows = [row("classify( score , late )"), row("summarize( rows )")]

    assert _matching_rows(rows, "classify( score , late )") == [rows[0]]
    assert _matching_rows(rows, "classify") == [rows[0]]


def test_the_long_name_picks_one_of_two_functions_sharing_an_identifier():
    rows = [row("dup( a )"), row("dup( a , b )")]

    assert _matching_rows(rows, "dup( a , b )") == [rows[1]]
    assert len(_matching_rows(rows, "dup")) == 2, "the bare name is still ambiguous"


# --- worklist: batches are an added field ------------------------------------

WL = Worklist([entry()], [entry("core/dormant.py")])
LATEST = {"id": 4, "commit": "abc123def456"}


def payload(batches):
    return _worklist_payload(WL, LATEST, SimpleNamespace(worklist_floor=5, churn_window_months=6),
                             False, batches)


def test_the_batched_payload_still_carries_the_whole_worklist():
    out = payload([Batch(["core/alpha.py"], [entry()])])

    assert out["active"][0]["path"] == "core/alpha.py"
    assert (out["run_id"], out["stale"], out["dormant_count"]) == (4, False, 1)
    assert out["batches"] == [{"files": ["core/alpha.py"],
                               "entries": [dict(out["active"][0])]}]


def test_the_batches_key_is_absent_without_the_flag():
    assert "batches" not in payload(None)


def test_asking_for_batches_changes_nothing_else_in_the_payload():
    plain = payload(None)
    batched = payload([Batch(["core/alpha.py"], [entry()])])

    assert {k: v for k, v in batched.items() if k != "batches"} == plain


# --- ratchet policy: absent is not clean -------------------------------------

REPORT = {"open": 1, "dropped_last_30d": 0, "oldest": []}


def cfg(age=None, repaid=None) -> SimpleNamespace:
    return SimpleNamespace(debt_max_age_months=age, repayment_min_per_30d=repaid)


def test_no_policy_knobs_report_null_not_a_clean_list():
    assert _policy_findings(cfg(), REPORT, enforce=True) is None


def test_a_report_without_enforce_reports_null():
    assert _policy_findings(cfg(repaid=1), REPORT, enforce=False) is None


def test_a_policy_that_ran_clean_reports_an_empty_list():
    assert _policy_findings(cfg(age=12), REPORT, enforce=True) == [], "no mark is 12 months old"


def test_a_breached_policy_reports_its_findings():
    findings = _policy_findings(cfg(repaid=2), REPORT, enforce=True)

    assert len(findings) == 1 and "stalled" in findings[0]


# --- claims: the way back out of a --claim -----------------------------------

HELD = [claim(1, "core/alpha.py", "alpha( a , b )"), claim(2, "core/beta.py", "beta( a )")]


def test_release_all_takes_every_open_claim():
    assert _claims_to_release(HELD, True, []) == HELD


def test_release_names_one_claim_by_path_and_long_name():
    assert _claims_to_release(HELD, False, ["core/alpha.py", "alpha( a , b )"]) == [HELD[0]]


def test_release_takes_the_bare_identifier_too():
    assert _claims_to_release(HELD, False, ["core/alpha.py", "alpha"]) == [HELD[0]]


def test_releasing_a_claim_nobody_holds_says_what_is_held():
    with pytest.raises(CrapkitError) as exc:
        _claims_to_release(HELD, False, ["core/alpha.py", "gamma"])

    assert "core/beta.py" in str(exc.value) and "alpha( a , b )" in str(exc.value)


def test_release_without_a_path_and_name_says_what_it_needs():
    with pytest.raises(CrapkitError, match="PATH NAME"):
        _claims_to_release(HELD, False, ["core/alpha.py"])


def test_a_path_that_matches_no_claim_is_not_released_by_name_alone():
    with pytest.raises(CrapkitError):
        _claims_to_release(HELD, False, ["core/other.py", "alpha( a , b )"])
