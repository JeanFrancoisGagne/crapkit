"""CI runs the calculation-accuracy tiers the way docs/accuracy.md says.

ci.yml's `accuracy-push` runs every push check on Ubuntu and the OS-sensitive
ones on Windows, `accuracy-xplat` compares the two receipts, the verdict job
judges change control, the wheel diff and the self-measurement floor over the
two measured wheels, and `accuracy-green` moves the ref change control judges
against. These read each command the way the runner does: through the argument
parser of the script it calls, with every `${{ matrix.* }}` filled.
"""
import json
from pathlib import Path
import re
import runpy
import shlex
import tomllib

import yaml

from test_ci_parallel_jobs import CI, arguments, rendered, step

ROOT = Path(__file__).resolve().parents[2]
RUN_TOOL = runpy.run_path(str(ROOT / "tools/accuracy/run.py"))
RETRO_TOOL = runpy.run_path(str(ROOT / "tools/accuracy/retro.py"))
LOCK = "tools/accuracy/requirements-push.txt"
RUNNER_OS = {"ubuntu-latest": "Linux", "windows-latest": "Windows"}


def _jobs(name: str = "ci.yml") -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))["jobs"]


def _push_rows() -> list[dict]:
    return _jobs()["accuracy-push"]["strategy"]["matrix"]["include"]


def _tier_run(row: dict):
    """The push tier's command for one matrix row, parsed by run.py's own parser."""
    command = step(_jobs()["accuracy-push"], "name", "the push tier")["run"]
    filled = rendered(command, row).replace("${{ runner.os }}", RUNNER_OS[row["os"]])
    return arguments(RUN_TOOL["_run_parser"]().parse_args, filled, "tools/accuracy/run.py")


def test_ubuntu_runs_every_push_check_and_windows_the_os_sensitive_ones():
    runs = {row["os"]: _tier_run(row) for row in _push_rows()}

    assert set(runs) == {"ubuntu-latest", "windows-latest"}
    assert {args.tier for args in runs.values()} == {"push"}
    sensitive = {runner: args.os_sensitive for runner, args in runs.items()}
    assert sensitive == {"ubuntu-latest": False, "windows-latest": True}
    assert {(args.workers, args.shard) for args in runs.values()} == {(4, None)}


def test_windows_has_os_sensitive_checks_to_run():
    checks = RUN_TOOL["load_checks"]()

    assert RUN_TOOL["selected"](checks, "push", None, "win32", True)


def _installs(job: dict) -> list[str]:
    return [item["run"] for item in job["steps"] if "pip install" in str(item.get("run", ""))]


def test_the_accuracy_jobs_install_the_hash_locked_tier():
    """No floating version reaches an oracle: the lock carries a hash for every
    package, and crapkit itself goes in with no dependencies of its own."""
    allowed = {f"python -m pip install --require-hashes --no-deps -r {LOCK}",
               "python -m pip install --no-deps -e ."}
    jobs = _jobs()
    installs = [line for name in ("accuracy-push", "accuracy-xplat", "verdict")
                for line in _installs(jobs[name])]

    assert installs and set(installs) <= allowed


def _upload_name(row: dict) -> str:
    upload = step(_jobs()["accuracy-push"], "uses", "actions/upload-artifact@")
    return rendered(upload["with"]["name"], row).replace("${{ runner.os }}", RUNNER_OS[row["os"]])


def test_each_push_receipt_reaches_the_xplat_comparison():
    jobs = _jobs()
    uploads = {_upload_name(row) for row in _push_rows()}
    pattern = step(jobs["accuracy-xplat"], "uses", "actions/download-artifact@")["with"]["pattern"]
    compare = step(jobs["accuracy-xplat"], "run", "python tools/accuracy/wheel_diff.py")
    words = shlex.split(compare["run"])
    receipts = {Path(_tier_run(row).receipt).name for row in _push_rows()}

    assert jobs["accuracy-xplat"]["needs"] == "accuracy-push"
    assert all(name.startswith(pattern.rstrip("*")) for name in uploads)
    assert words[2] == "xplat"
    assert {Path(word).name for word in words[3:]} == receipts


def test_the_nightly_cells_compare_their_exports_by_the_same_rule():
    xplat = _jobs("accuracy.yml")["xplat"]

    assert step(xplat, "run", "python tools/accuracy/wheel_diff.py xplat") is not None


def _retro_args(job: dict):
    """The retro.py command a job runs (inside `docker run` or not), parsed by
    retro.py's own parser."""
    (run,) = [item["run"] for item in job["steps"] if "tools/accuracy/retro.py" in str(item.get("run", ""))]
    command = run[run.index("python tools/accuracy/retro.py"):]
    return arguments(RETRO_TOOL["_parser"]().parse_args, command, "tools/accuracy/retro.py")


