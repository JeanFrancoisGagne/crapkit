"""A number past its documented bound stops crapkit before it is stored or printed.

Each test injects one wrong value by patching the crapkit function upstream of
a check (crapkit.invariants), runs the command in this process
(tests/e2e/cli_in_process.py), and reads what a user and an agent read: exit
5, the `crapkit: stopped: an internal check failed.` line, the --json error
object with kind `internal`, a store whose run count did not move, and a marks
file whose bytes did not change. The print-only sites (the gate verdict, the
hook, the packet, the coverage summary) print no verdict line. claude-hook is
the documented exception: every internal failure there exits 0 in silence
(README, `claude-hook`). The MCP case spawns, so its patch rides in on a
sitecustomize module on PYTHONPATH.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import cli_runner

PY = sys.executable
GEN = "gen_cov.py"
STOP = "crapkit: stopped: an internal check failed."

# A hermetic istanbul generator: every statement of each named source ran,
# except in a source named with a leading `0:`, where none did.
GEN_COV = """\
import json
import os
import sys

artifact, sources = sys.argv[1], sys.argv[2:]
out = {}
for spec in sources:
    cold = spec.startswith("0:")
    rel = spec[2:] if cold else spec
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
        f_hits[str(n)] = 0 if cold else 1
    stmt_map, s_hits = {}, {}
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.startswith("def "):
            continue
        stmt_map[str(i)] = {"start": {"line": i}, "end": {"line": i}}
        s_hits[str(i)] = 0 if cold else 1
    out[key] = {"path": key, "fnMap": fn_map, "f": f_hits, "branchMap": {}, "b": {},
                "statementMap": stmt_map, "s": s_hits}
