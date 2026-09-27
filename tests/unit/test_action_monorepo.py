"""The Action on a monorepo whose crapkit.toml sits below the repository top.

Every crapkit step of action.yml ran at the workspace root. On a checkout whose
crapkit root is packages/api, `crapkit coverage` found no crapkit.toml there
and exited 3, the base run refused the same way, and with gate "true" every
pull request failed its check (measured under act on README's job). The
`working-directory` input moves those steps.

These tests run the steps' own bodies under bash, the way a runner runs a
composite step: from the directory the step names, with its `env` and its
`${{ }}` expressions filled in, against a real repository and the real CLI.
"""
import os
import re
import sys
from pathlib import Path

import pytest

import hang_guard
from test_action_contract import ROOT, _bash, _step_named

API = "packages/api"

CONFIG = """[crapkit]
target = 6
mutation_command = "python -c pass"

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = ".crapkit/py.json"
parser = "coveragepy"
scopes = ["calc"]
full_suite = false
container_ok = true
"""

# The lane writes an empty coverage.py report and runs no suite. container_ok
# lets this file run inside a container, where crapkit refuses a coveragepy lane
# that does not set it.
MAKE_COV = ('import json, os\n'
            'os.makedirs(".crapkit", exist_ok=True)\n'
            'json.dump({"meta": {"branch_coverage": True}, "files": {}},'
            ' open(".crapkit/py.json", "w"))\n')

# What a pull_request job with delta "true" runs after the two setup steps, in
# order. The post step needs a pull request on GitHub and is left out.
_STEPS = ("score the base commit", "score the checkout", "the verdict", "the ranked worklist",
          "the changed files", "build the comment", "the exit code")

_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def _git(repo: Path, *args: str) -> str:
    result = hang_guard.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                            cwd=repo, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write(root: Path, files: dict) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8", newline="\n")


def _monorepo(workspace: Path) -> dict:
    """main holds the adoption in packages/api beside a web package; feature
    edits one function in each. Returns the event's base.sha and the fork."""
    _git(workspace.parent, "init", "-q", "-b", "main", workspace.name)
    _git(workspace, "config", "core.autocrlf", "false")
    _write(workspace, {f"{API}/crapkit.toml": CONFIG, f"{API}/make_cov.py": MAKE_COV,
                       f"{API}/.gitignore": ".crapkit/\n", "packages/web/index.js": "export const a = 1;\n",
                       f"{API}/calc/grade.py": "def grade(score):\n    return score\n"})
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "adopt crapkit in packages/api")
    fork = _git(workspace, "rev-parse", "HEAD")
    _git(workspace, "checkout", "-q", "-b", "feature")
    _write(workspace, {f"{API}/calc/grade.py": "def grade(score):\n    return score + 1\n",
                       "packages/web/index.js": "export const a = 2;\n"})
    _git(workspace, "commit", "-q", "-a", "-m", "a change in each package")
    return {"base": fork, "fork": fork}


def _shim(bin_dir: Path) -> None:
    """`crapkit` on PATH as this interpreter's crapkit, which is what the
    install step leaves a runner with."""
    bin_dir.mkdir()
    python = Path(sys.executable).as_posix()
    (bin_dir / "crapkit").write_text(f'#!/bin/sh\nexec "{python}" -m crapkit "$@"\n',
                                     encoding="utf-8", newline="\n")
    (bin_dir / "crapkit").chmod(0o755)


def _expand(value, context: dict) -> str:
    """`${{ expr }}` filled in from `context`. An expression the context does
    not hold is a KeyError, so a step that starts reading a new one fails here
    instead of running on an empty string."""
    return _EXPR.sub(lambda found: context[found.group(1)], str(value))


def _run_step(name: str, job: dict):
    """One step's body under `bash --noprofile --norc -eo pipefail`, from its
    `working-directory` under the workspace (the workspace itself when it
    names none), which is where a runner starts a composite step."""
    step = _step_named(name)
    script = job["tmp"] / f"{len(job['ran'])}.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    env = {**job["env"], **{key: _expand(value, job["context"]) for key, value in step.get("env", {}).items()}}
    cwd = job["workspace"] / _expand(step.get("working-directory", "."), job["context"])
    result = hang_guard.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                            cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace")
    job["ran"].append(f"--- {name} (exit {result.returncode})\n{result.stdout}{result.stderr}")
    return result


