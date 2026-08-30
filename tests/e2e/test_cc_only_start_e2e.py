"""The README's 60-second start, run verbatim on a repo with no coverage parser.

Nine of the fourteen languages score cc-only, and a repo made only of them was
the one shape the printed start could not survive: `init` wrote a bare scope and
said "declare a [[lane]]", `coverage` exited 3 on the empty lane list, and every
command that needs a scored run then reported there was none. The README
promises the opposite — "a cc-only scope declares coverage_optional = true,
scores crap = ccn, and needs no lane. Nothing about it is provisional."

So the commands here are read out of the README rather than typed: the test
fails the day the printed start stops matching the one that runs. The mixed repo
at the bottom is the other half of the same fix — a Go scope beside a Python one
must go cc-only without costing the Python scope its lane.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from crapkit.config import load_config_text

from conftest import cli_runner

ROOT = Path(__file__).resolve().parent.parent.parent

# Classify: base 1 + 5 ifs = 6, exactly the default ceiling. Scored cc-only it
# is crap 6.0 and remedy ok; scored no-lane the same function is crap 42.0 and
# remedy add-tests, about coverage no Go artifact could ever produce.
# Route: base 1 + 6 branch points = 9, over the ceiling either way.
ROUTE_GO = """package main

func Classify(n int, m int) string {
\tif n > 0 {
\t\treturn "pos"
\t}
\tif m > 0 {
\t\treturn "m"
\t}
\tif n == m {
\t\treturn "eq"
\t}
\tif n < m {
\t\treturn "lt"
\t}
\tif n+m == 0 {
\t\treturn "zero"
\t}
\treturn "none"
}

func Route(code int, force bool) string {
\tif code == 1 {
\t\treturn "one"
\t}
\tif code == 2 {
\t\treturn "two"
\t}
\tif code == 3 {
\t\treturn "three"
\t}
\tif code == 4 && force {
\t\treturn "four"
\t}
\tif code == 5 || force {
\t\treturn "five"
\t}
\tif code == 6 {
\t\treturn "six"
\t}
\treturn ""
}
"""
CLASSIFY_CCN = 6
ROUTE_CCN = 9

GRADE_PY = """def classify(score, attempts, late, bonus):
    if score > 90:
        return "A"
    if attempts > 3:
        return "retry"
    if late:
        return "late"
    if bonus:
        return "bonus"
    return "ok"
