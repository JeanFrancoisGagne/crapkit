"""`inventory`, `coverage` and `rescore` driven in-process over a tmp repo.

These three commands were 43% covered by tests/unit: every rule they enforce was
pinned one layer down (score_rows, lane_order, overlay_stale_coverage) and the
commands that compose those rules were reached only from tests/e2e, at a process
each. `main(argv)` is the same entry point `python -m crapkit` uses, so the
assertions here are the ones an e2e test makes — exit code, stdout, stderr,
store rows — for a hundredth of the wall time.

Every test asserts through the command. Nothing here reaches into a helper's
return value.
"""
import json

from cli_inproc_repo import (add_knotty, commit_all, istanbul, repo,  # noqa: F401
                             seed_artifacts, template_repo)

import pytest

from crapkit.cli import main
from crapkit.lanes import write_stamps
from crapkit.store import SnapshotStore


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    """The command at its public seam: exit code, stdout, stderr."""
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def runs(repo) -> list[dict]:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()


def head(repo) -> str:
    from cli_inproc_repo import git
    return git(repo, "rev-parse", "HEAD").strip()


# --- inventory ----------------------------------------------------------------

def test_inventory_writes_a_run_and_names_what_it_analyzed(repo, capsys):
    code, out, err = run(["inventory"], repo, capsys)

    assert (code, err) == (0, "")
    assert "3 functions in 2 files (0 cached)" in out, out
    assert [r["kind"] for r in runs(repo)] == ["inventory"]


def test_inventory_json_carries_the_corpus_counts(repo, capsys):
    code, out, _ = run(["inventory", "--json"], repo, capsys)
    summary = json.loads(out)

    assert code == 0
    assert (summary["files"], summary["functions"]) == (2, 3)
    assert summary["run_id"] == 1 and summary["skipped_max_bytes"] == 0
    assert summary["commit"] == head(repo)


def test_a_second_inventory_reads_the_analysis_cache(repo, capsys):
    run(["inventory"], repo, capsys)

    _, out, _ = run(["inventory", "--json"], repo, capsys)

    assert json.loads(out)["cache_hits"] == 2, "both files were analyzed a moment ago"


def test_inventory_exports_the_snapshot_as_tsv(repo, capsys):
    code, _, _ = run(["inventory", "--export", ".crapkit/inv.tsv"], repo, capsys)

    lines = (repo / ".crapkit" / "inv.tsv").read_text(encoding="utf-8").splitlines()
    assert code == 0
    assert lines[0].split("\t")[:3] == ["scope", "path", "long_name"]
    assert len(lines) == 4, lines


def test_inventory_writes_the_db_it_was_pointed_at(repo, capsys, tmp_path):
    elsewhere = tmp_path / "side" / "snap.sqlite"

    code, out, _ = run(["inventory", "--db", str(elsewhere), "--json"], repo, capsys)

    assert code == 0 and elsewhere.is_file()
    assert json.loads(out)["db"] == str(elsewhere)
    assert not (repo / ".crapkit" / "crap.sqlite").exists()


def test_inventory_outside_a_configured_repo_names_the_file_it_wanted(tmp_path, capsys):
    code, _, err = run(["inventory"], tmp_path, capsys)

    assert code == 3
    assert "no crapkit.toml" in err, err


# --- coverage -----------------------------------------------------------------

def test_coverage_scores_every_lane_and_writes_a_baseline_run(repo, capsys):
    seed_artifacts(repo)

    code, out, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert (code, err) == (0, "")
    assert "3 functions scored — 3 measured / 0 untested / 0 no-lane / 0 cc-only" in out
    assert [r["kind"] for r in runs(repo)] == ["coverage"]


def test_coverage_json_reports_per_scope_rollups_and_the_lane_provenance(repo, capsys):
    seed_artifacts(repo)

    _, out, _ = run(["coverage", "--reuse-artifacts", "--json"], repo, capsys)
    summary = json.loads(out)

    assert sorted(summary["by_scope"]) == ["src", "web"]
    assert sorted(summary["lanes"]) == ["ui", "unit"]
    assert summary["lanes"]["unit"]["scopes"] == ["src"]
    assert summary["lane_failures"] == {} and summary["measured"] == 3