def test_the_windows_nightly_cell_replays_the_past_bugs_the_image_cannot():
    """The retro job replays in the Linux image, where no Windows row runs: the
    Windows cell replays those rows, with the uv release pins.toml names."""
    jobs = _jobs("accuracy.yml")
    windows = jobs["windows"]
    setup = step(windows, "uses", "astral-sh/setup-uv@")
    pins = tomllib.loads((ROOT / "tools/accuracy/pins.toml").read_text(encoding="utf-8"))
    linux, native = _retro_args(jobs["retro"]), _retro_args(windows)

    assert (linux.command, linux.platform_only) == ("nightly", False)
    assert (native.command, native.platform_only) == ("nightly", True)
    assert f"uv {setup['with']['version']}" == pins["oracle"]["uv"]["version_line"]
    assert setup["if"] == step(windows, "run", "python tools/accuracy/retro.py")["if"]


COLD_SECONDS_A_ROW = 51.5  # a Linux row replayed with no worktree, venv or verdict kept, measured 2026-10-01
BOUND = re.compile(r"\$\{\{\s*fromJSON\('(\{.*?\})'\)\[inputs\.retro_seconds \|\| '(\d+)'\]\s*\}\}")


def _by_input(value, seconds: str) -> int:
    """A timeout-minutes the runner reads from the retro_seconds input, which a
    scheduled run leaves empty."""
    table, default = BOUND.fullmatch(str(value)).groups()
    return json.loads(table)[seconds or default]


def _workflow_inputs() -> dict:
    loaded = yaml.safe_load((ROOT / ".github/workflows/accuracy.yml").read_text(encoding="utf-8"))
    return loaded[True]["workflow_dispatch"]["inputs"]  # YAML 1.1 reads the key `on` as True


def _in_container_seconds(job: dict, seconds: str) -> int:
    replays = step(job, "run", "docker run")
    bound = replays["env"]["RETRO_SECONDS"].replace("${{ inputs.retro_seconds || '2400' }}", seconds or "2400")
    assert 'timeout "$RETRO_SECONDS" python tools/accuracy/retro.py nightly' in replays["run"]
    return int(bound)


def _linux_rows() -> int:
    bugs = RETRO_TOOL["read_table"](RETRO_TOOL["BUGS"], RETRO_TOOL["BUG_COLUMNS"])
    return sum(row["platform"] == "any" for row in RETRO_TOOL["_public"](bugs))


def test_one_dispatched_retro_run_can_judge_every_linux_row_cold():
    """The first nightly after the verdict cache's key moves replays every public
    Linux row, about 51.5 s each with nothing kept, and the scheduled bound cuts
    it at 2400 s. A dispatch with a larger retro_seconds seeds the cache in one
    run; the job and step bounds follow the input, so the container ends first."""
    retro_input = _workflow_inputs().get("retro_seconds", {})
    options = retro_input.get("options", [])
    job = _jobs("accuracy.yml")["retro"]

    assert retro_input.get("default") == "2400" and "2400" in options
    assert max(map(int, options)) >= _linux_rows() * COLD_SECONDS_A_ROW
    for seconds in ["", *options]:
        inside = _in_container_seconds(job, seconds)
        step_minutes = _by_input(step(job, "run", "docker run")["timeout-minutes"], seconds)
        job_minutes = _by_input(job["timeout-minutes"], seconds)
        assert inside == int(seconds or "2400")
        assert inside + 120 <= step_minutes * 60 and step_minutes + 10 <= job_minutes <= 360, seconds


RETRO_RELEASE = "retro replays for the release"
VERDICTS = ".crapkit/accuracy/retro-verdicts"  # retro.verdicts_dir() with no CRAPKIT_RETRO_VERDICTS


def _holds(condition, mode: str, row: dict) -> bool:
    """A step's `if:` as the runner reads it for one mode and matrix row; the
    operators these steps use map one to one onto Python's."""
    text = re.sub(r"matrix\.(\w+)", lambda found: repr(str(row[found.group(1)])), condition or "true")
    for name, value in (("needs.plan.outputs.mode", repr(mode)), ("needs.plan.outputs.tier", repr(mode)),
                        ("always()", "True"), ("&&", " and "), ("||", " or "), ("true", "True")):
        text = text.replace(name, value)
    return eval(text)


def _release_cells():
    """(job, matrix row, sys.platform) for every cell a release run starts."""
    jobs = _jobs("accuracy.yml")
    for row in jobs["oracles"]["strategy"]["matrix"]["include"]:
        yield jobs["oracles"], row, "linux"
    for python in jobs["windows"]["strategy"]["matrix"]["python"]:
        yield jobs["windows"], {"python": python}, "win32"
    yield jobs["macos"], {}, "darwin"


