"""Class U: a file a scope takes whose name is not UTF-8 stops verify, under every harness.

The class U fixture holds a file a scope takes whose name is not UTF-8, and
it runs fresh and upgrade under each harness tools/deploy/pins.toml pins, in
that harness's image. verify judges the name through the gate module, which
refuses it before it judges anything (gate-group-06), so verify prints what
the 0.8.1 tree printed and stops before any lane runs.

verify is CLI behavior the harness carries through its shell, so the cells
form the every-harness row `gate-group-06` with nightly = "core": a nightly run
keeps the 3 core-image harnesses and the release candidate runs all 17
(MAP.toml [every_harness]).

Each cell measures a repo whose lane appends to runs.log, commits
`src/caf\\xe9.py` under scope `src`, puts the harness's CLIs on PATH as its user
has them, and runs `crapkit verify` and `crapkit verify --json` from the shell:
exit 3, the one stderr line, the error object on stdout for --json and nothing
otherwise, the lane not run again and no run stored.

Fresh: the candidate installed as the README's 60-second start installs it.
Upgrade: crapkit N-1 measures the repo, the guide's pip row upgrades it to the
candidate, and the candidate measures it once before the change.

gate-group-07, -09 and -10 add their claimed-name cells here.
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

PACKET = "gate-group-06"
ROW = "gate-group-06"
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


def _harness_cells(prefix: str, scenario: str) -> list:
    """One parameter per pinned harness, each marked by @cell as a cell of this
    row, in the image pins.toml runs that harness in."""
    return [pytest.param(key, id=key, marks=_marks(cell(
        f"{prefix}-{key}", channel="pip venv", harness=key, scenario=scenario, use_cases=USE_CASES,
        os="linux", image=spec["image"], cadence="nightly", row=ROW)))
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
    """verify and verify --json from the shell: the 0.8.1 output, exit 3, and
    nothing measured or stored."""
    runs, stored = _lane_runs(repo), _stored_runs(repo)

    plain = box.script("crapkit verify", shell="sh", cwd=repo, expect=3)
    as_json = box.script("crapkit verify --json", shell="sh", cwd=repo, expect=3)

    assert (plain.stdout, plain.stderr) == ("", f"crapkit: {STOP}\n"), box.transcript.text()
    assert as_json.stderr == f"crapkit: {STOP}\n", box.transcript.text()
    assert json.loads(as_json.stdout) == {"schema": 1, "error": {
        "exit": 3, "kind": "config", "message": STOP,
        "unread_files": [{"path": SHOWN, "reason": UNREAD_NAME_REASON, "dirty": False}]}}, as_json.stdout
    assert (_lane_runs(repo), _stored_runs(repo)) == (runs, stored), box.transcript.text()


@pytest.mark.parametrize("key", _harness_cells(
    "lin-claimed-name", "fresh: README pip start; a committed src/caf\\xe9.py stops verify and verify --json "
                        "with the 0.8.1 line, exit 3, no lane run, no run stored"))
def test_claimed_name_fresh(box, templates, key):
    profiles.session_crapkit(box, templates)
    repo = _measured_repo(box)
    _commit_the_name(box, repo)
    _in_the_harness_shell(box, key)

    assert_verify_stops(box, repo)


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-claimed-name", "upgrade from N-1 by the guide's pip row: one coverage, then a committed "
                           "src/caf\\xe9.py stops verify and verify --json with the 0.8.1 line, exit 3"))
def test_claimed_name_upgrade(box, candidate, record_property, key):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _measured_repo(box)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0, note="not a guide step: one coverage after upgrading")
    _commit_the_name(box, repo)
    _in_the_harness_shell(box, key)

    assert_verify_stops(box, repo)
