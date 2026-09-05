"""`verify`, `test-scoped` and `hook-precommit` driven in-process over a tmp repo.

verify assembles a verdict out of parts that were each unit-tested one layer down
— pick_baseline, unstable_marks, stamp_conflict, closable_claims, evaluate — and
the assembly itself was reached only from tests/e2e, at a process per case. That
left 33 of the file's 49 functions with no unit test at all, the exit-code map
included.

`main(argv)` is the same entry point `python -m crapkit` uses. Everything here is
asserted through it: exit code, stdout, stderr, and what the store and the marks
file hold afterwards.
"""
import json

from cli_inproc_repo import (add_knotty, commit_all, git, istanbul,  # noqa: F401
                             repo, seed_artifacts, template_repo)

import pytest

from crapkit import config
from crapkit.cli import _verify_exit_code, main
from crapkit.cli import verifying
from crapkit.ratchet import RatchetEntry, dump_ratchet
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation, RatchetRegression, Verdict

MARKS = "crapkit-ratchet.tsv"


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    """The command at its public seam: exit code, stdout, stderr."""
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def store_of(repo) -> SnapshotStore:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite")


def head(repo) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def write_marks(repo, *entries: tuple[str, str, float], stamp: str | None = None) -> None:
    (repo / MARKS).write_text(
        dump_ratchet([RatchetEntry(*e) for e in entries], stamp=stamp),
        encoding="utf-8", newline="\n")


