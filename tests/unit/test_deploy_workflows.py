"""The workflows that run the deploy suite: ci.yml's four push jobs and
deploy.yml's nightly, weekly, release and published cadences.

Nothing runs a workflow before it reaches GitHub, so these tests hold what
can go wrong on the way: a runner label or a buildx and BuildKit version
that drifts from pins.toml, a job that skips the scope job's gate, a run.py
flag run.py does not take, a cell no job selects on a cadence it names, a
timeout that ignores the measured run, and the scripts the steps carry
inline. The scope, run and summary scripts are cut out of the YAML and run
here, the way the runner runs them.
"""
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import lizard
import pytest
import yaml
from _pytest.mark.expression import Expression

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import pins as pinsfile  # noqa: E402
import run  # noqa: E402

PINS = pinsfile.load()
MAP = tomllib.loads((ROOT / "tests" / "deploy" / "MAP.toml").read_text(encoding="utf-8"))
WORKFLOWS = ROOT / ".github" / "workflows"
CI = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
DEPLOY = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
PUSH_JOBS = {"deploy-linux": "linux", "deploy-linux-native": "linux", "deploy-windows": "windows",
             "deploy-action": "linux"}
# deploy.yml's jobs that take their matrix from the scope job, by MAP [jobs] runner.
MATRIX_RUNNERS = {"linux": "linux", "windows": "windows", "macos": "macos", "host": "linux", "tool": "linux"}


def triggers(workflow):
    """PyYAML reads the bare key `on` as True."""
    return workflow.get("on", workflow.get(True))


def steps(workflow):
    return [(name, step) for name, job in workflow["jobs"].items() for step in job.get("steps", [])]


def step_named(workflow, job, name):
    return next(step for step in workflow["jobs"][job]["steps"] if step.get("name") == name)


def _uses(step, action):
    return step.get("uses", "").startswith(action + "@")


# --- runners, pins and the gate -----------------------------------------------------

def test_ci_runs_the_four_deploy_push_jobs_on_their_pinned_runners():
    labels = {name: CI["jobs"][name]["runs-on"] for name in PUSH_JOBS}

    assert labels == {name: PINS["runners"][kind] for name, kind in PUSH_JOBS.items()}


def _expected_runner(name):
    """The pins.toml runner a deploy.yml job must name."""
    if name in MATRIX_RUNNERS:
        return PINS["runners"][MATRIX_RUNNERS[name]]
    return PINS["runners"]["windows" if name.endswith("-windows") else "linux"]


def test_every_deploy_yml_job_runs_on_its_pinned_label():
    wrong = {name: job["runs-on"] for name, job in DEPLOY["jobs"].items()
             if job["runs-on"] != _expected_runner(name)}

    assert wrong == {}


def buildx_options():
    return [step["with"] for workflow in (CI, DEPLOY) for _, step in steps(workflow)
            if _uses(step, "docker/setup-buildx-action")]


def test_every_buildx_step_installs_the_pinned_buildx_with_the_pinned_buildkit():
    wanted = {"version": PINS["images"]["buildx"], "driver-opts": f"image={PINS['images']['buildkit']}",
              "name": run.CONTAINER_BUILDER, "driver": "docker-container"}

    assert [{key: option.get(key) for key in wanted} for option in buildx_options()] == [wanted, wanted]


def _step_index(job, predicate):
    return next((index for index, step in enumerate(job["steps"]) if predicate(step)), None)


def _runs_containers(step):
    text = step.get("run", "") + json.dumps(step.get("env", {}))
    return "tools/deploy/run.py" in text and "--native" not in text


def test_a_container_run_comes_after_the_buildx_and_runtime_steps():
    for job in (CI["jobs"]["deploy-linux"], DEPLOY["jobs"]["linux"]):
        buildx = _step_index(job, lambda step: _uses(step, "docker/setup-buildx-action"))
        runtime = _step_index(job, lambda step: _uses(step, "crazy-max/ghaction-github-runtime"))
        cells = _step_index(job, lambda step: _runs_containers(step) or step.get("name") == "run the cells")

        assert buildx < runtime < cells


