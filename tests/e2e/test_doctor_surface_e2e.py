"""What `crapkit init` writes and what `crapkit doctor` answers.

init detects the runners the repo already has instead of leaving every lane
commented out (a config with no live lane scores everything no-lane, and the
burn-down has nothing to rank). doctor answers as data: a JSON report a wrapper
can read, advisory parallelism knobs, and a warning for directories whose tests
no lane measures. A hermetic istanbul generator stands in for a coverage tool.
"""
import json
import platform
import subprocess
from pathlib import Path

import pytest

from crapkit import __version__
from crapkit.analyze import ANALYSIS_VERSION
from crapkit.config import load_config_text

from conftest import cli_runner

GEN = "gen_cov.py"

GEN_COV = """\
# Fixture coverage generator: istanbul coverage-final.json for the named sources.
# argv: <artifact-path> <source> [<source> ...]
import json
import os
import sys

artifact, sources = sys.argv[1], sys.argv[2:]
out = {}
for rel in sources:
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
        f_hits[str(n)] = 1
    out[key] = {"path": key, "fnMap": fn_map, "f": f_hits, "branchMap": {}, "b": {}}
os.makedirs(os.path.dirname(artifact) or ".", exist_ok=True)
with open(artifact, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
"""

SOURCE = "def f(n):\n    if n > 1:\n        n = n + 1\n    return n\n"

ARTIFACT = ".crapkit/cov/unit.json"

CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
          f'[exclude]\nglobs = ["{GEN}"]\n\n'
          f'[[lane]]\nname = "unit"\ncommand = "python {GEN} {ARTIFACT} src/measured.py"\n'
          f'artifact = "{ARTIFACT}"\nparser = "istanbul"\nscopes = ["src"]\n\n'
          # a template for the laned scope keeps the scoped-tests WARN (pinned in
          # its own file) out of the exact warning lists these tests assert
          '[crapkit.scoped_tests]\nsrc = "python -c pass"\n')


run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace")


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i")


@pytest.fixture()
def py_repo(tmp_path: Path) -> Path:
    """A python repo with a pytest marker file and no crapkit.toml: what init
    has to work with."""
    repo = tmp_path / "py"
    write(repo, "pyproject.toml", '[project]\nname = "demo"\n')
    write(repo, "pylib/mod.py", SOURCE)
    write(repo, ".gitignore", ".crapkit/\n__pycache__/\n")
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)
    return repo


@pytest.fixture()
def measured_repo(tmp_path: Path) -> Path:
    """One lane that measures src/measured.py and never looks at src/quiet,
    which has a test file of its own sitting in tests/."""
    repo = tmp_path / "measured"
    write(repo, ".gitignore", ".crapkit/\n__pycache__/\n")
    write(repo, GEN, GEN_COV)
    write(repo, "src/measured.py", SOURCE)
    write(repo, "src/quiet/mod.py", SOURCE)
    write(repo, "tests/test_mod.py", "def test_mod():\n    assert True\n")
    write(repo, "crapkit.toml", CONFIG)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)
    return repo


# --- init: lanes the repo can already run -----------------------------------

