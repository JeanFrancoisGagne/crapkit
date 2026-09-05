"""The burn-down queue end to end: what it hands out, and what `empty` means.

Two scopes, one measured function and one nothing imports. `util/stats.py`
holds a ccn-4 function at 0% coverage, so its CRAP is 20 against a ceiling of 6.
The ccn-5 worklist floor used to hide exactly that: `next-item` answered
`empty: true` while `coverage` reported the repo over target.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

DARK = '''def dark(a, b, c):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    return 0
'''

TINY = '''def tiny(a):
    if a:
        return 1
    return 0
'''

# ccn 9, in a scope no lane's scopes list names: the row next-item can only
# report, never hand out
ORPHAN = '''def orphan(a, b, c, d, e, f, g, h):
    total = 0
    if a:
        total += 1
    if b:
        total += 2
    if c:
        total += 3
    if d:
        total += 4
    if e:
        total += 5
    if f:
        total += 6
    if g:
        total += 7
    if h:
        total += 8
    return total
'''

TOOLS_SCOPE = '''[[scope]]
name = "tools"
paths = ["tools"]
languages = ["python"]

[[lane]]'''

# the same scope with a ceiling nothing in it can breach: a wiring gap that is
# not also debt
TOOLS_SCOPE_LOOSE = TOOLS_SCOPE.replace('languages = ["python"]',
                                        'languages = ["python"]\ntarget = 99')

# and the same scope asking for no coverage at all: its rows score cc-only
TOOLS_SCOPE_CC_ONLY = TOOLS_SCOPE.replace('languages = ["python"]',
                                          'languages = ["python"]\ncoverage_optional = true')

REPORT = '''def summarize(rows):
    total = 0
    for r in rows:
        total += r
    if total > 10:
        return "big"
    if total < 0:
        return "neg"
    return str(total)
'''

SUITE = '''def test_summarize_runs():
    assert True
'''

# calc/report.py is measured at 2 of 4 branches (CRAP 6.0); util is never
# mentioned, which is what an untested file looks like in a coverage.py report
MAKE_COV = '''import json

files = {"calc/report.py": {
    "functions": {"summarize": {"start_line": 1,
                                "executed_lines": [1, 2, 3, 4],
                                "missing_lines": [5, 6, 7, 8, 9],
                                "summary": {"covered_lines": 4, "num_statements": 9,
                                            "num_branches": 4, "covered_branches": 2}}},
    "missing_lines": [5, 6, 7, 8, 9]}}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": files}, fh, sort_keys=True)
'''

WHOLE_SUITE = "python -m pytest tests -q -n 0 -p no:cacheprovider -o addopts="

CONFIG = f"""[crapkit]
target = 6
worklist_floor = 5