def test_deploy_yml_has_one_pull_request_trigger_with_no_path_filter_and_the_other_cadences():
    on = triggers(DEPLOY)

    assert on["pull_request"] == {"types": ["opened", "synchronize", "reopened", "labeled"]}
    assert [entry["cron"] for entry in on["schedule"]] == list(MAP["scope"]["schedules"])
    assert on["workflow_dispatch"]["inputs"]["cadence"]["options"] == ["nightly", "weekly", "release", "published"]
    assert on["push"] == {"tags": ["v*"]}


def test_the_scope_job_comes_first_and_every_other_job_gates_on_its_output():
    names = list(DEPLOY["jobs"])
    ungated = [name for name in names[1:] if DEPLOY["jobs"][name].get("needs") != "scope"
               or "needs.scope.outputs." not in str(DEPLOY["jobs"][name].get("if", ""))]

    assert names[0] == "scope"
    assert ungated == []


def test_the_scope_job_exports_one_matrix_per_runner_kind():
    outputs = DEPLOY["jobs"]["scope"]["outputs"]

    assert set(outputs) == {"cadence", "jobs", *MATRIX_RUNNERS}


# --- scripts cut out of the YAML -----------------------------------------------------

SCOPE_SCRIPT = step_named(DEPLOY, "scope", "choose the cadence and the jobs it runs")["run"]
RUN_SCRIPT = step_named(DEPLOY, "linux", "run the cells")["run"]
SUMMARY_SCRIPT = step_named(CI, "deploy-linux", "per-cell durations")["run"]


def steps_named(name, workflows):
    return [step for workflow in workflows for _, step in steps(workflow) if step.get("name") == name]


def test_every_copy_of_the_run_and_summary_scripts_is_the_same_python():
    runs, summaries = steps_named("run the cells", (DEPLOY,)), steps_named("per-cell durations", (CI, DEPLOY))

    assert {step["run"] for step in runs} == {RUN_SCRIPT} and {step["run"] for step in summaries} == {SUMMARY_SCRIPT}
    assert {step.get("shell") for step in runs + summaries} == {"python"}


@pytest.mark.parametrize("script", [SCOPE_SCRIPT, RUN_SCRIPT, SUMMARY_SCRIPT], ids=["scope", "run", "summary"])
def test_every_function_the_workflow_scripts_define_stays_at_ccn_5(script):
    functions = lizard.analyze_file.analyze_source_code("inline.py", script).function_list

    assert [(fn.name, fn.cyclomatic_complexity) for fn in functions if fn.cyclomatic_complexity > 5] == []


def _clean_env(extra):
    keep = {key: value for key, value in os.environ.items() if key.upper() not in {
        "EVENT_NAME", "ACTION", "LABEL", "LABELS", "BASE_SHA", "SCHEDULE", "CADENCE_INPUT", "REF_NAME"}}
    return {**keep, **extra}


def run_script(script, cwd, tmp_path, env):
    """Run a step's python the way `shell: python` does; return its outputs and summary."""
    (tmp_path / "step.py").write_text(script, encoding="utf-8")
    files = {"GITHUB_OUTPUT": tmp_path / "output", "GITHUB_STEP_SUMMARY": tmp_path / "summary"}
    done = subprocess.run([sys.executable, str(tmp_path / "step.py")], cwd=cwd, capture_output=True, text=True,
                          env=_clean_env({**{key: str(path) for key, path in files.items()}, **env}),
                          timeout=HANG_SECONDS)
    written = {key: path.read_text(encoding="utf-8") if path.exists() else "" for key, path in files.items()}
    return done, written


def scope(tmp_path, cwd=ROOT, **env):
    done, written = run_script(SCOPE_SCRIPT, cwd, tmp_path, env)
    assert done.returncode == 0, done.stderr
    outputs = dict(line.split("=", 1) for line in written["GITHUB_OUTPUT"].splitlines())
    return {key: value if key == "cadence" else json.loads(value) for key, value in outputs.items()}