os.makedirs(os.path.dirname(artifact) or ".", exist_ok=True)
with open(artifact, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""


def decisions(name: str, count: int) -> str:
    """`count` decisions, so ccn is one more."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, count + 1))
    return f"def {name}(n):\n{body}    return n\n"


# calc.py: alpha at ccn 2 and tangled at ccn 8, every line covered, so CRAP is
# ccn: 2 is ok and 8 is decompose against the target of 6. cold.py: cold at
# ccn 3 with nothing covered, CRAP 9 + 3 = 12, add-tests.
CALC = decisions("alpha", 1) + "\n\n" + decisions("tangled", 7)
COLD = decisions("cold", 2)
CONFIG = ("[crapkit]\ntarget = 6\n\n"
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
          f"[[lane]]\nname = \"unit\"\ncommand = '\"{PY}\" {GEN} cov/unit.json src/calc.py 0:src/cold.py'\n"
          'artifact = "cov/unit.json"\nparser = "istanbul"\nscopes = ["src"]\n')

run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **({"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {})}
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          check=True, capture_output=True, text=True, encoding="utf-8", env=env)
    return done.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, message: str, date: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, date=date)


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    """Two dated commits, one scored run, seeded marks for tangled (8.0) and
    cold (12.0), and the marks committed."""
    repo = tmp_path_factory.mktemp("guards") / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    write(repo, ".gitignore", ".crapkit/\ncov/\n__pycache__/\n")
    write(repo, GEN, GEN_COV)
    write(repo, "crapkit.toml", CONFIG)
    write(repo, "src/calc.py", CALC.replace("n + 1", "n + 3", 1))
    write(repo, "src/cold.py", COLD)
    commit(repo, "first", "2026-01-05T10:00:00Z")
    write(repo, "src/calc.py", CALC)
    commit(repo, "second", "2026-03-05T10:00:00Z")
    for argv in (("coverage", "--json"), ("ratchet", "seed")):
        done = run_cli(repo, *argv)
        assert done.returncode == 0, done.stdout + done.stderr
    commit(repo, "marks", "2026-03-05T11:00:00Z")
    return repo


@pytest.fixture()
def repo(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "repo"))


def runs(repo: Path) -> list[tuple]:
    """(id, kind, verdict_ok) per stored run, read with sqlite3."""
    uri = (repo / ".crapkit" / "crap.sqlite").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        return db.execute("SELECT id, kind, verdict_ok FROM runs ORDER BY id").fetchall()


def marks(repo: Path) -> bytes:
    return (repo / "crapkit-ratchet.tsv").read_bytes()


def stopped(done, check: str) -> None:
    """Exit 5 and the refusal, naming `check`, on stderr."""
    assert done.returncode == 5, done.stdout + done.stderr
    lines = done.stderr.splitlines()
    assert lines.count(STOP) == 1, done.stderr
    assert lines[lines.index(STOP) + 1] == f"  check: {check}", done.stderr
    assert "This is a crapkit bug, not a problem in your repo." in done.stderr


def error_object(done) -> dict:
    """The --json promise: one error object on stdout and nothing else."""
    payload = json.loads(done.stdout)
    assert payload["error"]["exit"] == 5 and payload["error"]["kind"] == "internal", payload
    assert payload["error"]["message"].startswith("stopped: an internal check failed.")
    return payload


def unchanged(repo: Path, before_runs: list, before_marks: bytes) -> None:
    assert runs(repo) == before_runs
    assert marks(repo) == before_marks


# --- the writes -------------------------------------------------------------------------


def test_every_command_passes_every_check_on_the_untouched_repo(repo: Path):
    """No false alarm: the same commands the tests below break all answer here."""
    write(repo, "src/calc.py", CALC.replace("return n\n", "n = n * 2\n    return n\n", 1))
    for argv, code in [(("coverage", "--json"), 0), (("worklist", "--json"), 0),
                       (("trend", "--json"), 0), (("digest",), 0), (("next-item",), 0),
                       (("brief", "src/calc.py", "tangled", "--json"), 0),
                       (("rescore", "--gate", "--json", "src/calc.py"), 0),
                       (("ratchet", "prune"), 0), (("verify", "--json"), 0)]:
        done = run_cli(repo, *argv)
        assert done.returncode == code, (argv, done.stdout, done.stderr)
        assert STOP not in done.stderr


def test_a_record_read_at_negative_nesting_stops_the_run(repo: Path, monkeypatch):
    from crapkit import analyze

    before = runs(repo), marks(repo)
    write(repo, "src/calc.py", CALC + "\n\n" + decisions("fresh", 1))  # a cache miss
    monkeypatch.setattr(analyze, "_nesting_depth", lambda rel_path, fn: -1)
    done = run_cli(repo, "coverage", "--json")
    stopped(done, "cognitive, nesting, params and occurrence must each be at least 0")
    error_object(done)
    unchanged(repo, *before)


def test_a_crap_below_ccn_stops_the_store_write(repo: Path, monkeypatch):
    from crapkit import score

    before = runs(repo), marks(repo)
    monkeypatch.setattr(score, "crap", lambda ccn, cov: ccn - 0.5)
    done = run_cli(repo, "coverage", "--json")
    stopped(done, "CRAP must lie between ccn and ccn^2+ccn")
    assert "No run was stored and no ratchet mark changed." in error_object(done)["error"]["message"]
    unchanged(repo, *before)


def test_a_remedy_off_the_readme_table_stops_the_store_write(repo: Path, monkeypatch):
    from crapkit import score

    before = runs(repo), marks(repo)
    monkeypatch.setattr(score, "remedy", lambda *args, **kwargs: "ok")
    done = run_cli(repo, "coverage", "--json")
    stopped(done, "remedy must follow the README remedy table at ceiling 6")
    error_object(done)
    unchanged(repo, *before)


def test_a_seed_that_raises_a_mark_stops_before_the_file(repo: Path, monkeypatch):
    from crapkit import ratchet

    def rising(marks_by_key, key, crap):
        old = marks_by_key[key]
        marks_by_key[key] = old._replace(crap=old.crap + 1.0)
        return 0, 1

    before = runs(repo), marks(repo)
    monkeypatch.setattr(ratchet, "_seed_mark", rising)
    stopped(run_cli(repo, "ratchet", "seed"), "a mark never rises")
    unchanged(repo, *before)


def test_a_tighten_that_raises_a_mark_stops_before_the_file(repo: Path, monkeypatch):
    """The one write after the store: verify records its run before it tightens,
    so the stop leaves that run without a verdict, which no reader trusts."""
    from crapkit import ratchet

    before_runs, before_marks = runs(repo), marks(repo)
    monkeypatch.setattr(ratchet, "_updated_mark",
                        lambda entry, row, held, ceiling_of: entry._replace(crap=entry.crap + 1))
    done = run_cli(repo, "verify", "--json")
    stopped(done, "a mark never rises")
    error_object(done)
    assert marks(repo) == before_marks
    assert runs(repo)[:-1] == before_runs and runs(repo)[-1][1:] == ("verify", None)


def test_a_mark_the_file_cannot_hold_stops_the_dump(repo: Path, monkeypatch):
    from crapkit import ratchet

    before = runs(repo), marks(repo)
    monkeypatch.setattr(ratchet, "prune_ratchet",
                        lambda prior, fresh: ([e._replace(crap=e.crap + 0.00001) for e in prior], 0))
    done = run_cli(repo, "ratchet", "prune")
    stopped(done, "a mark must be a finite positive number held at four decimals")
    assert "The marks file was not rewritten." in done.stderr
    unchanged(repo, *before)


def test_a_verdict_whose_exit_breaks_the_precedence_stops_before_the_run(repo: Path, monkeypatch):
    from crapkit.cli import verifying

    before = runs(repo), marks(repo)
    monkeypatch.setattr(verifying, "_verify_exit_code", lambda verdict: 7)
    done = run_cli(repo, "verify", "--json")
    stopped(done, "verify exits 0 by the README precedence 6 > 7 > 8 > 9 > 0, "
                  "and stores ok only with exit 0")
    error_object(done)
    unchanged(repo, *before)


def test_a_verdict_that_breaks_after_the_run_is_stored_leaves_it_unverdicted(repo: Path,
                                                                           monkeypatch):
    from crapkit.cli import verifying

    real, calls = verifying._verify_exit_code, []

    def second_call_lies(verdict):
        calls.append(1)
        return real(verdict) if len(calls) == 1 else 9

    before_runs, before_marks = runs(repo), marks(repo)
    monkeypatch.setattr(verifying, "_verify_exit_code", second_call_lies)
    done = run_cli(repo, "verify", "--json")
    stopped(done, "verify exits 0 by the README precedence 6 > 7 > 8 > 9 > 0, "
                  "and stores ok only with exit 0")
    assert "The run was stored without a verdict" in error_object(done)["error"]["message"]
    assert marks(repo) == before_marks
    assert runs(repo)[:-1] == before_runs and runs(repo)[-1][1:] == ("verify", None)


# --- the prints -------------------------------------------------------------------------


def test_the_coverage_summary_prints_no_grade_the_band_table_refuses(repo: Path, monkeypatch):
    """The summary is computed after the run is stored, so the run stays; the
    line with the grade never prints."""
    from crapkit import score

    before_marks = marks(repo)
    monkeypatch.setattr(score, "grade", lambda over, total: "A+")
    done = run_cli(repo, "coverage", "--json")
    stopped(done, "the grade follows the README band table (F)")
    assert "Nothing was printed as a verdict." in error_object(done)["error"]["message"]
    assert marks(repo) == before_marks


def test_a_scope_grade_off_the_band_table_stops_trend(repo: Path, monkeypatch):
    from crapkit import digest

    before = runs(repo), marks(repo)
    monkeypatch.setattr(digest, "grade", lambda over, total: "A+")
    done = run_cli(repo, "trend", "--json")
    stopped(done, "the grade follows the README band table (F)")
    error_object(done)
    unchanged(repo, *before)


def test_an_over_count_past_the_function_count_stops_the_digest(repo: Path, monkeypatch):
    from crapkit import digest

    assert run_cli(repo, "coverage").returncode == 0  # the digest pairs two runs
    before = runs(repo), marks(repo)
    monkeypatch.setattr(digest, "_over_count", lambda rows, ceiling_of: len(rows) + 1)
    done = run_cli(repo, "digest")
    stopped(done, "0 <= over_target <= functions")
    assert "CRAP load" not in done.stdout
    unchanged(repo, *before)


def test_a_worklist_that_drops_a_row_over_its_ceiling_prints_nothing(repo: Path, monkeypatch):
    from crapkit import worklist

    before = runs(repo), marks(repo)
    monkeypatch.setattr(worklist.Admission, "admits",
                        lambda self, path, ccn, *, over_target: False)
    done = run_cli(repo, "worklist", "--json")
    stopped(done, "every row over its ceiling is admitted to the worklist")
    error_object(done)
    unchanged(repo, *before)


def test_a_worklist_ranked_against_risk_prints_nothing(repo: Path, monkeypatch):
    from crapkit import worklist

    before = runs(repo), marks(repo)
    monkeypatch.setattr(worklist, "_rank_key", lambda e: (e.risk, e.path))
    done = run_cli(repo, "worklist")
    stopped(done, "the worklist ranks by risk, highest first")
    assert done.stdout == ""
    unchanged(repo, *before)


def test_a_commit_weighed_past_one_half_stops_the_worklist(repo: Path, monkeypatch):
    from crapkit import churn

    for cached in (repo / ".crapkit").glob("*churn*"):
        cached.unlink()
    monkeypatch.setattr(churn, "_twr", lambda ts, oldest, newest: 0.9)
    done = run_cli(repo, "worklist", "--json")
    stopped(done, "a commit weighs at most 0.5 unless every commit shares one timestamp")
    error_object(done)


def test_a_packet_remedy_off_today_s_ceiling_stops_the_brief(repo: Path, monkeypatch):
    from crapkit import packet

    monkeypatch.setattr(packet, "remedy", lambda ccn, crap, ceiling: "ok")
    done = run_cli(repo, "brief", "src/calc.py", "tangled", "--json")
    stopped(done, "remedy must follow the README remedy table at ceiling 6")
    error_object(done)


def test_a_budget_off_its_definition_stops_next_item(repo: Path, monkeypatch):
    from crapkit import packet

    monkeypatch.setattr(packet, "round", lambda value: value + 3, raising=False)
    done = run_cli(repo, "next-item")
    stopped(done, "est_uncovered_paths is (1 - cov) * ccn rounded, from 0 to ccn")
    assert done.stdout == ""


def test_the_gate_prints_no_verdict_over_a_row_off_the_remedy_table(repo: Path, monkeypatch):
    from crapkit import score

    write(repo, "src/calc.py", CALC.replace("return n\n", "n = n * 2\n    return n\n", 1))
    monkeypatch.setattr(score, "remedy", lambda *args, **kwargs: "ok")
    done = run_cli(repo, "rescore", "--gate", "--json", "src/calc.py")
    stopped(done, "remedy must follow the README remedy table at ceiling 6")
    error_object(done)
    assert "GATE" not in done.stderr


def test_the_commit_hook_prints_no_verdict_over_a_violation_under_its_ceiling(repo: Path,
                                                                             monkeypatch):
    from crapkit import hook

    write(repo, "src/calc.py", CALC.replace("n = n + 1", "n = n + 5", 1))
    git(repo, "add", "src/calc.py")
    monkeypatch.setattr(hook, "_file_violations", lambda rel, records, ranges, ceiling: [
        hook.Violation(rel, "alpha( n )", 1, 2, "alpha( n )")])
    done = run_cli(repo, "hook-precommit")
    stopped(done, "a gate violation has ccn over its file's ceiling")
    assert "crapkit gate:" not in done.stdout + done.stderr


def test_claude_hook_turns_the_stop_into_its_documented_silence(repo: Path, monkeypatch):
    """README: any internal failure of `claude-hook` exits 0 in silence. The
    stop still keeps a breach list past its bound from printing as advice."""
    from crapkit.cli import claude_hook

    write(repo, "src/calc.py", CALC + "\n\n" + decisions("wild", 8))  # unmarked, ccn 9
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "cwd": str(repo),
                          "tool_input": {"file_path": str(repo / "src" / "calc.py")}})
    advised = run_cli(repo, "claude-hook", "--protocol", "1", stdin=payload)
    assert advised.returncode == 2 and "crapkit advisory:" in advised.stderr, advised.stderr
    monkeypatch.setattr(claude_hook, "_breaches", lambda records, ranges, ceiling: records)
    monkeypatch.setattr(claude_hook, "_marks_for", lambda *args, **kwargs: set())
    silent = run_cli(repo, "claude-hook", "--protocol", "1", stdin=payload)
    assert (silent.returncode, silent.stdout, silent.stderr) == (0, "", "")


# --- MCP --------------------------------------------------------------------------------


def test_mcp_answers_the_stop_as_a_tool_error_of_kind_internal(repo: Path, tmp_path: Path):
    """The server runs each tool as a CLI child, so the patch goes in through a
    sitecustomize module every child imports at start."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import crapkit.digest\ncrapkit.digest.grade = lambda over, total: 'A+'\n",
        encoding="utf-8")
    path = os.pathsep.join(filter(None, (str(shim), os.environ.get("PYTHONPATH"))))
    frames = "\n".join(json.dumps(frame) for frame in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "get_trend", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "list_worklist", "arguments": {}}},
    ]) + "\n"
    served = run_cli(repo, "mcp", "--repo", str(repo), stdin=frames,
                     env_extra={"PYTHONPATH": path, "CRAPKIT_OVERRIDE_REASON": None})
    replies = {m["id"]: m["result"] for m in map(json.loads, served.stdout.strip().splitlines())}
    trend = replies[2]
    assert trend["isError"] is True and "structuredContent" not in trend, trend
    error = json.loads(trend["content"][0]["text"])["error"]
    assert (error["exit"], error["kind"]) == (5, "internal"), error
    assert error["message"].startswith("stopped: an internal check failed.")
    assert replies[3]["isError"] is False, "the server keeps answering, and a clean tool answers"