def _runs_retro_release(job: dict, row: dict, platform: str) -> bool:
    """Whether the cell's release tier, read by run.py's own parser and check
    selection, runs `retro.py release`."""
    command = job["steps"][_tier_index(job)]["run"].replace("\\\n", " ")
    command = command[command.index("python tools/accuracy/run.py"):].replace('"$TIER"', "release")
    command = rendered(command.replace("${{ needs.plan.outputs.tier }}", "release"), row)
    args = arguments(RUN_TOOL["_run_parser"]().parse_args, command, "tools/accuracy/run.py")
    checks = RUN_TOOL["selected"](RUN_TOOL["load_checks"](), "release", args.shard, platform, args.os_sensitive)
    return RETRO_RELEASE in {check.name for check in checks}


def _verdict_cache_steps(job: dict, row: dict, action: str, where) -> list[dict]:
    """The steps of `action` on the verdict cache that run at release for `row`,
    at an index `where` accepts."""
    return [item for index, item in enumerate(job["steps"])
            if str(item.get("uses", "")).startswith(action) and item.get("with", {}).get("path") == VERDICTS
            and where(index) and _holds(item.get("if"), "release", row)]


def test_a_release_cell_judges_an_unchanged_row_by_its_kept_verdict():
    """`retro.py release` replays a stale row with no verdict kept at the release
    tier. Without the cache each release cell, and each re-run of one, replayed
    every stale row again; with it a row whose digest and env key held is judged
    by the verdict kept for them. A cell that does not run the check keeps none."""
    keys = []
    for job, row, platform in _release_cells():
        tier = _tier_index(job)
        restores = _verdict_cache_steps(job, row, "actions/cache/restore@", lambda index: index < tier)
        saves = _verdict_cache_steps(job, row, "actions/cache/save@", lambda index: index > tier)
        if not _runs_retro_release(job, row, platform):
            assert restores == saves == [], row
            continue
        assert (len(restores), len(saves)) == (1, 1), row
        key = rendered(restores[0]["with"]["key"], row)
        assert key == rendered(saves[0]["with"]["key"], row) and key.endswith("${{ github.run_id }}")
        assert key.startswith(rendered(restores[0]["with"]["restore-keys"], row))
        assert "always()" in saves[0]["if"]
        keys.append(key)

    assert len(keys) == len(set(keys)) == 3


def _nightly_npm_prefixes() -> list[str]:
    """The Node tool sets docs/accuracy.md's nightly recipe installs, in its order."""
    text = (ROOT / "docs" / "accuracy.md").read_text(encoding="utf-8")
    section = text.split("### Nightly", 1)[1].split("\n### ", 1)[0]
    return [line.split("--prefix ", 1)[1].split()[0] for line in section.splitlines()
            if line.startswith("npm ci --prefix ")]


def test_the_windows_cell_installs_the_node_tools_the_nightly_recipe_names():
    """The Windows cell runs the nightly tier natively, outside the image that
    carries both Node tool sets: without tools/accuracy/node/nightly the
    istanbul-lib and crap-typescript check cannot load istanbul-lib-instrument
    and fails, and the producer reruns and Stryker end as infra misses."""
    windows = _jobs("accuracy.yml")["windows"]
    prefixes = _nightly_npm_prefixes()

    assert prefixes == ["tools/accuracy/node/push", "tools/accuracy/node/nightly"]
    for prefix in prefixes:
        install = step(windows, "run", f"npm ci --prefix {prefix} ")
        assert windows["steps"].index(install) < _tier_index(windows), prefix
        assert install.get("if", "needs.plan.outputs.tier == 'nightly'") == (
            "needs.plan.outputs.tier == 'nightly'"), prefix


def test_the_oracle_cells_reach_pypi_and_the_mutation_cells_do_not():
    """The nightly corpus and coverage shards list crapkit's releases and install
    the recorded coverage producers from PyPI; offline, each of those checks
    ends as an infra miss and the tier exits 3. A mutant never needs the network."""
    jobs = _jobs("accuracy.yml")
    def docker(name: str) -> list[str]:
        return [item["run"] for item in jobs[name]["steps"] if "docker run" in str(item.get("run", ""))]
    oracle, mutation = docker("oracles"), docker("mutation-diff") + docker("mutation-full")

    assert oracle and not any("--network" in run for run in oracle)
    assert mutation and all("--network none" in run for run in mutation)


CORPUS_CELLS = ("oracles", "lizard-edge", "windows", "macos")


def _tier_index(job: dict) -> int:
    return next(index for index, item in enumerate(job["steps"])
                if "tools/accuracy/run.py --tier" in str(item.get("run", "")))


