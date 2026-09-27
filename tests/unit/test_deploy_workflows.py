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

import calibrate  # noqa: E402
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
MATRIX_RUNNERS = {"linux": "linux", "arm": "arm", "windows": "windows", "macos": "macos", "host": "linux",
                  "tool": "linux"}
# The jobs that build images and run cells in containers.
CONTAINER_JOBS = [CI["jobs"]["deploy-linux"], DEPLOY["jobs"]["linux"], DEPLOY["jobs"]["arm"]]


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


def test_every_runner_pins_toml_names_runs_a_job():
    """A pinned runner no job names is a platform nothing tests: pins.toml's
    arm runner sat unread while lin-arm64 skipped on x86_64."""
    used = {job["runs-on"] for workflow in (CI, DEPLOY) for job in workflow["jobs"].values()}

    assert sorted(set(PINS["runners"].values()) - used) == []


def buildx_options():
    return [step["with"] for workflow in (CI, DEPLOY) for _, step in steps(workflow)
            if _uses(step, "docker/setup-buildx-action")]


def test_every_buildx_step_installs_the_pinned_buildx_with_the_pinned_buildkit():
    wanted = {"version": PINS["images"]["buildx"], "driver-opts": f"image={PINS['images']['buildkit']}",
              "name": run.CONTAINER_BUILDER, "driver": "docker-container"}

    assert [{key: option.get(key) for key in wanted} for option in buildx_options()] == [wanted] * len(CONTAINER_JOBS)


def _step_index(job, predicate):
    return next((index for index, step in enumerate(job["steps"]) if predicate(step)), None)


def _runs_containers(step):
    text = step.get("run", "") + json.dumps(step.get("env", {}))
    return "tools/deploy/run.py" in text and "--native" not in text


def test_a_container_run_comes_after_the_buildx_and_runtime_steps():
    for job in CONTAINER_JOBS:
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
    linux = {"nightly-linux-core", "nightly-linux-full", "nightly-act", "lin-repeat", "lin-clock"}
    assert linux <= _names(plan, "linux")
    assert {"win-repeat", "nightly-windows-a", "nightly-windows-b"} == _names(plan, "windows")
    assert _names(plan, "host") == {"nightly-host"}
    assert plan["linux"][0]["runs"] == ["--cadence nightly --os linux --image core --cache gha -n 4"]


def test_the_weekly_schedule_runs_macos_arm64_the_network_cells_and_calibration(tmp_path):
    plan = scope(tmp_path, EVENT_NAME="schedule", SCHEDULE="17 7 * * 1")

    assert plan["cadence"] == "weekly" and set(plan["jobs"]) == scheduled("weekly")
    assert _names(plan, "linux") == {"weekly-online", "weekly-py315", "latest-harnesses"}
    assert _names(plan, "macos") == {"weekly-macos"} and _names(plan, "arm") == {"weekly-arm64"}
    assert _names(plan, "tool") == {"calibrate-all"} and "weekly-host" not in plan["jobs"]


