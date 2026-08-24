"""End-to-end: the committed micro-fixture tree, real git, real lizard, real CLI.

Slow lane by design (real subprocesses); everything asserts through the CLI seam.
The fixture sources live in tests/fixtures/mini_repo and are copied into a tmp
git repo per test, so the tree is committed (reviewable, stable) while each test
still gets an isolated repository.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def run_cli(repo: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "crapkit", *args],
        cwd=repo, capture_output=True, text=True, timeout=120, env=env,
    )


def cache_entries(repo: Path) -> set[str]:
    """The content hashes the analysis cache currently holds."""
    raw = (repo / ".crapkit" / "cache.json").read_text(encoding="utf-8")
    return set(json.loads(raw)["entries"])


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def test_inventory_end_to_end_deterministic_and_cached(mini_repo: Path):
    first = run_cli(mini_repo, "inventory", "--export", "inv1.tsv", "--json")
    assert first.returncode == 0, first.stderr
    summary1 = json.loads(first.stdout)

    second = run_cli(mini_repo, "inventory", "--export", "inv2.tsv", "--json")
    assert second.returncode == 0, second.stderr
    summary2 = json.loads(second.stdout)

    inv1 = (mini_repo / "inv1.tsv").read_text(encoding="utf-8")
    inv2 = (mini_repo / "inv2.tsv").read_text(encoding="utf-8")
    assert inv1 == inv2, "same commit must produce byte-identical exports"

    assert summary1["functions"] == 4, inv1
    assert summary1["cache_hits"] == 0
    assert summary2["cache_hits"] == summary2["files"] > 0

    assert "node_modules" not in inv1
    assert "app.test.ts" not in inv1

    by_name = {}
    header, *rows = inv1.strip().splitlines()
    cols = header.split("\t")
    for row in rows:
        vals = dict(zip(cols, row.split("\t")))
        by_name[vals["long_name"].split("(")[0].strip()] = vals

    dispatch = by_name["dispatch"]
    assert int(dispatch["ccn_std"]) > int(dispatch["ccn_mod"]), "switch must separate the variants"
    assert int(dispatch["ccn"]) == int(dispatch["ccn_mod"])
    assert "inner" in by_name, "arrow function must get its own frame"
    guarded = by_name["guarded"]
    assert int(guarded["ccn_std"]) >= 4, "boolean op and comprehension must count in python"


def test_corrupt_cache_self_heals(mini_repo: Path):
    assert run_cli(mini_repo, "inventory", "--export", "inv1.tsv", "--json").returncode == 0
    (mini_repo / ".crapkit" / "cache.json").write_text("{ truncated garbage", encoding="utf-8")
    res = run_cli(mini_repo, "inventory", "--export", "inv2.tsv", "--json")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["cache_hits"] == 0, "corrupt cache must read as cold, not crash"
    inv1 = (mini_repo / "inv1.tsv").read_text(encoding="utf-8")
    inv2 = (mini_repo / "inv2.tsv").read_text(encoding="utf-8")
    assert inv1 == inv2


def test_missing_lizard_exits_with_distinct_tool_code(mini_repo: Path):
    shim = FIXTURES / "shims" / "lizard"
    res = run_cli(mini_repo, "inventory", env_extra={"PYTHONPATH": str(shim)})
    assert res.returncode == 5, (res.returncode, res.stderr)
    assert "lizard" in res.stderr.lower()


def test_missing_config_exits_with_distinct_code(tmp_path: Path):
    bare = tmp_path / "bare"
    bare.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=bare, check=True, capture_output=True)
    res = run_cli(bare, "inventory")
    assert res.returncode == 3
    assert "crapkit.toml" in res.stderr


def test_outside_a_git_repo_exits_with_distinct_code(tmp_path: Path):
    loose = tmp_path / "loose"
    loose.mkdir()
    shutil.copy(FIXTURES / "mini_repo" / "crapkit.toml", loose / "crapkit.toml")
    res = run_cli(loose, "inventory")
    assert res.returncode == 4
    assert "git" in res.stderr.lower()


def test_worklist_end_to_end_floor_and_ranking(mini_repo: Path):
    assert run_cli(mini_repo, "inventory", "--json").returncode == 0
    res = run_cli(mini_repo, "worklist", "--json")
    assert res.returncode == 0, res.stderr
    wl = json.loads(res.stdout)
    names = [e["function"] for e in wl["active"]]
    assert any("guarded" in n for n in names), wl
    assert not any("dispatch" in n for n in names), "min-ccn 2 sits under the floor of 5"
    top = wl["active"][0]
    assert top["ccn"] == 5 and top["commits"] >= 1
    assert "dormant_count" in wl


def test_worklist_without_snapshot_fails_loudly(mini_repo: Path):
    res = run_cli(mini_repo, "worklist", "--json")
    assert res.returncode != 0
    assert "crapkit coverage" in res.stderr


def test_coverage_single_lane_flags_other_scope_no_lane(mini_repo: Path):
    res = run_cli(mini_repo, "coverage", "--lane", "unit", "--json")
    assert res.returncode == 0, res.stderr
    s = json.loads(res.stdout)
    assert s["measured"] == 1 and s["untested"] == 2 and s["no_lane"] == 1, s
    assert s["over_target"] == 1, "guarded: cc5 at cov 0 in a no-lane scope scores 30"


def test_coverage_end_to_end_scores_and_flags(mini_repo: Path):
    res = run_cli(mini_repo, "coverage", "--export", "cov1.tsv", "--json")
    assert res.returncode == 0, res.stderr
    s = json.loads(res.stdout)
    assert s["measured"] == 2 and s["untested"] == 2 and s["no_lane"] == 0, s
    assert s["lanes"]["unit"]["exit_code"] == 0
    assert s["lanes"]["py"]["exit_code"] == 0, "real pytest under xdist -n 2 must combine and pass"

    text = (mini_repo / "cov1.tsv").read_text(encoding="utf-8")
    header, *rows = text.strip().splitlines()
    cols = header.split("\t")
    assert {"cov", "flag", "crap", "remedy"} <= set(cols)
    by_name = {dict(zip(cols, r.split("\t")))["long_name"].split("(")[0].strip(): dict(zip(cols, r.split("\t"))) for r in rows}
    d = by_name["dispatch"]
    assert d["flag"] == "measured" and float(d["cov"]) == 0.75
    assert abs(float(d["crap"]) - (4 * 0.25 ** 3 + 2)) < 1e-9
    assert by_name["plain"]["flag"] == "untested"
    g = by_name["guarded"]
    assert g["flag"] == "measured" and float(g["cov"]) > 0, "xdist fragments must combine into real branch data"
    assert float(g["crap"]) < 30

    again = run_cli(mini_repo, "coverage", "--export", "cov2.tsv", "--json", "--reuse-artifacts")
    assert again.returncode == 0, again.stderr
    assert (mini_repo / "cov1.tsv").read_bytes() == (mini_repo / "cov2.tsv").read_bytes()


HIGH_CC_FN = """
export function tangled(a: number, b: number): number {
  let r = 0;
  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }
  if (a > 10 && b > 10) { r += 3; }
  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }
  return r;
}
"""


def _stage(repo: Path, rel: str, content: str):
    p = repo / rel
    p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=repo, check=True, capture_output=True)


def test_hook_blocks_staged_function_over_target(mini_repo: Path):
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 6, (res.returncode, res.stdout, res.stderr)
    assert "tangled" in res.stdout + res.stderr


def test_hook_passes_staged_function_under_target(mini_repo: Path):
    _stage(mini_repo, "src/simple.ts", "export function tiny(a: number) { return a > 0 ? a : 0; }\n")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def test_hook_gates_each_staged_file_when_two_share_a_basename(mini_repo: Path):
    # Staged blobs are analyzed out of a temp tree. Keyed by basename these two
    # are one file, and whichever landed last would gate both — the clean twin
    # inheriting a ccn it does not have, or the tangled one escaping.
    (mini_repo / "src" / "alpha").mkdir()
    (mini_repo / "src" / "beta").mkdir()
    _stage(mini_repo, "src/alpha/index.ts", HIGH_CC_FN)
    _stage(mini_repo, "src/beta/index.ts", "export function tiny(a: number) { return a; }\n")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 6, (res.returncode, res.stdout, res.stderr)
    assert "src/alpha/index.ts" in res.stdout
    assert "src/beta/index.ts" not in res.stdout, "a clean file must not inherit its twin's analysis"


def test_hook_leaves_the_repo_analysis_cache_untouched(mini_repo: Path):
    assert run_cli(mini_repo, "inventory", "--json").returncode == 0, "seed the cache"
    cache = mini_repo / ".crapkit" / "cache.json"
    before = cache.read_bytes()
    _stage(mini_repo, "src/simple.ts", "export function tiny(a: number) { return a > 0 ? a : 0; }\n")
    assert run_cli(mini_repo, "hook-precommit").returncode == 0
    assert cache.read_bytes() == before, \
        "the hook sees one commit's files; a whole-repo cache round trip costs more than it saves"


def test_hook_ignores_unstaged_changes(mini_repo: Path):
    (mini_repo / "src" / "extra.ts").write_text(HIGH_CC_FN, encoding="utf-8")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def test_hook_checks_the_staged_blob_not_the_working_tree(mini_repo: Path):
    _stage(mini_repo, "src/simple.ts", "export function tiny(a: number) { return a; }\n")
    (mini_repo / "src" / "simple.ts").write_text(HIGH_CC_FN, encoding="utf-8")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0, "working-tree noise must not block a clean staged blob"


def test_one_failing_lane_does_not_abort_the_others(mini_repo: Path):
    extra = ('\n[[lane]]\nname = "bad"\ncommand = "python -c \\"print(1)\\""\n'
             'artifact = "never-written.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    cfg = mini_repo / "crapkit.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + extra, encoding="utf-8")
    res = run_cli(mini_repo, "coverage", "--json")
    assert res.returncode == 5, (res.returncode, res.stdout, res.stderr)
    s = json.loads(res.stdout)
    assert s["measured"] == 2, "good lanes must still score"
    assert "bad" in s["lane_failures"]
    assert "bad" not in s["lanes"]


def test_verify_gate_blocks_then_override_grants_with_full_audit(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0, "baseline"
    (mini_repo / "src" / "extra.ts").write_text(HIGH_CC_FN, encoding="utf-8")
    subprocess.run(["git", "add", "src/extra.ts"], cwd=mini_repo, check=True, capture_output=True)

    blocked = run_cli(mini_repo, "verify", "--json")
    assert blocked.returncode == 6, (blocked.returncode, blocked.stdout, blocked.stderr)
    verdict = json.loads(blocked.stdout)
    assert verdict["ok"] is False
    assert verdict["gate_violations"][0]["long_name"].startswith("tangled")

    granted = run_cli(mini_repo, "verify", "--json", "--override", "2am hotfix")
    assert granted.returncode == 0, (granted.returncode, granted.stdout, granted.stderr)
    g = json.loads(granted.stdout)
    assert g["ok"] is True and g["overridden"], g
    assert "OVERRIDE (2am hotfix)" in (mini_repo / "alert.log").read_text(encoding="utf-8")
    ratchet = (mini_repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")
    assert "tangled" in ratchet, "the debt is diff-visible in the committed ratchet"


def test_verify_catches_new_test_failures_vs_baseline(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0, "baseline"
    (mini_repo / "pylib" / "test_new.py").write_text(
        "def test_fresh_regression():\n    assert 1 == 2\n", encoding="utf-8")
    res = run_cli(mini_repo, "verify", "--json")
    assert res.returncode == 8, (res.returncode, res.stdout, res.stderr)
    verdict = json.loads(res.stdout)
    assert any("test_fresh_regression" in f for f in verdict["new_failures"])


def test_verify_clean_change_passes_and_tightens_nothing(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0, "baseline"
    (mini_repo / "src" / "simple.ts").write_text(
        "export function tiny(a: number) { return a > 0 ? a : 0; }\n", encoding="utf-8")
    res = run_cli(mini_repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert json.loads(res.stdout)["ok"] is True


def test_hook_override_env_grants_only_with_full_audit(mini_repo: Path):
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    blocked = run_cli(mini_repo, "hook-precommit")
    assert blocked.returncode == 6

    granted = run_cli(mini_repo, "hook-precommit", env_extra={"CRAPKIT_OVERRIDE_REASON": "prod down"})
    assert granted.returncode == 0, (granted.returncode, granted.stdout, granted.stderr)
    assert "OVERRIDE (prod down)" in (mini_repo / "alert.log").read_text(encoding="utf-8")
    assert "tangled" in (mini_repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


def test_hook_override_without_alert_command_still_blocks(mini_repo: Path):
    cfg = mini_repo / "crapkit.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'alert_command = "python append_alert.py"', ""), encoding="utf-8")
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    res = run_cli(mini_repo, "hook-precommit", env_extra={"CRAPKIT_OVERRIDE_REASON": "prod down"})
    assert res.returncode != 0, "no alert channel, no override — the gate holds"


def tighten(repo: Path, target: int) -> None:
    """Drop the ceiling under the fixture's own scores so the queue has work in it.

    At target 6 every scored function in mini_repo sits at or under its ceiling and
    next-item answers empty — the queue working, not the queue broken.
    """
    cfg = repo / "crapkit.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("target = 6", f"target = {target}"),
                   encoding="utf-8")


def test_next_item_stops_when_every_candidate_sits_at_its_ceiling(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0

    out = json.loads(run_cli(mini_repo, "next-item").stdout)

    assert out["empty"] is True and "item" not in out, "an agent looping here must be able to stop"
    assert out["reasons"]["all_remaining_at_or_under_target"] == 1


def test_next_item_hands_the_top_entry_with_scores_as_json(mini_repo: Path):
    tighten(mini_repo, 4)
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    res = run_cli(mini_repo, "next-item", "--top", "9")
    assert res.returncode == 0, res.stderr
    item = json.loads(res.stdout)
    assert item["empty"] is False
    e = item["items"][0]
    assert {"path", "function", "start", "end", "ccn", "crap", "cov", "flag", "remedy", "commits",
            "nesting"} <= set(e)
    # a ceiling of 4 puts every ccn-2 function at 0% coverage over target too
    # (CRAP 6), so guarded is one queue entry among several rather than the only one
    guarded = next(i for i in item["items"] if "guarded" in i["function"])
    # remediation estimates: how much work this item is, so a session can budget
    assert guarded["est_splits"] == 2, "ccn 5 against a ceiling of 4 takes two pieces"
    assert guarded["est_uncovered_paths"] == round((1 - guarded["cov"]) * guarded["ccn"])


def test_coverage_summary_carries_a_grade(mini_repo: Path):
    res = run_cli(mini_repo, "coverage", "--json")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["grade"] in ("A+", "A", "B", "C", "D", "F")


def test_next_item_on_a_clean_queue_says_empty(mini_repo: Path):
    cfg = mini_repo / "crapkit.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("target = 6", "target = 6\nworklist_floor = 99"),
                   encoding="utf-8")
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    res = run_cli(mini_repo, "next-item")
    assert res.returncode == 0
    assert json.loads(res.stdout)["empty"] is True


def test_rescore_overlays_fresh_complexity_on_stale_coverage(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    runs_before = json.loads(run_cli(mini_repo, "worklist", "--json").stdout)["run_id"]
    (mini_repo / "pylib" / "mod.py").write_text(
        "def guarded(a, b):\n    return [x for x in range(a) if x % 2] if (a and b) else []\n",
        encoding="utf-8")
    res = run_cli(mini_repo, "rescore", "--json", "pylib/mod.py")
    assert res.returncode == 0, res.stderr
    overlay = json.loads(res.stdout)
    (fn,) = overlay["functions"]
    assert fn["path"] == "pylib/mod.py"
    assert fn["stale_coverage"] is True
    runs_after = json.loads(run_cli(mini_repo, "worklist", "--json").stdout)["run_id"]
    assert runs_after == runs_before, "rescore must not write baseline runs"


def test_rescore_merges_the_shared_cache_instead_of_truncating_it(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    before = cache_entries(mini_repo)
    assert len(before) > 1, "the fixture must cache more files than the one being rescored"

    res = run_cli(mini_repo, "rescore", "--json", "pylib/mod.py")
    assert res.returncode == 0, res.stderr
    assert before <= cache_entries(mini_repo), "rescoring one file must not evict the others"

    warm = run_cli(mini_repo, "inventory", "--json")
    assert warm.returncode == 0, warm.stderr
    summary = json.loads(warm.stdout)
    assert summary["cache_hits"] == summary["files"], "the run after a rescore stays warm"


def test_inventory_still_evicts_entries_for_content_that_left_the_corpus(mini_repo: Path):
    assert run_cli(mini_repo, "inventory", "--json").returncode == 0
    stale = cache_entries(mini_repo)
    (mini_repo / "pylib" / "mod.py").write_text("def flat(a):\n    return a\n", encoding="utf-8")
    assert run_cli(mini_repo, "inventory", "--json").returncode == 0
    assert not stale <= cache_entries(mini_repo), "a corpus-wide run rebuilds, it does not merge"


def test_test_scoped_runs_the_isolated_lane_for_the_files_scope(mini_repo: Path):
    cfg = mini_repo / "crapkit.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") +
                   '\n[crapkit.scoped_tests]\npy = "python -m pytest {files} -n 0 -p no:cacheprovider -o addopts= -q"\n',
                   encoding="utf-8")
    ok = run_cli(mini_repo, "test-scoped", "pylib/test_mod.py")
    assert ok.returncode == 0, (ok.returncode, ok.stdout, ok.stderr)

    (mini_repo / "pylib" / "test_broken.py").write_text("def test_no():\n    assert False\n", encoding="utf-8")
    bad = run_cli(mini_repo, "test-scoped", "pylib/test_broken.py")
    assert bad.returncode != 0


def test_test_scoped_without_template_for_scope_is_loud(mini_repo: Path):
    res = run_cli(mini_repo, "test-scoped", "pylib/test_mod.py")
    assert res.returncode == 3
    assert "scoped_tests" in res.stderr


def test_digest_silent_when_unchanged_and_speaks_on_regression(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    assert run_cli(mini_repo, "coverage", "--json", "--reuse-artifacts").returncode == 0
    quiet = run_cli(mini_repo, "digest")
    assert quiet.returncode == 0 and quiet.stdout.strip() == "", quiet.stdout

    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    assert run_cli(mini_repo, "coverage", "--json", "--reuse-artifacts").returncode == 0
    loud = run_cli(mini_repo, "digest")
    assert loud.returncode == 0 and "new over target" in loud.stdout, loud.stdout
    assert "tangled" in loud.stdout

    trend = run_cli(mini_repo, "trend", "--json")
    assert trend.returncode == 0
    runs = json.loads(trend.stdout)["runs"]
    assert len(runs) == 3
    assert runs[-1]["over_target"] == runs[0]["over_target"] + 1


def test_rerunning_verify_on_a_still_broken_tree_stays_failed(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    (mini_repo / "pylib" / "test_new.py").write_text(
        "def test_fresh_regression():\n    assert 1 == 2\n", encoding="utf-8")
    first = run_cli(mini_repo, "verify", "--json")
    assert first.returncode == 8
    second = run_cli(mini_repo, "verify", "--json")
    assert second.returncode == 8, "a failed verify must never become the next baseline"


def test_hook_passes_what_verify_then_fails_the_designed_split(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    cc5_uncovered = (
        "export function fiveDeep(a: number): number {\n"
        "  if (a > 1) { return 1; } if (a > 2) { return 2; }\n"
        "  if (a > 3) { return 3; } if (a > 4) { return 4; }\n"
        "  return 0;\n}\n")
    _stage(mini_repo, "src/five.ts", cc5_uncovered)
    hook = run_cli(mini_repo, "hook-precommit")
    assert hook.returncode == 0, "cc 5 clears the cc-only necessary condition"
    ver = run_cli(mini_repo, "verify", "--json")
    assert ver.returncode == 6, "cov 0 at cc 5 is crap 30: the sufficient condition fails"
    v = json.loads(ver.stdout)
    assert any("fiveDeep" in g["long_name"] for g in v["gate_violations"])


def test_verify_refuses_a_rewritten_baseline_commit(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--amend", "-m", "rewritten"], cwd=mini_repo, check=True, capture_output=True)
    res = run_cli(mini_repo, "verify", "--json")
    assert res.returncode not in (0, 6, 7, 8), (res.returncode, res.stdout)
    assert "ancestor" in res.stderr


def test_renamed_file_functions_still_face_the_gate(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add tangled"],
                   cwd=mini_repo, check=True, capture_output=True)
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0, "re-baseline with tangled committed"
    subprocess.run(["git", "mv", "src/extra.ts", "src/renamed.ts"], cwd=mini_repo, check=True, capture_output=True)
    res = run_cli(mini_repo, "verify", "--json")
    assert res.returncode == 6, "a pure rename is still a touch; the gate follows the function"
    v = json.loads(res.stdout)
    assert any(g["path"] == "src/renamed.ts" for g in v["gate_violations"])


def test_passing_verify_advances_the_trend_through_the_cli(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0, "baseline"
    (mini_repo / "src" / "simple.ts").write_text(
        "export function tiny(a: number) { return a > 0 ? a : 0; }\n", encoding="utf-8")
    ver = run_cli(mini_repo, "verify", "--json")
    assert ver.returncode == 0, (ver.stdout, ver.stderr)
    trend = run_cli(mini_repo, "trend", "--json")
    runs = json.loads(trend.stdout)["runs"]
    assert len(runs) == 2, "a PASSING verify must join the trend, not vanish (verdict never stamped)"
    assert runs[-1]["run_id"] == json.loads(ver.stdout)["run_id"]


def test_digest_refuses_to_compare_mismatched_lane_sets(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    assert run_cli(mini_repo, "coverage", "--lane", "unit", "--json").returncode == 0
    d = run_cli(mini_repo, "digest")
    assert d.returncode == 0
    assert "nothing comparable" in d.stdout, (
        "full-vs-subset comparison manufactures phantom regressions", d.stdout)


def test_hook_override_stages_the_ratchet_debt_into_the_commit(mini_repo: Path):
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    res = run_cli(mini_repo, "hook-precommit",
                  env_extra={"CRAPKIT_OVERRIDE_REASON": "prod down"})
    assert res.returncode == 0, (res.stdout, res.stderr)
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=mini_repo,
                            capture_output=True, text=True).stdout.split()
    assert "crapkit-ratchet.tsv" in staged, \
        "the debt must land IN the commit, not dangle in the working tree"


def test_lane_subset_run_never_becomes_the_verify_baseline(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0, "full baseline"
    sub = run_cli(mini_repo, "coverage", "--lane", "unit", "--json")
    assert sub.returncode == 0
    ver = run_cli(mini_repo, "verify", "--json")
    assert ver.returncode == 0, (ver.stdout, ver.stderr)
    out = json.loads(ver.stdout)
    assert out["baseline_run"] == 1, \
        "the --lane subset run must be skipped: its missing lanes would turn every pre-existing failure into a phantom NEW one"
    assert out["new_failures"] == []


def test_invalid_toml_exits_3_not_1(mini_repo: Path):
    (mini_repo / "crapkit.toml").write_text("[[scope\nname = broken", encoding="utf-8")
    res = run_cli(mini_repo, "inventory")
    assert res.returncode == 3, (res.returncode, res.stderr)
    assert "Traceback" not in res.stderr, "config errors are user errors, not crashes"


def test_rescore_default_is_a_human_table_sorted_by_ccn(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    res = run_cli(mini_repo, "rescore", "src/app.ts")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert any("ccn" in line and "crap" in line for line in lines[:3]), \
        "the refactor loop needs a scannable table, not a JSON blob"
    assert "plain" in res.stdout, "the file's functions appear by name"
    assert "{" not in res.stdout.split("stale", 1)[0][:1], "JSON only behind --json"


def test_hook_notes_a_stale_staged_blob(mini_repo: Path):
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    (mini_repo / "src" / "extra.ts").write_text(
        "export function tiny(a: number) { return a; }\n", encoding="utf-8")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 6, "the STAGED blob is what commits; it still gates"
    assert "differs from the working tree" in res.stdout, \
        "after an unstaged fix the developer needs to hear 're-stage', not a stale number"


def test_per_scope_target_admits_what_the_default_would_block(mini_repo: Path):
    # Raise the src scope's ceiling to 20: the staged ccn-13 function that the
    # repo default of 6 blocks must now commit clean, while py stays at 6.
    toml = (mini_repo / "crapkit.toml").read_text(encoding="utf-8")
    toml = toml.replace('name = "src"', 'name = "src"\ntarget = 20', 1)
    (mini_repo / "crapkit.toml").write_text(toml, encoding="utf-8")
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def test_runs_command_lists_history_without_sql(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    res = run_cli(mini_repo, "runs", "--json")
    assert res.returncode == 0, res.stderr
    runs = json.loads(res.stdout)["runs"]
    assert runs[-1]["kind"] == "coverage" and "commit" in runs[-1]
    human = run_cli(mini_repo, "runs")
    assert "coverage" in human.stdout


def test_overrides_command_reads_the_audit_trail(mini_repo: Path):
    _stage(mini_repo, "src/extra.ts", HIGH_CC_FN)
    assert run_cli(mini_repo, "hook-precommit",
                   env_extra={"CRAPKIT_OVERRIDE_REASON": "audit demo"}).returncode == 0
    res = run_cli(mini_repo, "overrides", "--json")
    assert res.returncode == 0, res.stderr
    trail = json.loads(res.stdout)["overrides"]
    assert trail and trail[0]["reason"] == "audit demo" and "commit" in trail[0]


def test_explain_shows_a_function_trajectory(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    assert run_cli(mini_repo, "coverage", "--json", "--reuse-artifacts").returncode == 0
    res = run_cli(mini_repo, "explain", "src/app.ts", "plain")
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("run ") >= 2, "one line per run the function appears in"
    assert "ccn" in res.stdout


def test_next_item_top_n_and_exclude(mini_repo: Path):
    tighten(mini_repo, 4)
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    top = run_cli(mini_repo, "next-item", "--top", "2")
    assert top.returncode == 0, top.stderr
    items = json.loads(top.stdout)["items"]
    assert len(items) >= 1
    first = items[0]["function"]
    excl = run_cli(mini_repo, "next-item", "--exclude", first.split("(")[0].strip())
    assert json.loads(excl.stdout).get("empty") is True or \
        json.loads(excl.stdout)["item"]["function"] != first


def test_next_item_empty_says_why(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    res = run_cli(mini_repo, "next-item", "--exclude", "tangled", "--exclude", "guarded")
    out = json.loads(res.stdout)
    if out.get("empty"):
        assert "reasons" in out, "an empty queue must say what was filtered and why"


def test_duplication_runs_against_the_snapshot(mini_repo: Path):
    assert run_cli(mini_repo, "inventory").returncode == 0
    res = run_cli(mini_repo, "duplication", "--json")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert "pairs" in out and isinstance(out["pairs"], list)


def test_explain_history_lists_touching_commits(mini_repo: Path):
    assert run_cli(mini_repo, "coverage", "--json").returncode == 0
    res = run_cli(mini_repo, "explain", "pylib/mod.py", "guarded", "--history")
    assert res.returncode == 0, res.stderr
    assert "init" in res.stdout, "the fixture's one commit must appear as function history"


def test_hook_names_staged_source_files_no_scope_claims(mini_repo: Path):
    """Round-4 audit: a ccn-9 file at the repo root passed the hook in silence.
    The gate cannot judge it, but the hole must be visible when it opens."""
    _stage(mini_repo, "loose.ts", HIGH_CC_FN)
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert "loose.ts" in res.stderr
    assert "no scope" in res.stderr


def test_hook_stays_silent_for_unscoped_files_of_no_scored_language(mini_repo: Path):
    _stage(mini_repo, "notes.md", "# nothing to gate\n")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0
    assert "notes.md" not in res.stderr


def test_hook_never_warns_about_deliberately_excluded_files(mini_repo: Path):
    """Test files and exclude-glob matches are outside scopes ON PURPOSE; naming
    them as ungated holes turned every crapkit commit into a false alarm."""
    (mini_repo / "tests").mkdir(exist_ok=True)
    _stage(mini_repo, "tests/test_extra.py", "def test_x():\n    assert True\n")
    res = run_cli(mini_repo, "hook-precommit")
    assert res.returncode == 0
    assert "test_extra" not in res.stderr
