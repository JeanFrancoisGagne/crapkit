"""What `crapkit init` writes and what `crapkit doctor` answers.

init detects the runners the repo already has instead of leaving every lane
commented out (a config with no live lane scores everything no-lane, and the
burn-down has nothing to rank). doctor answers as data: a JSON report a wrapper
can read, advisory parallelism knobs, and a warning for directories whose tests
no lane measures. A hermetic istanbul generator stands in for a coverage tool.
"""
import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

from crapkit import __version__
from crapkit.analyze import ANALYSIS_VERSION
from crapkit.config import load_config_text

from conftest import cli_runner, repo_warnings, repo_warns

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
    assert repo_warns(res.stdout) == [], res.stdout


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
    assert lane["refusal"] is None

    (measured_repo / ARTIFACT).unlink()
    (gone,) = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)["lanes"]
    assert gone["artifact_present"] is False
    assert gone["commit"] == head, "the stamp outlives the artifact it describes"


# --- the runner each lane runs ------------------------------------------------

UNKNOWN_RUNNER = (f"note lane 'unit': runner unknown (python {GEN} {ARTIFACT} src/measured.py "
                  "names none crapkit knows); runner-specific hints and refusals are off for it")


def test_doctor_says_the_generator_lane_names_no_runner_and_what_that_turns_off(measured_repo: Path):
    """A python script that is not pytest: unknown, a note, and doctor still passes."""
    res = run_cli(measured_repo, "doctor")
    (lane,) = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)["lanes"]

    assert res.returncode == 0, res.stdout + res.stderr
    assert UNKNOWN_RUNNER in res.stdout.splitlines(), res.stdout
    assert lane["toolchain"] == {"name": None, "source": None}


def test_doctor_reads_the_runner_from_the_command_init_wrote(py_repo: Path):
    assert run_cli(py_repo, "init").returncode == 0

    res = run_cli(py_repo, "doctor")
    (lane,) = json.loads(run_cli(py_repo, "doctor", "--json").stdout)["lanes"]

    assert "ok   lane 'py': runs pytest (named in its command)" in res.stdout.splitlines()
    assert lane["toolchain"] == {"name": "pytest", "source": "command"}


def test_a_package_json_doctor_cannot_read_warns_and_doctor_still_passes(measured_repo: Path):
    """init refuses an unreadable root package.json; doctor names it and goes on."""
    (measured_repo / "package.json").write_bytes("﻿{}".encode("utf-16-le"))
    git(measured_repo, "add", "package.json")

    res = run_cli(measured_repo, "doctor")

    warns = [line for line in repo_warns(res.stdout) if "package.json" in line]
    assert res.returncode == 0, res.stdout + res.stderr
    assert warns == ["WARN package.json: it is not UTF-8 (first bytes ff fe = UTF-16, the "
                     "PowerShell 5.1 Out-File default); save it as UTF-8; doctor read the runner "
                     "of each lane under it from the lane's command alone"], res.stdout


# --- doctor --json: the refusal reuse reads --------------------------------

FAILING = "command = 'python -c \"raise SystemExit(1)\"'"
LANE_COMMAND = f'command = "python {GEN} {ARTIFACT} src/measured.py"'


def refused(repo: Path) -> Path:
    """A good run, then an attempt that exits 1 and writes nothing, so the good
    run's artifact is the leftover the failed attempt stands in front of."""
    assert run_cli(repo, "coverage", "--json").returncode == 0
    config = (repo / "crapkit.toml").read_text(encoding="utf-8")
    write(repo, "crapkit.toml", config.replace(LANE_COMMAND, FAILING))
    failed = run_cli(repo, "coverage", "--json")
    assert failed.returncode == 5, failed.stdout + failed.stderr
    return repo


def test_doctor_names_a_lane_whose_last_attempt_left_its_artifact_unwritten(measured_repo: Path):
    repo = refused(measured_repo)

    payload = json.loads(run_cli(repo, "doctor", "--json").stdout)
    text = run_cli(repo, "doctor")

    (lane,) = payload["lanes"]
    assert lane["artifact_present"] is True
    assert lane["refusal"] == (f"its last attempt wrote no artifact, and the {ARTIFACT} on disk "
                               "predates it; --reuse-artifacts will not score it until a run of "
                               "the lane writes it again")
    assert f"lane 'unit': {lane['refusal']}" in payload["warnings"]
    assert f"WARN lane 'unit': {lane['refusal']}" in text.stdout.splitlines()
    assert text.returncode == 0, "a refused leftover is a warning: the next run clears it"


def test_a_lane_with_a_good_run_reports_no_refusal(measured_repo: Path):
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0

    (lane,) = json.loads(run_cli(measured_repo, "doctor", "--json").stdout)["lanes"]

    assert lane["refusal"] is None


def _touch(path: Path) -> None:
    later = path.stat().st_mtime + 60
    os.utime(path, (later, later))


def _rewrite(path: Path) -> None:
    """A salvage: new bytes combined by hand over the leftover."""
    path.write_bytes(path.read_bytes().replace(b'"f": {"0": 1}', b'"f": {"0": 2}'))