def test_a_release_dispatch_runs_nightly_and_weekly_entries_with_the_release_cadence(tmp_path):
    plan = scope(tmp_path, EVENT_NAME="workflow_dispatch", CADENCE_INPUT="release")

    assert plan["cadence"] == "release" and set(plan["jobs"]) == scheduled("release")
    assert {"nightly-linux-core", "weekly-online"} <= _names(plan, "linux")
    pushed = ("lin-repeat", "lin-clock")  # the push set by design: twice cold, and on a moved clock
    assert all("--cadence release" in args for entry in plan["linux"] if entry["name"] not in pushed
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


FAKE_RUN = """import sys
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

def ci_invocations(names=PUSH_JOBS):
    """The run.py argument lists ci.yml's deploy jobs pass, one per matrix part."""
    return [shlex.split(line)[2:] for name in names for line in run_lines(CI["jobs"][name])]


def run_lines(job):
    """Each run.py line of a job's steps, once for each value of its matrix
    `part`, as the runner substitutes it."""
    parts = job.get("strategy", {}).get("matrix", {}).get("part", [None])
    return [step["run"].replace("${{ matrix.part }}", str(part)) for step in job["steps"]
            if "tools/deploy/run.py" in step.get("run", "") for part in parts]


def upload_names(job):
    return [step["with"]["name"] for step in job["steps"] if _uses(step, "actions/upload-artifact")]


def test_deploy_linux_splits_the_push_set_between_parts_that_run_side_by_side():
    """One runner ran the Linux push set's tests in 363 to 525 s, and restoring and
    loading the core image adds 3 to 4 minutes: past the push budget of 10 minutes."""
    job = CI["jobs"]["deploy-linux"]
    parts = [vars(run.parse(argv)) for argv in ci_invocations(["deploy-linux"])]

    assert [args.pop("shard") for args in parts] == ["1/2", "2/2"]
    assert parts[0] == parts[1]
    assert job["strategy"]["fail-fast"] is False
    assert upload_names(job) == ["deploy-linux-${{ matrix.part }}"]


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


RUN_LINE = re.compile(r"`python tools/deploy/run\.py ([^`]*)`")


def skip_lines(root=ROOT / "tests" / "deploy"):
    """(test file, argv) for each run.py line a deploy test prints for its reader to run."""
    return [(path.name, shlex.split(found)) for path in sorted(root.glob("test_*.py"))
            for found in RUN_LINE.findall(path.read_text(encoding="utf-8"))]


def test_every_run_py_line_a_deploy_test_prints_runs_the_cells_it_names():
    """lin-arm64 skipped on x86_64 saying run.py built amd64 only, long after run.py
    gained cells-arm64: the line a skip prints must be one run.py takes and that selects the cell."""
    lines = skip_lines()

    assert lines and [(name, argv) for name, argv in lines if not runs_its_cells(argv)] == []


def runs_its_cells(argv):
    """argv parses, and selects each cell it names on the OS it runs."""
    args = run.parse(argv)
    return all(cell in MAP["cell"] and selects(argv, cell, {**MAP["cell"][cell], "os": args.os}) for cell in args.cell)


def test_a_printed_run_py_line_that_misses_its_cell_is_caught():
    assert runs_its_cells(["--image", "cells-arm64", "--cadence", "weekly", "--cell", "lin-arm64"])
    assert not runs_its_cells(["--image", "cells-arm64", "--cadence", "push", "--cell", "lin-arm64"])
    assert not runs_its_cells(["--cell", "lin-no-such-cell"])


def test_every_cell_and_packet_a_run_py_call_names_is_in_the_map():
    parsed = [run.parse(argv) for argv in all_argvs()]
    cells = {cell for args in parsed for cell in args.cell}
    packets = {args.packet for args in parsed} - {None}

    assert sorted(cells - set(MAP["cell"])) == []
    assert sorted(packets - set(MAP["packets"])) == []


FLAGS = {"online": False, "docker_host": False, "nonblocking": False, "real_cli": True}


def oses(cell):
    """The OSes a cell names: one, or a list when its tests run on each of them."""
    return list(cell["os"]) if isinstance(cell["os"], list) else [cell["os"]]


def markers(cell):
    """The pytest markers @cell gives a cell with these fields (kit/cells.py)."""
    names = {*cell["cadence"].split("+"), *oses(cell), *([f"image_{cell['image']}"] if cell.get("image") else [])}
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


def halves(cell):
    """The cell once for each OS a job must select it on. A gap may block all
    of it or one OS; a job cell is its workflow job, whatever OS a test models."""
    blocked = cell.get("blocked", {})
    if isinstance(blocked, str):
        return []
    if "job" in cell:
        return [cell]
    return [{**cell, "os": name} for name in oses(cell) if name not in blocked]


def waiting(cells):
    """The cells a job must run, once per OS: a blocked cell or OS, or a cell no packet owns yet, waits."""
    return [(cell_id, half) for cell_id, cell in cells.items() if cell["packet"] != "unowned" for half in halves(cell)]


def _label(cell_id, cell, cadence):
    return f"{cell_id} on {cadence}" + ("" if "job" in cell else f", {cell['os']}")


def unreached(cells, calls):
    """(cell, cadence, os) no job runs."""
    return [_label(cell_id, cell, cadence) for cell_id, cell in waiting(cells) for cadence in cell["cadence"].split("+")
            if not reaches(cell_id, cell, cadence, calls)]


IN_RELEASE = {"push", "nightly", "weekly"}


def release_calls(calls):
    """What a release runs: deploy.yml's release entries, and ci.yml's push jobs,
    which run on the release commit."""
    return [("release", argv) for when, argv in calls if when in ("release", "push")]


def unreached_by_release(cells, calls):
    release = release_calls(calls)
    return sorted({cell_id for cell_id, cell in waiting(cells)
                   if IN_RELEASE & set(cell["cadence"].split("+")) and not reaches(cell_id, cell, "release", release)})


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

    assert "lin-gemini on nightly, linux" in unreached(by_blocking(False), no_full)
    assert "lin-gemini on nightly, linux" not in unreached(by_blocking(False), CALLS)


def test_every_push_nightly_and_weekly_cell_runs_in_a_release():
    assert unreached_by_release(by_blocking(False), BLOCKING_CALLS) == []
    assert unreached_by_release(by_blocking(True), CALLS) == []


def test_a_release_with_no_release_entries_is_caught():
    push_only = [(when, argv) for when, argv in CALLS if when != "release"]

    assert "lin-jest-start" in unreached_by_release(MAP["cell"], push_only)
    assert "lin-pip-start-py311" not in unreached_by_release(MAP["cell"], push_only)


def test_a_cell_no_job_selects_is_caught():
    cells = {"win-odd-cell": {"packet": "deploy-ci", "cadence": "nightly", "os": "windows"}}

    assert unreached(cells, CALLS) == ["win-odd-cell on nightly, windows"]


def test_a_cell_on_two_oses_reaches_a_job_on_each():
    """The nightly Windows jobs run one packet each, so a packet they leave out
    runs its Windows half nowhere while a Linux job still selects its Linux half."""
    cell = {"packet": "deploy-ci", "cadence": "nightly", "os": ["linux", "windows"], "image": "core"}

    assert unreached({"docs-odd-cell": cell}, CALLS) == ["docs-odd-cell on nightly, windows"]


def test_the_one_os_a_gap_blocks_needs_no_job():
    cell = {"packet": "deploy-ci", "cadence": "nightly", "os": ["linux", "windows"], "image": "core"}

    assert unreached({"docs-odd-cell": {**cell, "blocked": {"windows": "a-gap"}}}, CALLS) == []
    assert unreached({"docs-odd-cell": {**cell, "blocked": "a-gap"}}, CALLS) == []


def test_a_container_run_never_selects_a_host_cell():
    host = MAP["cell"]["lin-native-start"]

    assert not selects(["--cadence", "push", "--os", "linux", "--image", "core"], "lin-native-start", host)
    assert selects(["--native", "--os", "linux", "--cadence", "push", "--cell", "lin-native-start"],
                   "lin-native-start", host)


def _args(job):
    return [run.parse(argv) for _, argv in _job_invocations(job)]


def narrowed(args):
    """A run that names cells, or a packet other than the kit's, collects only those."""
    return bool(args.cell) or args.packet not in (None, "deploy-kit")


def pytest_halves(cells):
    """(cell, one OS of it) for every cell pytest runs that a job must select."""
    return [(cell_id, half) for cell_id, half in waiting(cells) if "job" not in half]


def _selects_nothing(argv, written):
    return narrowed(run.parse(argv)) and not any(selects(argv, cell_id, cell) for cell_id, cell in written)


def unblocked_calls(jobs):
    """(entry, argv) for every run.py call an entry no gap blocks makes."""
    return [(name, argv) for name, job in jobs.items() if not job.get("blocked") for _, argv in _job_invocations(job)]


def empty_runs(jobs=MAP["jobs"], cells=MAP["cell"]):
    """Each run of an unblocked entry that names cells or a packet and selects
    no cell a packet writes: pytest collects nothing there and exits 5."""
    written = pytest_halves(cells)
    return [f"{name}: {' '.join(argv)}" for name, argv in unblocked_calls(jobs) if _selects_nothing(argv, written)]


def test_every_run_that_names_cells_or_a_packet_selects_a_cell_the_tree_holds():
    assert empty_runs() == []


def test_a_run_that_names_only_cells_no_packet_writes_is_caught():
    jobs = {"host-x": {"runner": "host", "when": ["weekly"], "runs": ["--native --os linux --cadence {cadence} "
                                                                       "--cell lin-podman"]}}

    assert empty_runs(jobs) == ["host-x: --native --os linux --cadence weekly --cell lin-podman"]
    assert empty_runs({"host-x": {**jobs["host-x"], "blocked": "a-gap"}}) == []


def entry_images(job):
    """The images an entry builds: each container run's, and calibrate.py's for its command."""
    images = {args.image for args in _args(job) if not args.native}
    return images | ({calibrate.IMAGE} if "tools/deploy/calibrate.py" in job.get("command", "") else set())


def test_an_entry_that_builds_on_the_full_image_frees_the_runners_disk_first():
    """full is 13.6 GB on disk and full-latest 22.1 GB; the runner's own tools leave less than that free."""
    crowded = [name for name, job in MAP["jobs"].items() if not job.get("free_disk")
               and any("full" in pinsfile.IMAGE_CHAIN[image] for image in entry_images(job))]

    assert crowded == []


def frees_disk(kind):
    return any(step.get("if") == "matrix.free_disk" for step in DEPLOY["jobs"][kind]["steps"])


def test_every_runner_kind_given_a_free_disk_entry_has_the_step_that_frees_it():
    kinds = {job["runner"] for job in MAP["jobs"].values() if job.get("free_disk")}

    assert sorted(kind for kind in kinds if not frees_disk(kind)) == []


def placed_by_arch(job):
    """An arm entry builds arm64 images only, and no other entry builds one."""
    arm64 = [image.endswith(pinsfile.ARM64) for image in entry_images(job)]
    return bool(arm64) and all(arm64) if job["runner"] == "arm" else not any(arm64)


def test_the_arm_runner_builds_the_arm64_images_and_no_other_runner_does():
    assert [name for name, job in MAP["jobs"].items() if not placed_by_arch(job)] == []
    assert not placed_by_arch({"runner": "linux", "runs": ["--cadence weekly --os linux --image cells-arm64"],
                               "when": ["weekly"]})


def test_lin_arm64_runs_on_the_arm_runner():
    """On the x86_64 runner, with --image cells, the cell skipped and the job passed."""
    runners = [job["runner"] for job in MAP["jobs"].values() if any("lin-arm64" in args.cell for args in _args(job))]

    assert runners == ["arm"]


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


def test_the_action_log_job_reads_deploy_action_s_finished_log():
    """A step cannot read its own job's log, so the posting lines are checked by
    a job that needs deploy-action, runs whatever it concluded, and may read
    the run's logs."""
    job = CI["jobs"]["deploy-action-log"]
    check = step_named(CI, "deploy-action-log", "assert what deploy-action's log shows")

    assert (job["needs"], job["if"], job["runs-on"]) == ("deploy-action", "always()", PINS["runners"]["linux"])
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert CI["jobs"]["deploy-action"]["outputs"] == {"outcome": "${{ steps.action.outcome }}"}
    assert "--job deploy-action" in check["run"] and "needs.deploy-action.outputs.outcome" in check["run"]
    assert check["env"] == {"GH_TOKEN": "${{ github.token }}"}


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


# --- the GitHub Actions cache -------------------------------------------------------
# Which image each job caches there is a cost choice: a repository gets 10 GB
# of Actions cache, and past it GitHub evicts the least recently used entry.
# tools/deploy/README.md gives the measured sizes behind the choice, so its
# table must say what the workflows run.

GUIDE = ROOT / "tools" / "deploy" / "README.md"


def _cache_word(args):
    return "--no-cache" if args.no_cache else args.cache


def _push_calls():
    return [(name, argv) for name in PUSH_JOBS for argv in ci_invocations([name])]


def _linux_entry_calls(jobs):
    """The run.py calls of the deploy.yml entries that run cells in containers,
    on the x86_64 runner (linux) or the arm64 one (arm)."""
    linux = {name: job for name, job in jobs.items() if job["runner"] in ("linux", "arm")}
    return [(name, argv) for name, job in linux.items() for _, argv in _job_invocations(job)]


def cache_rows(jobs=MAP["jobs"]):
    """(job, image, cache) for each run.py call that builds a Linux image, as
    ci.yml's push jobs and deploy.yml's linux entries run it; a native run
    builds no image."""
    parsed = [(name, run.parse(argv)) for name, argv in _push_calls() + _linux_entry_calls(jobs)]
    return {(name, args.image, _cache_word(args)) for name, args in parsed if not args.native}


def _table_rows(section):
    return [re.findall(r"`([^`]+)`", line) for line in section.splitlines() if line.startswith("| `")]


def _cache_section(text):
    return text.split("\n## The GitHub Actions cache\n", 1)[-1].split("\n## ", 1)[0]


def guide_cache_rows(text):
    """(job, image, cache): the first three backticked words of each row of the
    guide's table under "## The GitHub Actions cache" that names a job."""
    jobs = MAP["jobs"].keys() | PUSH_JOBS.keys()
    return {tuple(words[:3]) for words in _table_rows(_cache_section(text)) if words[0] in jobs}


def test_the_deploy_guide_says_how_each_job_caches_the_image_it_builds():
    assert guide_cache_rows(GUIDE.read_text(encoding="utf-8")) == cache_rows()


def test_a_job_that_changes_how_it_caches_its_image_is_caught():
    jobs = {**MAP["jobs"], "nightly-linux-full": {**MAP["jobs"]["nightly-linux-full"],
                                                  "runs": ["--cadence {cadence} --os linux --image full --cache gha"]}}

    assert ("nightly-linux-full", "full", "gha") in cache_rows(jobs) - guide_cache_rows(GUIDE.read_text(encoding="utf-8"))


def cold_builds(rows):
    """(job, image) for each job that builds with --cache local an image some
    job keeps in the Actions cache. A fresh runner holds no layers, so that job
    builds the image cold, which [budget.deploy-linux] puts at up to 40 minutes
    for core on a 4-core runner."""
    cold = {(image, "local") for _, image, cache in rows if cache == "gha"}
    return sorted((name, image) for name, image, cache in rows if (image, cache) in cold)


def test_every_job_that_builds_an_image_the_actions_cache_holds_reads_it():
    assert cold_builds(cache_rows()) == []


def test_a_job_that_builds_a_cached_image_with_the_local_cache_is_caught():
    jobs = {**MAP["jobs"], "weekly-online": {**MAP["jobs"]["weekly-online"],
                                             "runs": ["--online --cadence {cadence} --os linux --image core -n 4"]}}

    assert ("weekly-online", "core") in cold_builds(cache_rows(jobs))


# The images built on full, whose layers take 13.62 GB of the runner's disk in
# the daemon alone.
ON_FULL = {image for image, chain in pinsfile.IMAGE_CHAIN.items() if "full" in chain}


def crowded_builds(jobs):
    """Linux entries that build full or an image on it without freeing the
    runner's disk first (deploy.yml's free_disk step)."""
    builds = {name for name, argv in _linux_entry_calls(jobs) if run.parse(argv).image in ON_FULL}
    return sorted(name for name in builds if not jobs[name].get("free_disk"))


def test_every_job_that_builds_full_or_an_image_on_it_frees_the_runners_disk_first():
    assert ON_FULL == {"full", "gui", "full-latest"}
    assert crowded_builds(MAP["jobs"]) == []


def test_a_job_that_builds_gui_on_a_full_disk_is_caught():
    jobs = {**MAP["jobs"], "nightly-gui": {**MAP["jobs"]["nightly-gui"], "free_disk": False}}

    assert crowded_builds(jobs) == ["nightly-gui"]


# Compressed bytes from `docker save` of the images one builder built at
# 9707cc6d on 2026-09-25, and of the Dockerfile stage each image starts with:
# cells-pre is the first 16 layers of core and ci, core-pre the first 18 of
# core, full-pre the first 24 of full and gui. The guide's figures for images
# that build on each other's cached layers come from these, so a figure that
# counts a shared layer twice, or adds an image's layers to the wrong stage,
# fails here.
IMAGE_BYTES = {"core": 1_454_922_334, "ci": 555_781_981, "full": 3_762_100_936, "gui": 4_359_133_961}
STAGE_BYTES = {"cells-pre": 442_722_398, "core-pre": 1_353_670_817, "full-pre": 3_660_849_466}


def megabytes(size):
    return f"{round(size / 1e6):,} MB"


def gigabytes(size):
    return f"{size / 1e9:.2f} GB"


def starts_with(image):
    """The Dockerfile stage an image adds its own layers to: the -pre stage of
    the image below it in IMAGE_CHAIN."""
    return pinsfile.IMAGE_CHAIN[image][-2] + "-pre"


def own_bytes(image):
    return IMAGE_BYTES[image] - STAGE_BYTES[starts_with(image)]


def chained_bytes(images):
    """What the Actions cache holds when core builds every stage and each other
    image reads the scope that holds the stage it starts with (ci and full
    read core's, gui reads full's), so it stores only its own layers."""
    return IMAGE_BYTES["core"] + sum(own_bytes(image) for image in images if image != "core")


def _cells(section):
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines() if line.startswith("| `")]


