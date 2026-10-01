"""The deploy record is keyed on the tag commit's git tree id.

The deploy stage once keyed the record on candidate.py's hash of the working
tree's bytes. A Windows checkout with core.autocrlf=true holds two text fixtures
with CRLF on disk while `git status` stays clean, so the 0.8.1 release hashed the
tag commit to 2d2b705b and deploy.yml's scope job, on an LF Linux checkout of the
same commit, hashed it to fedbb54a and refused the run. Git's tree id names the
committed content, not the bytes a checkout happens to write, so both sides read
one key from one commit, and any committed byte moves it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hang_guard import HANG_SECONDS
from test_release_tool import release

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
sys.path.append(str(ROOT / "tools" / "deploy"))

import candidate  # noqa: E402
import export  # noqa: E402

VERSION = "0.5.2"
LOCK = '[newest]\ncrapkit = "0.5.1"\nlizard = "1.24.0"\n'
FILES = {"pyproject.toml": '[project]\nname = "crapkit"\nversion = "0.9.0"\n',
         "tests/fixtures/two_lines.txt": "first\nsecond\n",
         "tests/fixtures/notes.md": "# notes\n\nline\n"}


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=Deploy Test", "-c", "user.email=deploy@example.test",
                           *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def origin(tmp_path) -> Path:
    """A repository whose tag commit stores every text file with LF."""
    root = tmp_path / "origin"
    for name, text in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text.encode("utf-8"))
    git(root.parent, "init", "-q", "-b", "main", str(root))
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", ".")
    git(root, "commit", "-qm", "the tag commit")
    git(root, "tag", f"v{VERSION}")
    return root


def _clone(origin: Path, dest: Path, autocrlf: str) -> Path:
    git(origin.parent, "-c", f"core.autocrlf={autocrlf}", "clone", "-q",
        "--config", f"core.autocrlf={autocrlf}", str(origin), str(dest))
    return dest


@pytest.fixture
def checkouts(origin, tmp_path) -> tuple[Path, Path]:
    """The release's Windows checkout (CRLF on disk) and the runner's LF one."""
    return _clone(origin, tmp_path / "windows", "true"), _clone(origin, tmp_path / "runner", "false")


def _source_hash(checkout: Path, out: Path) -> str:
    export.write_tree(checkout, out.with_suffix(".tar"))
    record = candidate.stage(out.with_suffix(".tar"), out, _lock(out.parent))
    return record["source_hash"]


def _lock(directory: Path) -> Path:
    path = directory / "wheelhouse.lock"
    path.write_text(LOCK, encoding="utf-8")
    return path


def test_the_two_checkouts_hold_different_bytes_and_a_clean_status(checkouts):
    """The fault's premise: same commit, same clean status, other bytes on disk."""
    windows, runner = checkouts
    fixture = "tests/fixtures/two_lines.txt"

    assert (windows / fixture).read_bytes() == b"first\r\nsecond\r\n"
    assert (runner / fixture).read_bytes() == b"first\nsecond\n"
    assert git(windows, "status", "--porcelain") == git(runner, "status", "--porcelain") == ""


def test_the_old_working_tree_hash_splits_one_commit_in_two(checkouts, tmp_path):
    """What the 0.8.1 scope job saw: 2d2b705b on Windows, fedbb54a on the runner."""
    windows, runner = checkouts

    assert _source_hash(windows, tmp_path / "w") != _source_hash(runner, tmp_path / "r")


def test_an_autocrlf_checkout_and_an_lf_checkout_give_one_key(checkouts):
    windows, runner = checkouts

    key = release.deploy_key(windows, VERSION)

    assert key == release.deploy_key(runner, VERSION) == git(runner, "rev-parse", "HEAD^{tree}")
    assert len(key) in (40, 64) and int(key, 16) >= 0


def test_one_committed_byte_moves_the_key(checkouts):
    windows, _ = checkouts
    before = release.deploy_key(windows, VERSION)
    path = windows / "tests" / "fixtures" / "notes.md"
    path.write_bytes(path.read_bytes() + b"!")
    git(windows, "commit", "-qam", "one byte")
    git(windows, "tag", "-f", f"v{VERSION}")

    assert release.deploy_key(windows, VERSION) != before


def test_a_missing_tag_refuses_with_its_name(origin):
    git(origin, "tag", "-d", f"v{VERSION}")

    with pytest.raises(release.ReleaseError, match=f"v{VERSION}"):
        release.deploy_key(origin, VERSION)


# --- the scope job compares the same key on the tree it checked out ---------------------------

def _scope_step() -> dict:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["scope"]["steps"]
    (step,) = [step for step in steps if step.get("if") == "inputs.tree != ''"]
    return step


def _bash() -> str | None:
    """Git's own bash on Windows: System32's bash.exe starts WSL, not a shell."""
    if os.name != "nt":
        return shutil.which("bash")
    found = shutil.which("git")
    tops = Path(found).resolve().parents[1:3] if found else []  # Git\cmd\git.exe, Git\mingw64\bin\git.exe
    beside = [top / "bin" / "bash.exe" for top in tops]
    return next((str(path) for path in beside if path.is_file()), None)


def _run_scope(checkout: Path, tree: str) -> subprocess.CompletedProcess:
    bash = _bash()
    if bash is None:
        pytest.skip("no bash to run the scope step with")
    env = {**os.environ, "TREE": tree}
    return subprocess.run([bash, "-c", _scope_step()["run"]], cwd=checkout, env=env,
                          capture_output=True, text=True, timeout=HANG_SECONDS)


def test_the_scope_step_reads_the_tree_id_and_no_working_tree_bytes():
    step = _scope_step()

    assert step["env"] == {"TREE": "${{ inputs.tree }}"}
    assert "git rev-parse 'HEAD^{tree}'" in step["run"]
    assert "candidate.py" not in step["run"] and "export.py" not in step["run"]


def test_the_runner_accepts_the_key_the_windows_release_recorded(checkouts):
    windows, runner = checkouts

    done = _run_scope(runner, release.deploy_key(windows, VERSION))

    assert (done.returncode, done.stderr) == (0, "")


def test_the_runner_refuses_another_tree_and_names_both(checkouts):
    _, runner = checkouts
    other = "0" * 40

    done = _run_scope(runner, other)

    found = git(runner, "rev-parse", "HEAD^{tree}")
    assert done.returncode == 1
    assert done.stderr.strip() == (f"deploy scope: the checkout's tree is {found}, and the release "
                                   f"dispatched this run for {other}")


def test_the_run_is_named_for_the_tree_the_release_dispatched_with():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = workflow.get("on", workflow.get(True))

    assert workflow["run-name"] == "deploy ${{ inputs.cadence || github.event_name }} ${{ inputs.tree }}"
    assert on["workflow_dispatch"]["inputs"]["tree"]["default"] == ""
    assert "source_hash" not in on["workflow_dispatch"]["inputs"]