def _job_env(tmp: Path) -> dict:
    _shim(tmp / "bin")
    python_dir = str(Path(sys.executable).parent)
    path = os.pathsep.join([str(tmp / "bin"), python_dir, os.environ["PATH"]])
    return {**os.environ, "PATH": path, "GITHUB_ACTION_PATH": ROOT.as_posix(),
            "RUNNER_TEMP": tmp.as_posix(), "GITHUB_OUTPUT": (tmp / "outputs").as_posix()}


def _state_directory(job: dict) -> str:
    """The state step's own output: a fresh directory under RUNNER_TEMP."""
    (job["tmp"] / "outputs").write_text("", encoding="utf-8")
    assert _run_step("isolate this invocation's state", job).returncode == 0, job["ran"]
    return (job["tmp"] / "outputs").read_text(encoding="utf-8").strip().split("=", 1)[1]


def _pull_request_job(tmp: Path, event: dict, **inputs: str) -> dict:
    """README's job on a pull request, gate "true", run step by step. Returns
    the state directory, each step's result and the log of the whole job."""
    job = {"tmp": tmp, "workspace": tmp / "workspace", "env": _job_env(tmp), "ran": []}
    attempted = _EXPR.fullmatch(_step_named("score the base commit")["if"]).group(1)
    job["context"] = {"inputs.gate": "true", "inputs.delta": "true", "inputs.top": "5",
                      "inputs.working-directory": ".", attempted: "true",
                      "github.event.pull_request.base.sha": event["base"],
                      **{f"inputs.{name}": value for name, value in inputs.items()}}
    job["context"]["steps.state.outputs.directory"] = _state_directory(job)
    job["results"] = {name: _run_step(name, job) for name in _STEPS}
    job["state"] = Path(job["context"]["steps.state.outputs.directory"])
    return job


def _read(state: Path, name: str) -> str:
    path = state / name
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


@pytest.fixture(scope="module")
def monorepo_job(tmp_path_factory) -> tuple[dict, dict]:
    """One job, read by every test below: it runs the lanes twice."""
    tmp = tmp_path_factory.mktemp("monorepo")
    (tmp / "workspace").mkdir()
    event = _monorepo(tmp / "workspace")
    return _pull_request_job(tmp, event, **{"working-directory": API}), event


def test_the_steps_score_the_package_the_working_directory_names(monorepo_job):
    """The report's shape: crapkit.toml in packages/api, README's job with the
    input set. Coverage runs there, the base run is made at the fork point's
    packages/api, and gate "true" exits with verify's own code."""
    job, event = monorepo_job
    state = job["state"]

    outcome = {"coverage exit": _read(state, "crapkit-coverage.exit"),
               "base run at": _read(state, "crapkit-base.sha"),
               "verify exit": _read(state, "crapkit-verify.exit"),
               "job exit": job["results"]["the exit code"].returncode}

    assert outcome == {"coverage exit": "0", "base run at": event["fork"], "verify exit": "0",
                       "job exit": 0}, "\n".join(job["ran"])


def test_the_changed_files_are_named_from_the_package_the_worklist_ranks(monorepo_job):
    """The worklist names grade.py `calc/grade.py`, from the crapkit root. A
    top-relative `packages/api/calc/grade.py` matched no row of it, and the web
    package's file is no part of what this root scores."""
    job, _ = monorepo_job

    changed = (job["state"] / "crapkit-changed.txt").read_bytes().split(b"\0")

    assert changed == [b"calc/grade.py", b""], "\n".join(job["ran"])


def test_the_comment_judges_the_package_diff(monorepo_job):
    job, _ = monorepo_job

    comment = _read(job["state"], "crapkit-comment.md")

    assert "**verify passed.**" in comment and "1 changed file." in comment, "\n".join(job["ran"])
    assert "### Worklist: 1 changed file" in comment


def test_a_job_that_leaves_the_input_out_is_told_to_set_it(tmp_path):
    """README's job as it was, on the same repository: coverage at the top
    still exits 3, and the comment now says which input moves it."""
    (tmp_path / "workspace").mkdir()
    job = _pull_request_job(tmp_path, _monorepo(tmp_path / "workspace"))

    comment = _read(job["state"], "crapkit-comment.md")

    assert _read(job["state"], "crapkit-coverage.exit") == "3", "\n".join(job["ran"])
    assert "set the action's `working-directory` input to the directory that holds crapkit.toml" in comment, comment
