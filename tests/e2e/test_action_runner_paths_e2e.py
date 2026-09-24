r"""The Action's bash steps score a pull request wherever the runner keeps its
temp directory and its workspace.

A windows-latest runner hands the composite steps RUNNER_TEMP as `D:\a\_temp`,
backslashes and all, and runs them under Git Bash; a self-hosted runner can put
a space or non-ASCII in either path. The steps build a state directory with
mktemp under RUNNER_TEMP, add a worktree at the fork point, run `crapkit
coverage --repo "$base"`, copy its store, run verify with `--base`, and build
the comment from files under that directory. The boundary hunt ran them in each
spelling and found every one sound; no test ran the steps at all.

Here each run step the job needs, read from action.yml with its `${{ }}`
expressions filled in the way a pull_request event with delta "true" fills
them, runs under `bash --noprofile --norc -eo pipefail` from the fixture
checkout. The steps that install crapkit and post the comment are left out:
the first would reinstall this tree, the second needs GitHub.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
STEPS = ("isolate this invocation's state", "score the base commit", "score the checkout",
         "the verdict", "the changed files", "build the comment")
CLEAN = "def alpha(n):\n    if n > 1:\n        n = n + 1\n    return n\n"
TANGLED = ("def alpha(n):\n" + "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
           + "    return n\n")


def _bash() -> str:
    """The bash a runner's `shell: bash` step runs under. On Windows that is
    Git Bash, never the WSL launcher in System32."""
    bash = shutil.which("bash")
    if os.name == "nt" and (bash is None or "system32" in bash.lower()):
        bash = _git_bash()
    if bash is None:
        pytest.skip("no bash to run the steps under")
    return bash


def _git_bash() -> str | None:
    """bin/bash.exe of the Git for Windows install whose git is on PATH."""
    git = shutil.which("git")
    candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else Path()
    return str(candidate) if candidate.is_file() else None


def _search_path() -> str:
    """PATH with the `python` and the `crapkit` console script the steps call
    first. In a venv the two sit side by side; a Windows install with no venv,
    as setup-python's is on a CI runner, keeps the script in Scripts."""
    scripts = sysconfig.get_path("scripts")
    if shutil.which("crapkit", path=scripts) is None:
        pytest.skip("needs the crapkit console script of this python (pip install -e .)")
    return os.pathsep.join([str(Path(sys.executable).parent), scripts, os.environ["PATH"]])


def _steps() -> list[dict]:
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    return [next(step for step in steps if step.get("name") == name) for name in STEPS]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                          capture_output=True, text=True, timeout=HANG_SECONDS).stdout.strip()


def _pull_request(workspace: Path) -> str:
    """A checkout whose base commit holds a clean function and whose head
    commit pushes it over the ceiling. The base commit's sha."""
    (workspace / "src").mkdir(parents=True)
    (workspace / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
        'languages = ["python"]\ncoverage_optional = true\n', encoding="utf-8")
    (workspace / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (workspace / "src" / "mod.py").write_text(CLEAN, encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    base = _git(workspace, "rev-parse", "HEAD")
    (workspace / "src" / "mod.py").write_text(TANGLED, encoding="utf-8")
    _git(workspace, "commit", "-qam", "head")
    return base


def _filled(text: str, values: dict) -> str:
    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: values[m.group(1)], text)


def _state_directory(output: Path) -> str:
    lines = output.read_text(encoding="utf-8").splitlines()
    return next(line.split("=", 1)[1] for line in lines if line.startswith("directory="))


def _run_steps(workspace: Path, runner_temp: Path, base: str) -> Path:
    """Every step in STEPS, in order, as the runner runs it. The state
    directory the first step made."""
    output = runner_temp / "github_output"
    output.write_text("", encoding="utf-8")
    env = {**os.environ, "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output),
           "GITHUB_ACTION_PATH": str(ROOT), "PATH": _search_path()}
    values = {"github.event.pull_request.base.sha": base, "inputs.top": "5",
              "github.event_name == 'pull_request' && inputs.delta == 'true'": "true"}
    for step in _steps():
        if step.get("id") != "state":
            values["steps.state.outputs.directory"] = _state_directory(output)
        step_env = {key: _filled(str(value), values) for key, value in (step.get("env") or {}).items()}
        done = subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c",
                               _filled(step["run"], values)], cwd=workspace, env={**env, **step_env},
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=HANG_SECONDS)
        assert done.returncode == 0, (step["name"], done.stdout, done.stderr)
    return Path(_state_directory(output))


# id -> the directory names a runner gives its temp directory and its workspace
RUNNERS = {
    "plain": ("_temp", "work"),
    "spaces": ("_temp dir", "work dir"),
    "non-ascii": ("_t\u00e9mp", "w\u00f6rk"),
}


@pytest.mark.parametrize("which", RUNNERS)
def test_the_steps_judge_the_pull_request_wherever_the_runner_keeps_its_paths(tmp_path, which):
    temp_name, work_name = RUNNERS[which]
    runner_temp = tmp_path.resolve() / temp_name
    runner_temp.mkdir()
    workspace = tmp_path.resolve() / work_name / "repo"
    base = _pull_request(workspace)

    state = _run_steps(workspace, runner_temp, base)

    assert (state / "crapkit-base.sha").read_text(encoding="utf-8").strip() == base, \
        (state / "crapkit-base.log").read_text(encoding="utf-8", errors="replace")
    assert (state / "crapkit-verify.exit").read_text(encoding="utf-8").strip() == "6"
    assert "alpha" in (state / "crapkit-comment.md").read_text(encoding="utf-8")