[crapkit.scoped_tests]
calc = "{WHOLE_SUITE}"
util = "{WHOLE_SUITE}"

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[[scope]]
name = "util"
paths = ["util"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["calc", "util"]
full_suite = false
"""


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def edit_config(repo: Path, old: str, new: str) -> None:
    cfg = repo / "crapkit.toml"
    text = cfg.read_text(encoding="utf-8")
    assert old in text, f"config no longer contains {old!r}"
    cfg.write_text(text.replace(old, new), encoding="utf-8")


def commit(repo: Path, message: str) -> None:
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", message], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    for rel, text in (("util/stats.py", DARK), ("calc/report.py", REPORT),
                      ("tests/test_curve.py", SUITE), ("make_cov.py", MAKE_COV),
                      ("crapkit.toml", CONFIG),
                      (".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")):
        write(tmp_path, rel, text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    commit(tmp_path, "init")
    return tmp_path


def scored(repo: Path) -> dict:
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def next_item(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "next-item", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


# --- admission ---------------------------------------------------------------

def test_the_queue_hands_out_debt_the_ccn_floor_used_to_hide(repo: Path):
    assert scored(repo)["over_target"] == 1, "the fixture has one function over 6"

    out = next_item(repo)

    assert out["empty"] is False, "an agent looping here reported the burn-down done"
    assert out["item"]["path"] == "util/stats.py"
    assert out["item"]["ccn"] == 4 and out["item"]["crap"] == 20.0
    assert out["item"]["remedy"] == "add-tests"


def worklist(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "worklist", "--json", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def test_the_worklist_shows_the_debt_that_graded_the_repo_f(repo: Path):
    """The human entry point printed `0 active` on a repo `coverage` had just
    graded F, because its floor hid the same ccn-4 row next-item hands out."""
    assert scored(repo)["grade"] == "F"

    out = worklist(repo)

    assert [e["path"] for e in out["active"]] == ["util/stats.py"]
    assert out["active"][0]["ccn"] == 4, "under the floor of 5, and over target 6"


def test_the_worklist_floor_still_hides_a_row_at_its_ceiling(repo: Path):
    edit_config(repo, "target = 6", "target = 30")
    assert scored(repo)["over_target"] == 0

    out = worklist(repo)

    assert out["active"] == [] and out["dormant_count"] == 0


def test_the_floor_cannot_veto_debt_however_high_it_is(repo: Path):
    edit_config(repo, "worklist_floor = 5", "worklist_floor = 99")
    scored(repo)

    out = next_item(repo)

    assert out["empty"] is False, "the floor orders the queue, it does not gate debt"
    assert out["item"]["path"] == "util/stats.py"


def test_empty_means_nothing_anywhere_is_over_target(repo: Path):
    edit_config(repo, "target = 6", "target = 30")
    assert scored(repo)["over_target"] == 0

    out = next_item(repo)

    assert out["empty"] is True
    assert out["reasons"]["below_floor"] == 2, \
        "both rows are ccn 4 under a floor of 5, and both sit at their ceiling"


def test_a_per_scope_ceiling_reaches_below_the_pushdown_floor(repo: Path):
    """util's ceiling of 4 makes a ccn-2 function at 0% coverage (CRAP 6) debt.
    A pushdown pinned to the ccn floor would leave it in SQLite unread."""
    write(repo, "util/tiny.py", TINY)
    edit_config(repo, 'name = "util"\npaths = ["util"]\nlanguages = ["python"]',
                'name = "util"\npaths = ["util"]\nlanguages = ["python"]\ntarget = 4')
    commit(repo, "a ccn-2 function and a tighter ceiling")
    scored(repo)

    paths = [i["path"] for i in next_item(repo, "--top", "5")["items"]]

    assert "util/tiny.py" in paths, "ccn 2 against a ceiling of 4 is over target"


def test_a_no_lane_row_over_target_is_counted_so_the_loop_cannot_stop(repo: Path):
    """An empty queue with a ccn-9 no-lane function over its ceiling is not a
    finished burn-down. `skipped_no_lane` alone never said which of those rows
    was debt, so the stop condition had nothing to branch on."""
    write(repo, "tools/helper.py", ORPHAN)
    edit_config(repo, "[[lane]]", TOOLS_SCOPE)
    edit_config(repo, "target = 6", "target = 30")
    commit(repo, "a scope no lane covers")
    assert scored(repo)["no_lane"] == 1

    out = next_item(repo)

    assert out["empty"] is True and out["skipped_no_lane"] == 1
    assert out["reasons"]["no_lane_over_target"] == 1


def test_a_no_lane_scope_with_nothing_over_target_reports_zero(repo: Path):
    write(repo, "tools/helper.py", ORPHAN)
    edit_config(repo, "[[lane]]", TOOLS_SCOPE_LOOSE)
    commit(repo, "a scope no lane covers, under a ceiling of 99")
    scored(repo)

    out = next_item(repo, "--exclude", ".py")

    assert out["empty"] is True
    assert out["reasons"]["no_lane"] == 1
    assert out["reasons"]["no_lane_over_target"] == 0


def test_below_floor_stops_counting_the_debt_the_queue_now_hands_out(repo: Path):
    scored(repo)

    out = next_item(repo, "--exclude", "dark")

    assert out["empty"] is True
    assert out["reasons"]["excluded_by_flag"] == 1
    assert out["reasons"]["below_floor"] == 1, \
        "only summarize, at its ceiling under the floor; dark is a candidate now"


# --- the untested note -------------------------------------------------------

def test_a_cc_only_row_blames_its_own_scope_and_never_a_lane(repo: Path):
    """A stale lane note used to win here and name lane 'py', which does not
    cover scope 'tools'. Committing and rerunning coverage, as it said, changed
    nothing: coverage_optional is why there are no lines, and it is permanent."""
    write(repo, "tools/helper.py", ORPHAN)
    edit_config(repo, "[[lane]]", TOOLS_SCOPE_CC_ONLY)
    commit(repo, "a scope that asks for no coverage")
    assert scored(repo)["cc_only"] == 1
    write(repo, "util/stats.py", DARK + "\n\n")  # the py lane's artifact goes stale

    (item,) = [i for i in next_item(repo, "--top", "5")["items"]
               if i["path"] == "tools/helper.py"]

    assert item["flag"] == "cc-only" and item["uncovered_lines"] is None
    assert item["uncovered_lines_note"] == (
        "scope 'tools' sets coverage_optional = true, so no artifact can name "
        "uncovered lines for tools/helper.py")


def test_brief_and_next_item_name_the_same_cause_for_null_lines(repo: Path):
    scored(repo)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True,
                           capture_output=True, text=True).stdout
    assert dirty.strip() == "", "the note must not blame uncommitted edits on a clean tree"

    item = next_item(repo)["item"]
    out = json.loads(run_cli(repo, "brief", "util/stats.py", item["function"],
                             "--json").stdout)

    assert item["uncovered_lines"] is None and out["uncovered_lines"] is None
    assert item["uncovered_lines_note"] == out["uncovered_lines_note"]
    assert "untested" in item["uncovered_lines_note"]
    assert "commit" not in item["uncovered_lines_note"], \
        "AGENTS.md told an agent to commit; a clean tree proves that never clears it"


# --- test-scoped -------------------------------------------------------------

def test_a_template_without_files_runs_the_scopes_whole_suite(repo: Path):
    """Naming a source file used to substitute it into pytest's collection
    list, where it collects nothing: exit 5, which crapkit reported as exit 1."""
    res = run_cli(repo, "test-scoped", "util/stats.py")

    assert res.returncode == 0, res.stdout + res.stderr


def test_a_template_with_files_still_receives_the_named_test_file(repo: Path):
    edit_config(repo, f'util = "{WHOLE_SUITE}"',
                'util = "python -m pytest {files} -q -n 0 -p no:cacheprovider -o addopts="')
    write(repo, "util/test_stats.py", "def test_dark_exists():\n    assert True\n")
    commit(repo, "a test under the util scope")

    res = run_cli(repo, "test-scoped", "util/test_stats.py")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 passed" in res.stdout


def test_naming_a_test_file_under_several_templates_prints_the_working_recipe(repo: Path):
    res = run_cli(repo, "test-scoped", "tests/test_curve.py")

    assert res.returncode == 3
    assert "{files}" in res.stderr and "paths" in res.stderr


# --- the MCP view of the same queue ------------------------------------------

def _rpc(msg_id: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})


def mcp_next_item(repo: Path, arguments: dict) -> dict:
    """One tools/call against the real server process, over stdio."""
    request = _rpc(1, "tools/call", {"name": "get_next_item", "arguments": arguments}) + "\n"
    proc = run_cli(repo, "mcp", "--repo", str(repo), stdin=request)
    call = json.loads(proc.stdout.strip())["result"]
    assert call["isError"] is False, call
    return json.loads(call["content"][0]["text"])


def test_the_mcp_tool_carries_every_exclude_fragment_through(repo: Path):
    """The served schema types `exclude` as an array. Both fragments have to
    reach the CLI, or the array is a shape the docs promise and the tool drops."""
    scored(repo)

    out = mcp_next_item(repo, {"exclude": ["dark", "summarize"]})

    assert out["empty"] is True
    assert out["reasons"]["excluded_by_flag"] == 2


def test_one_fragment_in_the_array_leaves_the_other_row_queued(repo: Path):
    scored(repo)

    out = mcp_next_item(repo, {"exclude": ["summarize"]})

    assert out["empty"] is False
    assert out["item"]["path"] == "util/stats.py"
