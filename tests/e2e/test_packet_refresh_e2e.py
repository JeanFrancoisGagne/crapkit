"""`commands.refresh` has to clear what `stale` complains about.

The packet used to answer its own staleness warning with another `brief`, which
re-reads the very snapshot that is stale: a session that followed the field got
the same `stale: true` back and no way forward. This runs the string the packet
prints, verbatim, against a repo whose HEAD has moved past its run, and checks
the flag actually goes down.

The budget pin lives here too, because it is a claim about two commands rather
than about one function: `brief` and `next-item` have to publish the same
estimates for the same row.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_LADDER = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, 8))

ALPHA = f"def alpha(a, b):\n    r = 0\n{_LADDER}    return r\n# rev 0\n"

MAKE_COV = '''import json

files = {"core/alpha.py": {
    "functions": {"alpha": {"start_line": 1,
                            "executed_lines": list(range(1, 18)),
                            "missing_lines": [],
                            "summary": {"covered_lines": 17, "num_statements": 17,
                                        "num_branches": 2, "covered_branches": 0}}},
    "missing_lines": [4, 6, 16]}}

with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True}, "files": files}, fh, sort_keys=True)
'''

CONFIG = """[crapkit]
target = 6
worklist_floor = 5

[crapkit.scoped_tests]
core = "python -m pytest {files}"

[[scope]]
name = "core"
paths = ["core"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["core"]
full_suite = false
timeout_seconds = 90
"""


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, env=dict(os.environ))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    for rel, text in (("core/alpha.py", ALPHA), ("make_cov.py", MAKE_COV),
                      ("crapkit.toml", CONFIG)):
        write(tmp_path, rel, text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    _git(tmp_path, "config", "core.autocrlf", "false")
    commit(tmp_path, "init")
    assert run_cli(tmp_path, "coverage", "--json").returncode == 0
    return tmp_path


def brief(repo: Path, *args: str) -> dict:
    res = run_cli(repo, "brief", *args, "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def next_item(repo: Path) -> dict:
    res = run_cli(repo, "next-item")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)["item"]


# --- the refresh field is a command that actually refreshes -------------------

def test_the_refresh_command_clears_the_staleness_it_is_printed_for(repo: Path):
    write(repo, "core/alpha.py", ALPHA.replace("# rev 0", "# rev 1"))
    commit(repo, "move HEAD past the run")
    stale_packet = brief(repo, "core/alpha.py", "alpha")
    assert stale_packet["stale"] is True, "HEAD has moved, so the packet is stale"

    refresh = stale_packet["commands"]["refresh"]
    ran = subprocess.run(shlex.split(refresh), cwd=repo, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300,
                         env=dict(os.environ))

    assert ran.returncode == 0, refresh + "\n" + ran.stdout + ran.stderr
    assert brief(repo, "core/alpha.py", "alpha")["stale"] is False, \
        "the field a stale packet points at has to be the one that clears it"


def test_the_packet_says_the_refresh_writes_a_run(repo: Path):
    """The other three commands change nothing on disk. A read-only session has
    to be able to tell which one it may not run."""
    commands = brief(repo, "core/alpha.py", "alpha")["commands"]

    assert commands["refresh_writes_run"] is True
    assert commands["refresh"].startswith("python -m crapkit coverage")


# --- one budget, both commands ----------------------------------------------

def test_brief_and_next_item_publish_the_same_budget_for_one_row(repo: Path):
    """The pin on the shared helper. Two derivations of `ceil(ccn / target)`
    drift, and the drift shows up as a queue and a packet disagreeing on how
    much work one function is."""
    item = next_item(repo)
    packet = brief(repo, item["path"], item["function"])

    for key in ("est_splits", "est_uncovered_paths", "remedy", "target", "handle"):
        assert packet[key] == item[key], f"{key} disagrees between the two views"
    assert packet["est_splits"] == 2, "ccn 8 against a ceiling of 6 takes two pieces"


def test_a_named_functions_handle_is_its_bare_name(repo: Path):
    assert next_item(repo)["handle"] == "alpha"
    assert brief(repo, "core/alpha.py", "alpha")["handle"] == "alpha"
