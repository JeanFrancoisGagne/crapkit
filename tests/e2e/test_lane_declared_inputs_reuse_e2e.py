"""`--reuse-unchanged` on a lane that declares the paths it reads.

Without `inputs` a lane is reused only at the same clean HEAD, so a docs commit
or one untracked file anywhere reruns every lane. A lane that lists its inputs
is reused while its stamp's commit is still behind HEAD, no committed, staged,
unstaged or untracked change touches those paths, its artifact bytes still
match, and its own config block, env included, is the one it was measured with.

The decision is observed as lane RERUNS: the lane command counts its own runs.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit.config import load_config_text
from crapkit.lanes import lane_reuse_commit, lane_reuse_verdict, run_lane, write_stamps

from conftest import cli_runner
from hang_guard import HANG_SECONDS

PY = sys.executable.replace("\\", "/")

APP_TS = "export function one(): number {\n  return 1;\n}\n"

MAKE_COV = (
    "import json, os, pathlib, sys\n"
    "root = os.getcwd()\n"
    'app = os.path.join(root, "src", "app.ts")\n'
    'with open("runs.txt", "a", encoding="utf-8") as fh:\n'
    '    fh.write("run\\n")\n'
    "artifact = {app: {\n"
    '    "fnMap": {"0": {"name": "one", "decl": {"start": {"line": 1}},\n'
    '                    "loc": {"start": {"line": 1}, "end": {"line": 3}}}},\n'
    '    "f": {"0": 1}, "statementMap": {"0": {"start": {"line": 2}}}, "s": {"0": 1},\n'
    '    "branchMap": {}, "b": {}}}\n'
    'pathlib.Path(root, "coverage").mkdir(exist_ok=True)\n'
    'pathlib.Path(root, "coverage", "final.json").write_text(json.dumps(artifact), encoding="utf-8")\n'
)


def _toml(inputs: str, env: str = "one") -> str:
    return f"""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "unit"
command = '"{PY}" make_cov.py'
artifact = "coverage/final.json"
parser = "istanbul"
scopes = ["src"]
{inputs}

[lane.env]
MODE = "{env}"
"""


INPUTS = 'inputs = ["src", "make_cov.py"]'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True).stdout


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mini"
    _write(root, "src/app.ts", APP_TS)
    _write(root, "docs/notes.md", "notes\n")
    _write(root, "make_cov.py", MAKE_COV)
    _write(root, "crapkit.toml", _toml(INPUTS))
    _write(root, ".gitignore", ".crapkit/\ncoverage/\nruns.txt\n")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")
    return root


def _lane(repo: Path):
    return load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8")).lanes[0]


def _measure(repo: Path) -> str:
    """Run the lane once and persist its stamp; the commit it was measured at."""
    lane = _lane(repo)
    write_stamps(repo, {lane.artifact: run_lane(repo, lane).stamp})
    return _git(repo, "rev-parse", "HEAD").strip()


def _runs(repo: Path) -> int:
    marker = repo / "runs.txt"
    return len(marker.read_text(encoding="utf-8").splitlines()) if marker.is_file() else 0


def test_the_stamp_names_its_proof_apart_from_the_lane_input_paths(repo: Path):
    """`Lane.inputs` holds paths; the hash reuse compares is the stamp's `proof`."""
    _measure(repo)
    stamps = json.loads((repo / ".crapkit" / "artifacts.json").read_text(encoding="utf-8"))
    stamp = stamps[_lane(repo).artifact]

    assert stamp["proof"] and "inputs" not in stamp


# --- what does not block reuse --------------------------------------------------

def test_a_commit_outside_the_inputs_leaves_the_lane_reusable(repo: Path):
    measured = _measure(repo)
    _write(repo, "docs/notes.md", "edited\n")
    _commit(repo, "docs only")

    assert lane_reuse_commit(repo, _lane(repo)) == measured


def test_an_untracked_file_outside_the_inputs_leaves_the_lane_reusable(repo: Path):
    measured = _measure(repo)
    _write(repo, "docs/draft.md", "new\n")

    assert lane_reuse_commit(repo, _lane(repo)) == measured


def test_an_unrelated_environment_variable_does_not_block_reuse(repo: Path, monkeypatch):
    measured = _measure(repo)
    monkeypatch.setenv("CRAPKIT_UNRELATED_FOR_TEST", "changed")

    assert lane_reuse_commit(repo, _lane(repo)) == measured


def test_other_crapkit_toml_settings_do_not_block_reuse(repo: Path):
    measured = _measure(repo)
    toml = (repo / "crapkit.toml").read_text(encoding="utf-8")
    _write(repo, "crapkit.toml", toml.replace("target = 6", "target = 8"))
    _commit(repo, "raise the ceiling")

    assert lane_reuse_commit(repo, _lane(repo)) == measured


def test_a_measurement_taken_beside_an_untracked_file_outside_the_inputs_is_proof(repo: Path):
    _write(repo, "docs/draft.md", "new\n")
    measured = _measure(repo)

    assert lane_reuse_commit(repo, _lane(repo)) == measured


# --- what does ------------------------------------------------------------------

@pytest.mark.parametrize("change", ["unstaged", "staged", "untracked", "committed"])
def test_any_change_under_an_input_reruns_the_lane(repo: Path, change: str):
    _measure(repo)
    rel = "src/new.ts" if change == "untracked" else "src/app.ts"
    _write(repo, rel, APP_TS + "// touched\n")
    if change == "staged":
        _git(repo, "add", rel)
    if change == "committed":
        _commit(repo, "touch src")

    assert lane_reuse_commit(repo, _lane(repo)) == ""


def test_a_change_to_a_declared_input_file_reruns_the_lane(repo: Path):
    _measure(repo)
    _write(repo, "make_cov.py", MAKE_COV + "# edited\n")

    assert lane_reuse_commit(repo, _lane(repo)) == ""