def scheduled(cadence):
    """What MAP [jobs] schedules for a cadence: every entry naming it that no gap blocks."""
    return {name for name, job in MAP["jobs"].items() if cadence in job["when"] and not job.get("blocked")}


def _names(plan, runner):
    return {entry["name"] for entry in plan[runner]}


def test_the_nightly_schedule_runs_the_nightly_set_and_no_blocked_job(tmp_path):
    plan = scope(tmp_path, EVENT_NAME="schedule", SCHEDULE="17 6 * * *")

    assert plan["cadence"] == "nightly" and set(plan["jobs"]) == scheduled("nightly")
    assert {"nightly-linux-core", "nightly-linux-full", "nightly-act", "lin-repeat"} <= _names(plan, "linux")
    assert {"win-repeat", "nightly-windows-a", "nightly-windows-b"} == _names(plan, "windows")
    assert "lin-clock" not in plan["jobs"] and plan["host"] == []
    assert plan["linux"][0]["runs"] == ["--cadence nightly --os linux --image core --cache gha -n 4"]


def test_the_weekly_schedule_runs_macos_the_network_cells_and_calibration(tmp_path):
    plan = scope(tmp_path, EVENT_NAME="schedule", SCHEDULE="17 7 * * 1")

    assert plan["cadence"] == "weekly" and set(plan["jobs"]) == scheduled("weekly")
    assert _names(plan, "linux") == {"weekly-online"} and _names(plan, "macos") == {"weekly-macos"}
    assert _names(plan, "tool") == {"calibrate-all"}


def test_a_release_dispatch_runs_nightly_and_weekly_entries_with_the_release_cadence(tmp_path):
    plan = scope(tmp_path, EVENT_NAME="workflow_dispatch", CADENCE_INPUT="release")

    assert plan["cadence"] == "release" and set(plan["jobs"]) == scheduled("release")
    assert {"nightly-linux-core", "weekly-online"} <= _names(plan, "linux")
    assert all("--cadence release" in args for entry in plan["linux"] if entry["name"] != "lin-repeat"
               for args in entry["runs"])


@pytest.mark.parametrize("env", [{"EVENT_NAME": "push", "REF_NAME": "v0.8.1"},
                                 {"EVENT_NAME": "workflow_dispatch", "CADENCE_INPUT": "published"}])
def test_a_tag_push_or_a_published_dispatch_runs_only_the_published_smoke(tmp_path, env):
    plan = scope(tmp_path, **env)

    assert plan["cadence"] == "published"
    assert set(plan["jobs"]) == scheduled("published") == {"published-online", "published-action-tag"}


def _git(repo, *args):
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.hooksPath=.no-hooks"]
    subprocess.run(["git", *identity, *args], cwd=repo, check=True, capture_output=True, timeout=HANG_SECONDS)


def pull_request_repo(tmp_path, changed):
    """A repo holding the map, a base commit and one commit that touches `changed`."""
    repo = tmp_path / "repo"
    (repo / "tests" / "deploy").mkdir(parents=True)
    (repo / "tests" / "deploy" / "MAP.toml").write_bytes((ROOT / "tests" / "deploy" / "MAP.toml").read_bytes())
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
                          timeout=HANG_SECONDS).stdout.strip()
    (repo / changed).parent.mkdir(parents=True, exist_ok=True)
    (repo / changed).write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo, base


@pytest.mark.parametrize("changed, cadence", [
    ("README.md", "nightly"),
    ("plugin/hooks/hooks.json", "nightly"),
    ("src/crapkit/cli/admin.py", "nightly"),
    ("docs/é.md", "nightly"),
    ("tests/unit/test_config.py", "none"),
    ("src/crapkit/score.py", "none"),
])
def test_a_pull_request_runs_the_nightly_set_only_when_it_changes_an_install_surface(tmp_path, changed, cadence):
    repo, base = pull_request_repo(tmp_path, changed)
    plan = scope(tmp_path, cwd=repo, EVENT_NAME="pull_request", ACTION="synchronize", BASE_SHA=base, LABELS="[]")

    assert plan["cadence"] == cadence
    assert (plan["jobs"] == []) is (cadence == "none")


