"""crapkit's GitHub Action, called by a repository that adopted crapkit.

Every cell builds the consumer with tools/deploy/consumer.py: the Python
quickstart's adoption committed on main (the fork point), then a feature
branch whose one commit makes the ratchet-marked `grade()` worse. The job is
README's "The whole job those four lines sit in", run by act inside the ci
image with no network (kit/act.py), and tools/deploy/assert_action.py reads
its log the way ci.yml's deploy-action job reads the GitHub runner's.

The gha-act-* cells take the action as README pins it,
`JeanFrancoisGagne/crapkit@v<candidate>`, from the sandbox's git mirror. The
gha-action-* cells are the act model of the jobs ci.yml and deploy.yml run on
a GitHub runner: the crapkit checkout sits at `crapkit/` in the workspace,
`python crapkit/tools/deploy/consumer.py` runs from the workspace root with
its defaults, the step is `uses: ./crapkit` with `pull-requests: read`, gate
"true" and delta "false", and the job's next step is ci.yml's own
`assert_action.py --cell <id>` over the action's state directory. The log
is then held to the same cell's checks. gha-action-windows runs that job on
this Windows machine under act, with `runs-on: windows-latest`.

On Linux the consumer commits `container_ok = true` on its lane
(docs/lanes.md, "Containers") because act runs the job inside the image's
container, where crapkit's guard would refuse the lane;
gha-action-container-job is the cell that leaves it out and asserts that
refusal.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from kit import act, docsnip, gitmirror, state, wheels
from kit.cells import cell
from kit.sandbox import Toolchain

PACKET = "deploy-action"
DEPLOY_TOOLS = wheels.SRC / "tools" / "deploy"
CONSUMER = Path("tools") / "deploy" / "consumer.py"
FORK_REPO = "someone/consumer-fork"
# The release a team pins behind the candidate: analysis version 10, where the
# candidate seeds under 12 (docs/upgrading.md, "Analysis version 11" and "Analysis
# version 12", which give the same three commands).
OLD = "0.7.6"
HARNESS = "act (pinned), self-hosted in the ci image"
RUNNER = "GitHub runner (act model)"


@functools.cache
def tool(name: str):
    """tools/deploy/<name>.py, a script ci.yml runs, as a module. Its
    directory goes on sys.path (last), as it is for the script itself, so
    assert_action.py finds consumer.py beside it."""
    if str(DEPLOY_TOOLS) not in sys.path:
        sys.path.append(str(DEPLOY_TOOLS))
    spec = importlib.util.spec_from_file_location(name, DEPLOY_TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_action():
    return tool("assert_action")


BREACH = tool("consumer").BREACH
STAMP_REFUSAL = _assert_action().STAMP_REFUSAL


def consumer_argv(box, candidate, checkout: bool, seed: str | None) -> list[str]:
    """ci.yml's line when crapkit is checked out beside the consumer
    (`python crapkit/tools/deploy/consumer.py`, its defaults seeding from that
    checkout), else the staged tree's script seeded from the candidate's wheel."""
    python = box.toolchain.python("3.12")
    if checkout:
        return [python, str(Path("crapkit") / CONSUMER), *(["--seed-from", seed] if seed else [])]
    return [python, str(candidate.staged / CONSUMER), "--root", ".", "--seed-from", seed or str(candidate.wheel)]


def build_consumer(box, candidate, *flags: str, checkout: bool = False, seed: str | None = None) -> tuple[Path, dict]:
    """consumer.py's repository at <box>/workspace, seeded by the candidate
    unless `seed` names another pip requirement. With `checkout`, the way the
    deploy-action job builds it: crapkit checked out to crapkit/ first."""
    root = box.root / "workspace"
    root.mkdir()
    if checkout:
        shutil.copytree(candidate.staged, root / "crapkit")
    step = box.run(consumer_argv(box, candidate, checkout, seed) + list(flags), cwd=root, expect=0)
    return root, json.loads(step.stdout.strip().splitlines()[-1])


def runner_job(cell_id: str, *, runs_on: str = "ubuntu-latest") -> act.Job:
    """ci.yml's deploy-action job in README's shape: `uses: ./crapkit` with
    pull-requests: read, gate "true", delta "false", then ci.yml's in-job
    `assert_action.py --cell <cell_id>`."""
    job = act.crapkit_job("./crapkit", permission="read", runs_on=runs_on, gate="true", delta="false")
    return job.plus(*act.assert_step(cell_id))


def check_runner_cell(runner, result, cell_id: str, event: str) -> None:
    """The job's log held to the cell's checks, and the in-job check passed."""
    runner.check(result, "--cell", cell_id, "--event", event)
    assert f"Success - Main {act.ASSERT_STEP}" in result.log, "ci.yml's in-job check failed; its output is in the log"
    assert "skip posting: not read" in result.log


def published(box, candidate, runner) -> str:
    """The candidate released on the mirror; README's `uses:` resolves to it."""
    mirror = gitmirror.make(box)
    mirror.publish(candidate.staged, candidate.version)
    return runner.release(mirror, candidate.version)


