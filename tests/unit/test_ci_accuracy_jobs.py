"""CI runs the calculation-accuracy tiers the way docs/accuracy.md says.

ci.yml's `accuracy-push` runs every push check on Ubuntu and the OS-sensitive
ones on Windows, `accuracy-xplat` compares the two receipts, the verdict job
judges change control, the wheel diff and the self-measurement floor over the
two measured wheels, and `accuracy-green` moves the ref change control judges
against. These read each command the way the runner does: through the argument
parser of the script it calls, with every `${{ matrix.* }}` filled.
"""
from pathlib import Path
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
    assert (native.command, native.platform_only, native.slice_of) == ("nightly", True, 7)
    assert f"uv {setup['with']['version']}" == pins["oracle"]["uv"]["version_line"]
    assert setup["if"] == step(windows, "run", "python tools/accuracy/retro.py")["if"]


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
