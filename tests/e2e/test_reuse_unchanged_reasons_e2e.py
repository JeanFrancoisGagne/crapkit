"""`--reuse-unchanged` says why a lane reruns, and only what a lane reads decides it.

A declined reuse used to print nothing: the one trace of a rerun that costs a
large consumer repo up to 88 minutes was a missing "reusing without rerun"
line. Two things declined it with nothing a lane reads changed: a `cd` between
two runs (OLDPWD was part of the proof), and a lane output git does not ignore,
which left the tree dirty after every run so that no stamp ever held a proof.

The decision is observed as lane RERUNS: each lane command counts its own runs.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import cli_runner

PY = sys.executable.replace("\\", "/")

APP_TS = "export function one(): number {\n  return 1;\n}\n"

# argv: the artifact to write, the counter file to append a line to.
MAKE_COV = (
    "import json, os, pathlib, sys\n"
    "root = os.getcwd()\n"
    "artifact, counter = sys.argv[1], sys.argv[2]\n"
    'with open(counter, "a", encoding="utf-8") as fh:\n'
    '    fh.write("run\\n")\n'
    'app = os.path.join(root, "src", "app.ts")\n'
    "data = {app: {\n"
    '    "fnMap": {"0": {"name": "one", "decl": {"start": {"line": 1}},\n'
    '                    "loc": {"start": {"line": 1}, "end": {"line": 3}}}},\n'
    '    "f": {"0": 1}, "statementMap": {"0": {"start": {"line": 2}}}, "s": {"0": 1},\n'
    '    "branchMap": {}, "b": {}}}\n'
    "pathlib.Path(root, artifact).parent.mkdir(parents=True, exist_ok=True)\n"
    "pathlib.Path(root, artifact).write_text(json.dumps(data), encoding='utf-8')\n"
)

TOML = f"""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "unit"
command = '"{PY}" make_cov.py coverage/unit.json runs-unit.txt'
artifact = "coverage/unit.json"
parser = "istanbul"
scopes = ["src"]

[[lane]]
name = "more"
command = '"{PY}" make_cov.py out/more.json runs-more.txt'
artifact = "out/more.json"
parser = "istanbul"
scopes = ["src"]
"""

IGNORED = ".crapkit/\ncoverage/\nout/\nruns-*.txt\n"

_run_cli = cli_runner(timeout=300)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


def _build(root: Path, ignore: str) -> Path:
    _write(root, "src/app.ts", APP_TS)
    _write(root, "docs/notes.md", "notes\n")
    _write(root, "make_cov.py", MAKE_COV)
    _write(root, "crapkit.toml", TOML)
    _write(root, ".gitignore", ignore)
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")
    return root


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _build(tmp_path / "mini", IGNORED)


def _runs(repo: Path, lane: str) -> int:
    counter = repo / f"runs-{lane}.txt"
    return len(counter.read_text(encoding="utf-8").splitlines()) if counter.is_file() else 0


def _coverage(repo: Path, *args: str, env: dict | None = None):
    res = _run_cli(repo, "coverage", *args, env_extra=env)
    assert res.returncode == 0, res.stderr
    return res


def _reasons(res) -> dict:
    return {name: lane.get("rerun_reason") for name, lane in json.loads(res.stdout)["lanes"].items()}


def _rerun_lines(res) -> list[str]:
    return [line for line in res.stderr.splitlines() if ": rerunning: " in line]


# --- the reason a declined reuse prints --------------------------------------

def test_an_uncommitted_edit_is_named_on_stderr_and_in_the_json(repo: Path):
    _coverage(repo)
    _write(repo, "src/app.ts", APP_TS + "// edited\n")

    res = _coverage(repo, "--reuse-unchanged", "--json")

    reason = "the working tree has 1 uncommitted change(s): src/app.ts"
    assert _rerun_lines(res) == [f"crapkit: lane 'unit': rerunning: {reason}",
                                 f"crapkit: lane 'more': rerunning: {reason}"]
    assert _reasons(res) == {"unit": reason, "more": reason}
    assert (_runs(repo, "unit"), _runs(repo, "more")) == (2, 2)


def test_a_reused_lane_has_an_empty_reason_and_no_rerun_line(repo: Path):
    _coverage(repo)

    res = _coverage(repo, "--reuse-unchanged", "--json")

    assert _rerun_lines(res) == []
    assert _reasons(res) == {"unit": "", "more": ""}
    assert (_runs(repo, "unit"), _runs(repo, "more")) == (1, 1)


def test_without_reuse_unchanged_the_json_carries_no_reason(repo: Path):
    res = _coverage(repo, "--json")

    assert _reasons(res) == {"unit": None, "more": None}


def test_a_measurement_taken_over_an_edit_is_named_as_no_proof(repo: Path):
    """The artifact describes the edit, so reverting the edit does not make it
    a measurement of the commit."""
    _write(repo, "src/app.ts", APP_TS + "// edited\n")
    _coverage(repo, "--lane", "unit")
    _git(repo, "checkout", "--", "src/app.ts")

    res = _coverage(repo, "--reuse-unchanged", "--lane", "unit")

    assert _rerun_lines(res) == ["crapkit: lane 'unit': rerunning: its stamp holds no proof: it was "
                                 "measured with uncommitted changes, or by a crapkit that recorded none"]


def test_a_lane_that_never_ran_names_its_missing_artifact(repo: Path):
    res = _coverage(repo, "--reuse-unchanged")

    assert _rerun_lines(res) == ["crapkit: lane 'unit': rerunning: no artifact at coverage/unit.json",
                                 "crapkit: lane 'more': rerunning: no artifact at out/more.json"]


def test_a_new_commit_names_both_commits(repo: Path):
    _coverage(repo)
    built = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "docs/notes.md", "edited\n")
    head = _commit(repo, "docs only")

    res = _coverage(repo, "--reuse-unchanged", "--lane", "unit")

    assert _rerun_lines(res) == [f"crapkit: lane 'unit': rerunning: HEAD is {head[:11]} and its "
                                 f"artifact was built at {built[:11]}"]


def test_a_changed_environment_variable_is_named(repo: Path):
    _coverage(repo, env={"CRAPKIT_REUSE_PROBE": "one"})

    res = _coverage(repo, "--reuse-unchanged", "--lane", "unit", env={"CRAPKIT_REUSE_PROBE": "two"})

    assert _rerun_lines(res) == ["crapkit: lane 'unit': rerunning: 1 environment variable(s) "
                                 "changed: CRAPKIT_REUSE_PROBE"]


def test_a_changed_untracked_crapkit_toml_and_lane_table_are_named(tmp_path: Path):
    """A tracked crapkit.toml edit is an uncommitted change; an ignored one is
    proved by its bytes, and the lane's own table apart from them."""
    repo = _build(tmp_path / "mini", IGNORED + "crapkit.toml\n")
    _coverage(repo)
    _write(repo, "crapkit.toml", TOML.replace("target = 6", "target = 7"))

    res = _coverage(repo, "--reuse-unchanged")

    assert _rerun_lines(res) == ["crapkit: lane 'unit': rerunning: crapkit.toml changed",
                                 "crapkit: lane 'more': rerunning: crapkit.toml changed"]
    slower = 'scopes = ["src"]\ntimeout_seconds = 600\n'
    _write(repo, "crapkit.toml", TOML.replace('scopes = ["src"]\n', slower, 1))

    res = _coverage(repo, "--reuse-unchanged")

    assert _rerun_lines(res) == [
        "crapkit: lane 'unit': rerunning: crapkit.toml changed; its lane table changed",
        "crapkit: lane 'more': rerunning: crapkit.toml changed"]


