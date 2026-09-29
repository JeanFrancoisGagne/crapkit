"""A CRAP that is exactly its ceiling is at the ceiling, on every surface.

README's rule is `crap > ceiling`. CRAP(18, 2/3) = 18^2 * (1/3)^3 + 18 is 30
exactly, and README recommends `target = 30` for crap4j's threshold, but the
double crapkit computes is 30.000000000000004. Every site that compared that
double with the ceiling read the function as over it: remedy said add-tests,
the totals counted it, seed marked it, the gate refused it.
"""
from types import SimpleNamespace

from crapkit.cli.scoring import _coverage_summary
from crapkit.digest import build_digest, totals
from crapkit.packet import file_totals
from crapkit.ratchet import RatchetEntry, seed_ratchet, update_ratchet
from crapkit.sarif import over_target_results
from crapkit.score import ScoredRow, crap, over_ceiling, remedy
from crapkit.store import SnapshotStore
from crapkit.verify import evaluate, unmarked_over_ceiling
from crapkit.worklist import closable_claims

AT_30 = crap(18, 2 / 3)


def at_30(name: str = "f( )", value: float = AT_30) -> ScoredRow:
    return ScoredRow("src", "src/a.py", name, 1, 9, 18, 18, 18, 3, 1, 1, 2 / 3, "measured",
                     value, "ok")


def test_the_double_of_an_exact_30_lands_above_30():
    assert AT_30 == 30.000000000000004


def test_a_score_a_few_units_in_the_last_place_over_its_ceiling_is_at_it():
    assert not over_ceiling(AT_30, 30)
    assert not over_ceiling(30.0, 30)


def test_the_closest_exact_crap_above_a_whole_ceiling_still_reads_over_it():
    """CRAP(1, 399/400) = 1 + 1/64,000,000, the smallest excess over a whole
    number at ccn 1 to 60 and totals up to 400."""
    value = crap(1, 399 / 400)

    assert value == 1.000000015625
    assert over_ceiling(value, 1)
    assert remedy(1, value, 1) == "add-tests"


def test_remedy_says_ok():
    assert remedy(18, AT_30, 30) == "ok"
    assert remedy(18, AT_30, 30, shared_span=True) == "ok"


def test_the_run_totals_count_nothing_over_target():
    assert totals([at_30()], target=30).over_target == 0


def test_a_brief_s_file_totals_count_nothing_over_target():
    assert file_totals([at_30()], {}, 30)["over_target"] == 0


def test_the_digest_names_no_new_function_over_its_ceiling():
    digest = build_digest([], [at_30()], ceiling_of=lambda scope: 30)

    assert [line for line in digest.lines if line.startswith("new over ceiling")] == []


def test_the_digest_calls_a_drop_from_the_ceiling_drift_not_an_improvement():
    before, after = at_30(), at_30(value=crap(18, 0.7))
    digest = build_digest([before], [after], ceiling_of=lambda scope: 30)

    assert [line for line in digest.lines if line.startswith("improved")] == []


def test_seed_places_no_mark():
    marks, added, tightened = seed_ratchet([], [at_30()], target=30)

    assert (marks, added, tightened) == ([], 0, 0)


def test_update_drops_the_mark_of_a_function_brought_to_its_ceiling():
    prior = [RatchetEntry("src/a.py", "f( )", 40.0)]

    assert update_ratchet(prior, [at_30()], target=30) == []


def test_verify_counts_it_as_no_unmarked_debt():
    assert unmarked_over_ceiling([at_30()], [], 30) == []


def test_the_gate_passes_a_touched_function_at_its_ceiling():
    verdict = evaluate(fresh=[at_30()], changed_ranges={"src/a.py": [(1, 9)]}, ratchet=[],
                       baseline_failures=set(), fresh_failures=set(), target=30)

    assert verdict.gate_violations == []


def test_a_claim_on_a_function_brought_to_its_ceiling_is_released():
    claim = {"id": 1, "path": "src/a.py", "long_name": "f( )", "commit": "c1",
             "created_at": "2026-01-01T00:00:00Z"}

    assert closable_claims([claim], [at_30()], target=30, scope_targets={},
                           stale_commits=set()) == [1]


def test_sarif_reports_no_over_target_result():
    assert over_target_results([at_30()], {}, 30) == []


def test_the_coverage_summary_counts_nothing_over_target():
    run = SimpleNamespace(scored=[at_30()], commit="c1", cache_hits=0, provenance={},
                          lane_errors={}, corpus=SimpleNamespace(files=1, skipped_max_bytes=0))
    cfg = SimpleNamespace(target=30, scope_targets={"src": 30}, ceilings={"default": 30},
                          ceiling_of=lambda scope: 30)
    shape = SimpleNamespace(kind="coverage", unmeasured=[])

    summary = _coverage_summary(1, run, cfg, shape, "crap.sqlite")

    assert (summary["over_target"], summary["grade"]) == (0, "A+")
    assert summary["by_scope"]["src"]["over_target"] == 0


def test_trend_s_stored_rollup_counts_nothing_over_target(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=[at_30()], lanes={"unit": {}})

    assert store.run_totals(target=30)[run_id][1] == 0
    assert store.run_scope_totals(target=30)[run_id]["src"][1] == 0


def test_a_rollup_stored_under_the_old_rule_is_decided_again(tmp_path):
    """A store filled by an earlier crapkit holds over_target 1 for this run at
    target 30. Its cache key names the rule, so trend never reads that row."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=[at_30()], lanes={"unit": {}})
    with store._conn:
        store._conn.executemany("INSERT INTO run_rollup VALUES (?, '[30,[]]', ?, ?, ?, ?)",
                                [(run_id, "", 0, 0, 0.0), (run_id, "src", 1, 1, AT_30)])

    assert store.run_totals(target=30)[run_id][1] == 0