def test_init_writes_a_live_pytest_lane_doctor_accepts(py_repo: Path):
    res = run_cli(py_repo, "init")
    assert res.returncode == 0, res.stderr

    cfg = load_config_text((py_repo / "crapkit.toml").read_text(encoding="utf-8"))
    (lane,) = cfg.lanes
    assert (lane.parser, lane.scopes) == ("coveragepy", ("pylib",))
    assert lane.artifact == ".crapkit/cov/py.json"
    assert "pytest" in lane.command

    doctor = run_cli(py_repo, "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "1 lane(s) declared" in doctor.stdout


def test_init_keeps_a_template_for_the_runner_it_did_not_find(py_repo: Path):
    assert run_cli(py_repo, "init").returncode == 0
    text = (py_repo / "crapkit.toml").read_text(encoding="utf-8")
    assert '# parser = "istanbul"' in text, "no package.json, so the js lane stays a template"
    assert '# parser = "coveragepy"' not in text


def test_doctor_says_nothing_about_coverage_gaps_without_a_store(py_repo: Path):
    assert run_cli(py_repo, "init").returncode == 0
    res = run_cli(py_repo, "doctor")
    assert "WARN" not in res.stdout


# --- doctor --json ----------------------------------------------------------

def test_doctor_json_reports_the_whole_state_in_one_sorted_object(measured_repo: Path):
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0

    res = run_cli(measured_repo, "doctor", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert list(payload) == sorted(payload), "keys must be sorted for a stable diff"
    assert payload["problems"] == []
    assert payload["analysis_version"] == ANALYSIS_VERSION
    assert payload["versions"]["crapkit"] == __version__
    assert payload["versions"]["python"] == platform.python_version()
    assert payload["versions"]["lizard"]
    assert payload["newest_run"] == {"id": 1, "kind": "coverage", "verdict_ok": None}
    assert payload["store"]["path"] == ".crapkit/crap.sqlite"
    assert payload["store"]["present"] is True
    assert payload["store"]["size_bytes"] > 0


def test_doctor_json_separates_lane_rot_from_a_stale_artifact(measured_repo: Path):
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0
    head = git(measured_repo, "rev-parse", "HEAD")

    (lane,) = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)["lanes"]
    assert lane["name"] == "unit"
    assert lane["artifact"] == ARTIFACT
    assert lane["artifact_present"] is True
    assert lane["commit"] == head
    assert isinstance(lane["seconds"], float)

    (measured_repo / ARTIFACT).unlink()
    (gone,) = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)["lanes"]
    assert gone["artifact_present"] is False
    assert gone["commit"] == head, "the stamp outlives the artifact it describes"


def test_doctor_json_and_text_report_the_same_problems_and_exit_code(measured_repo: Path):
    with open(measured_repo / "crapkit.toml", "a", encoding="utf-8") as fh:
        fh.write('\n[[scope]]\nname = "ghost"\npaths = ["nowhere"]\n'
                 'languages = ["python"]\ntarrget = 5\n')

    text = run_cli(measured_repo, "doctor")
    machine = run_cli(measured_repo, "doctor", "--json")

    assert text.returncode == machine.returncode == 1
    payload = json.loads(machine.stdout)
    assert payload["problems"] == [ln[5:] for ln in text.stdout.splitlines()
                                   if ln.startswith("FAIL ")]
    assert any("tarrget" in p for p in payload["problems"])


def test_doctor_json_names_the_files_behind_a_problem(measured_repo: Path):
    """A problem string a wrapper cannot act on is prose with extra steps."""
    write(measured_repo, "loose.py", SOURCE)
    commit_all(measured_repo)

    payload = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)

    assert payload["problems"] == ["1 tracked file(s) match a scope language but "
                                   "no scope path: loose.py — add a [[scope]] claiming "
                                   "them, or an [exclude] glob (docs/configuration.md)"]


def test_doctor_json_prints_nothing_but_the_object(measured_repo: Path):
    res = run_cli(measured_repo, "doctor", "--json", "--show-files")
    assert json.loads(res.stdout)["problems"] == []


# --- doctor --tune ----------------------------------------------------------

def test_doctor_tune_suggests_knobs_and_touches_no_config(measured_repo: Path):
    before = (measured_repo / "crapkit.toml").read_bytes()
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0

    res = run_cli(measured_repo, "doctor", "--tune")

    assert res.returncode == 0, res.stdout + res.stderr
    lines = res.stdout.splitlines()
    assert lines[1] == "[crapkit]"
    knobs = dict(ln.split(" = ") for ln in lines if " = " in ln)
    assert set(knobs) == {"max_parallel_lanes", "analysis_workers", "mutation_workers"}
    assert all(int(v) >= 1 for v in knobs.values())
    assert "s serial -> ~" in lines[-1], "the recorded lane duration is the cost signal"
    assert (measured_repo / "crapkit.toml").read_bytes() == before