def stage_rows(text):
    """The cells of each row of the guide's table of what each image starts with."""
    return {tuple(row) for row in _cells(_cache_section(text)) if row[0].strip("`") in IMAGE_BYTES}


def measured_stage_rows():
    return {(f"`{image}`", megabytes(IMAGE_BYTES[image]),
             f"`{starts_with(image)}` ({megabytes(STAGE_BYTES[starts_with(image)])})", megabytes(own_bytes(image)))
            for image in IMAGE_BYTES}


def stated_cache_figures(section):
    """(images, figure) for each figure the guide gives for images built on each
    other's cached layers: the last column of each row that names two or more
    images, and the options table's "for all four"."""
    rows = [(tuple(re.findall(r"`([^`]+)`", row[0])), row[2]) for row in _cells(section) if " and " in row[0]]
    return rows + [(tuple(IMAGE_BYTES), figure) for figure in re.findall(r"(\d+\.\d\d GB) for all four", section)]


def wrong_cache_figures(text):
    """(images, the figure the guide gives, the figure the layers give) for each
    stated figure the measured bytes do not give."""
    return [(images, figure, gigabytes(chained_bytes(images))) for images, figure in
            stated_cache_figures(_cache_section(text)) if figure != gigabytes(chained_bytes(images))]


def test_the_deploy_guide_gives_the_stage_each_image_starts_with_and_its_own_layers_as_measured():
    assert starts_with("ci") == starts_with("core") == "cells-pre"
    assert stage_rows(GUIDE.read_text(encoding="utf-8")) == measured_stage_rows()