@pytest.mark.parametrize("env, cadence", [
    ({"ACTION": "labeled", "LABEL": "deploy-full", "LABELS": '["deploy-full"]'}, "release"),
    ({"ACTION": "labeled", "LABEL": "documentation", "LABELS": '["documentation"]'}, "none"),
    ({"ACTION": "synchronize", "LABELS": '["deploy-full", "documentation"]'}, "release"),
])
def test_the_deploy_full_label_runs_the_release_set_and_another_label_runs_nothing(tmp_path, env, cadence):
    repo, base = pull_request_repo(tmp_path, "tests/unit/test_config.py")

    assert scope(tmp_path, cwd=repo, EVENT_NAME="pull_request", BASE_SHA=base, **env)["cadence"] == cadence


def test_the_scope_summary_names_the_cadence_and_why(tmp_path):
    _, written = run_script(SCOPE_SCRIPT, ROOT, tmp_path, {"EVENT_NAME": "schedule", "SCHEDULE": "17 7 * * 1"})

    assert written["GITHUB_STEP_SUMMARY"].startswith("deploy scope: weekly (schedule 17 7 * * 1); jobs: ")


FAKE_RUN = """import pathlib, sys
with open("calls.txt", "a", encoding="utf-8") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(int(sys.argv[1]))
"""


def test_the_run_step_calls_run_py_once_per_entry_into_its_own_out_dir_and_fails_on_any(tmp_path):
    (tmp_path / "tools" / "deploy").mkdir(parents=True)
    (tmp_path / "tools" / "deploy" / "run.py").write_text(FAKE_RUN, encoding="utf-8")
    done, _ = run_script(RUN_SCRIPT, tmp_path, tmp_path, {"RUNS": json.dumps(["0 --packet a", "3 --packet b", "0"])})

    assert done.returncode == 3
    assert (tmp_path / "calls.txt").read_text(encoding="utf-8").splitlines() == [
        "0 --packet a --out .crapkit/deploy-out/0", "3 --packet b --out .crapkit/deploy-out/1",
        "0 --out .crapkit/deploy-out/2"]


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.deploy.test_fresh_start" name="test_start" time="3.5">
<properties><property name="cell_id" value="lin-pip-start-py311"/></properties></testcase>
<testcase classname="tests.deploy.test_kit_pieces" name="test_piece" time="0.2"><skipped message="x"/></testcase>
<testcase classname="tests.deploy.test_harness_profiles" name="test_sim[zed|win]" time="7.3">
<properties><property name="cell_id" value="lin-profiles-sim"/></properties><failure message="x"/></testcase>
</testsuite></testsuites>
"""


def test_the_summary_lists_each_cell_with_its_seconds_slowest_first(tmp_path):
    out = tmp_path / ".crapkit" / "deploy-out" / "1"
    out.mkdir(parents=True)
    (out / "junit-0.xml").write_text(JUNIT, encoding="utf-8")
    _, written = run_script(SUMMARY_SCRIPT, tmp_path, tmp_path, {})
    lines = written["GITHUB_STEP_SUMMARY"].splitlines()
    where = os.path.join("1", "junit-0.xml")

    assert lines[0] == "### Deploy cells: 3 tests, 11 s"
    assert lines[4:] == [f"| 7.3 | lin-profiles-sim | `test_sim[zed/win]` | failed | {where} |",
                         f"| 3.5 | lin-pip-start-py311 | `test_start` | passed | {where} |",
                         f"| 0.2 | kit | `test_piece` | skipped | {where} |"]


def test_the_summary_says_so_when_no_junit_was_written(tmp_path):
    _, written = run_script(SUMMARY_SCRIPT, tmp_path, tmp_path, {})

    assert written["GITHUB_STEP_SUMMARY"] == "### Deploy cells: no JUnit was written\n"


# --- every run.py call, and what it selects -------------------------------------------

def ci_invocations():
    """The run.py argument lists ci.yml's deploy jobs pass."""
    lines = [step["run"] for name in PUSH_JOBS for step in CI["jobs"][name]["steps"]
             if "tools/deploy/run.py" in step.get("run", "")]
    return [shlex.split(line)[2:] for line in lines]


