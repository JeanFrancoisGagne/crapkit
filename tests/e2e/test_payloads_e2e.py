"""The agent-facing payloads end to end: the dark lines on next-item and
explain, the one-call brief, and the per-scope rollups on coverage and trend.

Five functions across three scopes, scored by a stub coverage.py artifact whose
numbers are hand-set, so every value here is arithmetic on the fixture rather
than a rerun of crapkit's own formula. Every function reads 0 of 2 branches, so
crap = ccn^2 + ccn: alpha 72, gamma 72, beta 42, helper 6, ok 2.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

_LADDER = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, 8))

# ccn 8, span 1-17; the trailing comment is what the coupling commits rewrite,
# so five more commits never move a line number.
ALPHA = f"def alpha(a, b):\n    r = 0\n{_LADDER}    return r\n# rev 0\n"

# the same body under another name: 13 of alpha's 14 shingles, similarity 0.9286
GAMMA = f"def gamma(a, b):\n    r = 0\n{_LADDER}    return r\n"

# beta spans 1-13 (ccn 6) and helper spans 16-19 (ccn 2), with line 14 blank
BETA = '''def beta(a, b):
    total = 0
    for x in range(a):
        total += x
    while total > b:
        total -= b
    if total == 0:
        return "zero"
    if total < 0:
        return "negative"
    if total > 100:
        return "big"
    return str(total)


def helper(a):
    if a:
        return 1
    return 0
# rev 0
'''

OK = "def ok(a):\n    return a\n"

MAKE_COV = '''"""Fixture coverage generator: a coverage.py report with hand-set numbers.

The file-level `missing` list is what the dark-line readers parse; the
per-function branch counts are what the CRAP score joins on. They are
independent on purpose, so a test can pin either without moving the other.
"""
import json

PLAN = {
    "core/alpha.py": {"missing": [4, 6, 16], "functions": {"alpha": [1, 17]}},
    "core/beta.py": {"missing": [4, 8, 14, 18],
                     "functions": {"beta": [1, 13], "helper": [16, 19]}},
    "extra/gamma.py": {"missing": [], "functions": {"gamma": [1, 17]}},
    "clean/ok.py": {"missing": [], "functions": {"ok": [1, 2]}},
}

files = {}
for path, spec in PLAN.items():
    entry = {}
    for name, (start, end) in spec["functions"].items():
        entry[name] = {
            "start_line": start,
            "executed_lines": list(range(start, end + 1)),
            "missing_lines": [],
            "summary": {"covered_lines": end - start + 1,
                        "num_statements": end - start + 1,
                        "num_branches": 2, "covered_branches": 0},
        }
    files[path] = {"functions": entry, "missing_lines": spec["missing"]}

with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": files}, fh, sort_keys=True)
'''

CONFIG = """[crapkit]
target = 6
worklist_floor = 5