def test_one_failed_lane_leaves_its_scopes_unmeasured_and_the_run_untrusted(repo, capsys):
    """A failed lane must not read as untested code: its scopes fall back to
    `no-lane`, and the run is `partial`, so verify never baselines against it."""
    seed_artifacts(repo, ui=False)

    code, out, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 5
    assert "lane 'ui' FAILED" in err and "lane 'ui' FAILED" in out
    assert "2 measured / 0 untested / 1 no-lane" in out, out
    assert [r["kind"] for r in runs(repo)] == ["partial"]


def test_every_lane_failing_ends_the_run_instead_of_scoring_nothing(repo, capsys):
    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 5
    assert "every lane failed" in err, err
    assert runs(repo) == []


def test_a_lane_subset_is_a_partial_run(repo, capsys):
    seed_artifacts(repo)

    code, out, _ = run(["coverage", "--reuse-artifacts", "--lane", "unit"], repo, capsys)

    assert code == 0
    assert "2 measured / 0 untested / 1 no-lane" in out, out
    assert [r["kind"] for r in runs(repo)] == ["partial"]


def test_reuse_unchanged_skips_the_lane_whose_scopes_have_not_moved(repo, capsys):
    """The stamp says which commit the artifact describes. Nothing under either
    scope moved since, so neither command needs to run again."""
    seed_artifacts(repo)
    commit = head(repo)
    write_stamps(repo, {"coverage/unit.json": {"commit": commit, "lane": "unit", "seconds": 1.0},
                        "coverage/ui.json": {"commit": commit, "lane": "ui", "seconds": 1.0}})

    code, _, err = run(["coverage", "--reuse-unchanged"], repo, capsys)

    assert code == 0
    assert err.count("artifact still matches its scopes; reusing without rerun") == 2, err


def test_reuse_unchanged_reruns_the_lane_whose_scope_moved(repo, capsys):
    """The one test that runs a lane command. `ui` has no stamp, so it reruns
    and records one; `unit` is reused and records nothing."""
    seed_artifacts(repo)
    write_stamps(repo, {"coverage/unit.json": {"commit": head(repo), "lane": "unit",
                                               "seconds": 1.0}})

    code, _, err = run(["coverage", "--reuse-unchanged"], repo, capsys)

    assert code == 0
    assert err.count("reusing without rerun") == 1, err
    assert "lane 'unit'" in err and "lane 'ui'" not in err


def test_lanes_run_in_parallel_score_exactly_what_serial_lanes_score(repo, capsys):
    """max_parallel_lanes moves wall time only. The fold is in declaration
    order whatever order the lanes finished in, so the numbers cannot drift."""
    seed_artifacts(repo)
    _, serial, _ = run(["coverage", "--reuse-artifacts", "--json"], repo, capsys)
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(text.replace("target = 6", "target = 6\nmax_parallel_lanes = 2"),
                                       encoding="utf-8")

    code, parallel, err = run(["coverage", "--reuse-artifacts", "--json"], repo, capsys)

    assert code == 0
    assert "lane 'unit' started" in err and "lane 'ui' finished" in err
    for field in ("crap_load", "by_scope", "measured", "functions"):
        assert json.loads(parallel)[field] == json.loads(serial)[field], field


def test_coverage_writes_the_exports_it_was_asked_for(repo, capsys):
    seed_artifacts(repo)
    add_knotty(repo)

    code, _, _ = run(["coverage", "--reuse-artifacts", "--export", ".crapkit/scored.tsv",
                      "--sarif", ".crapkit/findings.sarif"], repo, capsys)

    assert code == 0
    assert "knotty" in (repo / ".crapkit" / "scored.tsv").read_text(encoding="utf-8")
    sarif = json.loads((repo / ".crapkit" / "findings.sarif").read_text(encoding="utf-8"))
    assert sarif["runs"][0]["results"], "an untested ccn-8 function is over the target"