def _job_invocations(job):
    return [(cadence, shlex.split(args.replace("{cadence}", cadence))) for cadence in job["when"]
            for args in job.get("runs", [])]


def invocations(jobs=MAP["jobs"]):
    """(trigger, argv) for every run.py call either workflow makes; a blocked entry makes none."""
    found = [("push", argv) for argv in ci_invocations()]
    return found + [call for job in jobs.values() if not job.get("blocked") for call in _job_invocations(job)]


def all_argvs():
    return ci_invocations() + [argv for job in MAP["jobs"].values() for _, argv in _job_invocations(job)]


def test_every_run_py_call_parses_and_names_its_os():
    parsed = [run.parse(argv) for argv in all_argvs()]

    assert len(parsed) > 20
    assert [argv for argv in all_argvs() if "--os" not in argv] == []


def test_every_cell_and_packet_a_run_py_call_names_is_in_the_map():
    parsed = [run.parse(argv) for argv in all_argvs()]
    cells = {cell for args in parsed for cell in args.cell}
    packets = {args.packet for args in parsed} - {None}

    assert sorted(cells - set(MAP["cell"])) == []
    assert sorted(packets - set(MAP["packets"])) == []


FLAGS = {"online": False, "docker_host": False, "nonblocking": False, "real_cli": True}


def markers(cell):
    """The pytest markers @cell gives a cell with these fields (kit/cells.py)."""
    names = {*cell["cadence"].split("+"), cell["os"], *([f"image_{cell['image']}"] if cell.get("image") else [])}
    return names | {flag for flag, default in FLAGS.items() if cell.get(flag, default)}


def selects(argv, cell_id, cell):
    """Whether run.py with argv runs this cell: its -m expression, then --cell and --packet."""
    args, names = run.parse(argv), markers(cell)
    expression = run.marker_expression(args.cadence, args.os, None if args.native else args.image, args.online)
    if not Expression.compile(expression).evaluate(lambda name, **_: name in names):
        return False
    return (not args.cell or cell_id in args.cell) and args.packet in (None, cell["packet"])


def job_runs_on(job, trigger):
    """A job cell's workflow job runs on this trigger. ci.yml runs on every push,
    the release commit's included."""
    workflow, _, name = job.partition(":")
    if workflow == "ci.yml":
        return trigger in ("push", "release") and name in CI["jobs"]
    entry = MAP["jobs"].get(name, {})
    return trigger in entry.get("when", []) and not entry.get("blocked")


def reaches(cell_id, cell, trigger, calls):
    if "job" in cell:
        return job_runs_on(cell["job"], trigger)
    return any(when == trigger and selects(argv, cell_id, cell) for when, argv in calls)


def waiting(cells):
    """The cells a job must run: a blocked cell, or one no packet owns yet, waits."""
    return [(cell_id, cell) for cell_id, cell in cells.items() if not cell.get("blocked") and cell["packet"] != "unowned"]


def unreached(cells, calls):
    """(cell, cadence) pairs no job runs."""
    return [f"{cell_id} on {cadence}" for cell_id, cell in waiting(cells) for cadence in cell["cadence"].split("+")
            if not reaches(cell_id, cell, cadence, calls)]


IN_RELEASE = {"push", "nightly", "weekly"}


def release_calls(calls):
    """What a release runs: deploy.yml's release entries, and ci.yml's push jobs,
    which run on the release commit."""
    return [("release", argv) for when, argv in calls if when in ("release", "push")]