def test_a_stage_row_that_adds_gui_to_the_whole_full_image_is_caught():
    text = GUIDE.read_text(encoding="utf-8").replace("| `full-pre` (3,661 MB) |", "| `full` (3,762 MB) |")

    assert stage_rows(text) != measured_stage_rows()


def test_the_deploy_guide_computes_the_cache_for_images_built_on_each_other_from_their_own_layers():
    text = GUIDE.read_text(encoding="utf-8")

    assert "for all four" in _cache_section(text)
    assert wrong_cache_figures(text) == []


def test_a_cache_figure_that_counts_the_debian_base_layer_twice_is_caught():
    four = tuple(IMAGE_BYTES)
    twice = gigabytes(chained_bytes(four) - 29_830_418)
    text = GUIDE.read_text(encoding="utf-8").replace(gigabytes(chained_bytes(four)), twice)

    assert wrong_cache_figures(text) == [(four, twice, "4.67 GB")] * 2


# run.py caches with mode=min, which BuildKit documents as "only export layers
# for the resulting image": a scope holds every layer of its image, the stages
# its FROM lines chain through included, and none of the stages it copies files
# from with COPY --from. ci and full reading core's scope finds the stages they
# start with only because of that, so the guide's gloss must say it, and must
# not call it the image's own layers, which the stage table uses for the layers
# an image adds to the stage it starts with.
DOCKERFILE = ROOT / "tests" / "deploy" / "docker" / "Dockerfile"


