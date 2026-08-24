"""End-to-end for the read-side commands nobody had exercised: overrides, trend,
the plain worklist table, explain's ratchet mark, `ratchet report`, and verify's
uncovered-changed-line warning.

Every assertion goes through the CLI on a repo built here in tmp_path: real git,
a real istanbul lane (a python script standing in for vitest --coverage), real
exit codes. The ratchet history commits at fixed unix timestamps so the burn-down
ages are exact instead of wall-clock dependent.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Line numbers are load-bearing: the lane artifact below marks line 4 dead, and
# the diff-coverage test edits exactly that line.
APP_TS = """export function plain(x: number): number {
  const a = x + 1;
  const b = a + 2;
  const c = b + 3;
  return a + b + c;
}

export function pick(kind: string): number {
  if (kind === "a") { return 1; }
  if (kind === "b") { return 2; }
  return 0;
}
"""

# ccn 9, so the pre-commit gate refuses it and the audited override has something
# to grant.
MESSY_TS = """export function messy(n: number): number {
  let t = 0;
  if (n > 1) { t += 1; }
  if (n > 2) { t += 1; }
  if (n > 3) { t += 1; }
  if (n > 4) { t += 1; }
  if (n > 5) { t += 1; }
  if (n > 6) { t += 1; }
  if (n > 7) { t += 1; }
  if (n > 8) { t += 1; }
  return t;
}
"""

LANE_SCRIPT = '''"""Stand-in for vitest --coverage: writes an istanbul artifact for src/app.ts.

statementMap entry "2" (line 4) never ran: that is the dead statement the
diff-coverage warning has to find.
"""
import json
from pathlib import Path

root = Path.cwd().resolve()
app = str(root / "src" / "app.ts")
artifact = {
    app: {
        "path": app,
        "fnMap": {
            "0": {"name": "plain", "decl": {"start": {"line": 1}},
                  "loc": {"start": {"line": 1}, "end": {"line": 6}}},
            "1": {"name": "pick", "decl": {"start": {"line": 8}},
                  "loc": {"start": {"line": 8}, "end": {"line": 12}}},
        },
        "f": {"0": 2, "1": 2},
        "branchMap": {
            "0": {"loc": {"start": {"line": 9}}},
            "1": {"loc": {"start": {"line": 10}}},
        },
        "b": {"0": [1, 1], "1": [1, 0]},
        "statementMap": {
            "0": {"start": {"line": 2}}, "1": {"start": {"line": 3}},
            "2": {"start": {"line": 4}}, "3": {"start": {"line": 5}},
            "4": {"start": {"line": 9}}, "5": {"start": {"line": 10}},
            "6": {"start": {"line": 11}},
        },
        "s": {"0": 1, "1": 1, "2": 0, "3": 1, "4": 1, "5": 1, "6": 1},
    }
}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump(artifact, fh)
'''

ALERT_SCRIPT = '''"""The override's human channel: append the alert line to alerts.txt."""
import sys

line = sys.stdin.read()
with open("alerts.txt", "a", encoding="utf-8") as fh:
    fh.write(line)