def unreached_by_release(cells, calls):
    release = release_calls(calls)
    return [cell_id for cell_id, cell in waiting(cells)
            if IN_RELEASE & set(cell["cadence"].split("+")) and not reaches(cell_id, cell, "release", release)]


CALLS = invocations()
# A nonblocking job's failure never fails the run, so it cannot be what guards a blocking cell.
BLOCKING_CALLS = invocations({name: job for name, job in MAP["jobs"].items() if not job.get("nonblocking")})


def by_blocking(nonblocking):
    return {cell_id: cell for cell_id, cell in MAP["cell"].items() if bool(cell.get("nonblocking")) is nonblocking}


def test_every_cell_reaches_a_blocking_job_on_each_cadence_it_names():
    assert unreached(by_blocking(False), BLOCKING_CALLS) == []


def test_every_nonblocking_cell_reaches_some_job_on_each_cadence_it_names():
    assert unreached(by_blocking(True), CALLS) == []


def test_a_cell_only_a_nonblocking_job_selects_is_caught():
    no_full = invocations({name: job for name, job in MAP["jobs"].items()
                           if name != "nightly-linux-full" and not job.get("nonblocking")})

    assert "lin-gemini on nightly" in unreached(by_blocking(False), no_full)
    assert "lin-gemini on nightly" not in unreached(by_blocking(False), CALLS)


def test_every_push_nightly_and_weekly_cell_runs_in_a_release():
    assert unreached_by_release(by_blocking(False), BLOCKING_CALLS) == []
    assert unreached_by_release(by_blocking(True), CALLS) == []


def test_a_release_with_no_release_entries_is_caught():
    push_only = [(when, argv) for when, argv in CALLS if when != "release"]

    assert "lin-jest-start" in unreached_by_release(MAP["cell"], push_only)
    assert "lin-pip-start-py311" not in unreached_by_release(MAP["cell"], push_only)


def test_a_cell_no_job_selects_is_caught():
    cells = {"win-odd-cell": {"packet": "deploy-ci", "cadence": "nightly", "os": "windows"}}

    assert unreached(cells, CALLS) == ["win-odd-cell on nightly"]


def test_a_container_run_never_selects_a_host_cell():
    host = MAP["cell"]["lin-native-start"]

    assert not selects(["--cadence", "push", "--os", "linux", "--image", "core"], "lin-native-start", host)
    assert selects(["--native", "--os", "linux", "--cadence", "push", "--cell", "lin-native-start"],
                   "lin-native-start", host)


def job_defined(job):
    workflow, _, name = job.partition(":")
    return name in (CI["jobs"] if workflow == "ci.yml" else MAP["jobs"])


def test_every_job_cell_names_a_job_the_workflows_define():
    assert [cell["job"] for cell in MAP["cell"].values() if "job" in cell and not job_defined(cell["job"])] == []


def test_a_job_cell_naming_a_job_no_workflow_has_is_caught():
    assert not job_defined("deploy.yml:no-such-job") and not job_defined("ci.yml:no-such-job")


def test_every_action_entry_is_a_deploy_yml_job_of_its_name_and_every_job_is_an_entry_or_a_matrix():
    actions = {name for name, job in MAP["jobs"].items() if job["runner"] == "action"}

    assert set(DEPLOY["jobs"]) == {"scope", *MATRIX_RUNNERS, *actions}
    assert {job["runner"] for job in MAP["jobs"].values()} <= {*MATRIX_RUNNERS, "action"}


def test_the_tag_upgrade_starts_from_a_release_the_wheelhouse_holds():
    job = DEPLOY["jobs"]["gha-action-tag-upgrade"]
    tags = re.findall(r"crapkit-v(\d+\.\d+\.\d+)", json.dumps(job))

    assert tags and set(tags) <= set(PINS["wheelhouse"]["crapkit"])


# --- timeouts ---------------------------------------------------------------------------

def budget_problems(name, budget, timeout):
    """Once a run is measured the timeout is 1.5 times it, rounded up; until then the budget's own."""
    want = math.ceil(1.5 * budget["measured"]) if budget["measured"] else budget["timeout"]
    return [] if timeout == want else [f"{name}: timeout {timeout}, want {want}"]


