"""hook-precommit refuses a marked function whose staged ccn alone passed its mark.

tools/demo's fixture, scored by a pytest lane and seeded: `read_row` carries
the mark 7.3333 (ccn 6, two thirds of its branches run). Two more branches
take it to ccn 8. A staged blob carries no coverage, so the hook knows the
function's CRAP only as the range from 8 (every branch run) to 72 (none run).
CRAP is never under ccn, so the mark rose at any coverage: the hook exits 6 and
names the function, its ccn and its mark. `rescore --gate` refuses the same
tree, and with the edit committed past the hook, `verify --json` reports the
ratchet regression the hook saw coming.

The differential holds the hook to verify on the committed tree: every
function the hook refuses as a marked rise is a ratchet_regression item, and
the function whose whole range sits at or under its mark is not one.

`audit`, added to the fixture here, is a function no test runs: seed marks it
at its untested CRAP, 56, the high end of the range a comment edit leaves it.
`banner` is untested too, marked 20 at ccn 4. No test reaches either one, so
each mark is ccn^2 + ccn and rises with ccn: a branch added to either leaves a
range that straddles its mark, and the hook warns. The warn differential holds
those warns to verify: every warned function is a ratchet_regression item, and
every regression the hook did not refuse was warned.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import child_env, cli_runner
from repo_templates import copy_of, template

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "demo"))
import demo_repo  # noqa: E402

run_cli = cli_runner(timeout=600, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[exclude]
globs = ["tests/**"]

[[lane]]
name = "py"
command = "python -m pytest tests -p no:cacheprovider -p no:randomly --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/py.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/py.xml"
parser = "coveragepy"
scopes = ["calc"]
full_suite = false
env = { COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }
"""
AUDIT = '''

def audit(rows, key):
    """How many rows carry the key, banded by score where they do not."""
    total = 0
    for row in rows:
        if row is None:
            continue
        if key in row:
            total += 1
        elif row.get("score", 0) > 90:
            total += 2
        elif row.get("score", 0) > 80:
            total += 3
        elif row.get("score", 0) > 70:
            total += 4
    return total
'''
READ_ROW = "read_row( line , cols )"
TWO_MORE_BRANCHES = ('    if row.get("score") is None:\n        row["score"] = 0\n',
                     '    if row.get("score") is None:\n        row["score"] = 0\n'
                     '    if row.get("name") is None:\n        row["name"] = ""\n'
                     '    if row.get("bonus") is None:\n        row["bonus"] = 0\n')
RISE_HEAD = "crapkit gate: 1 staged function(s) rose past their ratchet mark:"
RISE_ROW = re.compile(r"^  ccn +(\d+)  (\S+):(\d+)  (.+?)  mark (\d+\.\d{4})$", re.M)
WARN_HEAD = "rose in ccn and may pass their ratchet mark; `crapkit verify` judges them with coverage:"
WARN_ROW = re.compile(r"^  ccn (\S+) -> (\d+)  (\S+):(\d+)  (.+?)  mark (\d+\.\d{4})$", re.M)
BANNER = "banner( bins , total )"
AUDIT_NAME = "audit( rows , key )"
BANNER_AT_7 = ("def banner(bins, total):\n",
               "def banner(bins, total):\n    if total is None or bins is None or not bins:\n        return ''\n")
AUDIT_AT_8 = ('        elif row.get("score", 0) > 70:\n',
              '        elif row.get("score", 0) > 75:\n            total += 5\n'
              '        elif row.get("score", 0) > 70:\n')
IDENTITY = ("-c", "user.email=t@t", "-c", "user.name=t")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def edit(repo: Path, rel: str, old: str, new: str) -> None:
    path = repo / rel
    text = path.read_bytes().decode("utf-8")
    assert old in text, rel
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def _seed(repo: Path) -> None:
    """The demo repo, adopted, scored by its pytest lane and seeded."""
    demo_repo.build(repo)
    (repo / "crapkit.toml").write_bytes(CONFIG.encode("utf-8"))
    (repo / ".gitignore").write_bytes(b".crapkit/\n.coverage\n__pycache__/\n")
    report = repo / "calc" / "report.py"
    report.write_bytes(report.read_bytes() + AUDIT.encode("utf-8"))
    git(repo, "add", "-A")
    git(repo, *IDENTITY, "commit", "-q", "-m", "adopt crapkit")
    for step in (("coverage",), ("ratchet", "seed")):
        done = run_cli(repo, *step)
        assert done.returncode == 0, done.stdout + done.stderr
    git(repo, "add", "crapkit-ratchet.tsv")
    git(repo, *IDENTITY, "commit", "-q", "-m", "record the debt")


@pytest.fixture()
def demo(tmp_path: Path) -> Path:
    return copy_of(template(tmp_path, "demo-seeded", _seed), tmp_path / "repo")


