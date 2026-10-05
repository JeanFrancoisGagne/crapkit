"""Class U: a file a scope takes whose name is not UTF-8 stops verify, under every harness.

The class U fixture holds a file a scope takes whose name is not UTF-8, and
it runs fresh and upgrade under each harness tools/deploy/pins.toml pins, in
that harness's image. verify judges the name through the gate module, which
refuses it before it judges anything (gate-group-06), so verify stops before
any lane runs and prints the line the 0.8.1 tree printed.

verify is CLI behavior the harness carries through its shell, so the cells
form every-harness rows with nightly = "core": a nightly run keeps the 3
core-image harnesses and the release candidate runs all 17 (MAP.toml
[every_harness]).

Each cell measures a repo whose lane appends to runs.log, commits
`src/caf\\xe9.py` under scope `src`, puts the harness's CLIs on PATH as its user
has them, and runs crapkit from the shell. Row `gate-group-06` runs `crapkit
verify`: exit 3, the one stderr line, nothing on stdout. Row `gate-group-10`
runs `crapkit verify --json`: exit 3, the same stderr line, and a verify
payload whose findings hold one unreadable_name item, where 0.8.1 printed an
error object. In both the lane does not run again and no run is stored.

Fresh: the candidate installed as the README's 60-second start installs it.
Upgrade: crapkit N-1 measures the repo, the guide's pip row upgrades it to the
candidate, and the candidate measures it once before the change.

Row `gate-group-07` runs `crapkit rescore --gate --json` on the name: exit 3,
the argument refusal's stderr line and its error object, and check_gate over
MCP stdio: isError false, gate.ok false and the name in gate.unread_files, the
answers 0.8.1 gave.

Cells `lin-claimed-name-claude-hook` and `lin-up-claimed-name-claude-hook`
(gate-group-09) feed `crapkit claude-hook --protocol 1` a PostToolUse Edit of
the name in Claude Code's shell: claude-hook hands it to the gate module,
which refuses it before judging anything, so the hook exits 2 with the two
advisory lines 0.8.1 printed, nothing on stdout, no lane run and no run
stored.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from kit import profiles, state, wheels
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "gate-group-06"
ROW = "gate-group-06"
# verify --json's cells: gate-group-10 turns the stop into a verify payload.
JSON_ROW = "gate-group-10"
HARNESSES = profiles.pins()["harness"]
NAME = b"src/caf\xe9.py"
SHOWN = "src/caf\\xe9.py"
STOP = (f"{SHOWN} is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit reads every path "
        "as UTF-8; a file a scope takes is refused, not left out, so no gate passes it unread: rename it (git mv) "
        "to a UTF-8 name")
UNREAD_NAME_REASON = ("its name is not UTF-8, and crapkit reads every path as UTF-8: rename it (git mv) to a "
                      "UTF-8 name")
PIP = "pip in the active environment"
USE_CASES = "verify, class U path names"

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[exclude]
globs = ["make_cov.py"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["src"]
full_suite = false
inputs = ["src", "make_cov.py"]
"""
# A coverage.py report for src/app.py, written by the lane itself so the lane
# needs no test runner; each run appends a line to runs.log.
MAKE_COV = (
    "import json\n"
    "open('runs.log', 'a', encoding='ascii').write('run\\n')\n"
    "report = {'meta': {'branch_coverage': True}, 'files': {'src/app.py': {'functions': {\n"
    "    'f': {'start_line': 1, 'executed_lines': [1, 2], 'missing_lines': [],\n"
    "          'summary': {'covered_lines': 2, 'num_statements': 2,\n"
    "                      'num_branches': 0, 'covered_branches': 0}}}}}}\n"
    "open('cov.json', 'w', encoding='utf-8').write(json.dumps(report))\n"
)
SOURCE = "def f(x):\n    return x\n"