@pytest.fixture()
def baselined(repo, capsys):
    """A repo carrying one trusted coverage run at HEAD, which is what verify
    measures against."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


# --- the exit-code map --------------------------------------------------------
#
# Five codes, and their ORDER is the contract: a caller reading exit 8 has to be
# able to conclude there was no gate violation and no regression.

def verdict(**kw) -> Verdict:
    return Verdict(**{"ok": False, "gate_violations": [], "ratchet_regressions": [],
                      "new_failures": [], "dirty_failures": [], **kw})


GATE = GateViolation("src/a.ts", "f ( x )", 3, 9, 0.5, 84.0, "decompose")
ROSE = RatchetRegression("src/a.ts", "g ( y )", 4.0, 9.0)


def test_a_clean_verdict_exits_0():
    assert _verify_exit_code(verdict(ok=True)) == 0


def test_a_gate_violation_outranks_every_other_finding():
    assert _verify_exit_code(verdict(gate_violations=[GATE], ratchet_regressions=[ROSE],
                                     new_failures=["t::a"]), True) == 6


def test_a_ratchet_regression_outranks_a_new_failure():
    assert _verify_exit_code(verdict(ratchet_regressions=[ROSE], new_failures=["t::a"])) == 7


def test_a_new_failure_alone_is_8():
    assert _verify_exit_code(verdict(new_failures=["t::a"])) == 8


def test_the_diff_coverage_breach_is_9_and_never_hides_a_finding():
    assert _verify_exit_code(verdict(ok=True), True) == 9
    assert _verify_exit_code(verdict(new_failures=["t::a"]), True) == 8


# --- which run the verdict is measured against --------------------------------

def test_verify_with_no_snapshot_says_which_command_makes_one(repo, capsys):
    code, _, err = run(["verify"], repo, capsys)

    assert code == 1
    assert "no baseline snapshot" in err and "crapkit coverage" in err, err


def test_an_inventory_run_is_no_baseline_and_the_refusal_says_why(repo, capsys):
    run(["inventory"], repo, capsys)

    code, _, err = run(["verify"], repo, capsys)

    assert code == 1
    assert "no trusted scored baseline" in err, err
    assert "failed verifies and hook runs never serve as baselines" in err


def test_naming_a_run_that_is_not_trusted_is_refused(baselined, capsys):
    """The refusal is about the run that was named, not about an empty store.

    Run 1 is trusted here, so "no trusted scored baseline, run `crapkit
    coverage` first" would be false in its first clause and expensive in its
    remedy (#27): the answer is `--baseline 1`, and the line says so.
    """
    code, _, err = run(["verify", "--reuse-artifacts", "--baseline", "99"], baselined, capsys)

    assert code == 1
    assert "no run 99 in the store" in err, err
    assert "trusted runs: 1" in err, err

    assert main(["inventory", "--repo", str(baselined)]) == 0
    capsys.readouterr()
    code, _, err = run(["verify", "--reuse-artifacts", "--baseline", "2"], baselined, capsys)

    assert code == 1
    assert "run 2 is an inventory run" in err, err
    assert "--baseline 1" in err, err


def test_naming_the_baseline_run_by_id_accepts_it(baselined, capsys):
    code, out, _ = run(["verify", "--reuse-artifacts", "--baseline", "1", "--json"],
                       baselined, capsys)

    assert code == 0
    assert json.loads(out)["baseline_run"] == 1


def test_a_run_taken_after_a_failed_verify_is_named_and_stepped_over(baselined, capsys):
    """The taint rule at the command. A `coverage` run between a failed verify
    and its fix would move the comparison point past the findings, so the
    baseline stays behind it and one stderr line says so."""
    store = store_of(baselined)
    failed = store.write_run(commit=head(baselined), tool_versions={}, rows=[],
                             lanes={"unit": {}}, kind="verify")
    store.set_verdict_ok(failed, False, findings=3)
    skipped = store.write_run(commit=head(baselined), tool_versions={}, rows=[],
                              lanes={"unit": {}}, kind="coverage")

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)

    assert code == 0
    assert f"run {skipped} is not the baseline" in err, err
    assert f"verify run {failed} FAILED with 3 finding(s)" in err, err
    assert json.loads(out)["baseline_run"] == 1


def test_a_baseline_commit_history_rewrote_away_is_refused(baselined, capsys):
    """An amend or a rebase leaves the baseline run pointing at a commit that is
    no longer an ancestor, and every diff measured from it is a fiction."""
    git(baselined, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "-c", "commit.gpgsign=false", "commit", "-q", "--amend", "-m", "reworded")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 4
    assert "is not an ancestor of HEAD" in err, err
    assert "rewrote history" in err and "shallow" not in err, err


def test_a_shallow_clone_missing_the_baseline_commit_is_named_as_such(baselined, capsys, tmp_path):
    """actions/checkout clones one commit by default. The commit the portable
    baseline names is not in that clone at all, and blaming a rewrite sent the
    platform engineer after a fresh baseline when the fix was a deeper fetch.
    Same exit 4; a different sentence."""
    run(["verify", "--reuse-artifacts", "--emit-baseline", "crapkit-baseline.tsv"],
        baselined, capsys)
    commit_all(baselined, "carry the baseline")
    clone = tmp_path / "shallow"
    git(baselined, "clone", "-q", "--depth", "1", "--no-local", str(baselined), str(clone))

    code, _, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "crapkit-baseline.tsv"],
                       clone, capsys)

    assert code == 4
    assert ("is not an ancestor of HEAD in this shallow clone, which does not hold it; "
            "set fetch-depth: 0 on the checkout or run git fetch --unshallow") in err, err
    assert "rewrote history" not in err


# --- the portable baseline file -----------------------------------------------

def test_a_baseline_written_out_reads_back_in(baselined, capsys):
    """The clone whose .crapkit/ is gitignored: the baseline travels as a file
    the repo carries. It holds no lane provenance, so `baseline_run` is null."""
    code, _, _ = run(["verify", "--reuse-artifacts", "--emit-baseline", ".crapkit/base.tsv"],
                     baselined, capsys)
    assert code == 0
    assert (baselined / ".crapkit" / "base.tsv").is_file()

    again, out, _ = run(["verify", "--reuse-artifacts", "--json",
                         "--baseline-tsv", ".crapkit/base.tsv"], baselined, capsys)
    payload = json.loads(out)

    assert again == 0
    assert payload["baseline_run"] is None
    assert payload["baseline_commit"] == head(baselined)


def test_a_missing_baseline_file_names_the_flag_that_writes_one(baselined, capsys):
    code, _, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "gone.tsv"],
                       baselined, capsys)

    assert code == 1
    assert "no baseline file at" in err and "--emit-baseline" in err, err


def test_an_unreadable_baseline_file_is_a_config_error(baselined, capsys):
    (baselined / "bad.tsv").write_text("not\ta\tbaseline\n", encoding="utf-8")

    code, _, err = run(["verify", "--reuse-artifacts", "--baseline-tsv", "bad.tsv"],
                       baselined, capsys)

    assert code == 3
    assert "unreadable baseline file bad.tsv" in err, err


# --- --base, and the run that has to sit behind the fork point ----------------

def test_base_measures_from_the_fork_point_and_takes_the_run_behind_it(baselined, capsys):
    code, out, _ = run(["verify", "--reuse-artifacts", "--base", "HEAD", "--json"],
                       baselined, capsys)

    assert code == 0
    assert json.loads(out)["baseline_run"] == 1


def test_base_refuses_when_every_trusted_run_sits_ahead_of_the_fork_point(repo, capsys):
    """A run made further up the branch measures the diff from its own commit,
    and everything committed before it stops being touched, which is exactly the
    shrinking --base exists to stop."""
    app = repo / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    commit_all(repo, "second")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()

    code, _, err = run(["verify", "--reuse-artifacts", "--base", "HEAD~1"], repo, capsys)

    assert code == 1
    assert "no trusted scored run at or behind" in err, err
    assert "crapkit coverage` on the base commit" in err, err


# --- the verdict --------------------------------------------------------------

def test_a_clean_tree_verifies_and_names_what_it_compared(baselined, capsys):
    code, out, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert (code, err) == (0, "")
    assert f"verify OK @ {head(baselined)[:11]}" in out, out
    assert "(0 changed files)" in out, out


def test_the_json_verdict_carries_the_receipt_that_dates_it(baselined, capsys):
    """tool_versions and the ratchet hash are what let a reader tell a verdict
    from a verdict taken under a different metric."""
    write_marks(baselined)

    _, out, _ = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)
    payload = json.loads(out)

    assert payload["ok"] is True and payload["commit"] == head(baselined)
    assert payload["tool_versions"]["crapkit"]
    assert len(payload["ratchet_sha256"]) == 64
    assert payload["committed_findings"] == 0 and payload["dirty_findings"] == 0
    assert payload["diff_uncovered_count"] == 0


