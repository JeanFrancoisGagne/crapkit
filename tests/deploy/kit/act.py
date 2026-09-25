"""GitHub Actions jobs run offline by act, for the Action cells.

    runner = act.Runner.make(box)
    job = act.readme_job().uses("JeanFrancoisGagne/crapkit@v0.8.1", gate="true")
    result = runner.run(consumer, job, act.pull_request(built))
    result.outcome      # steps.crapkit.outcome: "failure"
    result.log          # what act printed, the job log a user reads
    runner.comments()   # what the stub gh holds on the pull request

The job is README's "The whole job those four lines sit in" fence, read
through docsnip and edited step by step: the crapkit step takes an id,
continue-on-error and the cell's inputs, and a last step prints its outcome.

act runs the job on this machine (`-P ubuntu-latest=-self-hosted`) with
--action-offline-mode, so nothing is fetched:

  actions   every `actions/<name>@<ref>` the workflow and the crapkit action
            use resolves (--local-repository) to the checkout of that action
            pins.toml [actions] pins, which the ci image pre-fetched; a ref
            other than the pinned SHA is noted in the transcript
  crapkit   `JeanFrancoisGagne/crapkit@v<version>` resolves to that tag of the
            sandbox's git mirror, exported to a directory
  python    setup-python finds the pinned 3.12 in act's tool cache, a fresh
            venv per job, as a hosted runner starts each job on a clean image
  gh        kit/stub_gh/gh first on PATH, answering from a state directory
  pip       the sandbox's pip.conf: the wheelhouse and the candidate's dist/

The workspace is act's copy of the consumer with .gitignore not applied: act
otherwise honours .git/info/exclude and leaves out the `crapkit/` checkout a
`uses: ./crapkit` step reads.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tarfile
import time
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

import hang_guard

from kit import docsnip, wheels
from kit.transcript import Step

WINDOWS = os.name == "nt"
STUB_GH = Path(__file__).resolve().parent / "stub_gh" / "gh"
IMAGE_ACT = "/opt/act/bin/act"
IMAGE_ACTIONS = "/opt/act/cache"
REPO = "example/consumer"
TOKEN = "ghs_deploy_cell_token"
SLUG = "JeanFrancoisGagne/crapkit"
USES = re.compile(r"uses:\s*(?P<name>actions/[\w.-]+)@(?P<ref>[\w.-]+)")
OUTCOME = re.compile(r"crapkit-step-outcome=(?P<outcome>\w*)")
OUTCOME_STEP = ("- if: always()", '  run: echo "crapkit-step-outcome=${{ steps.crapkit.outcome }}"')
# One job runs the lanes up to three times (base, checkout, verify's reuse)
# and two pip installs: several suites' worth of the one hang bound.
JOB_SECONDS = 4 * hang_guard.HANG_SECONDS


# --- the job -------------------------------------------------------------------------

@dataclass(frozen=True)
class Job:
    """A workflow with one job, edited as lines: the head through `steps:`,
    then each step's lines without the list's indent."""
    head: tuple[str, ...]
    steps: tuple[tuple[str, ...], ...]
    indent: str

    @classmethod
    def parse(cls, text: str) -> "Job":
        lines = text.splitlines()
        start = next(index for index, line in enumerate(lines) if line.strip() == "steps:") + 1
        indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
        return cls(tuple(lines[:start]), tuple(_steps(lines[start:], indent)), indent)

    def text(self) -> str:
        body = [self.indent + line if line else "" for step in self.steps for line in step]
        return "\n".join([*self.head, *body]) + "\n"

    def find(self, holding: str) -> int:
        found = [index for index, step in enumerate(self.steps) if holding in "\n".join(step)]
        if not found:
            raise docsnip.DocSnipError(f"README.md > The GitHub Action: no step holds {holding!r}")
        return found[0]

    def with_step(self, holding: str, lines: tuple[str, ...] | None) -> "Job":
        """The step holding `holding` replaced by `lines`, or dropped for None."""
        index = self.find(holding)
        kept = () if lines is None else (tuple(lines),)
        return replace(self, steps=self.steps[:index] + kept + self.steps[index + 1:])

    def with_head(self, prefix: str, line: str) -> "Job":
        """The head line starting with `prefix` (stripped) replaced by `line`, indent kept."""
        index = next((i for i, text in enumerate(self.head) if text.strip().startswith(prefix)), None)
        if index is None:
            raise docsnip.DocSnipError(f"README.md > The GitHub Action: no {prefix!r} line in the job")
        old = self.head[index]
        head = list(self.head)
        head[index] = old[:len(old) - len(old.lstrip())] + line
        return replace(self, head=tuple(head))

    def plus(self, *lines: str) -> "Job":
        return replace(self, steps=self.steps + (tuple(lines),))

    def action_ref(self) -> str:
        """What the crapkit step's `uses:` names, e.g. JeanFrancoisGagne/crapkit@v0.8.1."""
        return self.steps[self.find(f"uses: {SLUG}@")][0].split("uses:", 1)[1].strip()

    def uses(self, ref: str, **inputs: str) -> "Job":
        """The crapkit step as `ref`, with an id, continue-on-error and these inputs."""
        return self.with_step(f"uses: {SLUG}@", action_step(ref, inputs))