def dockerfile_stages(text):
    """{stage: (what it is FROM, the stages it copies files from)}."""
    stages = {}
    for line in text.splitlines():
        if match := re.match(r"FROM (\S+) AS (\S+)", line):
            stages[match[2]] = (match[1], set())
        elif match := re.match(r"COPY --from=(\S+)", line):
            stages[list(stages)[-1]][1].add(match[1])
    return stages


STAGES = dockerfile_stages(DOCKERFILE.read_text(encoding="utf-8"))


def built_on(image):
    """The stages an image's FROM lines chain through, itself included."""
    chain = [image]
    while STAGES[chain[-1]][0] in STAGES:
        chain.append(STAGES[chain[-1]][0])
    return set(chain)


def copied_from(image):
    """The stages an image, or a stage it is built on, copies files from."""
    return set().union(*(STAGES[stage][1] for stage in built_on(image))) - built_on(image)


def _stage_names(text):
    return {word for word in re.findall(r"`([^`]+)`", text) if word in STAGES}


def min_mode_gloss(text):
    """What the guide's paragraph on mode=min says core's scope holds: the
    stages it names before "leaves out", those it names after, the size it
    gives, and whether it says "own layers"."""
    gloss = next(part for part in _cache_section(text).split("\n\n") if "`mode=min`" in part)
    exported, _, left_out = gloss.partition("leaves out")
    return {"exported": _stage_names(exported), "left out": _stage_names(left_out),
            "size": re.findall(r"[\d,]+ MB", exported), "own layers": "own layers" in gloss}