def _corpus_steps(job: dict) -> tuple[int, int]:
    """(index of the corpus cache restore, index of the fetch) in one job."""
    cached = [index for index, item in enumerate(job["steps"])
              if item.get("with", {}).get("path") == ".crapkit/corpus"]
    assert len(cached) == 1
    return cached[0], job["steps"].index(step(job, "name", "The full corpus"))


def test_every_cell_that_reads_the_full_corpus_fetches_it_before_its_tier():
    """Without the fetch every full-corpus check in the cell ends as an infra
    miss. A fetch that fails (a digest nobody published yet) must not stop the
    cell: the checks that need no corpus still run and report."""
    jobs = _jobs("accuracy.yml")
    for name in CORPUS_CELLS:
        cache, fetch = _corpus_steps(jobs[name])
        found = jobs[name]["steps"][fetch]

        assert cache < fetch < _tier_index(jobs[name]), name
        assert "corpus.py fetch --dest .crapkit/corpus" in found["run"], name
        assert (found["continue-on-error"], found["env"]["GH_TOKEN"]) == (True, "${{ github.token }}")


def test_the_image_cells_mount_the_fetched_corpus_where_the_image_looks():
    run = step(_jobs("accuracy.yml")["oracles"], "name", "run.py in the image")["run"]

    assert '${CRAPKIT_ACCURACY_CORPUS:+-v "$CRAPKIT_ACCURACY_CORPUS:/corpus:ro"}' in run


def _verdict_steps() -> tuple[list, int]:
    steps = _jobs()["verdict"]["steps"]
    join = steps.index(step(_jobs()["verdict"], "run", "python tools/testing/ci.py"))
    return steps, join


def _named(steps: list, names: tuple) -> list:
    return [next(item for item in steps if item.get("name") == name) for name in names]


def test_the_verdict_judges_accuracy_after_the_join_whatever_it_decided():
    steps, join = _verdict_steps()
    found = _named(steps, ("change control against the last green main commit",
                           "the two wheels on the small corpus",
                           "the candidate's lane measured every CLI entry point"))

    assert all(steps.index(item) > join for item in found)
    assert all("!cancelled()" in item["if"] for item in found)


def test_the_accuracy_steps_read_the_hand_offs_the_join_reads():
    measured = CI.parse_arguments(["--base", "HEAD"]).measured
    verdict = _jobs()["verdict"]
    control = step(verdict, "name", "change control against the last green main commit")["run"]
    diff = shlex.split(step(verdict, "name", "the two wheels on the small corpus")["run"])
    floor = step(verdict, "name", "the candidate's lane measured every CLI entry point")

    assert f"--measured {measured.as_posix()}" in control and "refs/accuracy/green" in control
    assert [Path(diff[diff.index(flag) + 1]) for flag in ("--base-wheel", "--candidate-wheel")] == [
        measured / "base", measured / "candidate"]
    assert Path(floor["env"]["CRAPKIT_SELF_MEASURE"]) == measured / "candidate" / "cov" / "py.json"


def test_the_floor_step_names_a_test_that_exists():
    floor = step(_jobs()["verdict"], "name", "the candidate's lane measured every CLI entry point")
    node = shlex.split(floor["run"])[-1]
    path, _, name = node.partition("::")

    assert f"def {name}(" in (ROOT / path).read_text(encoding="utf-8")


def test_the_green_ref_moves_only_after_a_main_push_whose_accuracy_passed():
    green = _jobs()["accuracy-green"]
    move = step(green, "name", "move refs/accuracy/green to this commit")

    assert set(green["needs"]) == {"verdict", "accuracy-push", "accuracy-xplat"}
    assert "github.event_name == 'push'" in green["if"] and "refs/heads/main" in green["if"]
    assert green["permissions"] == {"contents": "write", "issues": "write"}
    assert all(f"needs.{job}.result == 'success'" in move["if"] for job in green["needs"])
    assert "merge-base --is-ancestor" in move["run"], "the ref never moves backwards"


def _issue_steps() -> list[tuple[str, str]]:
    """(workflow:job, script) for each step that opens an issue."""
    return [(f"{path.name}:{name}", entry["run"])
            for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
            for name, job in _jobs(path.name).items() for entry in job.get("steps", [])
            if "gh issue create" in entry.get("run", "")]


def test_a_step_that_files_an_issue_under_a_label_makes_the_label_first():
    """GitHub refuses an issue whose label the repository lacks, and neither
    accuracy-red nor accuracy-infra existed: accuracy-green failed on 38531c54 at
    the step that files the red run, with "could not add label: 'accuracy-red'
    not found"."""
    unlabelled = [name for name, run in _issue_steps()
                  if run.find("gh label create") < 0 or run.find("gh label create") > run.find("gh issue")]

    assert len(_issue_steps()) == 2
    assert unlabelled == []