def test_the_json_verdict_carries_the_diff_coverage_ceiling(baselined, capsys):
    """The count alone cannot say whether exit 9 is near: the ceiling it is
    judged against travels with it, null when the repo set none (warn only)."""
    _, out, _ = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)
    assert json.loads(out)["diff_uncovered_max"] is None

    text = (baselined / "crapkit.toml").read_text(encoding="utf-8")
    (baselined / "crapkit.toml").write_text(
        text.replace("target = 6", "target = 6\ndiff_uncovered_max = 1"), encoding="utf-8")

    _, out, _ = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)
    assert json.loads(out)["diff_uncovered_max"] == 1


def test_a_function_this_tree_pushed_over_the_ceiling_fails_the_verdict(baselined, capsys):
    code, out, _ = run(["verify", "--reuse-artifacts"], _knotty(baselined), capsys)

    assert code == 6
    assert "verify FAILED @" in out and "GATE" in out and "knotty" in out, out
    assert "[dirty]" in out, "an uncommitted breach is not the committed tree's"
    assert "findings: 0 committed / 1 dirty" in out, out


def _knotty(repo):
    add_knotty(repo)
    return repo


def test_an_override_grants_a_pure_gate_violation_and_leaves_a_record(baselined, capsys):
    """--override is not a bypass: the violation is printed as OVERRIDDEN, the
    debt lands in the marks file, and the store carries the reason."""
    code, out, _ = run(["verify", "--reuse-artifacts", "--override", "shipping the spike"],
                       _knotty(baselined), capsys)

    assert code == 0
    assert "OVERRIDDEN" in out and "knotty" in out, out
    assert "knotty" in (baselined / MARKS).read_text(encoding="utf-8")
    assert "crapkit OVERRIDE (shipping the spike)" in \
        (baselined / "alerts.log").read_text(encoding="utf-8")


def test_a_granted_override_names_the_mark_it_wrote_and_asks_for_git_add(baselined, capsys):
    """The grant is the override's own write to the marks file: the OK line
    says so and names the file to stage, so a dirty marks file after a green
    run is no more a surprise here than after a tighten."""
    code, out, _ = run(["verify", "--reuse-artifacts", "--override", "shipping the spike"],
                       _knotty(baselined), capsys)

    assert code == 0
    assert (f"verify OK @ {head(baselined)[:11]} vs baseline {head(baselined)[:11]} "
            f"(1 changed files) ratchet: 1 mark granted -> git add {MARKS}") in out, out


def test_a_granted_override_keeps_ratchet_changes_null_under_json(baselined, capsys):
    """`ratchet_changes` counts what a tighten did; a grant is listed under
    `overridden` and is not counted twice."""
    code, out, _ = run(["verify", "--reuse-artifacts", "--override", "shipping the spike",
                        "--json"], _knotty(baselined), capsys)

    payload = json.loads(out)
    assert code == 0
    assert payload["ratchet_changes"] is None
    assert [v["long_name"] for v in payload["overridden"]] == ["knotty ( n )"]


def test_an_override_never_grants_a_ratchet_regression(baselined, capsys):
    """Regressions and new failures never qualify. A run holding both keeps its
    findings, and the exit code is still the gate's."""
    write_marks(baselined, ("src/app.ts", "plain ( x )", 0.1))

    code, out, _ = run(["verify", "--reuse-artifacts", "--override", "please"],
                       _knotty(baselined), capsys)

    assert code == 6
    assert "OVERRIDDEN" not in out, out
    assert "RATCHET" in out and "plain ( x ): 0.1 -> 2.5" in out, out


def test_a_mark_that_rose_is_its_own_exit_code(baselined, capsys):
    write_marks(baselined, ("src/app.ts", "plain ( x )", 0.1))

    code, out, _ = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 7
    assert "RATCHET  src/app.ts  plain ( x ): 0.1 -> 2.5" in out, out


# --- the marks file: the stamp guard, the tighten, and what holds it ----------

def test_marks_recorded_under_another_metric_are_refused_before_the_lanes_run(baselined, capsys):
    """Finding this out after a 40-minute lane run is too late, so the guard runs
    first. A metric bump that silently kept 40k old marks is what it exists for."""
    write_marks(baselined, ("src/app.ts", "plain ( x )", 9.0), stamp="lizard 0.0.1 analysis 0")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 3
    assert "were recorded under [lizard 0.0.1 analysis 0]" in err, err
    assert "crapkit ratchet seed" in err


def test_a_marks_file_written_before_stamping_warns_and_still_verifies(baselined, capsys):
    write_marks(baselined, ("src/app.ts", "plain ( x )", 9.0), stamp="")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert f"{MARKS} carries no metric stamp" in err, err


