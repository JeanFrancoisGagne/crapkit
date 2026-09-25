"""crapkit's GitHub Action, called by a repository that adopted crapkit.

Every cell builds the consumer with tools/deploy/consumer.py: the Python
quickstart's adoption committed on main (the fork point), then a feature
branch whose one commit makes the ratchet-marked `grade()` worse. The job is
README's "The whole job those four lines sit in", run by act inside the ci
image with no network (kit/act.py), and tools/deploy/assert_action.py reads
its log the way ci.yml's deploy-action job reads the GitHub runner's.

The gha-act-* cells take the action as README pins it,
`JeanFrancoisGagne/crapkit@v<candidate>`, from the sandbox's git mirror. The
gha-action-* cells are the act model of the jobs a GitHub runner runs: the
crapkit checkout sits at `crapkit/` in the workspace, consumer.py runs from
it, and the step is `uses: ./crapkit` with `pull-requests: read`.

The consumer commits `container_ok = true` on its lane (docs/lanes.md,
"Containers") because act runs the job inside the image's container, where
crapkit's guard would refuse the lane; gha-action-container-job is the cell
that leaves it out and asserts that refusal.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from kit import act, docsnip, gitmirror, wheels
from kit.cells import cell

PACKET = "deploy-action"
CONSUMER = Path("tools") / "deploy" / "consumer.py"
BREACH = "grade( score , attempts , late , bonus )"
FORK_REPO = "someone/consumer-fork"
HARNESS = "act (pinned), self-hosted in the ci image"


@functools.cache
def tool(name: str):
    """tools/deploy/<name>.py, a script ci.yml runs, as a module."""
    spec = importlib.util.spec_from_file_location(name, wheels.SRC / "tools" / "deploy" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_action():
    return tool("assert_action")


def build_consumer(box, candidate, *flags: str, checkout: bool = False) -> tuple[Path, dict]:
    """consumer.py's repository at <box>/workspace. With `checkout`, the way
    the deploy-action job builds it: crapkit checked out to crapkit/, and
    consumer.py run from that checkout, seeding from it."""
    root = box.root / "workspace"
    root.mkdir()
    script, seed = candidate.staged / CONSUMER, str(candidate.wheel)
    if checkout:
        shutil.copytree(candidate.staged, root / "crapkit")
        script, seed = root / "crapkit" / CONSUMER, "./crapkit"
    step = box.run([box.toolchain.python("3.12"), str(script), "--root", ".", "--seed-from", seed, *flags],
                   cwd=root, expect=0)
    return root, json.loads(step.stdout.strip().splitlines()[-1])


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


# --- the GitHub runner's jobs, modelled under act ---------------------------------------------

@cell("gha-action-consumer", channel="`uses: ./crapkit`, push event", harness="GitHub runner (act model)",
      scenario='fresh: pull-requests: read, delta "false", gate "true"; outcome failure; "gate is on: exiting '
               "with verify's code N\"; 'no pull request on this event'; the comment names the function",
      use_cases="Action verdict", os="linux", image="ci", cadence="push")
def test_the_runner_job_on_a_push_fails_on_the_marked_function(box, candidate):
    runner = act.Runner.make(box)
    root, built = build_consumer(box, candidate, "--container-ok", checkout=True)
    job = act.crapkit_job("./crapkit", permission="read", gate="true", delta="false")

    result = runner.run(root, job, act.push(built))

    runner.check(result, "--event", "push", "--gate-code", "7", "--function", BREACH)


@cell("gha-action-readonly-token", channel="`uses: ./crapkit`, pull_request event",
      harness="GitHub runner (act model)",
      scenario="fresh: pull-requests: read; 'posting the crapkit comment exited' with gh's 403; the gate "
               "still decides",
      use_cases="Action comment", os="linux", image="ci", cadence="push")
def test_the_runner_job_on_a_pull_request_with_a_read_token(box, candidate):
    runner = act.Runner.make(box)
    runner.readonly()
    root, built = build_consumer(box, candidate, "--container-ok", checkout=True)
    job = act.crapkit_job("./crapkit", permission="read", gate="true", delta="false")

    result = runner.run(root, job, act.pull_request(built))

    runner.check(result, "--event", "pull_request", "--post", "denied", "--gate-code", "7", "--function", BREACH)
    assert "gh's own error is above, and the verdict is in this job's log" in result.log


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
    assert 'pip install -e ".[dev]"' not in text and "actions/setup-python@v5" in text
    assert ('- uses: ./crapkit\n        id: crapkit\n        continue-on-error: true\n        with:\n'
            '          gate: "true"\n          delta: "false"\n') in text
    assert text.endswith('run: echo "crapkit-step-outcome=${{ steps.crapkit.outcome }}"\n')
    with pytest.raises(docsnip.DocSnipError, match="no step holds 'npm ci'"):
        job.with_step("npm ci", None)


@pytest.mark.kit
def test_consumer_marks_exactly_one_coveragepy_lane_container_ok():
    lane = '[[lane]]\nname = "py"\nparser = "coveragepy"\nscopes = ["calc"]\n'
    consumer = tool("consumer")

    assert consumer.with_container_ok(lane) == lane.replace('coveragepy"\n', 'coveragepy"\ncontainer_ok = true\n')
    with pytest.raises(SystemExit, match="found 0"):
        consumer.with_container_ok(lane.replace("coveragepy", "istanbul"))
