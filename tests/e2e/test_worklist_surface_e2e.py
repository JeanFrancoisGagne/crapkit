"""The worklist surface end to end: scope cuts, session claims, and batching.

A four-function repo across two scopes, scored by a stub coverage.py artifact so
the lane is hermetic and every CRAP value in the assertions is hand-computable:
crap = ccn^2 * (1 - cov)^3 + ccn, and the plan file decides cov.
"""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

MAKE_COV = '''"""Fixture coverage generator: a coverage.py-format artifact from cov_plan.json.

Absent or empty plan means every function reads as untested, which is what an
uncovered fixture wants; a plan entry sets one function's branch counts.
"""
import json
import os

plan = {}
if os.path.isfile("cov_plan.json"):
    with open("cov_plan.json", encoding="utf-8") as fh:
        plan = json.load(fh)

files = {}
for path, fns in plan.items():
    entry = {}
    for name, spec in fns.items():
        entry[name] = {
            "start_line": spec["start"],
            "executed_lines": list(range(spec["start"], spec["end"] + 1)),
            "missing_lines": [],
            "summary": {"covered_lines": spec["end"] - spec["start"] + 1,
                        "num_statements": spec["end"] - spec["start"] + 1,
                        "num_branches": spec["branches"],
                        "covered_branches": spec["covered"]},
        }
    files[path] = {"functions": entry, "missing_lines": []}

with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": files}, fh, sort_keys=True)
'''

CONFIG = """[crapkit]
target = 6
worklist_floor = 5

[[scope]]
name = "core"
paths = ["core"]
languages = ["python"]

[[scope]]
name = "extra"
paths = ["extra"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["core", "extra"]
full_suite = false
"""

# (relative path, function, if-count). ccn is 1 + if-count, so the four
# functions score 72, 56, 42 and 30 against a target of 6, all uncovered.
FUNCTIONS = [
    ("core/alpha.py", "alpha", 7),
    ("core/beta.py", "beta", 6),
    ("extra/gamma.py", "gamma", 5),
    ("extra/delta.py", "delta", 4),
]


run_cli = cli_runner(timeout=180)