"""

# pytest-cov is not a dependency of this suite, so the lane init detects gets its
# command swapped for this generator. Everything else init wrote stands, the
# parser included: what is under test is which scopes the lane claims and which
# scope went coverage_optional, not pytest's ability to write a report.
MAKE_COV = '''import json
import os

files = {"calc/grade.py": {"functions": {"classify": {"start_line": 1,
    "executed_lines": [1, 2, 3],
    "missing_lines": [4, 5, 6, 7, 8, 9],
    "summary": {"covered_lines": 3, "num_statements": 9,
                "num_branches": 8, "covered_branches": 4}}},
    "missing_lines": [4, 5, 6, 7, 8, 9]}}
os.makedirs(os.path.join(".crapkit", "cov"), exist_ok=True)
with open(os.path.join(".crapkit", "cov", "py.json"), "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": files}, fh, sort_keys=True)
'''


run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit_all(repo: Path, message: str) -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)


def start_commands() -> list[list[str]]:
    """The crapkit calls the README's 60-second start prints, in printed order.

    Comments come off and `&& git add ...` is dropped, because the test runs the
    git half itself. Nothing else is rewritten: an argument the README grows is
    an argument this test starts passing.
    """
    block = re.search(r"^## The 60-second start$(.*?)^## ", ROOT.joinpath("README.md")
                      .read_text(encoding="utf-8"), re.M | re.S).group(1)
    lines = [ln.split("#", 1)[0].strip() for ln in block.splitlines()
             if ln.startswith("crapkit ")]
    return [ln.split(" && ")[0].split() for ln in lines]


def rows_by_name(payload: dict) -> dict[str, dict]:
    """Rescored rows keyed by the bare identifier. A long_name carries the
    parameter list, and lizard spells it `f( a , b )` for Python and `f a , b`
    for Go, so the first token is the only common cut."""
    return {re.split(r"[ (]", r["function"], maxsplit=1)[0]: r for r in payload["functions"]}


@pytest.fixture()
def go_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "gorepo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    write(repo, "cmd/route.go", ROUTE_GO)
    commit_all(repo, "init")
    return repo


def test_the_readme_start_is_still_the_four_calls_this_test_runs():
    """A guard on the extractor, not on crapkit: a README rewrite that changes
    the sequence must change this list, not silently narrow what runs below."""
    assert start_commands() == [["crapkit", "init"], ["crapkit", "coverage"],
                                ["crapkit", "worklist"], ["crapkit", "ratchet", "seed"]]


def test_the_printed_start_runs_clean_on_a_go_only_repo(go_repo: Path):
    """Every line, in order, exit 0 — then the two verdict commands on top.

    `verify` and `rescore --gate` are the pair a session actually loops on. Both
    read the run `coverage` wrote, and neither could see it while an empty lane
    list meant "nothing was measured".
    """
    for command in start_commands():
        res = run_cli(go_repo, *command[1:])
        assert res.returncode == 0, f"`{' '.join(command)}` exited {res.returncode}: " + res.stderr
    run_git(go_repo, "add", "crapkit.toml", "crapkit-ratchet.tsv", ".gitignore")

    verify = run_cli(go_repo, "verify")
    gate = run_cli(go_repo, "rescore", "cmd/route.go", "--gate")

    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert gate.returncode == 0, gate.stdout + gate.stderr


def test_init_writes_the_key_that_makes_the_scope_scorable(go_repo: Path):
    res = run_cli(go_repo, "init")

    assert res.returncode == 0, res.stderr
    assert "coverage_optional = true" in (go_repo / "crapkit.toml").read_text(encoding="utf-8")


def test_the_init_summary_does_not_send_a_go_repo_looking_for_a_lane(go_repo: Path):
    """The printed next step was `declare a [[lane]] per coverage command`, and
    for this repo there is no lane to declare in any language crapkit reads."""
    res = run_cli(go_repo, "init")

    assert "declare a [[lane]]" not in res.stdout
    assert "crapkit coverage" in res.stdout


def test_doctor_does_not_call_the_missing_lane_a_gap(go_repo: Path):
    """`no [[lane]] declared — inventory works; coverage needs one` is true of a
    Python repo nobody wired and false here, where coverage needs none."""
    run_cli(go_repo, "init")

    res = run_cli(go_repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "coverage needs one" not in res.stdout
    assert "every scope is cc-only" in res.stdout


def test_coverage_scores_the_cc_only_rows_instead_of_exiting_3(go_repo: Path):
    run_cli(go_repo, "init")

    res = run_cli(go_repo, "coverage", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    summary = json.loads(res.stdout)
    assert (summary["cc_only"], summary["no_lane"]) == (2, 0)
    assert (summary["measured"], summary["untested"]) == (0, 0)


def test_the_stored_rows_have_the_cc_only_shape(go_repo: Path):
    """crap IS ccn: 6.0 for the ccn-6 function, not the 42.0 the CRAP formula
    returns at cov 0. And remedy answers ok or decompose, never add-tests."""
    run_cli(go_repo, "init")
    run_cli(go_repo, "coverage")

    res = run_cli(go_repo, "rescore", "cmd/route.go", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    rows = rows_by_name(json.loads(res.stdout))
    assert [(rows[n]["ccn"], rows[n]["crap"], rows[n]["flag"], rows[n]["cov"], rows[n]["remedy"])
            for n in ("Classify", "Route")] == [
        (CLASSIFY_CCN, float(CLASSIFY_CCN), "cc-only", 0.0, "ok"),
        (ROUTE_CCN, float(ROUTE_CCN), "cc-only", 0.0, "decompose")]


def test_the_run_coverage_wrote_is_the_baseline_verify_emits(go_repo: Path):
    """--emit-baseline reads the same trusted run the taint rule picks, so a
    portable baseline proves the run is trusted rather than merely present."""
    run_cli(go_repo, "init")
    run_cli(go_repo, "coverage")

    res = run_cli(go_repo, "verify", "--emit-baseline", "baseline.tsv", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["baseline_run"] is not None
    assert "Classify" in (go_repo / "baseline.tsv").read_text(encoding="utf-8")


def test_ratchet_seed_signs_the_function_over_the_ceiling(go_repo: Path):
    run_cli(go_repo, "init")
    run_cli(go_repo, "coverage")

    res = run_cli(go_repo, "ratchet", "seed")

    assert res.returncode == 0, res.stdout + res.stderr
    marks = (go_repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")
    assert "Route" in marks and "Classify" not in marks


def test_next_item_ranks_the_cc_only_debt_with_nothing_skipped(go_repo: Path):
    run_cli(go_repo, "init")
    run_cli(go_repo, "coverage")

    res = run_cli(go_repo, "next-item")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["skipped_no_lane"] == 0


# --- the mixed repo: one scope with a parser, one without --------------------

@pytest.fixture()
def mixed_repo(tmp_path: Path) -> Path:
    """A python scope with a detected lane and a go scope with none.

    The generator lands untracked, after the commit: crapkit scores git-tracked
    files only, so an untracked .py at the root is invisible to init, to the
    corpus and to doctor's unclaimed-file check.
    """
    repo = tmp_path / "mixed"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    write(repo, "pyproject.toml", '[project]\nname = "mixed"\nversion = "0"\n')
    write(repo, "calc/grade.py", GRADE_PY)
    write(repo, "cmd/route.go", ROUTE_GO)
    commit_all(repo, "init")
    write(repo, "make_cov.py", MAKE_COV)
    run_cli(repo, "init")
    _swap_lane_command(repo)
    return repo


def _swap_lane_command(repo: Path) -> None:
    """The fake producer writes coverage only, so the junit results file init
    declares for the pytest lane (#26) goes with the command it belonged to;
    a declared-but-missing results file refuses the lane."""
    path = repo / "crapkit.toml"
    text = path.read_text(encoding="utf-8")
    swapped = re.sub(r'^command = "(?:python3?|py) -m pytest .*"$', 'command = "python make_cov.py"',
                     text, count=1, flags=re.M)
    assert swapped != text, "init stopped writing a pytest lane for a pyproject repo"
    swapped = re.sub(r'^results_artifact = .*\n', "", swapped, count=1, flags=re.M)
    path.write_text(swapped, encoding="utf-8", newline="\n")


def test_init_marks_the_go_scope_and_leaves_the_python_scope_alone(mixed_repo: Path):
    text = (mixed_repo / "crapkit.toml").read_text(encoding="utf-8")
    go_stanza = text.split("[[scope]]")[2]

    assert text.count("coverage_optional = true") == 1
    assert 'name = "cmd"' in go_stanza and "coverage_optional = true" in go_stanza


def test_the_detected_lane_still_claims_the_python_scope(mixed_repo: Path):
    """The go scope going optional must not cost the py lane the scope it does
    measure, and the config init wrote must still leave doctor with nothing to
    report: no lane-less scope, no unclaimed file."""
    cfg = load_config_text((mixed_repo / "crapkit.toml").read_text(encoding="utf-8"))

    assert {lane.name: lane.scopes for lane in cfg.lanes} == {"py": ("calc",)}
    assert cfg.lane_less_scopes == ()
    assert run_cli(mixed_repo, "doctor").returncode == 0


def test_one_coverage_run_measures_python_and_scores_go_cc_only(mixed_repo: Path):
    res = run_cli(mixed_repo, "coverage", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    summary = json.loads(res.stdout)
    assert (summary["measured"], summary["cc_only"], summary["no_lane"]) == (1, 2, 0)


def test_the_go_row_scores_ccn_not_the_no_lane_formula(mixed_repo: Path):
    run_cli(mixed_repo, "coverage")

    res = run_cli(mixed_repo, "rescore", "cmd/route.go", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    classify = rows_by_name(json.loads(res.stdout))["Classify"]
    assert (classify["flag"], classify["crap"]) == ("cc-only", float(CLASSIFY_CCN))


def test_next_item_skips_nothing_for_want_of_a_lane(mixed_repo: Path):
    run_cli(mixed_repo, "coverage")

    res = run_cli(mixed_repo, "next-item")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["skipped_no_lane"] == 0