[[scope]]
name = "clean"
paths = ["clean"]
languages = ["python"]

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
scopes = ["clean", "core", "extra"]
full_suite = false
"""

# the TSV export header: brief publishes the scored row whole
SCORED_COLUMNS = {"scope", "path", "long_name", "start", "end", "ccn_std", "ccn_mod",
                  "ccn", "nloc", "params", "nesting", "cov", "flag", "crap", "remedy",
                  "cognitive"}


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Five functions in three scopes, then five commits that land alpha and
    beta together: support 6 with the initial commit, confidence 1.0 both ways."""
    for rel, text in (("core/alpha.py", ALPHA), ("core/beta.py", BETA),
                      ("extra/gamma.py", GAMMA), ("clean/ok.py", OK),
                      ("make_cov.py", MAKE_COV), ("crapkit.toml", CONFIG)):
        write(tmp_path, rel, text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    _git(tmp_path, "config", "core.autocrlf", "false")
    _commit(tmp_path, "init")
    for rev in range(1, 6):
        write(tmp_path, "core/alpha.py", ALPHA.replace("# rev 0", f"# rev {rev}"))
        write(tmp_path, "core/beta.py", BETA.replace("# rev 0", f"# rev {rev}"))
        _commit(tmp_path, f"rev {rev}")
    return tmp_path


def scored(repo: Path) -> dict:
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def next_item(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "next-item", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def brief(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "brief", *args, "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


# --- dark lines --------------------------------------------------------------

def test_the_next_item_names_the_lines_no_test_reached(repo: Path):
    scored(repo)

    item = next_item(repo)["item"]

    assert item["path"] == "core/alpha.py", "crap 72 on the most-churned file leads"
    assert item["uncovered_lines"] == [4, 6, 16]
    assert "uncovered_lines_note" not in item, \
        "a fresh artifact answers; the note exists for when none can"


def test_dark_lines_stop_at_the_function_that_owns_them(repo: Path):
    scored(repo)

    by_path = {i["path"]: i for i in next_item(repo, "--top", "3")["items"]}

    # core/beta.py is dark on 4, 8, 14 and 18; beta spans 1-13 and helper 16-19,
    # so line 14 (the blank between them) is nobody's to fix
    assert by_path["core/beta.py"]["uncovered_lines"] == [4, 8]
    assert by_path["extra/gamma.py"]["uncovered_lines"] == []


def test_a_deleted_artifact_costs_the_lines_not_the_command(repo: Path):
    scored(repo)
    (repo / "cov.json").unlink()

    item = next_item(repo)["item"]

    assert item["uncovered_lines"] is None, "[] is what a covered function reports"
    assert "cov.json" in item["uncovered_lines_note"]
    assert item["crap"] == 72.0, "the stored score is unaffected; only the lines are gone"


def test_an_edit_after_the_run_reads_as_stale_rather_than_as_line_numbers(repo: Path):
    scored(repo)
    write(repo, "core/alpha.py", ALPHA + "# edited after the run\n")

    item = next_item(repo)["item"]

    assert item["uncovered_lines"] is None
    assert "'py'" in item["uncovered_lines_note"], "the note names the lane to rerun"


def test_an_unreadable_artifact_is_a_note_not_an_exit_code(repo: Path):
    scored(repo)
    (repo / "cov.json").write_text("{ truncated", encoding="utf-8")

    item = next_item(repo)["item"]

    assert item["uncovered_lines"] is None
    assert "unreadable" in item["uncovered_lines_note"]


def test_a_file_no_artifact_measured_reports_null_lines_not_an_empty_list(repo: Path):
    # extra/gamma.py IS in the artifact with nothing dark, so [] is the honest
    # answer there; a file the lane never mentioned must read differently
    write(repo, "core/fresh.py", "def fresh(a):\n    if a:\n        return 1\n    return 0\n")
    _commit(repo, "a file the coverage plan never names")
    scored(repo)

    out = brief(repo, "core/fresh.py", "fresh")

    assert out["uncovered_lines"] is None
    assert "core/fresh.py" in out["uncovered_lines_note"]
    assert brief(repo, "extra/gamma.py", "gamma")["uncovered_lines"] == []


def test_the_plain_brief_prints_the_note_when_the_lines_are_null(repo: Path):
    write(repo, "core/fresh.py", "def fresh(a):\n    if a:\n        return 1\n    return 0\n")
    _commit(repo, "a file the coverage plan never names")
    scored(repo)

    res = run_cli(repo, "brief", "core/fresh.py", "fresh")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "uncovered lines: no lane artifact measured core/fresh.py" in res.stdout


def test_the_same_artifacts_produce_the_same_payload_twice(repo: Path):
    scored(repo)
    assert run_cli(repo, "next-item").stdout == run_cli(repo, "next-item").stdout


def test_explain_prints_the_dark_lines_of_the_function_it_explains(repo: Path):
    scored(repo)

    res = run_cli(repo, "explain", "core/beta.py", "helper")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "uncovered lines: 18" in res.stdout


def test_explain_says_why_when_no_artifact_can_answer(repo: Path):
    scored(repo)
    (repo / "cov.json").unlink()

    res = run_cli(repo, "explain", "core/beta.py", "helper")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "uncovered lines: none (" in res.stdout and "cov.json" in res.stdout


# --- brief -------------------------------------------------------------------

def test_brief_carries_the_whole_scored_row(repo: Path):
    scored(repo)

    out = brief(repo, "core/alpha.py", "alpha")

    assert set(out["scored"]) == SCORED_COLUMNS
    assert out["scored"]["ccn"] == 8 and out["scored"]["crap"] == 72.0
    assert out["scored"]["cognitive"] == 7
    assert out["scored"]["flag"] == "measured" and out["scored"]["remedy"] == "decompose"
    assert out["path"] == "core/alpha.py" and out["function"] == "alpha( a , b )"
    assert out["target"] == 6


def test_brief_carries_the_ratchet_mark_once_one_exists(repo: Path):
    scored(repo)
    assert brief(repo, "core/alpha.py", "alpha")["ratchet_mark"] is None

    assert run_cli(repo, "ratchet", "seed").returncode == 0

    assert brief(repo, "core/alpha.py", "alpha")["ratchet_mark"] == 72.0


def test_brief_names_the_twin_the_churn_and_the_coupled_file(repo: Path):
    scored(repo)

    out = brief(repo, "core/alpha.py", "alpha")

    (twin,) = out["duplication_twins"]
    assert twin["path"] == "extra/gamma.py" and twin["long_name"] == "gamma( a , b )"
    assert twin["similarity"] == 0.9286, "13 of alpha's 14 shingles are gamma's too"
    assert out["coupling"] == [{"path": "core/beta.py", "support": 6, "confidence": 1.0,
                                "is_test": False}]
    assert out["churn"]["commits"] == 6 and out["churn"]["authors"] == 1
    assert isinstance(out["churn"]["weight"], float)


def test_brief_reuses_the_dark_line_machinery(repo: Path):
    scored(repo)

    assert brief(repo, "core/beta.py", "beta")["uncovered_lines"] == [4, 8]
    assert brief(repo, "core/beta.py", "helper")["uncovered_lines"] == [18]


def test_brief_matches_the_bare_name_and_the_whole_long_name(repo: Path):
    scored(repo)

    assert brief(repo, "core/beta.py", "helper")["function"] == "helper( a )"
    assert brief(repo, "core/beta.py", "helper( a )")["function"] == "helper( a )"


def test_brief_takes_the_function_string_next_item_just_printed(repo: Path):
    scored(repo)
    printed = next_item(repo)["item"]["function"]
    assert printed == "alpha( a , b )", "next-item publishes the long name"

    out = brief(repo, "core/alpha.py", printed)

    assert out["function"] == printed and out["scored"]["crap"] == 72.0


def test_a_name_that_matches_nothing_lists_what_the_file_holds(repo: Path):
    scored(repo)

    res = run_cli(repo, "brief", "core/beta.py", "nope", "--json")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "beta" in res.stderr and "helper" in res.stderr
    assert "Traceback" not in res.stderr


# dup is ccn 2 at line 1 and ccn 1 at line 7; same is ccn 2 at both 11 and 17
SAME_NAME = '''def dup(a):
    if a > 1:
        return 1
    return 0


def dup(a):
    return a


def same(a):
    if a > 1:
        return 1
    return 0


def same(a):
    if a > 1:
        return 1
    return 0
'''


def test_twins_are_one_candidate_and_the_worst_of_them_wins(repo: Path):
    write(repo, "core/samename.py", SAME_NAME)
    _commit(repo, "one long_name, two spans")
    scored(repo)

    worst = brief(repo, "core/samename.py", "dup")["scored"]
    tied = brief(repo, "core/samename.py", "same")["scored"]

    assert (worst["start"], worst["ccn"]) == (1, 2), "the ccn-2 twin outranks the ccn-1 one"
    assert tied["start"] == 11, "equal twins resolve to the one that appears first"


def test_an_ambiguous_name_is_refused_with_its_candidates(repo: Path):
    write(repo, "core/twins.py", "def dup(a):\n    return a\n\n\ndef dup(a, b):\n    return a + b\n")
    _commit(repo, "two functions, one name")
    scored(repo)

    res = run_cli(repo, "brief", "core/twins.py", "dup", "--json")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "ambiguous" in res.stderr
    assert "dup( a )" in res.stderr and "dup( a , b )" in res.stderr

    # and the candidate it printed is the string that resolves it
    assert brief(repo, "core/twins.py", "dup( a , b )")["scored"]["params"] == 2


def test_brief_on_a_file_with_no_history_answers_null_rather_than_crashing(repo: Path):
    write(repo, "core/fresh.py", "def fresh(a):\n    return a\n")
    _git(repo, "add", "core/fresh.py")  # tracked, never committed: no churn, no coupling
    scored(repo)

    out = brief(repo, "core/fresh.py", "fresh")

    assert out["churn"] is None
    assert out["coupling"] == [] and out["duplication_twins"] == []
    assert out["ratchet_mark"] is None
    assert out["scored"]["ccn"] == 1


def test_brief_without_json_prints_a_short_summary(repo: Path):
    scored(repo)

    res = run_cli(repo, "brief", "core/alpha.py", "alpha")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "core/alpha.py" in res.stdout and "alpha( a , b )" in res.stdout
    assert "72" in res.stdout


# --- per-scope rollups -------------------------------------------------------

def test_coverage_json_rolls_the_totals_up_per_scope(repo: Path):
    by_scope = scored(repo)["by_scope"]

    assert by_scope == {
        "clean": {"functions": 1, "over_target": 0, "crap_load": 2.0, "grade": "A+"},
        "core": {"functions": 3, "over_target": 2, "crap_load": 120.0, "grade": "F"},
        "extra": {"functions": 1, "over_target": 1, "crap_load": 72.0, "grade": "F"},
    }


def test_trend_json_carries_the_same_rollup_per_run(repo: Path):
    scored(repo)

    res = run_cli(repo, "trend", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    (run,) = json.loads(res.stdout)["runs"]
    assert run["by_scope"]["core"] == {"functions": 3, "over_target": 2,
                                       "crap_load": 120.0, "grade": "F"}
    assert run["by_scope"]["clean"]["grade"] == "A+"
    assert run["crap_load"] == 194.0, "the three scopes add up to the repo total"