def _source(name: str, ifs: int) -> str:
    body = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, ifs + 1))
    return f"def {name}(a, b):\n    r = 0\n{body}    return r\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "extra").mkdir()
    for rel, name, ifs in FUNCTIONS:
        (tmp_path / rel).write_text(_source(name, ifs), encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    _commit(tmp_path, "init")
    return tmp_path


def scored(repo: Path) -> None:
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr


def worklist(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "worklist", "--json", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def next_item(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "next-item", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def test_worklist_without_a_scope_flag_holds_every_scope(repo: Path):
    scored(repo)
    paths = [e["path"] for e in worklist(repo)["active"]]
    assert sorted(paths) == ["core/alpha.py", "core/beta.py", "extra/delta.py", "extra/gamma.py"]


def test_worklist_scope_flag_cuts_to_the_named_scopes(repo: Path):
    scored(repo)
    assert [e["path"] for e in worklist(repo, "--scope", "core")["active"]] == \
        ["core/alpha.py", "core/beta.py"]
    both = worklist(repo, "--scope", "core", "--scope", "extra")["active"]
    assert sorted(e["path"] for e in both) == \
        ["core/alpha.py", "core/beta.py", "extra/delta.py", "extra/gamma.py"]


def test_worklist_scope_nobody_declared_is_empty_not_everything(repo: Path):
    scored(repo)
    assert worklist(repo, "--scope", "nope")["active"] == []


def test_next_item_scope_flag_picks_the_top_of_that_scope(repo: Path):
    scored(repo)
    assert next_item(repo)["item"]["path"] == "core/alpha.py", "crap 72 leads the repo"
    scoped = next_item(repo, "--scope", "extra")
    assert scoped["item"]["path"] == "extra/gamma.py", "crap 42 leads the extra scope"
    assert scoped["item"]["crap"] == 42.0


def test_next_item_scope_and_exclude_compose(repo: Path):
    scored(repo)
    item = next_item(repo, "--scope", "extra", "--exclude", "gamma")["item"]
    assert item["path"] == "extra/delta.py" and item["crap"] == 30.0


def test_next_item_below_floor_count_follows_the_scope_cut(repo: Path):
    # every function is ccn >= 5, so nothing sits below the floor in any scope;
    # a repo-wide count leaking into a scoped queue would say otherwise
    scored(repo)
    out = next_item(repo, "--scope", "nope")
    assert out["empty"] is True
    assert out["reasons"]["below_floor"] == 0


def test_a_store_nobody_claimed_in_answers_exactly_as_before(repo: Path):
    scored(repo)
    first = run_cli(repo, "next-item")
    second = run_cli(repo, "next-item")
    assert first.stdout == second.stdout
    assert "skipped_claimed" not in first.stdout, \
        "the claim key is opt-in: no claim taken, no key, byte-identical output"


def test_claiming_the_top_item_hands_the_next_session_the_one_below(repo: Path):
    scored(repo)
    assert next_item(repo, "--claim")["item"]["path"] == "core/alpha.py"
    after = next_item(repo)
    assert after["item"]["path"] == "core/beta.py"
    assert after["skipped_claimed"] == 1


def test_claims_walk_down_the_queue_and_an_all_claimed_queue_says_so(repo: Path):
    scored(repo)
    assert next_item(repo, "--claim")["item"]["path"] == "core/alpha.py"
    assert next_item(repo, "--claim")["item"]["path"] == "core/beta.py"
    pair = next_item(repo, "--claim", "--top", "2")["items"]
    assert [i["path"] for i in pair] == ["extra/gamma.py", "extra/delta.py"]
    out = next_item(repo)
    assert out["empty"] is True and out["skipped_claimed"] == 4


def cover_delta(repo: Path) -> None:
    """delta is ccn 5 spanning lines 1-11; 7 of its 10 branches covered puts it at
    5^2 * (1 - 0.7)^3 + 5 = 5.675, under the target of 6, so its remedy is ok."""
    (repo / "cov_plan.json").write_text(json.dumps(
        {"extra/delta.py": {"delta": {"start": 1, "end": 11, "branches": 10, "covered": 7}}}),
        encoding="utf-8")


def claims(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "claims", "--json", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def test_verify_releases_the_claim_on_a_function_back_under_target(repo: Path):
    scored(repo)
    held = next_item(repo, "--scope", "extra", "--exclude", "gamma", "--claim")["item"]
    assert held["path"] == "extra/delta.py"
    assert next_item(repo, "--scope", "extra", "--exclude", "gamma")["skipped_claimed"] == 1

    cover_delta(repo)
    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, res.stdout + res.stderr

    out = next_item(repo, "--scope", "extra", "--exclude", "gamma")
    assert "skipped_claimed" not in out, "the claim outlived its reason to exist"
    assert out["reasons"]["all_remaining_at_or_under_target"] == 1, \
        "delta is back in the ranking; it is just done"


def test_a_queue_whose_every_candidate_sits_at_target_is_empty(repo: Path):
    cover_delta(repo)
    scored(repo)

    out = next_item(repo, "--scope", "extra", "--exclude", "gamma")

    assert out["empty"] is True, "remedy ok on every candidate: an agent loop must stop"
    assert out["reasons"]["all_remaining_at_or_under_target"] == 1
    assert "item" not in out


def test_one_actionable_candidate_keeps_the_queue_answering(repo: Path):
    cover_delta(repo)
    scored(repo)

    out = next_item(repo, "--scope", "extra")

    assert out["empty"] is False
    assert out["item"]["path"] == "extra/gamma.py" and out["item"]["remedy"] == "add-tests"


def test_a_finished_queue_hands_nothing_out_so_claiming_it_hides_nothing(repo: Path):
    cover_delta(repo)
    scored(repo)

    assert next_item(repo, "--scope", "extra", "--exclude", "gamma", "--claim")["empty"] is True

    assert claims(repo)["claims"] == [], "there was nothing to hand out, so nothing was claimed"


def test_claims_lists_what_a_claiming_session_holds(repo: Path):
    scored(repo)
    next_item(repo, "--claim")

    (held,) = claims(repo)["claims"]

    assert held["path"] == "core/alpha.py" and held["long_name"] == "alpha( a , b )"
    assert held["commit"] and held["created_at"]


def test_releasing_a_claim_hands_the_item_back_to_the_queue(repo: Path):
    scored(repo)
    assert next_item(repo, "--claim")["item"]["path"] == "core/alpha.py"
    assert next_item(repo)["item"]["path"] == "core/beta.py"

    res = run_cli(repo, "claims", "release", "core/alpha.py", "alpha( a , b )")
    assert res.returncode == 0, res.stdout + res.stderr

    out = next_item(repo)
    assert out["item"]["path"] == "core/alpha.py"
    assert "skipped_claimed" not in out
    assert claims(repo)["claims"] == []


def test_release_takes_the_bare_name_as_well_as_the_printed_one(repo: Path):
    scored(repo)
    next_item(repo, "--claim")

    res = run_cli(repo, "claims", "release", "core/alpha.py", "alpha")

    assert res.returncode == 0, res.stdout + res.stderr
    assert claims(repo)["claims"] == []


def test_release_all_empties_the_claim_table(repo: Path):
    scored(repo)
    next_item(repo, "--claim", "--top", "3")
    assert len(claims(repo)["claims"]) == 3

    res = run_cli(repo, "claims", "release", "--all")

    assert res.returncode == 0, res.stdout + res.stderr
    assert claims(repo)["claims"] == []
    assert next_item(repo)["item"]["path"] == "core/alpha.py"


def test_releasing_a_claim_nobody_holds_says_what_is_held(repo: Path):
    scored(repo)
    next_item(repo, "--claim")

    res = run_cli(repo, "claims", "release", "core/beta.py", "beta")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "core/alpha.py" in res.stderr and "Traceback" not in res.stderr


def test_release_without_a_path_and_name_says_what_it_needs(repo: Path):
    scored(repo)

    res = run_cli(repo, "claims", "release")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "PATH NAME" in res.stderr and "--all" in res.stderr


def test_claims_without_json_prints_the_open_claims(repo: Path):
    scored(repo)
    next_item(repo, "--claim")

    res = run_cli(repo, "claims")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 open claim(s)" in res.stdout
    assert "core/alpha.py" in res.stdout and "alpha( a , b )" in res.stdout


def test_verify_keeps_the_claim_on_a_function_still_over_target(repo: Path):
    scored(repo)
    assert next_item(repo, "--claim")["item"]["path"] == "core/alpha.py"
    assert run_cli(repo, "verify", "--json").returncode == 0
    assert next_item(repo)["item"]["path"] == "core/beta.py", \
        "alpha still scores 72; nothing about a passing verify fixed it"


def backdate_claims(repo: Path, when: str) -> None:
    conn = sqlite3.connect(repo / ".crapkit" / "crap.sqlite")
    conn.execute("UPDATE attempts SET created_at = ?", (when,))
    conn.commit()
    conn.close()


def test_runs_prune_drops_claims_older_than_the_runs_it_keeps(repo: Path):
    scored(repo)
    assert next_item(repo, "--claim")["item"]["path"] == "core/alpha.py"
    backdate_claims(repo, "2000-01-01T00:00:00Z")

    res = run_cli(repo, "runs", "prune", "--keep", "1")
    assert res.returncode == 0, res.stdout + res.stderr

    out = next_item(repo)
    assert out["item"]["path"] == "core/alpha.py"
    assert "skipped_claimed" not in out


def couple(repo: Path) -> None:
    """Five commits touching core/alpha.py and core/beta.py together, which with
    the initial commit is support 6 at confidence 1.0 for that pair alone."""
    for i in range(5):
        for rel in ("core/alpha.py", "core/beta.py"):
            with (repo / rel).open("a", encoding="utf-8") as fh:
                fh.write(f"# touch {i}\n")
        _commit(repo, f"touch {i}")


def batches(repo: Path, count: str, *args: str) -> list[dict]:
    res = run_cli(repo, "worklist", "--batches", count, "--json", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)["batches"]


def test_batches_hold_co_changing_files_together_and_share_no_file(repo: Path):
    couple(repo)
    scored(repo)
    out = batches(repo, "2")
    assert [b["files"] for b in out] == [["core/alpha.py", "core/beta.py"],
                                         ["extra/delta.py", "extra/gamma.py"]]
    seen = [f for b in out for f in b["files"]]
    assert len(seen) == len(set(seen)), "a shared file is a worktree collision"


def test_a_batch_carries_whole_worklist_entries(repo: Path):
    couple(repo)
    scored(repo)
    first = batches(repo, "2")[0]["entries"][0]
    assert first["path"] == "core/alpha.py"
    assert first["function"] == "alpha( a , b )"
    assert first["ccn"] == 8
    assert {"scope", "start", "end", "commits", "authors", "risk"} <= set(first)


def test_the_same_store_and_history_split_the_same_way_every_time(repo: Path):
    couple(repo)
    scored(repo)
    first = run_cli(repo, "worklist", "--batches", "3", "--json")
    second = run_cli(repo, "worklist", "--batches", "3", "--json")
    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout == second.stdout


def test_more_batches_than_units_returns_one_per_unit(repo: Path):
    couple(repo)
    scored(repo)
    assert [b["files"] for b in batches(repo, "9")] == [
        ["core/alpha.py", "core/beta.py"], ["extra/delta.py"], ["extra/gamma.py"]]


def test_batching_composes_with_the_scope_cut(repo: Path):
    couple(repo)
    scored(repo)
    assert [b["files"] for b in batches(repo, "2", "--scope", "extra")] == [
        ["extra/delta.py"], ["extra/gamma.py"]]


def test_batches_are_added_to_the_worklist_payload_not_swapped_for_it(repo: Path):
    couple(repo)
    scored(repo)

    plain = worklist(repo)
    batched = worklist(repo, "--batches", "2")

    assert {k: v for k, v in batched.items() if k != "batches"} == plain, \
        "an agent reading active[] or stale off a batched call must not get a KeyError"
    assert [b["files"] for b in batched["batches"]] == [["core/alpha.py", "core/beta.py"],
                                                        ["extra/delta.py", "extra/gamma.py"]]


def test_the_plain_worklist_payload_carries_no_batches_key(repo: Path):
    scored(repo)

    assert "batches" not in worklist(repo)


def test_a_batch_count_below_one_is_a_config_error_not_a_traceback(repo: Path):
    scored(repo)
    res = run_cli(repo, "worklist", "--batches", "0", "--json")
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "batches" in res.stderr and "Traceback" not in res.stderr


def test_batches_without_json_print_one_line_each(repo: Path):
    couple(repo)
    scored(repo)
    res = run_cli(repo, "worklist", "--batches", "2")
    assert res.returncode == 0, res.stdout + res.stderr
    lines = [line for line in res.stdout.splitlines() if line.startswith("batch ")]
    assert len(lines) == 2 and "core/alpha.py" in lines[0]


def test_worklist_prefers_the_newest_scored_run_over_a_later_inventory(repo: Path):
    """Round-7 audit: worklist's own error told a stranger to run `inventory`,
    which then blinded the markers and split the two views onto different runs."""
    scored(repo)
    assert run_cli(repo, "inventory").returncode == 0

    wl = worklist(repo, "--json")
    ni = json.loads(run_cli(repo, "next-item").stdout)
    assert wl["run_id"] == ni["run_id"], "the two views must describe one run"
    assert all(e.get("flag") is not None for e in wl["active"]), "markers survive"


def test_worklist_with_no_runs_names_coverage_first(repo: Path):
    res = run_cli(repo, "worklist")
    assert res.returncode == 1
    out = res.stdout + res.stderr
    assert "crapkit coverage" in out
