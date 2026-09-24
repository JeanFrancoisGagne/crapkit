"""The Action's pull request flow, replayed from action.yml step by step.

tests/unit/test_action_contract.py reads the step bodies as text and runs the
exit step alone. This file runs the bodies that make a verdict, in order and
under `bash --noprofile --norc -eo pipefail` as `shell: bash` does on a runner:
the base commit scored in a worktree at the fork point, the checkout scored,
verify against that base, the worklist, the changed files, the comment, and the
exit code with `gate: "true"`. `crapkit` and `python` are shell functions that
start this suite's interpreter; the step bodies are action.yml's own, read and
never edited.

Three pull requests, each with t::c0 failing at the fork point and after:

- the fork point's lane declares no `results_artifact` and the pull request
  adds one: no run recorded which tests failed at the fork point, so t::c0
  counts as new, exit 8, and verify says the failure may predate the change;
- both commits declare one: t::c0 is forgiven and the check passes;
- the pull request's lane writes a junit that does not parse: coverage exits
  5, verify never runs, and the comment says there is no verdict.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard
from conftest import child_env, git, git_commit_all, git_init_repo

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable.replace("\\", "/")

STEPS = ("isolate this invocation's state", "score the base commit", "score the checkout",
         "the verdict", "the ranked worklist", "the changed files", "build the comment",
         "the exit code")

SRC = "def hot(n):\n    if n:\n        return 1\n    return 0\n"

# The lane: an istanbul artifact for src/a.py and a junit of three tests with
# t::c0 failing, or, from the pull request that breaks it, a junit cut short.
GEN = """import json, os
key = os.path.join(os.getcwd(), "src", "a.py")
json.dump({key: {"path": key,
    "fnMap": {"0": {"name": "hot", "decl": {"start": {"line": 1}},
                    "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
    "f": {"0": 1}, "branchMap": {}, "b": {},
    "statementMap": {"1": {"start": {"line": 2}, "end": {"line": 2}}}, "s": {"1": 1}}},
    open("cov.json", "w"))
open("junit.xml", "w").write(%s)
"""
FINISHED = ("'<testsuite name=\"t\" tests=\"3\"><testcase classname=\"t\" name=\"c0\">"
            "<failure message=\"boom\"/></testcase><testcase classname=\"t\" name=\"c1\"/>"
            "<testcase classname=\"t\" name=\"c2\"/></testsuite>'")
CUT_SHORT = "'<testsuite name=\"t\" tests=\"3\"><testcase classname=\"t\" na'"

TOML = f"""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = '"{PY}" gen.py'
artifact = "cov.json"
parser = "istanbul"
scopes = ["src"]
"""
DECLARED = 'results_artifact = "junit.xml"\n'


def _bash() -> str:
    """The bash a runner's `shell: bash` step runs under, or a skip. On Windows
    the `bash` on PATH can be the WSL launcher under System32, which cannot read
    the files this test writes; Git's own bash can."""
    bash = shutil.which("bash")
    if os.name == "nt" and (bash is None or "system32" in bash.lower()):
        bash = _git_bash()
    if bash is None:
        pytest.skip("no bash on PATH to run the Action's steps under")
    return bash


def _git_bash() -> str | None:
    found = shutil.which("git")
    candidate = Path(found).parent.parent / "bin" / "bash.exe" if found else None
    return str(candidate) if candidate and candidate.is_file() else None


def _steps() -> dict[str, dict]:
    yaml = pytest.importorskip("yaml")
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    steps = {step.get("name"): step for step in action["runs"]["steps"]}
    missing = [name for name in STEPS if name not in steps]
    assert not missing, f"action.yml lost the steps {missing}"
    return steps


def _commit(repo: Path, message: str, *, declared: bool, junit: str) -> str:
    (repo / "crapkit.toml").write_text(TOML + (DECLARED if declared else ""), encoding="utf-8",
                                       newline="\n")
    (repo / "gen.py").write_text(GEN % junit, encoding="utf-8", newline="\n")
    git_commit_all(repo, message)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


def pull_request(tmp_path: Path, *, fork_declares: bool, pr_junit: str) -> tuple[Path, str]:
    """A repo whose HEAD is a pull request one commit past its fork point."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text(SRC, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(".crapkit/\ncov.json\njunit.xml\n", encoding="utf-8")
    git_init_repo(repo)
    fork = _commit(repo, "the fork point", declared=fork_declares, junit=FINISHED)
    git(repo, "checkout", "-q", "-b", "pr")
    (repo / "src" / "b.py").write_text("def other(x):\n    return x\n", encoding="utf-8")
    _commit(repo, "the pull request", declared=True, junit=pr_junit)
    return repo, fork


class Runner:
    """The runner's side of a job: RUNNER_TEMP, GITHUB_OUTPUT, the action's
    path, and each `${{ }}` expression the replayed steps spell, resolved for a
    pull request with `gate: "true"` and the default `delta` and `top`."""

    def __init__(self, tmp_path: Path, repo: Path, fork: str):
        self.repo, self.bash, self.steps = repo, _bash(), _steps()
        (tmp_path / "runner-temp").mkdir()
        self.env = child_env({"RUNNER_TEMP": (tmp_path / "runner-temp").as_posix(),
                              "GITHUB_OUTPUT": (tmp_path / "github-output").as_posix(),
                              "GITHUB_ACTION_PATH": ROOT.as_posix(),
                              "CRAPKIT_OVERRIDE_REASON": None})
        self.expressions = {
            "${{ github.event.pull_request.base.sha }}": fork,
            "${{ inputs.top }}": "5",
            "${{ inputs.gate }}": "true",
            "${{ github.event_name == 'pull_request' && inputs.delta == 'true' }}": "true",
        }
        self.script = tmp_path / "step.sh"

    def run(self, name: str) -> subprocess.CompletedProcess:
        step = self.steps[name]
        if "if" in step and self.expressions[step["if"]] != "true":
            return subprocess.CompletedProcess([], 0, "", "")
        env = {**self.env, **{key: self.expressions[value] for key, value in
                              step.get("env", {}).items()}}
        self.script.write_text(f'crapkit() {{ "{PY}" -m crapkit "$@"; }}\n'
                               f'python() {{ "{PY}" "$@"; }}\n{step["run"]}',
                               encoding="utf-8", newline="\n")
        return hang_guard.run([self.bash, "--noprofile", "--norc", "-eo", "pipefail",
                               self.script.as_posix()], cwd=self.repo, env=env, text=True,
                              encoding="utf-8", errors="replace")

    def job(self) -> dict:
        """Every replayed step in order; what each printed, and the state dir."""
        printed = {}
        for name in STEPS:
            done = self.run(name)
            printed[name] = done
            if name == "isolate this invocation's state":
                self._state_from(Path(self.env["GITHUB_OUTPUT"]))
        return printed

    def _state_from(self, output: Path) -> None:
        directory = output.read_text(encoding="utf-8").split("directory=", 1)[1].strip()
        self.expressions["${{ steps.state.outputs.directory }}"] = directory
        self.state = Path(directory)

    def read(self, name: str) -> str:
        path = self.state / name
        return path.read_text(encoding="utf-8") if path.is_file() else ""


def replay(tmp_path: Path, *, fork_declares: bool, pr_junit: str) -> tuple[Runner, dict]:
    repo, fork = pull_request(tmp_path, fork_declares=fork_declares, pr_junit=pr_junit)
    runner = Runner(tmp_path, repo, fork)
    printed = runner.job()
    for name in STEPS[:-1]:
        assert printed[name].returncode == 0, (name, printed[name].stdout, printed[name].stderr)
    return runner, printed


def test_a_fork_point_that_recorded_no_failures_blames_the_failure_and_says_it_may_predate(
        tmp_path):
    runner, printed = replay(tmp_path, fork_declares=False, pr_junit=FINISHED)

    verdict = json.loads(runner.read("crapkit-verify.json"))
    assert runner.read("crapkit-verify.exit").strip() == "8"
    assert (verdict["new_failures"], verdict["lanes_without_baseline_results"]) == (["t::c0"], ["py"])
    assert ("warning: lane 'py': no trusted run at or behind the baseline recorded which of its "
            "tests failed, so its 1 new failure may predate this change") in printed["the verdict"].stderr
    comment = runner.read("crapkit-comment.md")
    assert "- new test failure: `t::c0`" in comment, comment
    assert ("- lane `py`: the baseline recorded no failure list, so its new failures may predate "
            "this change") in comment, comment
    assert printed["the exit code"].returncode == 8, printed["the exit code"].stdout


def test_a_fork_point_that_recorded_the_failure_forgives_it(tmp_path):
    runner, printed = replay(tmp_path, fork_declares=True, pr_junit=FINISHED)

    verdict = json.loads(runner.read("crapkit-verify.json"))
    assert runner.read("crapkit-verify.exit").strip() == "0"
    assert (verdict["new_failures"], verdict["forgiven_failures"]) == ([], ["t::c0"])
    assert "**verify passed.**" in runner.read("crapkit-comment.md")
    assert printed["the exit code"].returncode == 0, printed["the exit code"].stdout


def test_a_checkout_junit_that_does_not_parse_leaves_no_verdict(tmp_path):
    """The current-side absence inside the Action: the checkout's lane runs, so
    the unreadable junit fails it and coverage exits 5 before verify could
    reuse anything. verify does not run and nothing reads as a pass."""
    runner, printed = replay(tmp_path, fork_declares=True, pr_junit=CUT_SHORT)

    comment = runner.read("crapkit-comment.md")
    assert runner.read("crapkit-coverage.exit").strip() == "5"
    assert (runner.read("crapkit-verify.exit").strip(), runner.read("crapkit-verify.json")) == ("5", "")
    assert "no verdict" in comment and "verify passed" not in comment, comment
    assert printed["the exit code"].returncode == 5, printed["the exit code"].stdout