def marks(repo: Path) -> dict[str, str]:
    rows = (line.split("\t") for line in (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()
            if not line.startswith("#"))
    return {row[1]: row[2] for row in rows if row[0] != "path"}


def stage_read_row_at_ccn_8(repo: Path) -> None:
    edit(repo, "calc/parse.py", *TWO_MORE_BRANCHES)
    git(repo, "add", "calc/parse.py")


def regressions(repo: Path) -> dict[str, dict]:
    """verify --json's ratchet_regression items on the committed tree, by long name."""
    done = run_cli(repo, "verify", "--json")
    assert done.returncode in (6, 7), done.stdout + done.stderr
    return {item["long_name"]: item for item in json.loads(done.stdout)["findings"]
            if item["kind"] == "ratchet_regression"}


def test_the_seed_marks_the_probe_where_the_prd_says(demo: Path):
    assert marks(demo)[READ_ROW] == "7.3333"
    assert marks(demo)["audit( rows , key )"] == "56.0000"


def test_read_row_staged_at_ccn_8_is_refused_by_the_hook_and_by_rescore(demo: Path):
    stage_read_row_at_ccn_8(demo)

    hook = run_cli(demo, "hook-precommit")
    rescored = run_cli(demo, "rescore", "calc/parse.py", "--gate")

    assert hook.returncode == 6, hook.stdout + hook.stderr
    assert RISE_HEAD in hook.stdout, hook.stdout
    assert RISE_ROW.findall(hook.stdout) == [("8", "calc/parse.py", "16", READ_ROW, "7.3333")]
    assert hook.stdout.isascii()
    assert rescored.returncode == 6, rescored.stdout + rescored.stderr


def test_the_refused_rise_is_the_ratchet_regression_verify_reports(demo: Path):
    stage_read_row_at_ccn_8(demo)
    assert run_cli(demo, "hook-precommit").returncode == 6
    git(demo, *IDENTITY, "commit", "-q", "--no-verify", "-m", "past the hook")

    found = regressions(demo)

    assert found[READ_ROW]["recorded"] == 7.3333
    assert found[READ_ROW]["fresh_crap"] > 7.3333


def test_git_commit_runs_the_refusal_and_keeps_head(demo: Path):
    """The hook as git runs it: a pre-commit script, the commit refused, HEAD
    where it was. git sends a hook's stdout to its own stderr."""
    hook = demo / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" -m crapkit hook-precommit\n'.encode())
    hook.chmod(0o755)
    before = git(demo, "rev-parse", "HEAD")
    stage_read_row_at_ccn_8(demo)

    committed = subprocess.run(["git", *IDENTITY, "commit", "-q", "-m", "read more"], cwd=demo,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=child_env({"CRAPKIT_OVERRIDE_REASON": None}))

    assert committed.returncode != 0, committed.stdout + committed.stderr
    assert RISE_HEAD in committed.stderr, committed.stderr
    assert git(demo, "rev-parse", "HEAD") == before


def test_every_refused_rise_is_a_regression_and_no_high_end_pardon_is_one(demo: Path):
    """read_row at ccn 8 (a rise), audit under a comment (its range 7 to 56
    sits at its mark of 56: pardoned) and banner from ccn 4 to 7 against its
    mark of 20 (the range 7 to 56 straddles it, and ccn rose): the hook refuses
    the rise, warns about banner, counts audit, and verify on the committed
    tree agrees on every one."""
    edit(demo, "calc/parse.py", *TWO_MORE_BRANCHES)
    edit(demo, "calc/report.py", "    total = 0\n    for row in rows:\n",
         "    total = 0\n    # each row once\n    for row in rows:\n")
    edit(demo, "calc/report.py", *BANNER_AT_7)
    git(demo, "add", "-A")

    hook = run_cli(demo, "hook-precommit")
    refused = {row[3] for row in RISE_ROW.findall(hook.stdout)}
    warned = {row[4] for row in WARN_ROW.findall(hook.stderr)}
    git(demo, *IDENTITY, "commit", "-q", "--no-verify", "-m", "three edits")
    found = regressions(demo)

    assert hook.returncode == 6, hook.stdout + hook.stderr
    assert refused == {READ_ROW}
    assert WARN_ROW.findall(hook.stderr) == [("4", "7", "calc/report.py", "26", BANNER, "20.0000")], hook.stderr
    assert "crapkit gate: 1 staged function(s) carry a ratchet mark" in hook.stderr, hook.stderr
    assert refused | warned <= set(found), found
    assert AUDIT_NAME not in found, found


def test_every_warned_function_is_a_regression_and_every_unrefused_regression_was_warned(demo: Path):
    """audit from ccn 7 to 8 under its mark of 56 and banner from 4 to 7 under
    its mark of 20, both seeded at coverage 0: each range straddles its mark
    and each ccn rose. The hook warns about both and exits 0; verify on the
    committed tree reports exactly those two."""
    edit(demo, "calc/report.py", *AUDIT_AT_8)
    edit(demo, "calc/report.py", *BANNER_AT_7)
    git(demo, "add", "-A")

    hook = run_cli(demo, "hook-precommit")
    warned = {row[4] for row in WARN_ROW.findall(hook.stderr)}
    git(demo, *IDENTITY, "commit", "-q", "--no-verify", "-m", "two rises under their marks")
    found = regressions(demo)

    assert hook.returncode == 0, hook.stdout + hook.stderr
    assert f"crapkit gate: 2 staged marked function(s) {WARN_HEAD}" in hook.stderr, hook.stderr
    assert warned == {AUDIT_NAME, BANNER}, hook.stderr
    assert set(found) == warned, found
    assert (found[AUDIT_NAME]["recorded"], found[BANNER]["recorded"]) == (56.0, 20.0), found


def test_git_commit_prints_the_warn_and_records_the_commit(demo: Path):
    """The warn as git runs the hook: the commit goes through, and git passes
    the hook's stderr on."""
    hook = demo / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" -m crapkit hook-precommit\n'.encode())
    hook.chmod(0o755)
    before = git(demo, "rev-parse", "HEAD")
    edit(demo, "calc/report.py", *BANNER_AT_7)
    git(demo, "add", "-A")

    committed = subprocess.run(["git", *IDENTITY, "commit", "-q", "-m", "banner checks its input"], cwd=demo,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=child_env({"CRAPKIT_OVERRIDE_REASON": None}))

    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert f"crapkit gate: 1 staged marked function(s) {WARN_HEAD}" in committed.stderr, committed.stderr
    assert "  ccn 4 -> 7  calc/report.py:26  banner( bins , total )  mark 20.0000" in committed.stderr
    assert git(demo, "rev-parse", "HEAD") != before
