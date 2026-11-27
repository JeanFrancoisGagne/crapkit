"""A re-keyed or moved mark keeps its age, and nobody repaid it.

A mark committed 400 days ago is re-keyed 30 days ago: the function gained a
parameter, and the same 7.0000 now sits under its new key. ratchet report read
that commit as one repayment and one new mark 30 days old, so an age limit of
12 months passed and a repayment quota of one a month passed too. Every
reader of the history (the CLI's text and --json, --enforce, and the MCP
get_ratchet_report tool) now reads the re-key as one move: 400 days, nothing
repaid. A `ratchet move` of the file's marks to a new path reads the same way.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import cli_runner

_run = cli_runner(timeout=120, encoding="utf-8", errors="replace")

DAY = 86400
MARKS = "crapkit-ratchet.tsv"
OLD, NEW = "classify( a , b )", "classify( a , b , c = None )"
TOP = "# crapkit-analysis=13 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
CONFIG = ('[crapkit]\ntarget = 6\n{policy}\n'
          '[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n')
AGE_LIMIT = "debt_max_age_months = 12\n"
QUOTA = "repayment_min_per_30d = 1\n"
EXPIRED = f"mark {NEW} in calc/grade.py is 400d old (limit 360d)"
STALLED = "repayment stalled: 0 mark(s) repaid in 30d (policy wants 1)"


def _git(repo: Path, *args: str, days_ago: int = 0) -> None:
    """One git call, author and committer dated `days_ago`: an age counts
    from the commit that brought the mark in."""
    stamp = f"@{int(time.time()) - days_ago * DAY} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "user.email=t@t", "-c",
                    "user.name=t", *args], cwd=repo, env=env, check=True, capture_output=True)


def _commit(repo: Path, marks: str, days_ago: int, message: str) -> None:
    """The marks file committed as `marks`, dated `days_ago`."""
    (repo / MARKS).write_bytes((TOP + marks).encode("utf-8"))
    _git(repo, "add", MARKS)
    _git(repo, "commit", "-q", "-m", message, days_ago=days_ago)


def _configure(repo: Path, policy: str = "") -> None:
    (repo / "crapkit.toml").write_text(CONFIG.format(policy=policy), encoding="utf-8")


@pytest.fixture
def rekeyed(tmp_path: Path) -> Path:
    """The report probe: seeded 400 days ago, re-keyed 30 days ago, a twin added today."""
    repo = tmp_path / "rekeyed"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit(repo, f"calc/grade.py\t{OLD}\t7.0000\n", 400, "seed classify")
    _commit(repo, f"calc/grade.py\t{NEW}\t7.0000\n", 30, "re-key classify")
    _commit(repo, f"calc/grade.py\t{NEW}\t7.0000\ncalc/grade.py\t{NEW}#2\t9.0000\n", 0,
            "a second classify")
    _configure(repo)
    return repo


def _rpc(msg_id: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})


def _mcp_report(repo: Path) -> dict:
    frames = "\n".join([
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
        _rpc(2, "tools/call", {"name": "get_ratchet_report", "arguments": {}})]) + "\n"
    res = _run(repo, "mcp", "--repo", str(repo), stdin=frames)
    assert res.returncode == 0, res.stderr
    call = {m["id"]: m for m in map(json.loads, res.stdout.strip().splitlines())}[2]["result"]
    assert call["isError"] is False, call
    return call["structuredContent"]


def _ages(report: dict) -> dict:
    return {(row["path"], row["long_name"]): row["age_days"] for row in report["oldest"]}


def test_the_cli_and_the_mcp_tool_age_the_rekeyed_mark_from_its_seed(rekeyed: Path):
    text = _run(rekeyed, "ratchet", "report")
    as_json = _run(rekeyed, "ratchet", "report", "--json")

    assert text.returncode == 0, text.stdout + text.stderr
    assert text.stdout.startswith("ratchet burn-down: 2 open mark(s), 0 repaid"), text.stdout
    assert f"  400d  calc/grade.py  {NEW}\n" in text.stdout, text.stdout
    for report in (json.loads(as_json.stdout), _mcp_report(rekeyed)):
        assert _ages(report) == {("calc/grade.py", NEW): 400, ("calc/grade.py", f"{NEW}#2"): 0}
        assert (report["dropped_total"], report["uncommitted"]) == (0, 0), report


def test_enforce_fails_the_age_limit_and_the_quota_the_rekey_used_to_pass(rekeyed: Path):
    _configure(rekeyed, AGE_LIMIT + QUOTA)

    text = _run(rekeyed, "ratchet", "report", "--enforce")
    as_json = _run(rekeyed, "ratchet", "report", "--enforce", "--json")

    assert (text.returncode, as_json.returncode) == (1, 1), text.stdout + text.stderr
    assert f"  POLICY {EXPIRED}\n" in text.stdout and f"  POLICY {STALLED}\n" in text.stdout
    assert json.loads(as_json.stdout)["policy_violations"] == [EXPIRED, STALLED]


@pytest.mark.parametrize("committed", [True, False], ids=["committed", "on-disk"])
def test_a_ratchet_move_to_a_new_path_keeps_every_age(rekeyed: Path, committed: bool):
    """`ratchet move` takes both marks to calc/grading.py; committed or still
    on disk, neither restarts its age and nothing counts as repaid."""
    moved = _run(rekeyed, "ratchet", "move", "calc/grade.py", "calc/grading.py")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    if committed:
        _commit(rekeyed, (rekeyed / MARKS).read_text(encoding="utf-8")[len(TOP):], 0, "move")

    report = json.loads(_run(rekeyed, "ratchet", "report", "--json").stdout)

    assert _ages(report) == {("calc/grading.py", NEW): 400, ("calc/grading.py", f"{NEW}#2"): 0}
    assert (report["dropped_total"], report["uncommitted"]) == (0, 0 if committed else 2)


TS_CONFIG = ('[crapkit]\ntarget = 6\n\n'
             '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
             '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
             'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')
MAKE_COV = ("import json, os\n"
            "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
            "cov = {app: {'path': app,\n"
            "             'fnMap': {'0': {'name': 'tiny', 'decl': {'start': {'line': 1}},\n"
            "                             'loc': {'start': {'line': 1}, 'end': {'line': 1}}}},\n"
            "             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}\n"
            "json.dump(cov, open('cov.json', 'w'))\n")
TANGLED = ("export function tangled(a: number, b: number): number {\n"
           "  let r = 0;\n"
           "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
           "  if (a > 10 && b > 10) { r += 3; }\n"
           "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
           "  return r;\n}\n")


def test_prune_following_a_git_mv_keeps_the_age_and_repays_nothing(tmp_path: Path):
    """The mark was seeded and committed 400 days ago; `git mv` renames its
    file and `ratchet prune` follows the rename. The commit that holds the
    pruned marks moves the mark, and nobody repaid it."""
    repo = tmp_path / "renamed"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.ts").write_text("export function tiny(a: number) { return a; }\n",
                                         encoding="utf-8")
    (repo / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (repo / "crapkit.toml").write_text(TS_CONFIG, encoding="utf-8")
    (repo / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init", days_ago=401)
    assert _run(repo, "coverage", "--json").returncode == 0
    assert _run(repo, "ratchet", "seed").returncode == 0
    _git(repo, "add", MARKS)
    _git(repo, "commit", "-q", "-m", "seed", days_ago=400)
    _git(repo, "mv", "src/tangled.ts", "src/knots.ts")
    _git(repo, "commit", "-q", "-m", "rename tangled")
    assert _run(repo, "coverage", "--json").returncode == 0
    pruned = _run(repo, "ratchet", "prune")
    assert pruned.returncode == 0, pruned.stdout + pruned.stderr
    _git(repo, "add", MARKS)
    _git(repo, "commit", "-q", "-m", "prune follows the rename")

    report = json.loads(_run(repo, "ratchet", "report", "--json").stdout)

    assert [(row["path"], row["age_days"]) for row in report["oldest"]] == [("src/knots.ts", 400)]
    assert report["dropped_total"] == 0


def test_a_shallow_clone_still_refuses_enforce_and_names_the_cut_history(rekeyed: Path,
                                                                        tmp_path: Path):
    """A depth-1 clone holds the newest commit alone, so the age it can count
    is 0: --enforce refuses to judge it (exit 4), and the report says shallow."""
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", rekeyed.as_uri(), str(shallow)],
                   check=True, capture_output=True)
    _configure(shallow, AGE_LIMIT)

    refused = _run(shallow, "ratchet", "report", "--enforce")
    plain = _run(shallow, "ratchet", "report", "--json")

    assert refused.returncode == 4, refused.stdout + refused.stderr
    assert plain.returncode == 0 and json.loads(plain.stdout)["shallow"] is True
    assert len([line for line in plain.stderr.splitlines() if "shallow" in line]) == 1, plain.stderr