def test_a_clean_pass_tightens_the_marks(baselined, capsys):
    """On a commit the store has never scored, the tighten has nothing to
    compare this measurement against, so no mark is held back."""
    write_marks(baselined, ("src/app.ts", "plain ( x )", 9.0))
    git(baselined, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "empty")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert (code, err) == (0, "")
    assert "plain" not in (baselined / MARKS).read_text(encoding="utf-8"), \
        "a mark under the scope ceiling is debt paid off, not debt to tighten"


def test_no_tighten_passes_the_verdict_without_rewriting_the_marks(baselined, capsys):
    """The blunt escape: the verdict still stands, the file is simply not rewritten."""
    write_marks(baselined, ("src/app.ts", "plain ( x )", 9.0))
    before = (baselined / MARKS).read_text(encoding="utf-8")

    code, _, _ = run(["verify", "--reuse-artifacts", "--no-tighten"], baselined, capsys)

    assert code == 0
    assert (baselined / MARKS).read_text(encoding="utf-8") == before


def test_a_measurement_that_bounced_on_one_commit_holds_its_mark(baselined, capsys):
    """One commit measured twice cannot have improved, so a score that moved past
    tighten_max_jump is the measurement's own noise. Tightening on the lucky half
    makes the next run fail on the unlucky one."""
    write_marks(baselined, ("src/app.ts", "dispatch ( kind )", 6.0))
    istanbul(baselined, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 0), "plain": (13, 20, 1)})

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert "NO TIGHTEN  src/app.ts  dispatch ( kind )" in err, err
    assert "measurement moved 2.0 -> 6.0 on the same commit" in err, err
    assert "6.0000" in (baselined / MARKS).read_text(encoding="utf-8")


# --- what else a verdict settles ---------------------------------------------

def test_a_function_back_at_its_ceiling_releases_the_claim_on_it(baselined, capsys):
    """verify is the only command that rescores everything AND knows where HEAD
    is, so it is the one that can tell a finished claim from a held one."""
    store = store_of(baselined)
    store.record_claim(path="src/app.ts", long_name="dispatch ( kind )",
                       commit=head(baselined), handle="session-a")
    assert len(store.open_claims()) == 1

    code, _, _ = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert store_of(baselined).open_claims() == []


def test_a_lane_running_fewer_tests_than_the_baseline_is_named(baselined, capsys):
    """Suite decay passes a pass/fail check silently; say it out loud. Skips are
    the second half of the same decay and get their own line."""
    _junit(baselined, failing=False, skipped=True)
    _results_artifact(baselined)
    store_of(baselined).write_run(commit=head(baselined), tool_versions={}, rows=[],
                                  lanes={"unit": {"tests_total": 400, "tests_skipped": 0}},
                                  kind="coverage")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert "lane 'unit' runs 398 fewer tests than the baseline" in err, err
    assert "lane 'unit' skips 1 more tests than the baseline" in err, err
    assert "'ui'" not in err, "a lane the baseline never counted cannot have shrunk"


def test_a_lane_that_lost_its_test_count_names_the_gap(baselined, capsys):
    """The baseline counted this lane's tests, this run counted none: the lane
    no longer declares a results_artifact. Say the count is gone rather than
    subtract a number that does not exist."""
    store_of(baselined).write_run(commit=head(baselined), tool_versions={}, rows=[],
                                  lanes={"unit": {"tests_total": 100}}, kind="coverage")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert ("lane 'unit' wrote no test counts this run (no results_artifact was parsed), "
            "so the baseline's 100 tests cannot be compared") in err, err
    assert "fewer tests" not in err, err


def test_dead_lines_in_the_diff_warn_and_breach_the_ceiling(baselined, capsys):
    """Exit 9 is its own code because the tree is otherwise clean: nothing is
    over the gate, no mark rose, no test broke, and the change is still untested."""
    text = (baselined / "crapkit.toml").read_text(encoding="utf-8")
    (baselined / "crapkit.toml").write_text(
        text.replace("target = 6", "target = 6\ndiff_uncovered_max = 0"), encoding="utf-8")
    istanbul(baselined, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 2), "plain": (13, 20, 1)}, dead=(17,))
    app = baselined / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8").replace("return -x;", "return 0 - x;"),
                   encoding="utf-8")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 9
    assert "warning: 1 changed line(s) have no coverage" in err, err
    assert "uncovered src/app.ts:17" in err, err
    assert "1 uncovered changed line(s) over the ceiling 0" in err, err


def test_dead_changed_lines_under_the_ceiling_warn_without_failing(baselined, capsys):
    """The warning is unconditional; only the ceiling decides the verdict."""
    text = (baselined / "crapkit.toml").read_text(encoding="utf-8")
    (baselined / "crapkit.toml").write_text(
        text.replace("target = 6", "target = 6\ndiff_uncovered_max = 1"), encoding="utf-8")
    istanbul(baselined, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 2), "plain": (13, 20, 1)}, dead=(17,))
    app = baselined / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8").replace("return -x;", "return 0 - x;"),
                   encoding="utf-8")

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert "warning: 1 changed line(s) have no coverage" in err, err
    assert "over the ceiling" not in err, err