def readme_ref(candidate) -> str:
    ref = act.readme_job().action_ref()
    assert ref == f"{act.SLUG}@v{candidate.version}", f"README pins {ref}, the candidate is {candidate.version}"
    return ref


def logged_comment(result) -> str:
    return _assert_action().comment(_assert_action().lines(result.log))


def posted_bodies(runner) -> list[str]:
    return [comment["body"].strip() for comment in runner.comments()]


def rev(box, root: Path, ref: str) -> str:
    return box.run(["git", "rev-parse", ref], cwd=root, expect=0).stdout.strip()


def land_on_main(box, root: Path, change, message: str) -> dict:
    """`change` committed on main, and feature rebased onto it: a fix the
    team merged before the pull request's next push. The new fork, main and head."""
    box.run(["git", "checkout", "-q", "main"], cwd=root, expect=0)
    change()
    box.run(["git", "commit", "-q", "-a", "-m", message], cwd=root, env=box.commit_env(), expect=0)
    box.run(["git", "checkout", "-q", "feature"], cwd=root, expect=0)
    box.run(["git", "rebase", "-q", "main"], cwd=root, env=box.commit_env(), expect=0)
    return {"fork": rev(box, root, "main"), "main": rev(box, root, "main"), "head": rev(box, root, "HEAD")}


def container_fix() -> str:
    """docs/lanes.md's per-lane key for a container sized for the suite: the
    toml fence under "Containers", not the refusal quoted above it."""
    fixes = [block for block in docsnip.fences("docs/lanes.md") if block.heading == "Containers" and block.lang == "toml"]
    assert fixes, "docs/lanes.md > Containers: no toml fence"
    return fixes[0].text.strip()


def add_to_lane(config: Path, line: str) -> None:
    text = config.read_text(encoding="utf-8")
    assert text.count('parser = "coveragepy"\n') == 1, text
    config.write_text(text.replace('parser = "coveragepy"\n', f'parser = "coveragepy"\n{line}\n'), encoding="utf-8")


def local_crapkit(box, spec: str) -> dict:
    """crapkit and the lane's pytest-cov in a venv on the adopter's own
    machine: the environment of a shell with that venv first on PATH."""
    venv = box.root / "local-venv"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    scripts = venv / ("Scripts" if act.WINDOWS else "bin")
    box.run([str(scripts / "python"), "-m", "pip", "install", "-q", spec, "pytest", "pytest-cov"], expect=0)
    return {"PATH": os.pathsep.join([str(scripts), box.env["PATH"]])}


# --- the act cells ---------------------------------------------------------------------

@cell("gha-act-gate-base", channel="Action under act, README job", harness=HARNESS,
      scenario='fresh: gate "true" on a pull request; the base run at the fork point is made and the gate '
               "fails the check on the changed function, verify's code 6",
      use_cases="Action verdict and comment", os="linux", image="ci", cadence="nightly")
