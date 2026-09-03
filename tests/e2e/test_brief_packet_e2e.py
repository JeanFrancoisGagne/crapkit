"""The start-editing packet through the CLI, on a real repo with real git.

Same three-scope fixture the payload tests use: five functions, a stub coverage
artifact with hand-set numbers, and five commits that land alpha and beta
together. Every value asserted here is arithmetic on that fixture, so a packet
field that starts guessing fails rather than drifting.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

_LADDER = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, 8))

ALPHA = f"def alpha(a, b):\n    r = 0\n{_LADDER}    return r\n# rev 0\n"
GAMMA = f"def gamma(a, b):\n    r = 0\n{_LADDER}    return r\n"

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

MAKE_COV = '''import json

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
diff_uncovered_max = 3

[crapkit.scoped_tests]
core = "python -m pytest {files}"

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
timeout_seconds = 90
"""


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
    assert run_cli(tmp_path, "coverage", "--json").returncode == 0
    return tmp_path


def brief(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "brief", *args, "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


# --- the packet fields ----------------------------------------------------------

def test_the_packet_carries_the_functions_own_source(repo: Path):
    out = brief(repo, "core/beta.py", "helper")

    assert out["source"] == "def helper(a):\n    if a:\n        return 1\n    return 0"


def test_the_packet_carries_every_function_in_the_file_not_just_this_one(repo: Path):
    out = brief(repo, "core/beta.py", "helper")

    assert out["file_functions"] == [
        {"function": "beta( a , b )", "start": 1, "end": 13, "ccn": 6, "crap": 42.0,
         "remedy": "add-tests"},
        {"function": "helper( a )", "start": 16, "end": 19, "ccn": 2, "crap": 6.0,
         "remedy": "ok"},
    ]
    assert out["file_totals"] == {"functions": 2, "over_target": 1, "crap_load": 48.0}, \
        "helper sits exactly at the ceiling of 6, which is not over it"


def test_the_packet_spells_out_the_rule_the_gate_will_apply(repo: Path):
    rule = brief(repo, "core/alpha.py", "alpha")["gate_rule"]

    assert rule["ceiling"] == 6 and rule["diff_uncovered_max"] == 3
    assert rule["ratchet_mark"] is None and rule["mark_age_days"] is None
    assert "changed functions only" in rule["binds"]


def test_a_marked_function_reports_the_mark_and_how_long_it_has_stood(repo: Path):
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    _commit(repo, "seed the ratchet")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    rule = brief(repo, "core/alpha.py", "alpha")["gate_rule"]

    assert rule["ratchet_mark"] == 72.0
    assert rule["mark_age_days"] == 0, "the mark entered on the newest commit in its history"


def test_the_packet_names_the_lane_that_measures_the_scope(repo: Path):
    assert brief(repo, "core/alpha.py", "alpha")["lane"] == {
        "name": "py", "command": "python make_cov.py", "artifact": "cov.json",
        "parser": "coveragepy", "cwd": "", "env": {}, "timeout_seconds": 90}


def test_the_packet_carries_the_commands_to_run_next(repo: Path):
    out = brief(repo, "core/alpha.py", "alpha")

    assert out["commands"] == {
        "gate": "crapkit rescore core/alpha.py --gate",
        "scoped_tests": 'python -m pytest "core/alpha.py"',
        "verify": "crapkit verify",
        "refresh": "crapkit coverage --reuse-unchanged",
        "refresh_writes_run": True,
    }


def test_an_unconfigured_scope_gets_a_null_test_command_and_a_reason(repo: Path):
    out = brief(repo, "extra/gamma.py", "gamma")

    assert out["commands"]["scoped_tests"] is None
    assert "'extra'" in out["commands"]["scoped_tests_note"]


def test_the_packet_names_the_versions_that_produced_the_numbers(repo: Path):
    versions = brief(repo, "core/alpha.py", "alpha")["versions"]

    assert set(versions) == {"crapkit", "lizard", "analysis_version", "python"}
    assert isinstance(versions["analysis_version"], int)


def test_the_packet_reads_the_signature_off_the_long_name(repo: Path):
    assert brief(repo, "core/alpha.py", "alpha")["params"] == [
        {"name": "a", "type": None}, {"name": "b", "type": None}]


def test_a_function_nobody_claimed_has_no_attempts_and_no_regrowth(repo: Path):
    out = brief(repo, "core/alpha.py", "alpha")

    assert out["attempts"] == []
    assert out["regrowth"]["regrown"] is False
    assert out["regrowth"]["history"] == [[1, 8]], "one run, one point"


def test_a_claim_taken_on_this_function_shows_up_as_an_attempt(repo: Path):
    assert run_cli(repo, "next-item", "--claim").returncode == 0

    (attempt,) = brief(repo, "core/alpha.py", "alpha")["attempts"]

    assert attempt["closed"] is None and attempt["opened"]


def test_coupling_says_which_partner_is_a_test_file(repo: Path):
    out = brief(repo, "core/alpha.py", "alpha")

    assert out["coupling"] == [{"path": "core/beta.py", "support": 6,
                                "confidence": 1.0, "is_test": False}]


def test_coupling_leaves_out_a_partner_git_stopped_tracking(repo: Path):
    """beta.py leaves the index under a new name. Its six shared commits stay in
    the log for a year, and the packet would keep telling a session to open a
    file that is not there."""
    _git(repo, "mv", "core/beta.py", "core/beta2.py")
    _commit(repo, "rename beta")

    assert brief(repo, "core/alpha.py", "alpha")["coupling"] == []


def test_a_twin_reports_whether_it_is_contained(repo: Path):
    (twin,) = brief(repo, "core/alpha.py", "alpha")["duplication_twins"]

    assert twin["path"] == "extra/gamma.py" and twin["similarity"] == 0.9286
    assert twin["contained"] is False, "a flag the dup pass did not raise is not a maybe"


def test_notes_are_null_until_the_config_carries_them(repo: Path):
    assert brief(repo, "core/alpha.py", "alpha")["notes"] == {"repo": None, "scope": None}


def test_a_settled_tree_is_not_stale_and_a_moved_head_is(repo: Path):
    assert brief(repo, "core/alpha.py", "alpha")["stale"] is False

    write(repo, "clean/second.py", "def second(a):\n    return a\n")
    _commit(repo, "move HEAD past the run")

    assert brief(repo, "core/alpha.py", "alpha")["stale"] is True


# --- the fields that were already there ------------------------------------------

def test_every_field_the_brief_published_before_is_unchanged(repo: Path):
    out = brief(repo, "core/alpha.py", "alpha")

    assert out["path"] == "core/alpha.py" and out["function"] == "alpha( a , b )"
    assert out["target"] == 6 and out["ratchet_mark"] is None
    assert out["scored"]["crap"] == 72.0 and out["scored"]["cognitive"] == 7
    assert out["churn"]["commits"] == 6 and out["churn"]["authors"] == 1
    assert out["uncovered_lines"] == [4, 6, 16], "alpha spans 1-17"
    assert out["run_id"] == 1 and len(out["commit"]) == 40


def test_the_text_brief_keeps_its_lines_and_appends_the_new_ones(repo: Path):
    res = run_cli(repo, "brief", "core/alpha.py", "alpha")

    assert res.returncode == 0, res.stdout + res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == "core/alpha.py:1  alpha( a , b )"
    assert "uncovered lines: 4, 6" in res.stdout
    assert "gate ceiling 6  lane py" in res.stdout
    assert "1 function(s), 1 over ceiling, crap load 72.0" in res.stdout


# --- the start line as a name ----------------------------------------------------

def test_a_bare_start_line_names_the_function_that_opens_there(repo: Path):
    assert brief(repo, "core/beta.py", "16")["function"] == "helper( a )"
    assert brief(repo, "core/beta.py", "1")["function"] == "beta( a , b )"


def test_a_line_no_function_opens_on_says_which_lines_do(repo: Path):
    res = run_cli(repo, "brief", "core/beta.py", "17", "--json")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "no function starts at line 17" in res.stderr
    assert "1, 16" in res.stderr
    assert "Traceback" not in res.stderr


SAME_NAME = '''def dup(a):
    if a > 1:
        return 1
    return 0


def dup(a):
    return a
'''


def test_the_start_line_resolves_a_name_two_functions_share(repo: Path):
    write(repo, "core/samename.py", SAME_NAME)
    _commit(repo, "one name, two spans")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    assert brief(repo, "core/samename.py", "7")["scored"]["ccn"] == 1
    assert brief(repo, "core/samename.py", "1")["scored"]["ccn"] == 2


# --- batch mode -------------------------------------------------------------------

def test_a_batch_emits_one_packet_per_queue_item(repo: Path):
    res = run_cli(repo, "brief", "--batch", "2", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["schema"] == 1 and out["stale"] is False
    assert out["run_id"] == 1 and len(out["commit"]) == 40
    assert [p["function"] for p in out["packets"]] == ["alpha( a , b )", "gamma( a , b )"]


def test_a_batch_packet_is_what_a_single_brief_prints(repo: Path):
    batched = json.loads(run_cli(repo, "brief", "--batch", "1", "--json").stdout)

    single = brief(repo, "core/alpha.py", "alpha")

    assert batched["packets"][0] == {k: v for k, v in single.items() if k != "schema"}


def test_a_batch_packet_reports_the_twins_its_own_single_brief_reports(repo: Path):
    """Every packet in a batch is scored against ONE shingle index, built while
    the first packet was assembled. gamma is the second item in the queue, so
    its twins come out of an index another function paid for; a single brief
    builds that index for gamma alone. Both must name alpha at the same score."""
    batched = json.loads(run_cli(repo, "brief", "--batch", "3", "--json").stdout)
    packets = {p["path"]: p for p in batched["packets"]}

    twins = packets["extra/gamma.py"]["duplication_twins"]

    assert twins == brief(repo, "extra/gamma.py", "gamma")["duplication_twins"]
    assert [t["path"] for t in twins] == ["core/alpha.py"], "gamma is alpha's copy"


def test_a_batch_stops_at_the_queue_it_has(repo: Path):
    out = json.loads(run_cli(repo, "brief", "--batch", "50", "--json").stdout)

    assert [p["path"] for p in out["packets"]] == [
        "core/alpha.py", "extra/gamma.py", "core/beta.py"], \
        "three actionable rows; clean/ok.py and beta's helper are at their ceiling"


def test_a_batch_of_zero_is_refused(repo: Path):
    res = run_cli(repo, "brief", "--batch", "0", "--json")

    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "--batch" in res.stderr


def test_brief_without_a_function_says_what_it_needs(repo: Path):
    res = run_cli(repo, "brief", "core/alpha.py")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "--batch" in res.stderr and "PATH NAME" in res.stderr


def test_the_same_run_produces_the_same_batch_twice(repo: Path):
    first = run_cli(repo, "brief", "--batch", "3", "--json").stdout

    assert run_cli(repo, "brief", "--batch", "3", "--json").stdout == first


# --- next-item gains the same staleness verdict -----------------------------------

def test_next_item_says_whether_its_snapshot_still_describes_head(repo: Path):
    res = run_cli(repo, "next-item")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["stale"] is False

    write(repo, "clean/second.py", "def second(a):\n    return a\n")
    _commit(repo, "move HEAD past the run")

    assert json.loads(run_cli(repo, "next-item").stdout)["stale"] is True