def _marks(decorator) -> list:
    """The marks a decorator puts on a test. A lambda would not do: pytest
    reads one as a mark argument, not as the test."""
    def test():
        pass
    return decorator(test).pytestmark


def _harness_cells(prefix: str, scenario: str, row: str = ROW) -> list:
    """One parameter per pinned harness, each marked by @cell as a cell of
    `row`, in the image pins.toml runs that harness in. The row's key is the
    packet that writes its cells."""
    return [pytest.param(key, id=key, marks=_marks(cell(
        f"{prefix}-{key}", channel="pip venv", harness=key, scenario=scenario, use_cases=USE_CASES,
        os="linux", image=spec["image"], cadence="nightly", row=row, packet=row)))
        for key, spec in sorted(HARNESSES.items())]


def _measured_repo(box) -> Path:
    """The fixture repo, committed and measured by the crapkit first on PATH."""
    repo = box.root / "repo"
    (repo / "src").mkdir(parents=True)
    state.write(repo, {"crapkit.toml": CONFIG, ".gitignore": ".crapkit/\ncov.json\nruns.log\n",
                       "src/app.py": SOURCE, "make_cov.py": MAKE_COV})
    if profiles.in_container():
        profiles.allow_container_lane(repo)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "base")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    return repo


def _commit_the_name(box, repo: Path) -> None:
    """src/caf\\xe9.py on disk and committed: a POSIX file system holds any byte."""
    (repo / os.fsdecode(NAME)).write_text(SOURCE, encoding="utf-8")
    profiles.commit(box, repo, "add a Latin-1 name")


def _in_the_harness_shell(box, key: str) -> None:
    """The pinned harness CLIs on PATH, as their user has them; a harness with
    a command of its own must resolve to it."""
    profiles.add_harnesses(box)
    command = HARNESSES[key].get("command")
    assert command is None or box.which(command), f"{key}: {command} is not on PATH: {box.env['PATH']}"


def _stored_runs(repo: Path) -> int:
    with closing(sqlite3.connect(repo / ".crapkit" / "crap.sqlite")) as db:
        return db.execute("SELECT count(*) FROM runs").fetchone()[0]


def _lane_runs(repo: Path) -> int:
    return (repo / "runs.log").read_text(encoding="ascii").count("run")


def assert_verify_stops(box, repo: Path) -> None:
    """verify from the shell: the 0.8.1 output, exit 3, and nothing measured
    or stored."""
    runs, stored = _lane_runs(repo), _stored_runs(repo)

    plain = box.script("crapkit verify", shell="sh", cwd=repo, expect=3)

    assert (plain.stdout, plain.stderr) == ("", f"crapkit: {STOP}\n"), box.transcript.text()
    assert (_lane_runs(repo), _stored_runs(repo)) == (runs, stored), box.transcript.text()


# The one findings item verify --json prints for the committed name: ext4 holds
# the file, so git reads it clean.
FINDING = {"kind": "unreadable_name", "fails": True, "exit_code": 3, "overridable": False, "dirty": False,
           "rule": "unreadable name", "path": SHOWN, "scope": "src", "reason": STOP}


def assert_verify_json_gives_a_verdict(box, repo: Path) -> None:
    """verify --json from the shell: exit 3 and the 0.8.1 stderr line, and on
    stdout a verify payload, not an error object, nothing measured or stored."""
    runs, stored = _lane_runs(repo), _stored_runs(repo)

    as_json = box.script("crapkit verify --json", shell="sh", cwd=repo, expect=3)
    printed = json.loads(as_json.stdout)

    assert as_json.stderr == f"crapkit: {STOP}\n", box.transcript.text()
    assert printed["findings"] == [FINDING], as_json.stdout
    assert (printed["ok"], printed["run_id"], "error" in printed) == (False, None, False), as_json.stdout
    assert printed["counts"]["diff_uncovered_count"] == 0, as_json.stdout
    assert (_lane_runs(repo), _stored_runs(repo)) == (runs, stored), box.transcript.text()