def test_coverage_says_when_a_lane_measured_far_fewer_tests_than_before(repo, capsys):
    """`coverage` is the command that WRITES a baseline, and until this warning
    it said nothing at all about a suite that halved between two runs."""
    seed_artifacts(repo)
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    store.write_run(commit=head(repo), tool_versions={}, rows=[], kind="coverage",
                    lanes={"unit": {"tests_total": 400}, "ui": {"tests_total": 0}})

    code, _, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 0
    assert "lane 'unit' ran 0 tests, 400 fewer" in err, err
    assert "lane 'ui'" not in err, "a lane the baseline never counted cannot have dropped"


# --- rescore ------------------------------------------------------------------

@pytest.fixture()
def scored(repo, capsys):
    """A repo carrying one trusted coverage run, the way `rescore` needs it."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def test_rescore_prints_fresh_complexity_over_the_baselines_stale_coverage(scored, capsys):
    add_knotty(scored)

    code, out, _ = run(["rescore", "src/app.ts"], scored, capsys)

    assert code == 0
    assert "rescore vs run 1 @" in out and "coverage STALE, complexity fresh" in out
    assert "knotty" in out and " 8 " in out, out
    assert "web/ui.ts" not in out, "rescore answers about the files it was given"


def test_rescore_json_labels_the_coverage_as_the_baselines(scored, capsys):
    _, out, _ = run(["rescore", "src/app.ts", "--json"], scored, capsys)
    payload = json.loads(out)

    assert payload["baseline_run"] == 1
    assert {f["function"] for f in payload["functions"]} == {"dispatch ( kind )", "plain ( x )"}
    assert all(f["stale_coverage"] for f in payload["functions"])


def test_the_gate_passes_a_tree_that_changed_nothing_over_its_ceiling(scored, capsys):
    code, _, err = run(["rescore", "src/app.ts", "--gate"], scored, capsys)

    assert (code, err) == (0, "")


def test_the_gate_refuses_a_function_this_tree_pushed_over_the_ceiling(scored, capsys):
    add_knotty(scored)

    code, _, err = run(["rescore", "src/app.ts", "--gate"], scored, capsys)

    assert code == 6
    assert "1 rescored function(s) over their scope ceiling" in err, err
    assert "GATE" in err and "knotty" in err, err


def test_a_ratchet_mark_the_repo_already_signed_for_passes_the_gate(scored, capsys):
    """A mark is a recorded decision to carry the function as it stands. Past the
    mark verify would fail anyway, so the gate only holds what nothing covers."""
    add_knotty(scored)
    code, out, _ = run(["rescore", "src/app.ts", "--gate", "--json"], scored, capsys)
    knotty = next(f for f in json.loads(out)["functions"] if "knotty" in f["function"])
    assert code == 6
    (scored / "crapkit-ratchet.tsv").write_text(
        f"path\tlong_name\tcrap\nsrc/app.ts\t{knotty['function']}\t{knotty['crap']:.4f}\n",
        encoding="utf-8", newline="\n")

    again, _, err = run(["rescore", "src/app.ts", "--gate"], scored, capsys)

    assert (again, err) == (0, "")


def test_an_untracked_file_is_gated_in_full_and_says_so(scored, capsys):
    """git diff sees nothing of a file git tracks nothing of, so without this
    its violations print and the command still exits 0 — the gate lying."""
    (scored / "src" / "extra.ts").write_text(
        "export function loops(n: number): number {\n"
        + "".join(f"  if (n > {i}) {{ return {i}; }}\n" for i in range(1, 8))
        + "  return 0;\n}\n", encoding="utf-8")

    code, _, err = run(["rescore", "src/extra.ts", "--gate"], scored, capsys)

    assert code == 6
    assert "1 untracked file(s) gated in full (src/extra.ts)" in err, err
    assert "loops" in err


def test_rescore_without_a_snapshot_says_which_command_makes_one(repo, capsys):
    code, _, err = run(["rescore", "src/app.ts"], repo, capsys)

    assert code == 1
    assert "no snapshot" in err and "crapkit coverage" in err, err


def test_rescore_with_only_an_inventory_run_still_has_no_coverage_to_overlay(repo, capsys):
    run(["inventory"], repo, capsys)

    code, _, err = run(["rescore", "src/app.ts"], repo, capsys)

    assert code == 1
    assert "no scored run" in err, err
