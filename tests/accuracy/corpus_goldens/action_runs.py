"""Run the GitHub Action's own steps, read from action.yml, against a checkout.

run_action() reads action.yml from the checkout of the crapkit under test and
runs each step's `run:` body with bash, the way a runner does (`bash
--noprofile --norc -eo pipefail`), in the order the file lists them, with the
step's `env:` and every `${{ }}` expression answered for one Event. Three kinds
of step are left out: `uses:` steps and the pip install (crapkit is already
installed where kit.drive finds it), the step that makes the state directory
(run_action makes it), and any step that calls `gh api` (there is no pull
request to post to). An expression this module does not know stops the run,
so a new one in action.yml fails here instead of reading as empty.

The result holds each step's exit status and output, the comment the build
step printed, and the job's exit: the last step's status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess

import yaml

import hang_guard
from accuracy.corpus_goldens import shells
from accuracy.kit import drive, tiers

REPO = Path(__file__).resolve().parents[3]
_EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
_PULL_REQUEST_DELTA = "github.event_name == 'pull_request' && inputs.delta == 'true'"


class UnknownExpression(AssertionError):
    """action.yml asks for an expression the scenario runner cannot answer."""


@dataclass(frozen=True)
class Event:
    """What the workflow run hands the action; `date_now` freezes git's clock
    (GIT_TEST_DATE_NOW) so the churn window reads the fixture's dates, and `env`
    reaches every step (PYTHONPATH naming another crapkit, for one)."""
    name: str = "pull_request"
    base_sha: str = ""
    inputs: dict = field(default_factory=dict)
    date_now: int | None = None
    env: dict = field(default_factory=dict)


INPUT_DEFAULTS = {"gate": "false", "delta": "true", "top": "5", "python-version": "3.12"}


@dataclass(frozen=True)
class StepRun:
    name: str
    code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ActionRun:
    steps: tuple[StepRun, ...]
    state: Path

    @property
    def exit(self) -> int:
        return self.steps[-1].code

    @property
    def comment(self) -> str:
        built = [step.stdout for step in self.steps if step.name == "build the comment"]
        return built[0] if built else ""

    def file(self, name: str) -> str:
        """A file the steps left in the state directory, or in RUNNER_TEMP (its
        parent), where action.yml kept them before the state directory existed."""
        found = [path for path in (self.state / name, self.state.parent / name) if path.is_file()]
        return found[0].read_text(encoding="utf-8") if found else ""


def action_path() -> Path:
    """The checkout that holds the crapkit under test: its action.yml and
    tools/action/comment.py go with its code. An installed (non-editable)
    crapkit falls back to this repository."""
    python = os.environ.get(drive.PYTHON_ENV)
    if not python:
        return REPO
    done = subprocess.run([python, "-c", "import crapkit; print(crapkit.__file__)"],
                          capture_output=True, text=True)
    root = Path(done.stdout.strip()).resolve().parents[2]
    return root if (root / "action.yml").is_file() else REPO


def _answers(event: Event, state: Path) -> dict:
    inputs = {**INPUT_DEFAULTS, **event.inputs}
    pull_request = event.name == "pull_request"
    answers = {f"inputs.{name}": value for name, value in inputs.items()}
    answers.update({
        "steps.state.outputs.directory": state.as_posix(),
        "github.event.pull_request.base.sha": event.base_sha if pull_request else "",
        "github.event_name": event.name,
        _PULL_REQUEST_DELTA: str(pull_request and inputs["delta"] == "true").lower(),
        "github.token": "", "github.event.pull_request.number": "", "github.repository": "",
        "github.event.pull_request.head.repo.full_name": "",
    })
    return answers


def _answer(answers: dict, expression: str) -> str:
    if expression not in answers:
        raise UnknownExpression(f"action.yml asks for ${{{{ {expression} }}}}, which the "
                                "scenario runner does not answer; add it to _answers()")
    return answers[expression]


def expand(text: str, answers: dict) -> str:
    return _EXPRESSION.sub(lambda match: _answer(answers, match.group(1)), str(text))


def _left_out(step: dict) -> bool:
    body = step.get("run", "")
    return ("uses" in step or "pip install" in body or "gh api" in body
            or "GITHUB_OUTPUT" in body)


def _runs(step: dict, answers: dict) -> bool:
    condition = step.get("if")
    return condition is None or expand(condition, answers) == "true"


def steps(path: Path) -> list[dict]:
    action = yaml.safe_load((path / "action.yml").read_text(encoding="utf-8"))
    return action["runs"]["steps"]


def _step_env(step: dict, answers: dict, base: dict) -> dict:
    return {**base, **{name: expand(value, answers) for name, value in step.get("env", {}).items()}}


def _run_step(step: dict, answers: dict, env: dict, checkout: Path) -> StepRun:
    argv = [shells.executable("bash"), "--noprofile", "--norc", "-eo", "pipefail", "-c",
            expand(step["run"], answers)]
    done = hang_guard.run(argv, cwd=checkout, env=_step_env(step, answers, env), text=True,
                          encoding="utf-8", errors="replace")
    return StepRun(step.get("name", ""), done.returncode, done.stdout, done.stderr)


def _base_env(path: Path, scratch: Path, event: Event) -> dict:
    clock = {} if event.date_now is None else {"GIT_TEST_DATE_NOW": str(event.date_now)}
    return drive.child_env({"GITHUB_ACTION_PATH": path.as_posix(),
                            "RUNNER_TEMP": scratch.as_posix(),
                            "PYTHONIOENCODING": "utf-8", **clock, **event.env})


def _chosen(path: Path, answers: dict) -> list[dict]:
    return [step for step in steps(path) if not _left_out(step) and _runs(step, answers)]


def run_action(checkout: Path, event: Event, scratch: Path, path: Path | None = None) -> ActionRun:
    """Every step the action runs for `event`, in order, in `checkout`."""
    tiers.require_process("the Action's bash steps")
    path = path or action_path()
    state = scratch / "state"
    state.mkdir(parents=True)
    answers, env = _answers(event, state), _base_env(path, scratch, event)
    return ActionRun(tuple(_run_step(step, answers, env, checkout)
                           for step in _chosen(path, answers)), state)