def _fresh(box, templates, key) -> Path:
    """The README's start, a measured repo, the name committed, the harness's shell."""
    profiles.session_crapkit(box, templates)
    repo = _measured_repo(box)
    _commit_the_name(box, repo)
    _in_the_harness_shell(box, key)
    return repo


def _upgraded(box, candidate, record_property, key) -> Path:
    """N-1 measures the repo, the guide's pip row upgrades it, one coverage,
    then the name committed and the harness's shell."""
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _measured_repo(box)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0, note="not a guide step: one coverage after upgrading")
    _commit_the_name(box, repo)
    _in_the_harness_shell(box, key)
    return repo


@pytest.mark.parametrize("key", _harness_cells(
    "lin-claimed-name", "fresh: README pip start; a committed src/caf\\xe9.py stops verify with the 0.8.1 "
                        "line, exit 3, no lane run, no run stored"))
def test_claimed_name_fresh(box, templates, key):
    assert_verify_stops(box, _fresh(box, templates, key))


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-claimed-name", "upgrade from N-1 by the guide's pip row: one coverage, then a committed "
                           "src/caf\\xe9.py stops verify with the 0.8.1 line, exit 3"))
def test_claimed_name_upgrade(box, candidate, record_property, key):
    assert_verify_stops(box, _upgraded(box, candidate, record_property, key))


@pytest.mark.parametrize("key", _harness_cells(
    "lin-claimed-name-json", "fresh: README pip start; a committed src/caf\\xe9.py makes verify --json print a "
                             "verify payload with one unreadable_name finding, exit 3, no lane run, no run stored",
    JSON_ROW))
def test_claimed_name_json_fresh(box, templates, key):
    assert_verify_json_gives_a_verdict(box, _fresh(box, templates, key))


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-claimed-name-json", "upgrade from N-1 by the guide's pip row: one coverage, then a committed "
                                "src/caf\\xe9.py makes verify --json print a verify payload with one "
                                "unreadable_name finding, exit 3", JSON_ROW))
def test_claimed_name_json_upgrade(box, candidate, record_property, key):
    assert_verify_json_gives_a_verdict(box, _upgraded(box, candidate, record_property, key))


# rescore --gate's cells: gate-group-07 judges both gate adapters through the
# gate module, and each answers as 0.8.1 did.
GATE_ROW = "gate-group-07"
ARGUMENT_REFUSAL = (f"{SHOWN} is named in bytes that are not UTF-8, and crapkit reads every path as UTF-8: "
                    "rename it (git mv) to a UTF-8 name")
# The name as a POSIX shell hands it to crapkit: its own bytes, \xe9 in octal.
SHELL_NAME = "\"$(printf 'src/caf\\351.py')\""


def assert_the_gates_answer_as_081(box, repo: Path) -> None:
    """rescore --gate --json from the shell refuses the argument: exit 3, the
    one stderr line, and the error object listing the name (committed, so
    clean). check_gate over MCP stdio answers the verdict, isError false, with
    the name in gate.unread_files. Neither runs a lane or stores a run."""
    runs, stored = _lane_runs(repo), _stored_runs(repo)

    gate = box.script(f"crapkit rescore --gate --json {SHELL_NAME}", shell="sh", cwd=repo, expect=3)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        answer = client.call("check_gate", {"path": os.fsdecode(NAME)})

    assert gate.stderr == f"crapkit: {ARGUMENT_REFUSAL}\n", box.transcript.text()
    assert json.loads(gate.stdout) == {"error": {
        "exit": 3, "kind": "config", "message": ARGUMENT_REFUSAL,
        "unread_files": [{"path": SHOWN, "reason": UNREAD_NAME_REASON, "dirty": False}]}, "schema": 1}
    assert answer["isError"] is False, answer
    assert answer["structuredContent"]["gate"] == {
        "ok": False, "judged": 0, "ceilings": {}, "breaches": [], "untracked": [],
        "unread_files": [{"path": SHOWN, "reason": UNREAD_NAME_REASON, "dirty": True}]}, answer
    assert (_lane_runs(repo), _stored_runs(repo)) == (runs, stored), box.transcript.text()