def _steps(lines: list[str], indent: str) -> list[tuple[str, ...]]:
    steps: list[list[str]] = []
    for line in lines:
        if line.startswith(indent + "- "):
            steps.append([])
        if steps:
            steps[-1].append(line[len(indent):])
    return [tuple(step) for step in steps]


def action_step(ref: str, inputs: dict) -> tuple[str, ...]:
    with_lines = ("  with:", *(f'    {name}: "{value}"' for name, value in inputs.items())) if inputs else ()
    return (f"- uses: {ref}", "  id: crapkit", "  continue-on-error: true", *with_lines)


def readme_job() -> Job:
    """README's whole Action job, from the stamped tree."""
    return Job.parse(docsnip.fence("README.md", "The GitHub Action", contains="jobs:").text)


def crapkit_job(ref: str, *, events: str = "[push, pull_request]", install: bool = True,
                permission: str = "write", **inputs: str) -> Job:
    """README's job as a cell runs it: on these events, the crapkit step at
    `ref` with `inputs`, the install step kept or left out, and a last step
    that prints the crapkit step's outcome."""
    job = readme_job().with_head("on:", f"on: {events}").uses(ref, **inputs)
    job = job.with_head("pull-requests:", f"pull-requests: {permission}")
    job = job if install else job.with_step('pip install -e ".[dev]"', None)
    return job.plus(*OUTCOME_STEP)


# --- events -----------------------------------------------------------------------------

def pull_request(built: dict, *, number: int = 7, base: str | None = None, head_repo: str = REPO) -> dict:
    """A pull_request payload for `built` (consumer.py's JSON): base.sha is
    main's tip unless named, head.repo another repository for a fork."""
    pull = {"number": number, "base": {"sha": base or built["main"], "ref": "main", "repo": {"full_name": REPO}},
            "head": {"sha": built["head"], "ref": "feature", "repo": {"full_name": head_repo}}}
    return {"action": "synchronize", "number": number, "pull_request": pull, "repository": {"full_name": REPO}}


def push(built: dict) -> dict:
    return {"ref": "refs/heads/feature", "before": built["fork"], "after": built["head"],
            "repository": {"full_name": REPO}}


# --- the pins act resolves actions to ------------------------------------------------------

def pinned_actions() -> dict[str, str]:
    """actions/<name> -> the SHA pins.toml pins it at."""
    pins = tomllib.loads((wheels.SRC / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))
    return dict(value.split("#")[0].strip().split("@", 1) for value in pins["actions"].values())


def pinned_python(minor: str = "3.12") -> str:
    pins = tomllib.loads((wheels.SRC / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))
    return next(version for version in pins["python"]["versions"] if version.startswith(minor + "."))