def min_mode_scope(image="core"):
    return {"exported": built_on(image), "left out": copied_from(image),
            "size": [megabytes(IMAGE_BYTES[image])], "own layers": False}


def test_the_deploy_guide_says_mode_min_caches_every_layer_of_the_image_and_no_stage_it_copies_from():
    assert built_on("core") == {"base", "cells-pre", "core-pre", "core"}
    assert copied_from("core") == {"uv", "node", "runner", "npm-fixtures", "harness-core", "wheelhouse"}
    assert {starts_with("ci"), starts_with("full")} <= built_on("core")
    assert starts_with("gui") in built_on("full")
    assert min_mode_gloss(GUIDE.read_text(encoding="utf-8")) == min_mode_scope()


def test_a_gloss_that_calls_what_mode_min_caches_the_images_own_layers_is_caught():
    text = GUIDE.read_text(encoding="utf-8")
    gloss = next(part for part in _cache_section(text).split("\n\n") if "`mode=min`" in part)
    old = ("`--cache gha` makes BuildKit read and write an image's layers in the GitHub\n"
           "Actions cache, under `scope=crapkit-deploy-<image>` with `mode=min` (the\n"
           "image's own layers, not those of the stages that feed it).")

    assert min_mode_gloss(text.replace(gloss, old)) == {"exported": set(), "left out": set(), "size": [],
                                                        "own layers": True}