# --- what no longer declines it ----------------------------------------------

def test_a_cd_between_two_runs_does_not_rerun_the_lanes(repo: Path):
    _coverage(repo, env={"OLDPWD": "/a", "SHLVL": "1", "_": "/usr/bin/one"})

    res = _coverage(repo, "--reuse-unchanged", env={"OLDPWD": "/b", "SHLVL": "2", "_": "/usr/bin/two"})

    assert _rerun_lines(res) == []
    assert (_runs(repo, "unit"), _runs(repo, "more")) == (1, 1)


def test_lane_outputs_git_does_not_ignore_leave_every_lane_reusable(tmp_path: Path):
    """Each lane's artifact lands untracked beside the other's. Their bytes are
    proved by each stamp's digests, so neither voids the other's proof."""
    repo = _build(tmp_path / "mini", ".crapkit/\nruns-*.txt\n")
    _coverage(repo)
    assert "coverage/unit.json" in _git(repo, "status", "--porcelain", "-uall")

    res = _coverage(repo, "--reuse-unchanged")

    assert _rerun_lines(res) == []
    assert (_runs(repo, "unit"), _runs(repo, "more")) == (1, 1)


def test_an_untracked_file_that_no_lane_declares_still_reruns_and_is_named(tmp_path: Path):
    repo = _build(tmp_path / "mini", ".crapkit/\nruns-*.txt\n")
    _coverage(repo)
    _write(repo, "draft.md", "draft\n")

    res = _coverage(repo, "--reuse-unchanged", "--lane", "unit")

    assert _rerun_lines(res) == ["crapkit: lane 'unit': rerunning: the working tree has 1 "
                                 "uncommitted change(s): draft.md"]


# --- the hint a partial run ends with ------------------------------------------

def test_a_partial_run_on_a_clean_tree_hints_the_reuse_run(repo: Path):
    res = _coverage(repo, "--lane", "unit")

    assert res.stdout.splitlines()[-1].endswith("coverage --reuse-unchanged")


def test_a_partial_run_on_a_dirty_tree_says_the_hinted_run_reruns_lanes_without_inputs(repo: Path):
    _write(repo, "src/app.ts", APP_TS + "// edited\n")

    res = _coverage(repo, "--lane", "unit")

    assert res.stdout.splitlines()[-1].endswith(
        "coverage --reuse-unchanged (the working tree has uncommitted changes, so every lane "
        "that lists no `inputs` reruns)")
