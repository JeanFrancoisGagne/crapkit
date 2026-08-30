"""Verify plumbing and the MCP dispatcher, driven through the CLI only.

Every repo is built inline in tmp_path with real git and real lane subprocesses;
a hermetic istanbul generator stands in for a coverage tool, so a lane is fast
and its artifact is exactly what the test asked for. Lines carrying the DEAD
marker are the ones the generator reports as never executed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PY = sys.executable
GEN = "gen_cov.py"

GEN_COV = """\
# Fixture coverage generator: istanbul coverage-final.json for the named sources.
# argv: <artifact-path> <source> [<source> ...]
import json
import os
import sys

artifact, sources = sys.argv[1], sys.argv[2:]
out = {}
for rel in sources:
    with open(rel, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    key = os.path.join(os.getcwd(), rel.replace("/", os.sep))
    starts = [i + 1 for i, ln in enumerate(lines) if ln.startswith("def ")]
    fn_map, f_hits = {}, {}
    for n, start in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(lines)
        fn_map[str(n)] = {"name": lines[start - 1][4:].split("(")[0],
                          "decl": {"start": {"line": start}},
                          "loc": {"start": {"line": start}, "end": {"line": end}}}
        f_hits[str(n)] = 1
    stmt_map, s_hits = {}, {}
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.startswith("def "):
            continue
        stmt_map[str(i)] = {"start": {"line": i}, "end": {"line": i}}
        s_hits[str(i)] = 0 if "DEAD" in ln else 1
    out[key] = {"path": key, "fnMap": fn_map, "f": f_hits, "branchMap": {}, "b": {},
                "statementMap": stmt_map, "s": s_hits}
os.makedirs(os.path.dirname(artifact) or ".", exist_ok=True)
with open(artifact, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""

WIPE_LANE = """\
# Second lane's runner: cleans the shared cov/ dir the way a real coverage tool
# does, then writes its own artifact. The first lane's artifact does not survive.
import shutil
import subprocess
import sys

shutil.rmtree("cov", ignore_errors=True)
sys.exit(subprocess.run([sys.executable, "gen_cov.py", "cov/b.json", "srcb/mod_b.py"]).returncode)
"""

BAD_LANE = "# a runner that never writes its artifact\nraise SystemExit(3)\n"


def run_cli(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CRAPKIT_OVERRIDE_REASON", None)  # an inherited grant would rewrite the verdict
    return subprocess.run([PY, "-m", "crapkit", *args], cwd=repo, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, env=env)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def lane_command(*parts: str) -> str:
    """A TOML literal string running THIS interpreter, never whatever `python` resolves to."""
    return "'\"" + PY + "\" " + " ".join(parts) + "'"


def header(floor: int = 1) -> str:
    return f"[crapkit]\ntarget = 6\nworklist_floor = {floor}\n\n"


def scope_block(name: str, path: str) -> str:
    return f'[[scope]]\nname = "{name}"\npaths = ["{path}"]\nlanguages = ["python"]\n\n'


def lane_block(name: str, command: str, artifact: str, scopes: list) -> str:
    return (f'[[lane]]\nname = "{name}"\ncommand = {command}\nartifact = "{artifact}"\n'
            f'parser = "istanbul"\nscopes = {json.dumps(scopes)}\n\n')


def module_src(name: str) -> str:
    """A ccn-1 function, three lines long."""
    return f"def {name}():\n    value = 1\n    return value\n"


def branchy_src(name: str) -> str:
    """A ccn-2 function: one decision, enough to clear a worklist floor of 2."""
    return f"def {name}(n):\n    if n > 1:\n        n = n + 1\n    return n\n"


def new_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    write(repo, ".gitignore", ".crapkit/\ncov/\n__pycache__/\n")
    write(repo, GEN, GEN_COV)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")  # a CRLF filter would fake a whole-file diff
    return repo


@pytest.fixture()
def pair_repo(tmp_path: Path) -> Path:
    """Two healthy lanes, so a --lane subset run produces a partial run."""
    repo = new_repo(tmp_path, "pair")
    write(repo, "srcone/mod_one.py", module_src("alpha"))
    write(repo, "srctwo/mod_two.py", module_src("bravo"))
    write(repo, "crapkit.toml", header()
          + scope_block("one", "srcone") + scope_block("two", "srctwo")
          + lane_block("one", lane_command(GEN, "cov/one.json", "srcone/mod_one.py"),
                       "cov/one.json", ["one"])
          + lane_block("two", lane_command(GEN, "cov/two.json", "srctwo/mod_two.py"),
                       "cov/two.json", ["two"]))
    commit_all(repo, "init")
    return repo


@pytest.fixture()
def solo_repo(tmp_path: Path) -> Path:
    repo = new_repo(tmp_path, "solo")
    write(repo, "src/mod.py",
          "def bravo(n):\n    if n > 3:\n        n = n + 1\n"
          "    if n > 9:\n        n = n - 1\n    return n\n")
    write(repo, "crapkit.toml", header() + scope_block("src", "src")
          + lane_block("unit", lane_command(GEN, "cov/unit.json", "src/mod.py"),
                       "cov/unit.json", ["src"]))
    commit_all(repo, "init")
    return repo


def test_lane_flag_naming_a_missing_lane_exits_3_with_the_name(pair_repo: Path):
    res = run_cli(pair_repo, "coverage", "--lane", "ghost")
    assert res.returncode == 3, res.stdout + res.stderr
    assert "no lane named 'ghost'" in res.stderr


def test_a_config_declaring_no_lanes_exits_3(tmp_path: Path):
    repo = new_repo(tmp_path, "nolanes")
    write(repo, "src/mod.py", module_src("alpha"))
    write(repo, "crapkit.toml", header() + scope_block("src", "src"))
    commit_all(repo, "init")

    res = run_cli(repo, "coverage")
    assert res.returncode == 3, res.stdout + res.stderr
    assert "no [[lane]] to run" in res.stderr


def test_next_item_without_a_database_then_without_a_scored_run(solo_repo: Path):
    missing_db = run_cli(solo_repo, "next-item")
    assert missing_db.returncode == 1, missing_db.stdout + missing_db.stderr
    assert "no snapshot in" in missing_db.stderr
    assert "crapkit coverage" in missing_db.stderr

    assert run_cli(solo_repo, "inventory").returncode == 0
    unscored = run_cli(solo_repo, "next-item")
    assert unscored.returncode == 1, unscored.stdout + unscored.stderr
    assert "no scored run in" in unscored.stderr, "an inventory-only run is not a scored run"


def untest_bravo(repo: Path) -> None:
    """Leave four of bravo's five statements unrun: crap goes over the ceiling
    while ccn does not, which is the add-tests item. Fully covered — as the
    fixture ships — bravo sits AT its ceiling and the queue answers empty."""
    write(repo, "src/mod.py",
          "def bravo(n):\n    if n > 3:  # DEAD\n        n = n + 1  # DEAD\n"
          "    if n > 9:  # DEAD\n        n = n - 1  # DEAD\n    return n\n")
    commit_all(repo, "leave bravo's branches untested")


def test_a_fully_covered_queue_at_its_ceiling_answers_empty(solo_repo: Path):
    assert run_cli(solo_repo, "coverage", "--json").returncode == 0

    payload = json.loads(run_cli(solo_repo, "next-item").stdout)

    assert payload["empty"] is True, "bravo is ccn 3 at 100%: there is nothing to hand out"
    assert payload["reasons"]["all_remaining_at_or_under_target"] == 1


def test_next_item_top_1_returns_one_item_not_a_list(solo_repo: Path):
    untest_bravo(solo_repo)
    assert run_cli(solo_repo, "coverage", "--json").returncode == 0

    res = run_cli(solo_repo, "next-item", "--top", "1")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["empty"] is False
    assert "items" not in payload, "--top 1 answers with one item, never a list"
    item = payload["item"]
    assert item["path"] == "src/mod.py"
    assert "bravo" in item["function"]
    assert item["ccn"] == 3
    assert item["target"] == 6
    assert item["remedy"] == "add-tests"
    assert item["est_splits"] == 0, "ccn 3 sits under the ceiling, so nothing to split"
    assert item["est_uncovered_paths"] == round((1 - item["cov"]) * 3)

    batch = json.loads(run_cli(solo_repo, "next-item", "--top", "2").stdout)
    assert "item" not in batch
    assert [i["function"] for i in batch["items"]] == [item["function"]]


def test_verify_baseline_refuses_a_missing_id_and_a_partial_run(pair_repo: Path):
    assert run_cli(pair_repo, "coverage", "--json").returncode == 0
    subset = run_cli(pair_repo, "coverage", "--lane", "one", "--json")
    assert subset.returncode == 0, subset.stdout + subset.stderr
    kinds = {r["id"]: r["kind"]
             for r in json.loads(run_cli(pair_repo, "runs", "--json").stdout)["runs"]}
    assert kinds == {1: "coverage", 2: "partial"}

    ghost = run_cli(pair_repo, "verify", "--baseline", "99")
    assert ghost.returncode == 1, ghost.stdout + ghost.stderr
    assert "no run 99" in ghost.stderr and "trusted runs: 1" in ghost.stderr, ghost.stderr

    partial = run_cli(pair_repo, "verify", "--baseline", "2")
    assert partial.returncode == 1, partial.stdout + partial.stderr
    assert "run 2 is a partial run" in partial.stderr, partial.stderr
    assert "--baseline=1" in partial.stderr, partial.stderr

    named = run_cli(pair_repo, "verify", "--baseline", "1", "--json")
    assert named.returncode == 0, named.stdout + named.stderr
    assert json.loads(named.stdout)["baseline_run"] == 1


def test_verify_refuses_a_baseline_commit_amend_left_behind(pair_repo: Path):
    assert run_cli(pair_repo, "coverage", "--json").returncode == 0
    git(pair_repo, "-c", "user.email=t@t", "-c", "user.name=t",
        "commit", "--amend", "-q", "-m", "reworded")

    res = run_cli(pair_repo, "verify")
    assert res.returncode == 4, res.stdout + res.stderr
    assert "is not an ancestor of HEAD" in res.stderr


@pytest.fixture()
def staged_repo(tmp_path: Path) -> Path:
    """One function per way a worklist entry can be filtered out: below the
    floor, in a scope no lane covers, staged but never committed (so git log
    has never heard of it), and plain excluded by name."""
    repo = new_repo(tmp_path, "staged")
    write(repo, "src/kept.py", branchy_src("alpha"))
    write(repo, "src/small.py", module_src("tiny"))
    write(repo, "extra/orphan.py", branchy_src("gamma"))
    write(repo, "crapkit.toml", header(floor=2)
          + scope_block("src", "src") + scope_block("extra", "extra")
          + lane_block("unit", lane_command(GEN, "cov/unit.json", "src/kept.py",
                                            "src/small.py", "src/fresh.py"),
                       "cov/unit.json", ["src"]))
    commit_all(repo, "init")
    write(repo, "src/fresh.py", branchy_src("beta"))
    git(repo, "add", "src/fresh.py")
    return repo


def test_an_empty_queue_names_every_filter_that_emptied_it(staged_repo: Path):
    assert run_cli(staged_repo, "coverage", "--json").returncode == 0

    res = run_cli(staged_repo, "next-item", "--exclude", "kept")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["empty"] is True
    assert "item" not in payload and "items" not in payload
    assert payload["skipped_no_lane"] == 1

    reasons = payload["reasons"]
    assert reasons["excluded_by_flag"] == 1, reasons
    assert reasons["no_churn_in_window"] == 1, reasons
    assert reasons["below_floor"] == 1, reasons
    assert reasons["no_lane"] == 1, reasons
    assert reasons["churn_window_months"] == 12


@pytest.fixture()
def flaky_lane_repo(tmp_path: Path) -> Path:
    """Lane 'bad' exits nonzero and writes no artifact; lane 'good' is healthy."""
    repo = new_repo(tmp_path, "flaky")
    write(repo, "srcone/mod_one.py", module_src("alpha"))
    write(repo, "srctwo/mod_two.py", module_src("bravo"))
    write(repo, "bad_lane.py", BAD_LANE)
    write(repo, "crapkit.toml", header()
          + scope_block("one", "srcone") + scope_block("two", "srctwo")
          + lane_block("good", lane_command(GEN, "cov/one.json", "srcone/mod_one.py"),
                       "cov/one.json", ["one"])
          + lane_block("bad", lane_command("bad_lane.py"), "cov/two.json", ["two"]))
    commit_all(repo, "init")
    return repo


def test_one_failed_lane_still_scores_and_records_a_partial_run(flaky_lane_repo: Path):
    res = run_cli(flaky_lane_repo, "coverage", "--json")
    assert res.returncode == 5, res.stdout + res.stderr
    assert "lane 'bad' FAILED" in res.stderr
    assert "produced no artifact" in res.stderr

    summary = json.loads(res.stdout)
    assert set(summary["lane_failures"]) == {"bad"}
    assert "good" in summary["lanes"] and "bad" not in summary["lanes"]
    assert summary["measured"] == 1
    assert summary["no_lane"] == 1, "the failed lane's scope falls back to no-lane, not untested"

    runs = json.loads(run_cli(flaky_lane_repo, "runs", "--json").stdout)["runs"]
    assert [r["kind"] for r in runs] == ["partial"], "a run with a failed lane never serves as baseline"


def test_the_last_lane_failing_leaves_nothing_to_score(tmp_path: Path):
    repo = new_repo(tmp_path, "alldead")
    write(repo, "src/mod.py", module_src("alpha"))
    write(repo, "bad_lane.py", BAD_LANE)
    write(repo, "crapkit.toml", header() + scope_block("src", "src")
          + lane_block("bad", lane_command("bad_lane.py"), "cov/unit.json", ["src"]))
    commit_all(repo, "init")

    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 5, res.stdout + res.stderr
    assert "every lane failed" in res.stderr
    assert res.stdout.strip() == "", "no lane survived, so no run summary is printed"


@pytest.fixture()
def wiping_repo(tmp_path: Path) -> Path:
    """Lane 'b' wipes the shared cov/ dir before writing its own artifact, so
    lane 'a's artifact is gone by the time verify reads line-level truth."""
    repo = new_repo(tmp_path, "wiping")
    write(repo, "srca/mod_a.py", module_src("alpha"))
    write(repo, "srcb/mod_b.py", module_src("bravo"))
    write(repo, "wipe_lane.py", WIPE_LANE)
    write(repo, "crapkit.toml", header()
          + scope_block("a", "srca") + scope_block("b", "srcb")
          + lane_block("a", lane_command(GEN, "cov/a.json", "srca/mod_a.py"), "cov/a.json", ["a"])
          + lane_block("b", lane_command("wipe_lane.py"), "cov/b.json", ["b"]))
    commit_all(repo, "init")
    return repo


def test_a_lane_artifact_missing_on_disk_drops_out_of_diff_coverage(wiping_repo: Path):
    assert run_cli(wiping_repo, "coverage", "--json").returncode == 0
    for rel in ("srca/mod_a.py", "srcb/mod_b.py"):
        with open(wiping_repo / rel, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("MARKER = 1  # DEAD\n")
    commit_all(wiping_repo, "add an uncovered line to both scopes")

    res = run_cli(wiping_repo, "verify", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["diff_uncovered"] == [{"path": "srcb/mod_b.py", "line": 4}]
    assert "warning: 1 changed line(s) have no coverage" in res.stderr
    assert "uncovered srcb/mod_b.py:4" in res.stderr
    assert "srca/mod_a.py" not in res.stderr, "the wiped lane is skipped, never guessed at"


def test_mcp_tools_call_with_an_unknown_tool_name_is_an_error_result(solo_repo: Path):
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}),
        "",
        "{ not json at all",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "no_such_tool", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/nope"}),
    ]) + "\n"

    res = run_cli(solo_repo, "mcp", "--repo", str(solo_repo), stdin=requests)
    assert res.returncode == 0, res.stdout + res.stderr
    replies = {m["id"]: m for m in map(json.loads, res.stdout.strip().splitlines())}
    assert set(replies) == {1, 2, 3}, "junk lines and notifications get no reply"
    assert replies[1]["result"]["serverInfo"]["name"] == "crapkit"

    call = replies[2]["result"]
    assert call["isError"] is True, call
    text = call["content"][0]["text"]
    assert "unknown tool" in text and "no_such_tool" in text
    assert replies[3]["error"]["code"] == -32601