def test_a_changed_lane_env_reruns_the_lane(repo: Path):
    _measure(repo)
    _write(repo, "crapkit.toml", _toml(INPUTS, env="two"))
    _commit(repo, "new env")

    assert lane_reuse_commit(repo, _lane(repo)) == ""


def test_a_changed_input_list_reruns_the_lane(repo: Path):
    _measure(repo)
    _write(repo, "crapkit.toml", _toml('inputs = ["src", "make_cov.py", "docs"]'))
    _commit(repo, "wider inputs")

    assert lane_reuse_commit(repo, _lane(repo)) == ""


def test_an_artifact_rewritten_since_its_stamp_reruns_the_lane(repo: Path):
    _measure(repo)
    lane = _lane(repo)
    artifact = repo / lane.artifact
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert lane_reuse_verdict(repo, lane) == ("", "coverage/final.json: bytes differ from its stamp")


def test_a_stamp_commit_that_left_history_reruns_the_lane(repo: Path):
    first = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "docs/notes.md", "second\n")
    _commit(repo, "second")
    _measure(repo)
    _git(repo, "reset", "--hard", "-q", first)

    assert lane_reuse_commit(repo, _lane(repo)) == ""


def _refuse_kill(self):
    raise AssertionError(f"a running git read was killed: {self.args}")


def test_a_stamp_commit_that_left_history_kills_no_git_read(repo: Path, monkeypatch):
    """Reuse stops at the ancestry answer and never needs the status reads. A
    worktree `git diff` killed while it refreshes the index leaves
    .git/index.lock behind, so those reads are waited for, never killed."""
    _measure(repo)
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--amend", "-m", "amended")
    monkeypatch.setattr(subprocess.Popen, "kill", _refuse_kill)

    assert lane_reuse_commit(repo, _lane(repo)) == ""
    assert not (repo / ".git" / "index.lock").exists()


def test_a_measurement_taken_over_dirty_inputs_is_no_proof(repo: Path):
    """The artifact describes the edited file, not the commit the stamp names."""
    _write(repo, "src/app.ts", APP_TS + "// wip\n")
    _measure(repo)
    _commit(repo, "commit the wip")

    assert lane_reuse_commit(repo, _lane(repo)) == ""


# --- the reason a rerun gives ------------------------------------------------------

def test_a_change_under_the_inputs_is_named_with_the_stamp_commit(repo: Path):
    measured = _measure(repo)
    _write(repo, "src/app.ts", APP_TS + "// touched\n")

    assert lane_reuse_verdict(repo, _lane(repo)).reason == (
        f"1 change(s) under its inputs since {measured[:11]}: src/app.ts")


def test_a_changed_lane_table_is_named(repo: Path):
    _measure(repo)
    _write(repo, "crapkit.toml", _toml(INPUTS, env="two"))
    _commit(repo, "new env")

    assert lane_reuse_verdict(repo, _lane(repo)).reason == (
        "its lane table or env differs from the one it was measured with")


def test_a_stamp_commit_off_history_is_named(repo: Path):
    first = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "docs/notes.md", "second\n")
    _commit(repo, "second")
    measured = _measure(repo)
    _git(repo, "reset", "--hard", "-q", first)

    assert lane_reuse_verdict(repo, _lane(repo)).reason == (
        f"its artifact was built at {measured[:11]}, which is not behind HEAD")


def test_a_measurement_over_dirty_inputs_is_named_as_no_proof(repo: Path):
    _write(repo, "src/app.ts", APP_TS + "// wip\n")
    _measure(repo)
    _commit(repo, "commit the wip")

    assert lane_reuse_verdict(repo, _lane(repo)).reason.startswith("its stamp holds no proof: ")


def test_an_unignored_artifact_under_the_inputs_leaves_the_lane_reusable(repo: Path):
    """The artifact's bytes are proved by the stamp's digest, so the file the
    lane writes under its own inputs is not a change to them."""
    _write(repo, "crapkit.toml", _toml('inputs = ["src", "make_cov.py", "coverage"]'))
    _write(repo, ".gitignore", ".crapkit/\nruns.txt\n")
    measured = _commit(repo, "measure inside the inputs")
    _measure(repo)

    assert lane_reuse_verdict(repo, _lane(repo)) == (measured, "")


# --- through the CLI --------------------------------------------------------------

_run_cli = cli_runner(timeout=300)


def test_coverage_reuse_unchanged_skips_a_lane_whose_inputs_did_not_move(repo: Path):
    first = _run_cli(repo, "coverage", "--json")
    assert first.returncode == 0, first.stderr
    _write(repo, "docs/notes.md", "edited\n")
    _commit(repo, "docs only")
    _write(repo, "docs/draft.md", "untracked\n")

    second = _run_cli(repo, "coverage", "--reuse-unchanged", "--json")

    assert second.returncode == 0, second.stderr
    assert _runs(repo) == 1, "a docs commit and an untracked doc must not rerun the lane"
    measured = _git(repo, "rev-parse", "HEAD~1").strip()
    assert ("crapkit: lane 'unit': measurement inputs unchanged; reusing without rerun "
            f"(artifact built at {measured[:11]})") in second.stderr
    assert json.loads(second.stdout)["functions"] == json.loads(first.stdout)["functions"]


def test_coverage_reuse_unchanged_reruns_a_lane_whose_inputs_moved(repo: Path):
    assert _run_cli(repo, "coverage", "--json").returncode == 0
    _write(repo, "src/app.ts", APP_TS + "// touched\n")

    res = _run_cli(repo, "coverage", "--reuse-unchanged", "--json")

    assert res.returncode == 0, res.stderr
    assert _runs(repo) == 2
