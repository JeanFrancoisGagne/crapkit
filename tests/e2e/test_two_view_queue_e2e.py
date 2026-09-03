"""`worklist` and `next-item` are two views, and each row says which it is.

worklist is the risk map: every function above the floor, over-target rows and
no-lane rows included, ranked by risk. It does not empty. next-item is the
actionable queue: it drops what no lane measures and ranks by CRAP descending.
An agent that reads one and calls the other gets a different function, so the
row itself has to carry the reason.

A four-function repo across a lane-covered scope and a lane-less one, scored by
a stub coverage.py artifact so every CRAP value here is hand-computable:
crap = ccn^2 * (1 - cov)^3 + ccn, and cov = covered branches over branches.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

MAKE_COV = '''"""Fixture coverage generator: a coverage.py artifact from cov_plan.json.

A file with no plan entry is absent from the artifact, which reads as untested.
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
name = "web"
paths = ["web"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["core"]
full_suite = false
"""

# (path, function, if-count). ccn is one more than the if-count.
FUNCTIONS = [
    ("core/alpha.py", "alpha", 8),     # ccn 9, half covered -> crap 19.125, decompose
    ("core/beta.py", "beta", 5),       # ccn 6, untested     -> crap 42.0,   add-tests
    ("core/gamma.py", "gamma", 4),     # ccn 5, 80% covered  -> crap 5.2,    ok
    ("web/dispatch.py", "dispatch", 6),  # ccn 7, no lane    -> crap 56.0,   no-lane
]

# alpha: 8 of 16 branches is cov 0.5. gamma: 8 of 10 is cov 0.8, which lands it
# under the target of 6 and makes it the finished row.
COV_PLAN = {
    "core/alpha.py": {"alpha": {"start": 1, "end": 19, "branches": 16, "covered": 8}},
    "core/gamma.py": {"gamma": {"start": 1, "end": 11, "branches": 10, "covered": 8}},
}


run_cli = cli_runner(timeout=180, encoding="utf-8")


def _source(name: str, ifs: int) -> str:
    body = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, ifs + 1))
    return f"def {name}(a, b):\n    r = 0\n{body}    return r\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo_unscored(tmp_path: Path) -> Path:
    """The repo before any coverage run: only what the files and git provide."""
    return _built_repo(tmp_path)


def _built_repo(tmp_path: Path) -> Path:
    for rel, name, ifs in FUNCTIONS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_source(name, ifs), encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / "cov_plan.json").write_text(json.dumps(COV_PLAN), encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _built_repo(tmp_path)
    res = run_cli(tmp_path, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return tmp_path


def worklist_json(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "worklist", "--json", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


_ROW = re.compile(r"\s(\S+):\d+\s\s")


def worklist_rows(repo: Path) -> dict[str, str]:
    """path -> the printed row, so a marker assertion reads what a human sees."""
    res = run_cli(repo, "worklist")
    assert res.returncode == 0, res.stdout + res.stderr
    found = [(_ROW.search(ln), ln) for ln in res.stdout.splitlines() if ln.startswith("  risk")]
    return {m.group(1): ln for m, ln in found if m}


def queue(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "next-item", *args)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def test_the_two_views_lead_with_different_functions(repo: Path):
    """Same run, same admission floor, two orderings. Both are correct, and the
    docs used to call them "the same queue"."""
    ranked = [e["path"] for e in worklist_json(repo)["active"]]
    handed = [i["path"] for i in queue(repo, "--top", "5")["items"]]

    assert ranked[0] == "core/alpha.py", "worklist ranks on risk, ccn-first with no churn"
    assert handed[0] == "core/beta.py", "next-item ranks on crap"


def test_next_item_hands_them_out_by_crap_descending(repo: Path):
    craps = [i["crap"] for i in queue(repo, "--top", "5")["items"]]

    assert craps == sorted(craps, reverse=True), craps


def test_the_worklist_ranks_the_no_lane_row_the_queue_will_never_offer(repo: Path):
    ranked = [e["path"] for e in worklist_json(repo)["active"]]
    handed = [i["path"] for i in queue(repo, "--top", "5")["items"]]

    assert "web/dispatch.py" in ranked
    assert "web/dispatch.py" not in handed
    assert queue(repo)["skipped_no_lane"] == 1


def test_the_printed_row_says_which_rows_the_queue_declines(repo: Path):
    rows = worklist_rows(repo)

    assert rows["web/dispatch.py"].endswith("no-lane"), rows["web/dispatch.py"]
    assert rows["core/gamma.py"].endswith("ok"), rows["core/gamma.py"]
    assert rows["core/beta.py"].endswith("beta( a , b )"), "work left carries no marker"


def test_the_json_entries_carry_the_flag_and_the_remedy(repo: Path):
    by_path = {e["path"]: e for e in worklist_json(repo)["active"]}

    assert by_path["web/dispatch.py"]["flag"] == "no-lane"
    assert by_path["core/gamma.py"]["remedy"] == "ok"
    assert by_path["core/beta.py"]["flag"] == "untested"
    assert by_path["core/alpha.py"]["remedy"] == "decompose"


def test_the_header_counts_active_rows_against_their_total_and_dormant(repo: Path):
    res = run_cli(repo, "worklist")

    header = res.stdout.splitlines()[0]
    assert header.endswith("— 4 of 4 active (worklist_top 50), 0 dormant"), header


def test_an_inventory_only_store_leaves_both_fields_null(repo_unscored: Path):
    """Nothing scored a remedy, so the worklist may not invent one. A store that
    ALSO holds a scored run prefers it instead (round 7): see the worklist
    surface tests."""
    assert run_cli(repo_unscored, "inventory", "--json").returncode == 0

    entries = worklist_json(repo_unscored)["active"]

    assert {e["flag"] for e in entries} == {None}
    assert {e["remedy"] for e in entries} == {None}
    assert not any(ln.rstrip().endswith(("ok", "no-lane"))
                   for ln in worklist_rows(repo_unscored).values())