@pytest.mark.parametrize("act", [
    lambda path: None,
    _touch,
    lambda path: path.write_bytes(path.read_bytes()),
    _rewrite,
    lambda path: path.unlink(),
], ids=["untouched", "touched", "same-bytes-copy", "new-bytes", "deleted"])
def test_doctor_reports_a_refusal_exactly_when_reuse_refuses(measured_repo: Path, act):
    """doctor asks the question --reuse-artifacts asks, so the two never
    disagree about one leftover, whichever answer reuse gives a touch."""
    repo = refused(measured_repo)
    act(repo / ARTIFACT)

    (lane,) = json.loads(run_cli(repo, "doctor", "--json").stdout)["lanes"]
    reuse = run_cli(repo, "coverage", "--reuse-artifacts", "--json")

    assert (lane["refusal"] is not None) == (reuse.returncode == 5
                                             and "last attempt" in reuse.stderr), reuse.stderr


@pytest.mark.parametrize("content, why", [
    ("{ not json", "(it does not parse as JSON)"),
    ("[1, 2]", "(its top level is not an object)"),
    (b"\xff\xfe{", "(it does not parse as JSON)"),
], ids=["torn", "a-list", "not-utf8"])
def test_doctor_names_a_stamps_file_it_cannot_read(measured_repo: Path, content, why):
    """Read as no stamps, the file hides every commit, proof and refusal, so a
    refused leftover would pass --reuse-artifacts unseen."""
    assert run_cli(measured_repo, "coverage", "--json").returncode == 0
    stamps = measured_repo / ".crapkit" / "artifacts.json"
    if isinstance(content, bytes):
        stamps.write_bytes(content)
    else:
        stamps.write_text(content, encoding="utf-8")

    res = run_cli(measured_repo, "doctor", "--json")

    payload = json.loads(res.stdout)
    assert res.returncode == 0, payload["problems"]
    (warning,) = [w for w in payload["warnings"] if w.startswith(".crapkit/artifacts.json")]
    assert why in warning
    assert warning.endswith("so crapkit reads it as no stamps: --reuse-unchanged reruns every "
                            "lane and --reuse-artifacts refuses every lane's artifact, since it "
                            "cannot tell a failed attempt's leftover; the next lane run writes "
                            "the file again, or delete it")
    assert payload["lanes"][0]["commit"] is None


UNKNOWN = (".crapkit/artifacts.json cannot be read (it does not parse as JSON), so crapkit "
           f"cannot tell whether the {ARTIFACT} on disk is the file a failed attempt left; "
           "--reuse-artifacts will not score it until a run of the lane writes it again")
LEFTOVER = (f"its last attempt wrote no artifact, and the {ARTIFACT} on disk predates it; "
            "--reuse-artifacts will not score it until a run of the lane writes it again")


def _good_run(repo: Path) -> Path:
    assert run_cli(repo, "coverage", "--json").returncode == 0
    return repo


@pytest.mark.parametrize("before, sentence", [(_good_run, UNKNOWN), (refused, LEFTOVER)],
                         ids=["good-run", "refused-leftover"])
def test_doctor_gives_the_refusal_reuse_gives_when_the_stamps_file_is_torn(
        measured_repo: Path, before, sentence):
    """Reuse refuses an artifact whose record it cannot read, unless the
    store's copy of a refusal answers. doctor asks the same question."""
    repo = before(measured_repo)
    (repo / ".crapkit" / "artifacts.json").write_text("{ not json", encoding="utf-8")

    (lane,) = json.loads(run_cli(repo, "doctor", "--json").stdout)["lanes"]
    reuse = run_cli(repo, "coverage", "--reuse-artifacts", "--json")

    assert lane["refusal"] == sentence
    assert reuse.returncode == 5, reuse.stdout + reuse.stderr


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
                                   "no scope path: loose.py - add a [[scope]] claiming "
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
    warnings = repo_warns(res.stdout)
    assert warnings == [
        "WARN lane 'js' writes coverage/coverage-final.json at the repo root - point it "
        "under .crapkit/ (for example .crapkit/cov/js/) to keep the tree clean",
        "WARN lane 'js' writes junit.xml at the repo root - point it under .crapkit/ "
        "(for example .crapkit/cov/js/) to keep the tree clean"], res.stdout


def test_doctor_says_nothing_about_a_lane_that_writes_under_the_store(tmp_path: Path):
    repo = _lane_repo(tmp_path, "tidy",
                      _litter_config(".crapkit/cov/js/coverage-final.json",
                                     ".crapkit/cov/js/junit.xml"))

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert repo_warns(res.stdout) == [], res.stdout


def test_the_litter_warning_rides_the_machine_report(tmp_path: Path):
    repo = _lane_repo(tmp_path, "machine",
                      _litter_config("cov.json", ".crapkit/cov/js/junit.xml"))

    payload = json.loads(run_cli(repo, "doctor", "--json").stdout)

    assert payload["problems"] == []
    assert [w.split(" writes ")[1].split(" at ")[0]
            for w in repo_warnings(payload["warnings"])] == ["cov.json"]


def test_the_lane_init_scaffolds_draws_no_litter_warning(py_repo: Path):
    """The two halves of this change meet here: what init writes is what doctor
    asks for."""
    assert run_cli(py_repo, "init").returncode == 0

    res = run_cli(py_repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert repo_warns(res.stdout) == [], res.stdout