def test_gate_on_fails_the_pull_request_on_the_function_it_changed(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok")
    ref = published(box, candidate, runner)
    assert ref == readme_ref(candidate)

    result = runner.run(root, act.crapkit_job(ref, gate="true"), act.pull_request(built))

    runner.check(result, "--event", "pull_request", "--post", "posted", "--gate-code", "6",
                 "--function", BREACH, "--comment-has", "**verify failed, exit 6: complexity gate.**")
    assert f"the verdict covers the diff from {built['fork']}" in result.log
    assert posted_bodies(runner) == [logged_comment(result)]


@cell("gha-act-delta", channel="Action under act, README job", harness=HARNESS,
      scenario='fresh: delta "true" where main moved after the fork: the base run is at merge-base, not '
               "base.sha; gate off keeps the check green; a second run edits the one comment",
      use_cases="Action verdict and comment", os="linux", image="ci", cadence="nightly")
def test_delta_scores_the_fork_point_and_edits_one_comment(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok", "--main-moves")
    assert built["main"] != built["fork"]
    job = act.crapkit_job(published(box, candidate, runner), gate="false", delta="true")

    first = runner.run(root, job, act.pull_request(built))
    runner.check(first, "--event", "pull_request", "--expect-outcome", "success", "--gate", "off",
                 "--gate-code", "6", "--post", "posted", "--function", BREACH)
    assert f"the verdict covers the diff from {built['fork']}" in first.log
    assert "1 changed file(s)" in first.log

    second = runner.run(root, job, act.pull_request(built))
    assert [call["method"] for call in runner.calls()] == ["GET", "POST", "GET", "PATCH"]
    assert posted_bodies(runner) == [logged_comment(second)]


@cell("gha-act-push", channel="Action under act, README job", harness=HARNESS,
      scenario="fresh: a push event with gate \"true\": no base run, the ratchet regression fails the check "
               "with verify's code 7, the comment stays in the log and nothing is posted",
      use_cases="Action verdict", os="linux", image="ci", cadence="nightly")
def test_a_push_fails_on_the_ratchet_and_posts_nothing(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok")
    job = act.crapkit_job(published(box, candidate, runner), gate="true")

    result = runner.run(root, job, act.push(built))

    runner.check(result, "--event", "push", "--gate-code", "7", "--function", BREACH,
                 "--comment-has", "**verify failed, exit 7: ratchet regressions.**")
    assert runner.calls() == []


@cell("gha-act-readonly", channel="Action under act, README job", harness=HARNESS,
      scenario="fresh: a fork's pull request with a read-only token: gh's 403 on the post, the fork line, "
               "and the gate still fails the check",
      use_cases="Action comment", os="linux", image="ci", cadence="nightly")
def test_a_fork_pull_request_logs_the_refused_post_and_the_gate_still_decides(box, candidate):
    runner = act.Runner.make(box)
    runner.readonly()
    root, built = build_consumer(box, candidate, "--container-ok")
    job = act.crapkit_job(published(box, candidate, runner), gate="true")

    result = runner.run(root, job, act.pull_request(built, head_repo=FORK_REPO))

    runner.check(result, "--event", "pull_request", "--post", "denied", "--gate-code", "6", "--function", BREACH)
    assert "a fork pull request's token cannot write comments" in result.log
    refused = [call for call in runner.calls() if call["method"] == "POST"]
    assert [(call["exit"], call["body"].strip()) for call in refused] == [(1, logged_comment(result))]
    assert runner.comments() == []


@cell("gha-act-no-install", channel="Action under act, README job without its install step", harness=HARNESS,
      scenario="fresh: the job leaves out `pip install -e \".[dev]\"`: the lane cannot run, the comment says "
               "so, and gate \"true\" fails the check",
      use_cases="Action comment", os="linux", image="ci", cadence="nightly")
def test_a_job_without_the_install_step_names_the_missing_lane(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok")
    job = act.crapkit_job(published(box, candidate, runner), install=False, gate="true")

    result = runner.run(root, job, act.pull_request(built))

    runner.check(result, "--event", "pull_request", "--post", "posted", "--gate", "base",
                 "--comment-has", "**no verdict: `crapkit coverage` exited 5")


@cell("gha-act-monorepo", channel="Action under act, README job", harness=HARNESS,
      scenario="fresh: crapkit.toml in packages/api below the git top; README's job with the action's "
               "working-directory input; a verdict on packages/api",
      use_cases="Action verdict", os="linux", image="ci", cadence="nightly")
def test_a_monorepo_gets_a_verdict_on_the_package_it_adopted(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok", "--subdir", "packages/api")
    job = act.crapkit_job(published(box, candidate, runner), gate="true", **{"working-directory": "packages/api"})
    install = job.steps[job.find('pip install -e ".[dev]"')]
    job = job.with_step('pip install -e ".[dev]"', (*install, "  working-directory: packages/api"))

    result = runner.run(root, job, act.pull_request(built))

    runner.check(result, "--event", "pull_request", "--post", "posted", "--gate-code", "6", "--function", BREACH)


@cell("gha-act-skew", channel="Action @v0.7.6 under act", harness=HARNESS,
      scenario="skew: the marks were seeded by the candidate and the Action is still pinned at v0.7.6: exit 3 "
               "with the stamp refusal on the pull request; the pin moved to README's release gives a verdict",
      use_cases="team skew", os="linux", image="ci", cadence="nightly")
def test_an_action_pinned_behind_the_marks_refuses_until_the_pin_moves(box, candidate):
    assert OLD in wheels.releases()
    runner = act.Runner.make(box)
    root, _ = build_consumer(box, candidate, "--container-ok")
    built = land_on_main(box, root, lambda: state.launchers_written_back(box, root),
                         "crapkit.toml names the launcher until every reader runs 0.8.1")
    mirror = gitmirror.make(box)
    behind = act.crapkit_job(runner.release(mirror, OLD), gate="true")

    refused = runner.run(root, behind, act.pull_request(built))
    analysis = state.analysis_version(candidate)
    runner.check(refused, "--event", "pull_request", "--post", "posted", "--gate-code", "3",
                 "--comment-has", f"`crapkit verify` exited 3 and wrote no verdict: {STAMP_REFUSAL}{analysis} ",
                 "--comment-has", "but this run measures [crapkit-analysis=10 ")

    mirror.publish(candidate.staged, candidate.version)
    moved = runner.run(root, act.crapkit_job(runner.release(mirror, candidate.version), gate="true"),
                       act.pull_request(built))
    runner.check(moved, "--event", "pull_request", "--post", "posted", "--gate-code", "6", "--function", BREACH)


@cell("gha-act-skew", channel="Action @candidate under act", harness=HARNESS,
      scenario="skew: marks stamped by a newer analysis than the candidate's; the refusal says a newer crapkit "
               "wrote them and does not send the team to re-seed",
      use_cases="team skew", os="linux", image="ci", cadence="nightly")
def test_marks_from_a_newer_crapkit_are_named_as_newer(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok")
    analysis = int(root.joinpath("crapkit-ratchet.tsv").read_text(encoding="utf-8").split("crapkit-analysis=")[1].split()[0])
    newer = land_on_main(box, root, lambda: bump_stamp(root / "crapkit-ratchet.tsv", analysis),
                         "ratchet: marks from the next release")
    job = act.crapkit_job(published(box, candidate, runner), gate="true")

    result = runner.run(root, job, act.pull_request(newer))

    runner.check(result, "--event", "pull_request", "--post", "posted", "--gate-code", "3",
                 "--comment-has", f"{STAMP_REFUSAL}{analysis + 1} ", "--comment-has", "newer")
    assert "re-baseline with `crapkit ratchet seed`" not in logged_comment(result)


def bump_stamp(marks: Path, analysis: int) -> None:
    text = marks.read_text(encoding="utf-8")
    marks.write_text(text.replace(f"crapkit-analysis={analysis} ", f"crapkit-analysis={analysis + 1} "),
                     encoding="utf-8")


@cell("gha-action-container-job", channel="README job in a container", harness=RUNNER,
      scenario="fresh: deploy.yml's job runs inside a container with no container_ok: the guard refuses the "
               "lane, coverage's 5 fails the check and the log names the key; docs/lanes.md's key, merged on "
               "main, turns it into a verdict on the marked function",
      use_cases="container guard", os="linux", image="ci", cadence="nightly")
def test_a_container_job_names_the_guard_and_the_documented_key_clears_it(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, checkout=True)

    refused = runner.run(root, runner_job("gha-action-container-job"), act.push(built))
    check_runner_cell(runner, refused, "gha-action-container-job", "push")
    assert "host-only (container runs OOM); set container_ok = true" in refused.log

    fixed = land_on_main(box, root, lambda: add_to_lane(root / "crapkit.toml", container_fix()),
                         "crapkit: this job's container is sized for the suite")
    verdict = runner.run(root, runner_job("gha-action-consumer"), act.push(fixed))
    check_runner_cell(runner, verdict, "gha-action-consumer", "push")


def upgrade_steps(box, root: Path, candidate) -> None:
    """docs/upgrading.md's commands for analysis version 11, run on the
    adopter's machine with the candidate installed; the store they leave
    stays on that machine."""
    shell = local_crapkit(box, str(candidate.wheel))
    for command in docsnip.commands(docsnip.fence("docs/upgrading.md", "Analysis version 11", contains="ratchet prune")):
        box.script(command, cwd=root, env=shell, expect=0)
    shutil.rmtree(root / ".crapkit")


def move_the_pin(box, candidate):
    """A repo adopted under OLD whose job runs the action at @vOLD and then at
    ./crapkit (the candidate) in one workspace, then ci.yml's in-job check,
    as deploy.yml's gha-action-tag-upgrade job does. The runner, the repo,
    the consumer, and the candidate step's part of the log."""
    assert OLD in wheels.releases()
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok", checkout=True, seed=f"crapkit=={OLD}")
    job = act.upgrade_job(runner.release(gitmirror.make(box), OLD), "./crapkit", gate="true", delta="false")
    moved = runner.run(root, job.plus(*act.assert_step("gha-action-tag-upgrade")), act.push(built))
    old, new = moved.log.split("Run Main ./crapkit", 1)
    assert "gate is off: verify's code 7 is in the comment" in old
    return runner, root, built, act.Result(moved.step, new, moved.outcome)


@cell("gha-action-tag-upgrade", channel="Action pin moved", harness=RUNNER,
      scenario="upgrade: @v0.7.6 then ./crapkit in one job, .crapkit carried; the moved pin refuses the "
               "0.7.6 marks naming the re-seed, and after docs/upgrading.md's steps the gate judges again",
      use_cases="Action pin move", os="linux", image="ci", cadence="nightly")
def test_moving_the_pin_carries_the_store_and_the_upgrade_steps_restore_the_gate(box, candidate):
    runner, root, built, moved = move_the_pin(box, candidate)
    check_runner_cell(runner, moved, "gha-action-tag-upgrade", "push")
    assert "crapkit coverage exited 0" in moved.log and "sqlite" not in moved.log.lower()

    upgraded = land_on_main(box, root, lambda: upgrade_steps(box, root, candidate), "ratchet: re-seed after the upgrade")
    after = runner.run(root, runner_job("gha-action-consumer"), act.push(upgraded))
    check_runner_cell(runner, after, "gha-action-consumer", "push")


@cell("gha-action-tag-upgrade", channel="Action pin moved", harness=RUNNER,
      scenario="upgrade: the store the @v0.7.6 step left holds a failed verify; the moved pin's refusal must "
               "not send the reader to a run id that exists only in that runner's store",
      use_cases="Action pin move", os="linux", image="ci", cadence="nightly")
def test_the_moved_pins_refusal_names_no_run_the_reader_cannot_see(box, candidate):
    _, _, _, moved = move_the_pin(box, candidate)
    body = logged_comment(moved)

    assert STAMP_REFUSAL in body
    assert "--baseline" not in body and "from run " not in body


# --- the GitHub runner's jobs, modelled under act ---------------------------------------------

@cell("gha-action-consumer", channel="`uses: ./crapkit`, push event", harness=RUNNER,
      scenario='fresh: pull-requests: read, delta "false", gate "true"; outcome failure; "gate is on: exiting '
               "with verify's code N\"; 'no pull request on this event'; the comment names the function; "
               "ci.yml's in-job check passes",
      use_cases="Action verdict", os="linux", image="ci", cadence="push")
def test_the_runner_job_on_a_push_fails_on_the_marked_function(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok", checkout=True)

    result = runner.run(root, runner_job("gha-action-consumer"), act.push(built))

    check_runner_cell(runner, result, "gha-action-consumer", "push")


@cell("gha-action-readonly-token", channel="`uses: ./crapkit`, pull_request event", harness=RUNNER,
      scenario="fresh: pull-requests: read; 'posting the crapkit comment exited' with gh's 403; the gate "
               "still decides; ci.yml's in-job check passes",
      use_cases="Action comment", os="linux", image="ci", cadence="push")
def test_the_runner_job_on_a_pull_request_with_a_read_token(box, candidate):
    runner = act.Runner.make(box)
    runner.readonly()
    root, built = build_consumer(box, candidate, "--container-ok", checkout=True)

    result = runner.run(root, runner_job("gha-action-readonly-token"), act.pull_request(built))

    check_runner_cell(runner, result, "gha-action-readonly-token", "pull_request")
    assert "gh's own error is above, and the verdict is in this job's log" in result.log


@cell("gha-action-windows", channel="`uses: ./crapkit` on a Windows runner", harness="GitHub runner (act model, Windows)",
      scenario="fresh: deploy.yml's job with runs-on windows-latest, the action's bash steps in Git Bash: a push "
               "fails on the marked function with verify's code 7 and posts nothing; a pull request with a "
               "read token logs gh's 403 and the gate still decides; ci.yml's in-job check passes on both",
      use_cases="Action verdict, Action comment", os="windows", image=None, cadence="nightly")
def test_the_runner_job_on_windows_fails_on_the_marked_function(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, checkout=True)
    job = runner_job("gha-action-windows", runs_on="windows-latest")

    pushed = runner.run(root, job, act.push(built))
    check_runner_cell(runner, pushed, "gha-action-windows", "push")

    runner.readonly()
    pulled = runner.run(root, job, act.pull_request(built))
    check_runner_cell(runner, pulled, "gha-action-windows", "pull_request")


# --- the pieces, on every OS -------------------------------------------------------------------

ACT_LOG = """\
[crapkit.yml/crapkit] ⭐ Run Main build the comment
[crapkit.yml/crapkit]   | <!-- crapkit-action -->
[crapkit.yml/crapkit]   |
[crapkit.yml/crapkit]   | **verify failed, exit 7: ratchet regressions.**
[crapkit.yml/crapkit]   | - ratchet: `calc/grade.py` `grade( score , attempts , late , bonus )` 50.875 -> 82.9
[crapkit.yml/crapkit]   ✅  Success - Main build the comment [359.004518ms]
[crapkit.yml/crapkit] ⭐ Run Main post the comment
[crapkit.yml/crapkit]   | no pull request on this event: the comment above was not posted
[crapkit.yml/crapkit] ⭐ Run Main the exit code
[crapkit.yml/crapkit]   | gate is on: exiting with verify's code 7
"""


def runner_log(text: str) -> str:
    """An act log as the Actions API serves a job's log: a BOM, a timestamp per
    line, and ##[group] where act prints a step header."""
    stamped = []
    for raw in text.splitlines():
        entry = _assert_action().line(raw)
        stamped.append(entry.text if entry.output else "##[group]Run " + raw.rsplit("Main ", 1)[-1])
    return "﻿" + "".join(f"2026-09-24T10:00:0{index % 10}.1234567Z {text}\n" for index, text in enumerate(stamped))


PUSH_ARGS = ["--event", "push", "--outcome", "failure", "--gate-code", "7", "--function", BREACH]


@pytest.mark.kit
@pytest.mark.parametrize("form", ["act", "runner"])
def test_assert_action_reads_an_act_log_and_a_runner_log_alike(form, tmp_path, monkeypatch, capsys):
    log = tmp_path / "job.log"
    log.write_text(ACT_LOG if form == "act" else runner_log(ACT_LOG), encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))

    assert _assert_action().main(["--log", str(log), *PUSH_ARGS]) == 0

    body = _assert_action().comment(_assert_action().lines(log.read_text(encoding="utf-8")))
    assert body.startswith("<!-- crapkit-action -->") and body.endswith("50.875 -> 82.9")
    assert "ok   gate: \"gate is on: exiting with verify's code 7\"" in capsys.readouterr().out
    assert "**verify failed, exit 7" in (tmp_path / "summary.md").read_text(encoding="utf-8")


@pytest.mark.kit
@pytest.mark.parametrize("args, failed", [
    (["--expect-outcome", "success"], "FAIL outcome"),
    (["--gate-code", "6"], "FAIL gate"),
    (["--gate", "off"], "FAIL gate"),
    (["--function", "route("], "FAIL comment"),
    (["--post", "denied"], "FAIL posting"),
    (["--post", "posted"], "FAIL posting"),
])
def test_assert_action_names_each_promise_the_log_breaks(args, failed, tmp_path, capsys):
    log = tmp_path / "job.log"
    log.write_text(ACT_LOG, encoding="utf-8")

    assert _assert_action().main(["--log", str(log), *PUSH_ARGS, *args]) == 1
    assert failed in capsys.readouterr().out


@pytest.mark.kit
def test_a_denied_post_counts_only_with_the_403_before_it_and_the_gate_after_it():
    module = _assert_action()
    denied = ["gh: Resource not accessible by integration (HTTP 403)",
              "posting the crapkit comment exited 1: gh's own error is above",
              "gate is on: exiting with verify's code 6"]
    as_log = [module.Line(text, True) for text in denied]

    assert module.check_denied(as_log, "on").ok
    assert not module.check_denied(as_log[1:] + as_log[:1], "on").ok
    assert not module.check_denied([module.Line(denied[1].replace("1:", "0"), True)], "on").ok


def json_lines(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines() if text]


@pytest.mark.kit
def test_the_stub_gh_answers_the_actions_calls_and_refuses_like_gh(box):
    state = box.root / "gh-state"
    state.mkdir()
    box.prepend_path(act.install_stub(box.root / "gh-bin"))
    env = {"STUB_GH_STATE": str(state), "GH_TOKEN": act.TOKEN}
    body = box.root / "comment.json"
    body.write_text(json.dumps({"body": "<!-- crapkit-action -->\nfirst"}), encoding="utf-8")
    comments, lookup = "repos/o/r/issues/7/comments", ["--paginate", "--jq", ".[0].id // empty"]

    assert box.run(["gh", "api", comments, *lookup], env=env, expect=0).stdout == ""
    box.run(["gh", "api", "--silent", "--method", "POST", comments, "--input", str(body)], env=env, expect=0)
    found = box.run(["gh", "api", comments, *lookup], env=env, expect=0).stdout.strip()
    box.run(["gh", "api", "--silent", "--method", "PATCH", f"repos/o/r/issues/comments/{found}", "--input",
             str(body)], env=env, expect=0)
    (state / "mode").write_text("readonly\n", encoding="utf-8")
    refused = box.run(["gh", "api", "--method", "POST", comments, "--input", str(body)], env=env, expect=1)

    assert refused.stderr.strip() == "gh: Resource not accessible by integration (HTTP 403)"
    no_token = box.run(["gh", "api", comments], env={"STUB_GH_STATE": str(state)}, expect=4)
    assert no_token.stderr.startswith("gh: To use GitHub CLI in a GitHub Actions workflow")
    assert [call["method"] for call in json_lines(state / "calls.jsonl")] == ["GET", "POST", "GET", "PATCH", "POST"]


@pytest.mark.kit
def test_assert_action_fetches_a_finished_jobs_log_through_gh(box):
    state = box.root / "gh-state"
    (state / "logs").mkdir(parents=True)
    box.prepend_path(act.install_stub(box.root / "gh-bin"))
    jobs = [{"id": 10, "name": "deploy-action", "status": "in_progress"},
            {"id": 11, "name": "deploy-action", "status": "completed"},
            {"id": 12, "name": "test", "status": "completed"}]
    (state / "jobs.json").write_text(json.dumps({"total_count": 3, "jobs": jobs}), encoding="utf-8")
    (state / "logs" / "11.txt").write_text(runner_log(ACT_LOG), encoding="utf-8")
    env = {"STUB_GH_STATE": str(state), "GH_TOKEN": act.TOKEN, "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "5"}
    script = wheels.SRC / "tools" / "deploy" / "assert_action.py"

    box.run([box.toolchain["runner_python"], str(script), "--job", "deploy-action", "--wait", "0", *PUSH_ARGS],
            env=env, expect=0)
    assert [call["path"] for call in json_lines(state / "calls.jsonl")] == [
        "repos/o/r/actions/runs/5/jobs?per_page=100", "repos/o/r/actions/jobs/11/logs"]


@pytest.mark.kit
def test_the_act_job_is_readmes_job_with_the_cells_edits():
    job = act.crapkit_job("./crapkit", install=False, permission="read", gate="true", delta="false")
    text = job.text()

    assert act.readme_job().action_ref().startswith(f"{act.SLUG}@v")
    assert "on: [push, pull_request]" in text and "pull-requests: read" in text
    assert "runs-on: ubuntu-latest" in text
    assert 'pip install -e ".[dev]"' not in text and "actions/setup-python@v5" in text
    assert ('- uses: ./crapkit\n        id: crapkit\n        continue-on-error: true\n        with:\n'
            '          gate: "true"\n          delta: "false"\n') in text
    assert text.endswith('run: echo "crapkit-step-outcome=${{ steps.crapkit.outcome }}"\n')
    with pytest.raises(docsnip.DocSnipError, match="no step holds 'npm ci'"):
        job.with_step("npm ci", None)


@pytest.mark.kit
def test_the_runner_job_is_ci_ymls_job_with_its_in_job_check():
    text = runner_job("gha-action-windows", runs_on="windows-latest").text()

    assert "runs-on: windows-latest" in text and "runs-on: ubuntu-latest" not in text
    assert text.endswith(f"- name: {act.ASSERT_STEP}\n        if: always()\n        run: python "
                         "crapkit/tools/deploy/assert_action.py --cell gha-action-windows --outcome "
                         '"${{ steps.crapkit.outcome }}"\n')


@pytest.mark.kit
def test_consumer_excludes_what_the_workspace_held_before_it(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "crapkit").mkdir()
    (tmp_path / "crapkit-v0.7.6").mkdir()
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    consumer = tool("consumer")

    assert consumer.already_there(tmp_path) == ["/crapkit-v0.7.6/", "/crapkit/", "/notes.txt"]
    defaults = consumer.parse([])
    assert defaults.root == Path(".") and defaults.seed_from == str(DEPLOY_TOOLS.parents[1])


def state_dir(temp: Path, name: str, code: str, body: str, mtime: int) -> Path:
    """One crapkit.* directory as the action's steps leave it under RUNNER_TEMP."""
    state = temp / f"crapkit.{name}"
    state.mkdir(parents=True)
    (state / "crapkit-verify.exit").write_text(f"{code}\n", encoding="utf-8")
    (state / "crapkit-comment.md").write_text(body, encoding="utf-8")
    os.utime(state / "crapkit-verify.exit", (mtime, mtime))
    return state


@pytest.mark.kit
def test_assert_action_in_the_job_reads_the_newest_state_and_skips_the_log_lines(tmp_path, monkeypatch, capsys):
    older = f"<!-- crapkit-action -->\n**`crapkit verify` exited 3: {STAMP_REFUSAL}10 ...**"
    state_dir(tmp_path, "old", "3", older, 1_000_000)
    state_dir(tmp_path, "new", "7", f"<!-- crapkit-action -->\n- ratchet: `calc/grade.py` `{BREACH}`\n", 2_000_000)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert _assert_action().main(["--cell", "gha-action-windows", "--outcome", "failure"]) == 0
    out = capsys.readouterr().out
    assert "ok   gate: verify's code in crapkit-verify.exit is 7" in out and "skip posting: not read" in out

    assert _assert_action().main(["--cell", "gha-action-tag-upgrade", "--outcome", "failure"]) == 1
    assert "FAIL gate: verify's code in crapkit-verify.exit is 7, expected 3" in capsys.readouterr().out


@pytest.mark.kit
def test_assert_action_in_a_job_whose_action_never_ran_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert _assert_action().main(["--cell", "gha-action-consumer", "--event", "push", "--outcome", "failure"]) == 1
    assert "FAIL state: no crapkit.*/crapkit-verify.exit under $RUNNER_TEMP" in capsys.readouterr().out


@pytest.mark.kit
def test_a_cell_brings_its_checks_and_the_run_brings_its_event(monkeypatch):
    module = _assert_action()
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    upgrade = module.parse(["--cell", "gha-action-tag-upgrade", "--outcome", "failure"])
    moved = module.parse(["--cell", "gha-action-consumer", "--gate-code", "6", "--outcome", "failure"])

    assert (upgrade.event, upgrade.gate_code) == ("pull_request", "3")
    assert f"{STAMP_REFUSAL}10 " in upgrade.comment_has and moved.gate_code == "6"
    assert [module.default_post(event) for event in ("pull_request", "push", "schedule")] == ["denied", "none", "none"]
    monkeypatch.delenv("GITHUB_EVENT_NAME")
    with pytest.raises(SystemExit):
        module.parse(["--cell", "gha-action-consumer", "--outcome", "failure"])


@pytest.mark.kit
def test_act_and_the_pinned_actions_come_from_toolchain_json(tmp_path):
    (tmp_path / "act.exe").write_text("", encoding="utf-8")
    (tmp_path / "actions").mkdir()
    chain = Toolchain({"act": str(tmp_path / "act.exe"), "act_actions": str(tmp_path / "actions")},
                      tmp_path / "toolchain.json")

    assert act.act_binary(chain) == str(tmp_path / "act.exe")
    assert act.actions_dir(chain) == tmp_path / "actions"


@pytest.mark.kit
@pytest.mark.parametrize("find", ["act_binary", "actions_dir"])
def test_a_toolchain_json_that_names_no_act_gets_the_command_that_installs_it(find, tmp_path, monkeypatch):
    """A directory under the toolchain root that toolchain.json does not name
    is never used: the cell found a hand-unpacked act-windows/ on one machine
    and failed on every machine whose toolchain.py had put act elsewhere."""
    monkeypatch.setattr(act, "IMAGE_ACT", str(tmp_path / "opt" / "act"))
    monkeypatch.setattr(act, "IMAGE_ACTIONS", str(tmp_path / "opt" / "cache"))
    (tmp_path / "act-windows").mkdir()
    (tmp_path / "act-windows" / "act.exe").write_text("", encoding="utf-8")
    (tmp_path / "act-actions").mkdir()
    chain = Toolchain({}, tmp_path / "toolchain.json")

    with pytest.raises(AssertionError) as refused:
        getattr(act, find)(chain)
    assert str(tmp_path / "toolchain.json") in str(refused.value)
    assert "python tools/deploy/toolchain.py" in str(refused.value)


@pytest.mark.kit
def test_act_tells_a_lost_exit_line_from_one_printed_late():
    header, failed = "[crapkit.yml/crapkit] ⭐ Run Main the exit code\n", "[crapkit.yml/crapkit]   ❌  Failure - Main the exit code [13ms]\n"
    printed = "[crapkit.yml/crapkit]   | gate is on: exiting with verify's code 6\n"
    old_step = header + "[crapkit.yml/crapkit]   | gate is off: verify's code 7 is in the comment, this check stays green\n"

    assert act.lost_exit_line(header + failed)
    assert not act.lost_exit_line(header + printed + failed)
    assert not act.lost_exit_line(header + failed + "[crapkit.yml/crapkit] exit status 6\n" + printed)
    assert act.lost_exit_line(old_step + header + failed)


@pytest.mark.kit
def test_consumer_marks_exactly_one_coveragepy_lane_container_ok():
    lane = '[[lane]]\nname = "py"\nparser = "coveragepy"\nscopes = ["calc"]\n'
    consumer = tool("consumer")

    assert consumer.with_container_ok(lane) == lane.replace('coveragepy"\n', 'coveragepy"\ncontainer_ok = true\n')
    with pytest.raises(SystemExit, match="found 0"):
        consumer.with_container_ok(lane.replace("coveragepy", "istanbul"))