@pytest.mark.parametrize("key", _harness_cells(
    "lin-claimed-name-gate", "fresh: README pip start; on a committed src/caf\\xe9.py rescore --gate --json "
                             "refuses the argument at exit 3 and check_gate answers gate.ok false, the "
                             "name in gate.unread_files", GATE_ROW))
def test_claimed_name_gate_fresh(box, templates, key):
    assert_the_gates_answer_as_081(box, _fresh(box, templates, key))


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-claimed-name-gate", "upgrade from N-1 by the guide's pip row: one coverage, then on a committed "
                                "src/caf\\xe9.py rescore --gate --json refuses the argument at exit 3 and "
                                "check_gate answers gate.ok false", GATE_ROW))
def test_claimed_name_gate_upgrade(box, candidate, record_property, key):
    assert_the_gates_answer_as_081(box, _upgraded(box, candidate, record_property, key))


# claude-hook's cells: gate-group-09 judges the edited file through the gate
# module, which refuses the name, and the advisory keeps the two lines 0.8.1
# printed. Claude Code is the harness whose hook runs the command.
HOOK_PACKET = "gate-group-09"
HOOK_HARNESS = "claude-code"
HOOK_ADVISORY = [f"crapkit advisory: {SHOWN} is in scope 'src', but git names it in bytes that are not UTF-8 and "
                 "crapkit reads every path as UTF-8, so no function in it was judged (the edit landed; nothing "
                 "was blocked)",
                 "the commit gate refuses such a file (exit 3); rename it to a UTF-8 name"]


def assert_the_hook_advises_as_081(box, repo: Path) -> None:
    """A PostToolUse Edit of the name, as Claude Code sends it: exit 2, the
    two advisory lines on stderr, nothing on stdout, nothing measured or
    stored."""
    runs, stored = _lane_runs(repo), _stored_runs(repo)
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(repo),
                          "tool_input": {"file_path": str(repo / os.fsdecode(NAME))}})

    hook = box.run(["crapkit", "claude-hook", "--protocol", "1"], cwd=repo, input=payload, expect=2)

    assert (hook.stdout, hook.stderr.splitlines()) == ("", HOOK_ADVISORY), box.transcript.text()
    assert (_lane_runs(repo), _stored_runs(repo)) == (runs, stored), box.transcript.text()


@cell("lin-claimed-name-claude-hook", channel="pip venv", harness=HOOK_HARNESS, use_cases="claude-hook, class U "
      "path names", os="linux", image="core", cadence="nightly", packet=HOOK_PACKET,
      scenario="fresh: README pip start; a PostToolUse Edit of a committed src/caf\\xe9.py makes claude-hook exit "
               "2 with the 0.8.1 advisory lines, no lane run, no run stored")
def test_claimed_name_claude_hook_fresh(box, templates):
    assert_the_hook_advises_as_081(box, _fresh(box, templates, HOOK_HARNESS))


@cell("lin-up-claimed-name-claude-hook", channel="pip venv", harness=HOOK_HARNESS, use_cases="claude-hook, class U "
      "path names", os="linux", image="core", cadence="nightly", packet=HOOK_PACKET,
      scenario="upgrade from N-1 by the guide's pip row: one coverage, then a PostToolUse Edit of a committed "
               "src/caf\\xe9.py makes claude-hook exit 2 with the 0.8.1 advisory lines")
def test_claimed_name_claude_hook_upgrade(box, candidate, record_property):
    assert_the_hook_advises_as_081(box, _upgraded(box, candidate, record_property, HOOK_HARNESS))