def test_a_clean_tree_does_not_walk_the_artifacts_for_diff_coverage(
        baselined, capsys, monkeypatch):
    """diff_uncovered iterates the changed ranges, so an empty diff makes it []
    whatever the artifacts hold. Reading every lane's artifact to build that []
    is unconditional work that grows with lane count and artifact size; on a
    31,459-file tree whose istanbul lanes now fold their dead lines in as they
    are walked it is the last 0.2 s of a 17.6 s verify."""
    from crapkit import uncovered

    def refuse(*_args, **_kwargs):
        raise AssertionError("an empty diff must not walk the lane artifacts")

    monkeypatch.setattr(uncovered, "missing_by_path", refuse)

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)

    assert (code, err) == (0, "")
    assert json.loads(out)["diff_uncovered_count"] == 0


def test_an_empty_diff_cannot_breach_the_diff_coverage_ceiling(repo, capsys):
    """The skip has to leave exit 9 exactly as unreachable as it already was.
    The dead line seeded here is real and breaches the ceiling of 0 the moment
    it lands in a diff — that is the test two above — so the only thing keeping
    this run at 0 is the empty diff, both before and after the skip."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        text.replace("target = 6", "target = 6\ndiff_uncovered_max = 0"), encoding="utf-8")
    istanbul(repo, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 2), "plain": (13, 20, 1)}, dead=(17,))
    istanbul(repo, "coverage/ui.json", "web/ui.ts", {"render": (1, 3, 2)})
    commit_all(repo, "a ceiling of zero")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()

    code, out, err = run(["verify", "--reuse-artifacts"], repo, capsys)

    assert (code, err) == (0, "")
    assert "(0 changed files)" in out, out
    assert "have no coverage" not in err and "over the ceiling" not in err, err


def test_a_verdict_writes_the_findings_file_it_was_asked_for(baselined, capsys):
    code, _, _ = run(["verify", "--reuse-artifacts", "--sarif", ".crapkit/v.sarif"],
                     _knotty(baselined), capsys)

    sarif = json.loads((baselined / ".crapkit" / "v.sarif").read_text(encoding="utf-8"))
    assert code == 6
    assert any("knotty" in json.dumps(r) for r in sarif["runs"][0]["results"])


# --- what verify refuses to conclude on --------------------------------------

def test_verify_refuses_a_scope_no_lane_measures(repo, capsys):
    """Stricter than `coverage`, on purpose: coverage scores such a scope and
    flags it no-lane, verify calls coverage half the verdict."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(text.split("[[lane]]\nname = \"ui\"")[0],
                                       encoding="utf-8")

    code, _, err = run(["verify"], repo, capsys)

    assert code == 3
    assert "verify needs a [[lane]] for scope(s) web" in err, err


def test_verify_cannot_conclude_with_a_lane_that_failed(baselined, capsys):
    (baselined / "coverage" / "ui.json").unlink()

    code, _, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 5
    assert "verify cannot conclude with failed lanes" in err, err


def test_a_test_the_baseline_never_saw_fail_is_a_new_failure(baselined, capsys):
    """A lane declaring no retest_command keeps its failures untouched, so the
    verdict is the lane's report."""
    _junit(baselined, failing=True)
    _results_artifact(baselined)

    code, out, _ = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 8
    assert "NEW FAILURE  src/app.test.ts::renders" in out, out
    assert "findings: 1 committed / 0 dirty" in out, \
        "nothing in this tree edited the test file, so the failure is the commit's"


def test_a_failure_that_passes_on_rerun_is_reported_as_a_flake(baselined, capsys):
    """A lane declaring retest_command reruns just the newly-failed ids. Only
    the ids that pass the rerun leave the verdict."""
    _junit(baselined, failing=True)
    (baselined / "repair.py").write_text(
        "import pathlib\n"
        "pathlib.Path('junit.xml').write_text("
        "'<testsuite tests=\"1\"><testcase classname=\"src/app.test.ts\" "
        "name=\"renders\"/></testsuite>', encoding='utf-8')\n", encoding="utf-8")
    _results_artifact(baselined, retest=True)

    code, out, err = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0, (out, err)
    assert "flake retry: 1 of 1 new failures passed on rerun" in err, err


def _junit(repo, *, failing: bool, skipped: bool = False) -> None:
    body = '<failure message="boom">trace</failure>' if failing else ""
    extra = ('<testcase classname="src/app.test.ts" name="later"><skipped/></testcase>'
             if skipped else "")
    (repo / "junit.xml").write_text(
        f'<testsuite tests="{2 if skipped else 1}" failures="{int(failing)}">'
        f'<testcase classname="src/app.test.ts" name="renders">{body}</testcase>'
        f"{extra}</testsuite>", encoding="utf-8")


def _results_artifact(repo, retest: bool = False) -> None:
    """Give the `unit` lane a junit report, and optionally a retest command."""
    line = 'artifact = "coverage/unit.json"\nresults_artifact = "junit.xml"'
    if retest:
        line += '\nretest_command = "python repair.py {tests}"'
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        text.replace('artifact = "coverage/unit.json"', line), encoding="utf-8")


# --- test-scoped: routing changed files to their scope's own suite ------------