def test_doctor_tune_says_when_no_lane_has_ever_run(measured_repo: Path):
    res = run_cli(measured_repo, "doctor", "--tune")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "no durations recorded yet" in res.stdout


# --- doctor: tests exist, no lane measures them -----------------------------

def test_doctor_warns_about_a_directory_no_lane_measures(measured_repo: Path):
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0

    res = run_cli(measured_repo, "doctor")

    assert res.returncode == 0, "a measurement gap is a warning, never a failure"
    warning = [ln for ln in res.stdout.splitlines()
               if ln.startswith("WARN") and "no lane measures" in ln]
    assert len(warning) == 1, res.stdout
    assert "src/quiet" in warning[0]
    assert "tests/test_mod.py" in warning[0]
    assert "tests exist but no lane measures them" in warning[0]


def test_the_measured_directory_is_not_warned_about(measured_repo: Path):
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0
    payload = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)
    gaps = [w for w in payload["warnings"] if "no lane measures" in w]
    assert [w.split(":")[0] for w in gaps] == ["src/quiet"]


# --- doctor: lane artifacts that dirty the consumer's tree -------------------

def _litter_config(artifact: str, results: str) -> str:
    return ('[crapkit]\ntarget = 6\n\n'
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
            '[[lane]]\nname = "js"\ncommand = "python -c pass"\n'
            f'artifact = "{artifact}"\nresults_artifact = "{results}"\n'
            'parser = "istanbul"\nscopes = ["src"]\n\n'
            # the laned scope carries a template so the scoped-tests WARN
            # (pinned in its own file) stays out of these exact lists
            '[crapkit.scoped_tests]\nsrc = "python -c pass"\n')


def _lane_repo(tmp_path: Path, name: str, config: str) -> Path:
    repo = tmp_path / name
    write(repo, ".gitignore", ".crapkit/\ncoverage/\n")
    write(repo, "src/measured.py", SOURCE)
    write(repo, "crapkit.toml", config)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)
    return repo


def test_doctor_warns_about_a_lane_that_writes_into_the_consumers_tree(tmp_path: Path):
    """A 14-lane repo grew fifteen coverage-* directories and seven junit files
    at its root. Every lane worked, so this can only ever be a warning."""
    repo = _lane_repo(tmp_path, "litter",
                      _litter_config("coverage/coverage-final.json", "junit.xml"))

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, "breaking an existing consumer's gate would be worse"
    warnings = [ln for ln in res.stdout.splitlines() if ln.startswith("WARN")]
    assert warnings == [
        "WARN lane 'js' writes coverage/coverage-final.json at the repo root — point it "
        "under .crapkit/ (for example .crapkit/cov/js/) to keep the tree clean",
        "WARN lane 'js' writes junit.xml at the repo root — point it under .crapkit/ "
        "(for example .crapkit/cov/js/) to keep the tree clean"], res.stdout


def test_doctor_says_nothing_about_a_lane_that_writes_under_the_store(tmp_path: Path):
    repo = _lane_repo(tmp_path, "tidy",
                      _litter_config(".crapkit/cov/js/coverage-final.json",
                                     ".crapkit/cov/js/junit.xml"))

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "WARN" not in res.stdout, res.stdout


def test_the_litter_warning_rides_the_machine_report(tmp_path: Path):
    repo = _lane_repo(tmp_path, "machine",
                      _litter_config("cov.json", ".crapkit/cov/js/junit.xml"))

    payload = json.loads(run_cli(repo, "doctor", "--json").stdout)

    assert payload["problems"] == []
    assert [w.split(" writes ")[1].split(" at ")[0] for w in payload["warnings"]] == ["cov.json"]


def test_the_lane_init_scaffolds_draws_no_litter_warning(py_repo: Path):
    """The two halves of this change meet here: what init writes is what doctor
    asks for."""
    assert run_cli(py_repo, "init").returncode == 0

    res = run_cli(py_repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "WARN" not in res.stdout, res.stdout