def uses_in(texts: list[str]) -> set[tuple[str, str]]:
    return {(match["name"], match["ref"]) for text in texts for match in USES.finditer(text)}


def fetched_actions(actions: Path) -> dict[str, Path]:
    """actions/<name> -> its pinned checkout under `actions`, where the image fetched one."""
    pinned = {name: actions / f"{name.replace('/', '-')}@{sha}" for name, sha in pinned_actions().items()}
    return {name: path for name, path in pinned.items() if path.is_dir()}


def resolved(fetched: dict[str, Path], used: set[tuple[str, str]]) -> list[tuple[str, Path]]:
    """Each used `name@ref` with a fetched checkout, and that checkout."""
    return [(f"{name}@{ref}", fetched[name]) for name, ref in sorted(used) if name in fetched]


@dataclass
class Result:
    step: Step
    log: str
    outcome: str


@dataclass
class Runner:
    box: object
    act: str
    actions: Path
    cache: Path
    state: Path
    releases: dict[str, Path] = field(default_factory=dict)
    runs: int = 0

    @classmethod
    def make(cls, box) -> "Runner":
        root = box.root / "act"
        state = root / "gh-state"
        state.mkdir(parents=True, exist_ok=True)
        box.prepend_path(install_stub(root / "gh-bin"))
        return cls(box, act_binary(box.toolchain), Path(box.toolchain.get("act_actions", IMAGE_ACTIONS)),
                   root / "cache", state)

    # --- the stub gh ---
    def readonly(self) -> None:
        (self.state / "mode").write_text("readonly\n", encoding="utf-8")

    def comments(self) -> list[dict]:
        path = self.state / "comments.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def calls(self) -> list[dict]:
        path = self.state / "calls.jsonl"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return [json.loads(line) for line in text.splitlines() if line]

    # --- crapkit releases from the mirror ---
    def release(self, mirror, version: str) -> str:
        """`JeanFrancoisGagne/crapkit@v<version>`, resolved to that tag of `mirror`."""
        dest = self.box.root / "act" / f"crapkit-v{version}"
        if not dest.exists():
            archive = self.box.tmp / f"crapkit-v{version}.tar"
            mirror.git("archive", "--format=tar", "-o", str(archive), f"v{version}")
            with tarfile.open(archive) as tar:
                tar.extractall(dest, filter="tar")
        self.releases[f"{SLUG}@v{version}"] = dest
        return f"{SLUG}@v{version}"

    # --- running a job ---
    def local_repositories(self, workflow: str, workspace: Path) -> list[str]:
        """--local-repository for every action ref the job reaches."""
        texts = [workflow, *(path.read_text(encoding="utf-8") for path in _action_files(workspace, self.releases))]
        mapped = resolved(fetched_actions(self.actions), uses_in(texts))
        for ref, path in mapped:
            self.box.transcript.note(f"act resolves {ref} to the pinned checkout {path.name}")
        return [f"{ref}={path}" for ref, path in [*mapped, *self.releases.items()]]

    def argv(self, event: str, workflow: Path, payload: Path, workspace: Path) -> list[str]:
        argv = [self.act, event, "-e", str(payload), "-W", str(workflow), "-C", str(workspace),
                "-P", "ubuntu-latest=-self-hosted", "--action-offline-mode", "--no-cache-server",
                "--container-daemon-socket", "-", "--action-cache-path", str(self.cache),
                "--use-gitignore=false", "-s", f"GITHUB_TOKEN={TOKEN}"]
        repositories = self.local_repositories(workflow.read_text(encoding="utf-8"), workspace)
        return argv + [flag for repository in repositories for flag in ("--local-repository", repository)]

    def run(self, workspace: Path, job: Job, payload: dict, *, event: str | None = None) -> Result:
        """One act run of `job` on `workspace` for a pull_request or push `payload`."""
        self.runs += 1
        event = event or ("pull_request" if "pull_request" in payload else "push")
        workflow, event_file = self._write(job, payload)
        _origin(self.box, workspace)
        fresh_tool_cache(self.box, self.cache / "tool_cache")
        step = self._spawn(self.argv(event, workflow, event_file, workspace), workspace)
        self.box.transcript.attach(f"act run {self.runs} log", step.stdout)
        found = OUTCOME.search(step.stdout)
        return Result(step, step.stdout, found["outcome"] if found else "")

    def _write(self, job: Job, payload: dict) -> tuple[Path, Path]:
        where = self.box.root / "act" / f"run-{self.runs}"
        where.mkdir(parents=True, exist_ok=True)
        (where / "crapkit.yml").write_text(job.text(), encoding="utf-8")
        (where / "event.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return where / "crapkit.yml", where / "event.json"

    def _spawn(self, argv: list[str], cwd: Path) -> Step:
        env = {**self.box.env, "STUB_GH_STATE": str(self.state)}
        started = time.monotonic()
        done = hang_guard.run(argv, cwd=str(cwd), env=env, timeout=JOB_SECONDS)
        decode = _decode(done.stdout), _decode(done.stderr)
        step = Step(argv, str(cwd), done.returncode, *decode, round(time.monotonic() - started, 2), "act")
        return self.box.transcript.add(step)

    def check(self, result: Result, *flags: str, expect: int = 0):
        """tools/deploy/assert_action.py over this run's log, as ci.yml runs it."""
        log = self.box.root / "act" / f"run-{self.runs}" / "job.log"
        log.write_text(result.log, encoding="utf-8")
        script = wheels.SRC / "tools" / "deploy" / "assert_action.py"
        return self.box.run([self.box.toolchain["runner_python"], str(script), "--log", str(log),
                             "--outcome", result.outcome, *flags], expect=expect)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _action_files(workspace: Path, releases: dict[str, Path]) -> list[Path]:
    """The crapkit action.yml files a job can reach: ./crapkit and each release."""
    candidates = [workspace / "crapkit" / "action.yml", *(path / "action.yml" for path in releases.values())]
    return [path for path in candidates if path.exists()]


def install_stub(bin_dir: Path) -> Path:
    """kit/stub_gh/gh as `gh` in bin_dir. On Windows the script is gh.py,
    started by gh.cmd for a CreateProcess or cmd caller and by an sh `gh` for
    Git Bash, which a Windows runner's `shell: bash` step is."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / ("gh.py" if WINDOWS else "gh")
    shutil.copyfile(STUB_GH, script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if WINDOWS:
        (bin_dir / "gh.cmd").write_text('@python "%~dp0gh.py" %*\r\n', encoding="ascii")
        (bin_dir / "gh").write_text('#!/bin/sh\nexec python "$(dirname "$0")/gh.py" "$@"\n', encoding="ascii",
                                    newline="\n")
    return bin_dir


def _origin(box, workspace: Path) -> None:
    """An origin remote, as actions/checkout leaves one; act names the
    repository (github.repository) from it."""
    remotes = box.run(["git", "remote"], cwd=workspace, expect=0).stdout.split()
    if "origin" not in remotes:
        box.run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"], cwd=workspace, expect=0)


def fresh_tool_cache(box, tool_cache: Path) -> Path:
    """RUNNER_TOOL_CACHE holding the pinned 3.12 as setup-python finds it
    (Python/<version>/x64 and its .complete marker): a new venv each job."""
    version = pinned_python("3.12")
    home = tool_cache / "Python" / version
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(home / "x64")], expect=0)
    (home / "x64.complete").write_text("", encoding="utf-8")
    return home


def act_binary(toolchain) -> str:
    found = toolchain.get("act") or (IMAGE_ACT if Path(IMAGE_ACT).exists() else None)
    if not found:
        raise AssertionError("kit: this toolchain holds no act; the ci image has it at " + IMAGE_ACT)
    return found