def test_test_scoped_runs_the_template_of_the_scope_that_owns_the_file(repo, capsys):
    code, _, err = run(["test-scoped", "src/app.ts"], repo, capsys)

    assert (code, err) == (0, "")


def test_a_failing_runner_reports_its_own_exit_code_under_crapkits(repo, capsys):
    """The runner's code would collide with crapkit's 3/5/6/7/8, so the command
    exits 1 and names the runner's code in the line."""
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        text.replace('src = "python -c pass"',
                     "src = 'python -c \"import sys; sys.exit(3)\"'"), encoding="utf-8")

    code, _, err = run(["test-scoped", "src/app.ts"], repo, capsys)

    assert code == 1
    assert "scoped tests for 'src' failed (runner exit 3)" in err, err


def test_a_test_file_outside_every_scope_routes_to_the_only_templated_scope(repo, capsys):
    """Test directories sit outside every scope by design."""
    code, _, err = run(["test-scoped", "tests/app.test.ts"], repo, capsys)

    assert (code, err) == (0, "")


def test_a_test_file_two_templated_scopes_could_own_names_both_routes_out(repo, capsys):
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        text.replace('src = "python -c pass"',
                     'src = "python -c pass"\nweb = "python -c pass"'), encoding="utf-8")

    code, _, err = run(["test-scoped", "tests/app.test.ts"], repo, capsys)

    assert code == 3
    assert "2 scopes declare templates (src, web)" in err, err
    assert "scoped_tests template with no {files} placeholder" in err, err


def test_a_source_file_no_scope_claims_is_a_config_error(repo, capsys):
    code, _, err = run(["test-scoped", "docs/intro.md"], repo, capsys)

    assert code == 3
    assert "docs/intro.md belongs to no declared scope" in err, err


def test_a_scope_with_no_template_names_the_table_it_is_missing_from(repo, capsys):
    code, _, err = run(["test-scoped", "web/ui.ts"], repo, capsys)

    assert code == 3
    assert "no [crapkit.scoped_tests] template for scope 'web'" in err, err


# --- hook-precommit: the staged-blob gate ------------------------------------

def stage(repo, *paths: str) -> None:
    git(repo, "add", *paths)


def test_a_commit_that_stages_nothing_passes_the_gate(repo, capsys):
    code, out, err = run(["hook-precommit"], repo, capsys)

    assert (code, out, err) == (0, "", "")


def test_a_staged_function_over_the_ceiling_refuses_the_commit(repo, capsys):
    add_knotty(repo)
    stage(repo, "src/app.ts")

    code, out, err = run(["hook-precommit"], repo, capsys)

    assert (code, err) == (6, "")
    assert "1 staged function(s) exceed the complexity ceiling of 6" in out, out
    assert "ccn   8  src/app.ts:" in out and "knotty" in out, out
    assert "decompose before committing" in out


def test_a_fix_the_developer_forgot_to_stage_is_named_as_such(repo, capsys):
    """The STAGED blob is what commits. A byte compare against the working tree
    called every CRLF checkout stale, so git's own filters decide this."""
    add_knotty(repo)
    stage(repo, "src/app.ts")
    add_knotty(repo, "src/app.ts")

    code, out, _ = run(["hook-precommit"], repo, capsys)

    assert code == 6
    assert "note: src/app.ts differs from the working tree" in out, out
    assert "re-stage with `git add`" in out


def test_a_staged_function_the_repo_already_signed_for_is_not_gated(repo, capsys):
    """Existence, not the numeric rule: a blob carries no coverage, so the only
    question the hook can answer is whether the repo already signed for this
    function. `verify` keeps the numeric check."""
    add_knotty(repo)
    stage(repo, "src/app.ts")
    write_marks(repo, ("src/app.ts", "knotty ( n )", 72.0))

    code, out, err = run(["hook-precommit"], repo, capsys)

    assert (code, out) == (0, "")
    assert "1 staged function(s) carry a ratchet mark and were not gated" in err, err


def test_staged_source_no_scope_claims_is_named_and_not_gated(repo, capsys):
    (repo / "stray.ts").write_text("export function s(n: number): number { return n; }\n",
                                   encoding="utf-8")
    stage(repo, "stray.ts")

    code, _, err = run(["hook-precommit"], repo, capsys)

    assert code == 0
    assert "1 staged file(s) belong to no scope and were not gated: stray.ts" in err, err
    assert "add a [[scope]] claiming them" in err


def test_the_env_override_grants_the_commit_through_a_full_audit(repo, capsys, monkeypatch):
    """CRAPKIT_OVERRIDE_REASON is not a bypass: alert line, ratchet debt staged
    into the pending commit, and a snapshot record — all three or nothing."""
    add_knotty(repo)
    stage(repo, "src/app.ts")
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", "hotfix, ticket 41")

    code, out, _ = run(["hook-precommit"], repo, capsys)

    assert code == 0
    assert "override granted with full audit (hotfix, ticket 41)" in out, out
    assert "clear CRAPKIT_OVERRIDE_REASON now" in out
    assert "crapkit OVERRIDE (hotfix, ticket 41)" in (repo / "alerts.log").read_text(encoding="utf-8")
    assert "knotty" in (repo / MARKS).read_text(encoding="utf-8")
    assert MARKS in git(repo, "diff", "--cached", "--name-only")
    assert [r["kind"] for r in store_of(repo).list_runs()] == ["hook"]