'''

TOML = (
    '[crapkit]\ntarget = 6\nworklist_floor = 1\nchurn_window_months = 12\n'
    'alert_command = "python alert.py"\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
    'parser = "istanbul"\nscopes = ["src"]\n'
)

RATCHET = "crapkit-ratchet.tsv"
HEADER = "path\tlong_name\tcrap"
DAY = 86400
T_SEEDED = 1700000000
T_REPAID = T_SEEDED + 40 * DAY


def write(path: Path, text: str) -> None:
    """LF always: a CRLF artifact would move git's line numbers off the fixture's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def run_cli(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    # the CLI reconfigures piped streams to UTF-8; decode them the same way
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, **(env or {})})


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def head(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def commit(repo: Path, message: str, when: int | None = None) -> None:
    git(repo, "add", "-A")
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = f"@{when} +0000"
        env["GIT_COMMITTER_DATE"] = f"@{when} +0000"
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message],
                   cwd=repo, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    write(root / "src" / "app.ts", APP_TS)
    write(root / "lane.py", LANE_SCRIPT)
    write(root / "alert.py", ALERT_SCRIPT)
    write(root / "crapkit.toml", TOML)
    write(root / ".gitignore", ".crapkit/\ncov.json\ninv.tsv\nalerts.txt\n__pycache__/\n")
    git(root, "init", "-q", "-b", "main")
    commit(root, "init")
    return root


@pytest.fixture()
def scored_repo(repo: Path) -> Path:
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


@pytest.fixture()
def inventoried_repo(repo: Path) -> Path:
    res = run_cli(repo, "inventory", "--export", "inv.tsv")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


def long_name(repo: Path, needle: str) -> str:
    """The exact long_name crapkit recorded, read back from its own TSV export."""
    for row in (repo / "inv.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        cols = row.split("\t")
        if cols[1] == "src/app.ts" and needle in cols[2]:
            return cols[2]
    raise AssertionError(f"no function matching {needle!r} in the inventory export")


def marks_file(repo: Path, rows: list[tuple[str, str, str]]) -> None:
    body = "\n".join("\t".join(r) for r in rows)
    write(repo / RATCHET, f"{HEADER}\n{body}\n")


# --- overrides -------------------------------------------------------------

def test_overrides_prints_an_empty_trail_when_nothing_was_ever_granted(inventoried_repo: Path):
    plain = run_cli(inventoried_repo, "overrides")
    assert plain.returncode == 0, plain.stderr
    assert plain.stdout.strip() == ""

    as_json = run_cli(inventoried_repo, "overrides", "--json")
    assert as_json.returncode == 0, as_json.stderr
    assert json.loads(as_json.stdout) == {"overrides": [], "schema": 1}


def test_overrides_lists_a_granted_override_in_both_formats(repo: Path):
    write(repo / "src" / "messy.ts", MESSY_TS)
    git(repo, "add", "src/messy.ts")

    hook = run_cli(repo, "hook-precommit", env={"CRAPKIT_OVERRIDE_REASON": "cutover deadline"})
    assert hook.returncode == 0, hook.stdout + hook.stderr
    assert "override granted with full audit (cutover deadline)" in hook.stdout
    assert "crapkit OVERRIDE (cutover deadline)" in (repo / "alerts.txt").read_text(encoding="utf-8")

    as_json = run_cli(repo, "overrides", "--json")
    assert as_json.returncode == 0, as_json.stderr
    trail = json.loads(as_json.stdout)["overrides"]
    assert len(trail) == 1
    entry = trail[0]
    assert entry["path"] == "src/messy.ts"
    assert "messy" in entry["function"]
    assert entry["reason"] == "cutover deadline"
    assert entry["commit"] == head(repo)

    plain = run_cli(repo, "overrides")
    assert plain.returncode == 0, plain.stderr
    line = plain.stdout.strip()
    assert line.startswith(f"run {entry['run_id']:>3} @ {entry['commit'][:11]}")
    assert f"crap {entry['crap']:.1f}" in line
    assert "src/messy.ts" in line
    assert "(cutover deadline)" in line


def test_a_store_holding_only_an_override_is_not_a_worklist(repo: Path):
    """The audited hook override writes .crapkit/crap.sqlite before any scoring
    run exists, so the store check that guards worklist passes — and the one run
    in there is a hook run, which carries no rows at all. An empty ranking would
    read as a repo with no debt, so the message has to name both ways out."""
    write(repo / "src" / "messy.ts", MESSY_TS)
    git(repo, "add", "src/messy.ts")
    hook = run_cli(repo, "hook-precommit", env={"CRAPKIT_OVERRIDE_REASON": "cutover deadline"})
    assert hook.returncode == 0, hook.stdout + hook.stderr

    res = run_cli(repo, "worklist")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "crapkit coverage" in res.stderr
    assert "crapkit inventory" in res.stderr, "complexity-only ranking is the other route"
    assert "Traceback" not in res.stderr


# --- trend -----------------------------------------------------------------

def test_trend_prints_one_plain_line_per_scored_run(scored_repo: Path):
    second = run_cli(scored_repo, "coverage", "--json")
    assert second.returncode == 0, second.stderr

    as_json = run_cli(scored_repo, "trend", "--json")
    assert as_json.returncode == 0, as_json.stderr
    runs = json.loads(as_json.stdout)["runs"]
    assert len(runs) == 2

    plain = run_cli(scored_repo, "trend")
    assert plain.returncode == 0, plain.stderr
    lines = plain.stdout.splitlines()
    assert len(lines) == 2
    for line, run in zip(lines, runs):
        assert line.startswith(f"run {run['run_id']:>3} @ {run['commit'][:11]}")
        assert f"{run['over_target']} over target" in line
        assert f"load {run['crap_load']}" in line
        assert f"avg {run['avg']}" in line


def test_trend_refuses_a_repo_with_no_snapshot(repo: Path):
    assert not (repo / ".crapkit" / "crap.sqlite").exists()

    res = run_cli(repo, "trend")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "no snapshot in" in res.stderr
    assert res.stdout == ""


# --- worklist --------------------------------------------------------------

def test_worklist_prints_the_plain_table(inventoried_repo: Path):
    as_json = run_cli(inventoried_repo, "worklist", "--json")
    assert as_json.returncode == 0, as_json.stderr
    active = json.loads(as_json.stdout)["active"]
    assert len(active) == 2, "floor ccn>=1 admits both functions, and the file has churn"

    plain = run_cli(inventoried_repo, "worklist")
    assert plain.returncode == 0, plain.stderr
    lines = plain.stdout.splitlines()
    assert lines[0].startswith(f"worklist @ {head(inventoried_repo)[:11]}")
    assert "floor ccn>=1, churn 12mo" in lines[0]
    assert "2 active, 0 dormant" in lines[0]
    assert len(lines) == 3
    for line, entry in zip(lines[1:], active):
        assert f"ccn {entry['ccn']:>3}" in line
        assert f"{entry['path']}:{entry['start']}" in line
        assert entry["function"] in line
    assert "warning:" not in plain.stderr


def test_worklist_plain_table_warns_when_the_snapshot_predates_head(inventoried_repo: Path):
    write(inventoried_repo / "src" / "app.ts", APP_TS + "\nexport const tail = 1;\n")
    commit(inventoried_repo, "move head past the snapshot")

    plain = run_cli(inventoried_repo, "worklist")
    assert plain.returncode == 0, plain.stderr
    assert "HEAD has moved on" in plain.stderr
    assert plain.stdout.splitlines()[0].startswith("worklist @ ")


# --- explain: the ratchet mark line ---------------------------------------

def test_explain_says_no_ratchet_file_when_the_marks_file_is_absent(inventoried_repo: Path):
    assert not (inventoried_repo / RATCHET).exists()

    res = run_cli(inventoried_repo, "explain", "src/app.ts", "plain")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "  mark: no ratchet file" in res.stdout


def test_explain_says_none_when_the_function_carries_no_mark(inventoried_repo: Path):
    marks_file(inventoried_repo, [("src/other.ts", "elsewhere()", "12.5000")])

    res = run_cli(inventoried_repo, "explain", "src/app.ts", "plain")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "  mark: none (below target or never marked)" in res.stdout


def test_explain_prints_the_committed_high_water_mark(inventoried_repo: Path):
    marked = long_name(inventoried_repo, "plain")
    marks_file(inventoried_repo, [("src/app.ts", marked, "12.5")])

    res = run_cli(inventoried_repo, "explain", "src/app.ts", "plain")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "  mark: 12.5000 (committed high-water mark)" in res.stdout
    assert f"src/app.ts  {marked}" in res.stdout


# --- ratchet report --------------------------------------------------------

def test_ratchet_report_plain_counts_open_and_repaid_marks(repo: Path):
    marks_file(repo, [("src/app.ts", "plain()", "7.5000"),
                      ("src/app.ts", "zz()", "8.5000")])
    commit(repo, "seed two marks", when=T_SEEDED)
    marks_file(repo, [("src/app.ts", "plain()", "7.5000")])
    commit(repo, "repay one mark", when=T_REPAID)

    as_json = run_cli(repo, "ratchet", "report", "--json")
    assert as_json.returncode == 0, as_json.stderr
    report = json.loads(as_json.stdout)
    assert (report["open"], report["dropped_total"]) == (1, 1)

    plain = run_cli(repo, "ratchet", "report")
    assert plain.returncode == 0, plain.stderr
    lines = plain.stdout.splitlines()
    assert lines[0] == ("ratchet burn-down: 1 open mark(s), 1 repaid "
                        "(1 in the last 30d, 1 in 90d)")
    # the surviving mark entered 40 days before the newest commit in the file's history
    assert lines[1] == "     40d  src/app.ts  plain()"
    assert len(lines) == 2


def test_ratchet_report_json_says_null_when_no_policy_was_evaluated(repo: Path):
    marks_file(repo, [("src/app.ts", "plain()", "7.5000")])
    commit(repo, "seed one mark", when=T_SEEDED)

    plain = run_cli(repo, "ratchet", "report", "--json")
    assert plain.returncode == 0, plain.stderr
    assert json.loads(plain.stdout)["policy_violations"] is None, \
        "nothing judged the debt, and [] would read as a clean policy"

    enforced = run_cli(repo, "ratchet", "report", "--json", "--enforce")
    assert enforced.returncode == 0, enforced.stderr
    assert json.loads(enforced.stdout)["policy_violations"] is None, \
        "the config declares no debt policy for --enforce to run"


def test_ratchet_report_json_says_empty_when_a_policy_ran_clean(repo: Path):
    write(repo / "crapkit.toml", TOML.replace("target = 6", "target = 6\ndebt_max_age_months = 12"))
    marks_file(repo, [("src/app.ts", "plain()", "7.5000")])
    commit(repo, "seed one mark", when=T_SEEDED)

    res = run_cli(repo, "ratchet", "report", "--json", "--enforce")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["policy_violations"] == [], \
        "a policy ran against a 0d mark and found nothing"


# --- verify: dead lines inside the diff ------------------------------------

def test_verify_says_nothing_about_coverage_when_no_line_changed(scored_repo: Path):
    res = run_cli(scored_repo, "verify")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "no coverage" not in res.stderr
    assert "uncovered" not in res.stderr


def test_verify_warns_about_a_changed_line_that_never_ran(scored_repo: Path):
    app = scored_repo / "src" / "app.ts"
    write(app, APP_TS.replace("  const c = b + 3;", "  const c = b + 30;"))

    res = run_cli(scored_repo, "verify", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "warning: 1 changed line(s) have no coverage" in res.stderr
    assert "  uncovered src/app.ts:4" in res.stderr
    out = json.loads(res.stdout)
    assert out["diff_uncovered"] == [{"path": "src/app.ts", "line": 4}]
    assert out["diff_uncovered_count"] == 1