def test_each_push_job_keeps_the_timeout_its_budget_gives():
    problems = [problem for name in PUSH_JOBS
                for problem in budget_problems(name, MAP["budget"][name], CI["jobs"][name]["timeout-minutes"])]

    assert problems == []


def test_each_deploy_yml_entry_keeps_the_timeout_its_budget_gives():
    assert [problem for name, job in MAP["jobs"].items() for problem in budget_problems(name, job, job["timeout"])] == []


def test_each_action_job_runs_under_its_entrys_timeout():
    actions = [name for name, job in MAP["jobs"].items() if job["runner"] == "action"]

    assert {name: DEPLOY["jobs"][name]["timeout-minutes"] for name in actions} == {
        name: MAP["jobs"][name]["timeout"] for name in actions}


def test_a_measured_run_sets_the_timeout_to_one_and_a_half_times_it():
    assert budget_problems("deploy-windows", {"measured": 13, "timeout": 20}, 20) == []
    assert budget_problems("deploy-windows", {"measured": 9, "timeout": 20}, 20) == [
        "deploy-windows: timeout 20, want 14"]


# --- the deploy-action job, uploads and scripts a step runs ---------------------------

def test_the_push_action_job_reads_pull_requests_only_and_gates_with_delta_off():
    job = CI["jobs"]["deploy-action"]
    action = next(step for step in job["steps"] if step.get("uses") == "./crapkit")
    check = step_named(CI, "deploy-action", "assert what the consumer sees")

    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert action["with"] == {"gate": "true", "delta": "false"} and action["continue-on-error"] is True
    assert {"gha-action-consumer", "gha-action-readonly-token", "steps.action.outcome"} <= set(
        re.findall(r"gha-action-[a-z-]+|steps\.action\.outcome", check["run"]))


def deploy_steps():
    return [step for name, step in steps(CI) if name in PUSH_JOBS] + [step for _, step in steps(DEPLOY)]


def test_every_upload_keeps_the_hidden_out_dir_and_leaves_the_exported_tree_out():
    uploads = [step["with"] for step in deploy_steps() if _uses(step, "actions/upload-artifact")]
    kept = (".crapkit/deploy-out", "!.crapkit/deploy-out/**/in", "!.crapkit/deploy-out/**/native-*")

    assert len(uploads) == 3 + len(MATRIX_RUNNERS) - 1
    assert {upload["include-hidden-files"] for upload in uploads} == {True}
    assert {tuple(upload["path"].split()) for upload in uploads} == {kept}


SCRIPT = re.compile(r"tools/deploy/[\w-]+\.py")


def _scripts(texts):
    return {path for text in texts for path in SCRIPT.findall(text)}


def landed(packet):
    return any((ROOT / module).exists() for module in MAP["packets"][packet]["modules"])


def workflow_texts():
    """Every command a step or a MAP [jobs] entry runs."""
    texts = [step.get("run", "") for workflow in (CI, DEPLOY) for _, step in steps(workflow)]
    return texts + [job.get("command", "") + job.get("then", "") for job in MAP["jobs"].values()]


def pending_scripts():
    """Scripts a packet will add that has not landed in this tree."""
    return {path for name, packet in MAP["packets"].items() if not landed(name) for path in packet.get("scripts", [])}


def test_every_script_a_workflow_runs_is_in_the_tree_or_owned_by_a_packet_not_landed_yet():
    missing = _scripts(workflow_texts()) - pending_scripts()

    assert sorted(path for path in missing if not (ROOT / path).exists()) == []


def test_agents_md_documents_run_py_beside_the_test_schedule():
    tests = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("\n## Tests\n", 1)[1].split("\n## ", 1)[0]

    assert "<!-- /generated:test-schedule -->" in tests
    assert all(name in tests for name in ("python tools/deploy/run.py", "tests/deploy/MAP.toml", *PUSH_JOBS))