# --- the receipt is spelled for the shell that ran the hook --------------------
#
# `unset` is a POSIX builtin. Printed on Windows it errors, the variable stays
# set, and the next commit is granted a full override for a brand new violating
# function with nobody typing a reason.

def test_the_posix_receipt_names_unset(capsys, monkeypatch):
    monkeypatch.setattr(config, "SHELL_IS_CMD", False)

    verifying._print_clear_the_reason()

    out = capsys.readouterr().out
    assert "`unset CRAPKIT_OVERRIDE_REASON`" in out, out
    assert "$env:" not in out


def test_the_windows_receipt_names_both_windows_shells(capsys, monkeypatch):
    """SHELL_IS_CMD knows the platform, not which of the two shells the operator
    typed into, so the receipt hands over both spellings."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)

    verifying._print_clear_the_reason()

    out = capsys.readouterr().out
    assert "$env:CRAPKIT_OVERRIDE_REASON = $null" in out, out
    assert "set CRAPKIT_OVERRIDE_REASON=" in out, out
    assert "unset CRAPKIT_OVERRIDE_REASON" not in out, "the builtin neither shell has"


def test_the_receipt_says_an_exported_variable_is_cleared_where_it_was_set(capsys):
    """No shell command here clears a variable a CI job or a launcher exported,
    and that is the case where the grant repeats invisibly."""
    verifying._print_clear_the_reason()

    assert "clear it where it was set" in capsys.readouterr().out


# --- verify says what it did: the refused override and the marks file --------
#
# 0.5.0 item 3. A refused --override used to be exit 6 with no line naming why,
# and a green run rewrote the marks file on every pass, creating one to hold
# zero marks in a clean checkout and leaving a dirty one with nothing on the OK
# line to say what moved.

REFUSED_REGRESSION = ("override refused: 1 ratchet regression (src/app.ts plain ( x ) "
                      "0.1 -> 2.5) never qualifies for an override; "
                      "raise the mark by hand and commit it")
REFUSED_FAILURE = ("override refused: 1 new test failure (src/app.test.ts::renders) "
                   "never qualifies for an override; fix the failing test first")


def test_a_refused_override_says_why_on_stderr(baselined, capsys):
    """stderr, not stdout: `--json` prints one object, and the findings the run
    always printed stay where they were."""
    write_marks(baselined, ("src/app.ts", "plain ( x )", 0.1))

    code, out, err = run(["verify", "--reuse-artifacts", "--override", "please"],
                         _knotty(baselined), capsys)

    assert code == 6
    assert REFUSED_REGRESSION in err, err
    assert "override refused" not in out and "OVERRIDDEN" not in out, out


def test_a_refused_override_under_json_keeps_stdout_one_object(baselined, capsys):
    write_marks(baselined, ("src/app.ts", "plain ( x )", 0.1))

    code, out, err = run(["verify", "--reuse-artifacts", "--override", "please", "--json"],
                         _knotty(baselined), capsys)

    assert code == 6
    assert json.loads(out)["overridden"] == []
    assert REFUSED_REGRESSION in err, err


def test_a_new_failure_refuses_the_override_and_names_the_test(baselined, capsys):
    _junit(baselined, failing=True)
    _results_artifact(baselined)

    code, _, err = run(["verify", "--reuse-artifacts", "--override", "please"],
                       _knotty(baselined), capsys)

    assert code == 6
    assert REFUSED_FAILURE in err, err


def test_both_refusal_causes_share_one_line(baselined, capsys):
    """One line, both causes: a run holding a regression and a new failure is
    refused once, and each cause names its own escape."""
    write_marks(baselined, ("src/app.ts", "plain ( x )", 0.1))
    _junit(baselined, failing=True)
    _results_artifact(baselined)

    code, _, err = run(["verify", "--reuse-artifacts", "--override", "please"],
                       _knotty(baselined), capsys)

    assert code == 6
    assert ("override refused: 1 ratchet regression (src/app.ts plain ( x ) 0.1 -> 2.5) "
            "and 1 new test failure (src/app.test.ts::renders) never qualify for an "
            "override; raise the mark by hand and commit it; fix the failing test first") in err, err
    assert err.count("override refused") == 1


def test_an_override_on_a_passing_run_says_nothing(baselined, capsys):
    """Nothing was refused, so there is nothing to say."""
    code, _, err = run(["verify", "--reuse-artifacts", "--override", "please"],
                       baselined, capsys)

    assert (code, err) == (0, "")


@pytest.fixture()
def marked_debt(repo, capsys):
    """A baseline whose committed tree holds `knotty` (ccn 8, half covered, CRAP
    16): a marked function still over the ceiling, so a mark on it has room to
    tighten while a mark on `plain` (CRAP 2.5) has only room to drop."""
    add_knotty(repo)
    commit_all(repo, "add knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def test_a_green_run_that_moved_marks_says_so_and_asks_for_git_add(marked_debt, capsys):
    write_marks(marked_debt, ("src/app.ts", "knotty ( n )", 100.0),
                ("src/app.ts", "plain ( x )", 9.0))

    code, out, err = run(["verify", "--reuse-artifacts"], marked_debt, capsys)

    assert (code, err) == (0, "")
    assert (f"verify OK @ {head(marked_debt)[:11]} vs baseline {head(marked_debt)[:11]} "
            f"(0 changed files) ratchet: 1 dropped, 1 tightened -> git add {MARKS}") in out, out
    text = (marked_debt / MARKS).read_text(encoding="utf-8")
    assert "knotty ( n )\t16.0000" in text and "plain" not in text, text


def test_the_json_receipt_counts_what_the_tighten_did(marked_debt, capsys):
    write_marks(marked_debt, ("src/app.ts", "knotty ( n )", 100.0),
                ("src/app.ts", "plain ( x )", 9.0))

    _, out, _ = run(["verify", "--reuse-artifacts", "--json"], marked_debt, capsys)

    assert json.loads(out)["ratchet_changes"] == {"dropped": 1, "tightened": 1}


def test_a_green_run_with_nothing_to_move_leaves_the_marks_file_alone(marked_debt, capsys):
    """Written only when its text would change: a mark already at its measured
    value is not a tighten, and rewriting the file dirtied a clean checkout with
    a diff of nothing."""
    write_marks(marked_debt, ("src/app.ts", "knotty ( n )", 16.0))
    before = (marked_debt / MARKS).stat().st_mtime_ns

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], marked_debt, capsys)

    assert (code, err) == (0, "")
    assert (marked_debt / MARKS).stat().st_mtime_ns == before, "the file was rewritten"
    assert json.loads(out)["ratchet_changes"] is None


def test_a_green_run_with_nothing_to_move_prints_no_ratchet_suffix(marked_debt, capsys):
    write_marks(marked_debt, ("src/app.ts", "knotty ( n )", 16.0))

    _, out, _ = run(["verify", "--reuse-artifacts"], marked_debt, capsys)

    assert "ratchet:" not in out and "git add" not in out, out


def test_a_marks_file_written_before_stamping_is_restamped_and_says_so(marked_debt, capsys):
    """The one rewrite that moves no mark: a legacy file gains the stamp line.
    `0 dropped, 0 tightened` explained nothing about the dirty file it left."""
    write_marks(marked_debt, ("src/app.ts", "knotty ( n )", 16.0), stamp="")

    code, out, err = run(["verify", "--reuse-artifacts"], marked_debt, capsys)

    assert code == 0
    assert "carries no metric stamp" in err, err
    assert f"(0 changed files) ratchet: restamped -> git add {MARKS}" in out, out
    assert (marked_debt / MARKS).read_text(encoding="utf-8").startswith("# crapkit-analysis=")


def test_a_green_run_never_creates_a_marks_file_to_hold_zero_marks(baselined, capsys):
    """A clean CI checkout used to end with an untracked marks file holding a
    stamp, a header and no rows."""
    assert not (baselined / MARKS).exists()

    code, out, _ = run(["verify", "--reuse-artifacts"], baselined, capsys)

    assert code == 0
    assert not (baselined / MARKS).exists(), "verify created a marks file with no marks in it"
    assert "ratchet:" not in out, out


# --- standing debt no mark covers ---------------------------------------------
#
# Legacy debt is trend, not a blocker, until touched (test_verify.py). What that
# leaves is a function over its ceiling with no mark: the gate never looks at it
# and the ratchet has nothing to compare, so coverage rot on it passes unseen.
# A header-only marks file cannot say whether seed ran. verify counts it.

def _standing_debt(repo, capsys):
    """knotty committed BEFORE the baseline: over the ceiling, untouched by the
    diff verify will judge, and no marks file in sight."""
    add_knotty(repo)
    commit_all(repo, "knotty lands before the baseline")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def test_over_ceiling_functions_with_no_mark_are_counted_on_stderr(repo, capsys):
    code, out, err = run(["verify", "--reuse-artifacts", "--json"], _standing_debt(repo, capsys),
                         capsys)

    assert code == 0, err
    assert "warning: 1 function(s) over the ceiling carry no ratchet mark" in err, err
    assert "coverage loss included" in err and "ratchet seed" in err, err
    assert json.loads(out)["unmarked_over_target"] == 1


def test_a_mark_on_the_standing_debt_silences_the_count(repo, capsys):
    """The count reads the marks file the way the gate does, by ratchet key."""
    _standing_debt(repo, capsys)
    write_marks(repo, ("src/app.ts", "knotty ( n )", 16.0))

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], repo, capsys)

    assert code == 0, err
    assert "carry no ratchet mark" not in err, err
    assert json.loads(out)["unmarked_over_target"] == 0


def test_a_clean_tree_owes_no_standing_debt_line(baselined, capsys):
    """Nothing over the ceiling, no marks file: silence, and a zero in the JSON.
    A header-only marks file is the correct state of a repo with no debt."""
    code, out, err = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)

    assert (code, err) == (0, "")
    assert json.loads(out)["unmarked_over_target"] == 0
