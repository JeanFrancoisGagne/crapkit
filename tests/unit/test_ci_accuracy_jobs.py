"""CI runs the calculation-accuracy tiers the way docs/accuracy.md says.

ci.yml's `accuracy-push` runs every push check on Ubuntu and the OS-sensitive
ones on Windows, `accuracy-xplat` compares the two receipts, the verdict job
judges change control, the wheel diff and the self-measurement floor over the
two measured wheels, and `accuracy-green` moves the ref change control judges
against. These read each command the way the runner does: through the argument
parser of the script it calls, with every `${{ matrix.* }}` filled.
"""
import fnmatch
import json
from pathlib import Path
import re
import runpy
import shlex
import tomllib

import pytest
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


def _cell_receipts():
    """(artifact name, receipt file name) for every cell of the jobs xplat needs,
    read from each cell's upload step and its tier's --receipt."""
    jobs = _jobs("accuracy.yml")
    for name in jobs["xplat"]["needs"]:
        job = jobs[name]
        if not any("tools/accuracy/run.py --tier" in str(item.get("run", "")) for item in job["steps"]):
            continue
        rows = job.get("strategy", {}).get("matrix", {})
        rows = rows.get("include") or [{"python": python} for python in rows.get("python", [])] or [{}]
        upload = [item for item in job["steps"] if str(item.get("uses", "")).startswith("actions/upload-artifact@")
                  and "receipt-" in str(item.get("with", {}).get("name", ""))]
        for row in rows:
            command = job["steps"][_tier_index(job)]["run"].replace("\\\n", " ")
            receipt = re.search(r"--receipt (\S+)", rendered(command, row)).group(1)
            yield rendered(upload[0]["with"]["name"], row), Path(receipt).name


def test_xplat_refuses_when_a_cell_the_plan_names_handed_in_no_receipt():
    """xplat compares the receipts it downloads with one another and passes on
    one. A rerun of only the failed cells after a green cell's receipt expired,
    or a cell that died before writing its receipt, left it fewer receipts and a
    pass. The plan job names every receipt xplat compares, and xplat checks each
    is there before it compares."""
    jobs = _jobs("accuracy.yml")
    plan = step(jobs["plan"], "id", "plan")
    found = re.search(r'receipts="([^"]*)"', plan["run"])
    named = found.group(1).split() if found else []
    patterns = [item["with"]["pattern"] for item in jobs["xplat"]["steps"]
                if str(item.get("uses", "")).startswith("actions/download-artifact@")]
    compared = sorted(receipt for artifact, receipt in _cell_receipts()
                      if any(fnmatch.fnmatch(artifact, pattern) for pattern in patterns))
    assert sorted(named) == compared and len(compared) >= 3
    check = step(jobs["xplat"], "name", "every receipt the plan names is here")
    compare = step(jobs["xplat"], "run", "python tools/accuracy/wheel_diff.py xplat")
    assert jobs["plan"]["outputs"]["receipts"] == "${{ steps.plan.outputs.receipts }}"
    assert check["env"]["RECEIPTS"] == "${{ needs.plan.outputs.receipts }}"
    assert 'for name in $RECEIPTS' in check["run"] and '[ -f "receipts/$name" ]' in check["run"]
    assert jobs["xplat"]["steps"].index(check) < jobs["xplat"]["steps"].index(compare)


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


GROUND_TRUTH = "ground truth: every probe function in every recording"
# The plan step's tier for each mode that starts the Windows cells; a pull
# request's `accuracy` label runs the nightly tier.
TIER_OF = {"nightly": "nightly", "label": "nightly", "release": "release"}


def _needs_uv(windows: dict, mode: str, row: dict) -> bool:
    """A replay installs each commit's venv with `uv pip install`, and the ground
    truth check's nightly producer reruns build each Python producer's venv with
    uv: without it each rerun ends as an infra miss, and a tier whose only misses
    are infra runs again whole."""
    nightly = step(windows, "run", "python tools/accuracy/retro.py")
    replays = _holds(nightly["if"], mode, row) or (
        mode == "release" and _runs_retro_release(windows, row, "win32"))
    tier = TIER_OF[mode]
    return replays or (tier == "nightly" and GROUND_TRUTH in _cell_checks(windows, row, "win32", tier))


def test_every_windows_cell_that_runs_uv_has_it_before_its_tier():
    """The runner image ships no uv: without setup-uv a stale row in the release
    cell ends in FileNotFoundError instead of a verdict, and in the 3.11 nightly
    cell seven Python producer reruns failed with 'uv is not on PATH'."""
    windows = _jobs("accuracy.yml")["windows"]
    setup = step(windows, "uses", "astral-sh/setup-uv@")
    nightly = step(windows, "run", "python tools/accuracy/retro.py")
    first = min(_tier_index(windows), windows["steps"].index(nightly))
    for mode, tier in TIER_OF.items():
        for python in windows["strategy"]["matrix"]["python"]:
            row = {"python": python}

            assert _holds(setup["if"], mode, row, tier) == _needs_uv(windows, mode, row), (mode, python)
    assert windows["steps"].index(setup) < first


# Each Windows cell in minutes, job start to end, on windows-latest: the larger
# of run 36940595657 (26628afe; 3.11 99.9, 3.13 122.3) and run 36960774745
# (5ba27dd3; 3.11 111.1, 3.13 87.1). In run 36940595657 the 3.13 cell's nightly
# tier took 109.7 of its 122.3 min and its past-bug replays 10.0, which judged
# every row by a kept verdict in 0.1 min in run 36960774745; the 3.11 cell
# replays none.
WINDOWS_CELL_MINUTES = {"3.11": 111.1, "3.13": 122.3}
WINDOWS_REPLAY_MINUTES = 10.0


def test_the_windows_cells_get_twice_what_the_nightly_cell_measured():
    """Both cells were cancelled at a 75-minute bound nobody had measured, with the
    tier at 97% and 99%. The 170 minutes that followed came from one machine's
    seconds scaled by one push-tier pair, and the 3.13 cell then took 122.3 of
    them on a runner. The bound is twice the slower cell, as ci.yml bounds its
    legs, and the replay step's own bound covers twice its replays."""
    windows = _jobs("accuracy.yml")["windows"]
    replays = step(windows, "run", "python tools/accuracy/retro.py")
    slowest = max(WINDOWS_CELL_MINUTES.values())

    assert sorted(WINDOWS_CELL_MINUTES) == windows["strategy"]["matrix"]["python"]
    assert 2 * slowest <= windows["timeout-minutes"] <= min(2 * slowest + 5, 360)
    assert 2 * WINDOWS_REPLAY_MINUTES <= replays["timeout-minutes"]


# Each oracle leg in minutes, job start to end: the larger of run 36940595657
# (26628afe) and run 36960774745 (5ba27dd3). Both cut the verdict-score leg at
# the 60-minute bound. The verdict, score and suite-strength legs that run in
# its place have run on no runner. They were timed on one machine in
# crapkit-accuracy:a45958df8206 (run.py --tier nightly -n 4, the larger of two
# runs at this tree), as verdict-score was at 5ba27dd3, and are scaled to a
# runner by the legs timed both ways. The pytest session took this many times its minutes on
# that machine: history 1.94 to 2.75 (4.46 to 6.32 min against 2.30), coverage
# 2.29 to 2.48 (8.87 to 9.58 against 3.87), verdict-score at 62118e3e 2.27
# (47.62 against 20.95). A leg takes the largest, plus the 1.9 min a job spends
# outside the session: up to 1.61 pulling the image and starting pytest, 0.28 in
# its other steps. No leg runs verdict-score now; its row keeps a matrix that
# joins the three again red.
ORACLE_SESSION_PER_LOCAL = 6.32 / 2.30
ORACLE_OUTSIDE_SESSION_MINUTES = 1.9
ORACLE_LOCAL_MINUTES = {"verdict-score": 29.0, "verdict": 11.20, "score": 5.92, "suite-strength": 6.43}
ORACLE_LEG_MINUTES = {
    ("analysis", "3.11"): 8.72, ("analysis", "3.12"): 8.32, ("analysis", "3.13"): 7.72,
    ("analysis", "3.14"): 7.77, ("corpus", "3.11"): 11.20, ("corpus", "3.12"): 12.62,
    ("corpus", "3.13"): 11.65, ("corpus", "3.14"): 13.47, ("coverage", "3.12"): 10.95,
    ("history", "3.12"): 7.65,
    **{(shard, "3.12"): ORACLE_OUTSIDE_SESSION_MINUTES + minutes * ORACLE_SESSION_PER_LOCAL
       for shard, minutes in ORACLE_LOCAL_MINUTES.items()},
}
# A release run has finished no oracle leg (run 36847745298 stopped at the image),
# and at release the suite-strength leg replays every stale past-bug row:
# release keeps the bound every leg shared before the legs had their own.
RELEASE_ORACLE_MINUTES = 115
ORACLE_BOUND = re.compile(r"\$\{\{ needs\.plan\.outputs\.mode == 'release' && (\d+) \|\| matrix\.minutes \}\}")


def _oracle_rows() -> list[dict]:
    return _jobs("accuracy.yml")["oracles"]["strategy"]["matrix"]["include"]


def _oracle_legs() -> list[tuple[str, str]]:
    return [(row["shard"], row["python"]) for row in _oracle_rows()]


def _oracle_leg_minutes() -> dict[str, float]:
    """The minutes each leg of the oracles matrix measured, by `shard python`."""
    legs = _oracle_legs()
    assert set(legs) <= set(ORACLE_LEG_MINUTES), "measure each leg and name its minutes in ORACLE_LEG_MINUTES"
    return {f"{shard} {python}": ORACLE_LEG_MINUTES[shard, python] for shard, python in legs}


def _oracle_bounds() -> dict[str, int]:
    """The bound each oracle leg runs under outside a release, by `shard python`:
    the minutes its own matrix row carries."""
    job = _jobs("accuracy.yml")["oracles"]
    found = ORACLE_BOUND.fullmatch(str(job["timeout-minutes"]))

    assert found and int(found.group(1)) == RELEASE_ORACLE_MINUTES, job["timeout-minutes"]
    assert job["name"] == "oracles (${{ matrix.shard }}, ${{ matrix.python }})"
    return {f"{row['shard']} {row['python']}": row.get("minutes") for row in _oracle_rows()}


def test_every_oracle_leg_gets_twice_what_it_measured():
    """The legs shared one bound: at 60 minutes it cut the verdict-score leg in
    runs 36940595657 and 36960774745, and at 115 it gave an analysis leg fourteen
    times what it took. Each row carries its own bound, between twice what that
    leg measured and 5 minutes past it, as the Windows cells' is."""
    minutes = _oracle_leg_minutes()
    off = {leg: (round(minutes[leg], 2), bound) for leg, bound in _oracle_bounds().items()
           if bound is None or not 2 * minutes[leg] <= bound <= 2 * minutes[leg] + 5}

    assert off == {}, "each leg's minutes and the bound its row carries"


def test_every_shard_the_checks_name_runs_in_an_oracle_leg():
    """A shard no leg names runs in no CI cell, and a leg whose shard no checks
    module names runs nothing."""
    shards = {check.shard for check in RUN_TOOL["load_checks"]()}

    assert shards == {shard for shard, _ in _oracle_legs()}


# lizard-edge ran both corpus tiers and the comparison in 21.7 min in run
# 36940595657, its first run in the accuracy image, and in 23.4 in run 36960774745.
LIZARD_EDGE_MINUTES = 23.4


def test_lizard_edge_gets_twice_what_it_measured():
    bound = _jobs("accuracy.yml")["lizard-edge"]["timeout-minutes"]

    assert 2 * LIZARD_EDGE_MINUTES <= bound <= 2 * LIZARD_EDGE_MINUTES + 5


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


def _holds(condition, mode: str, row: dict, tier: str | None = None) -> bool:
    """A step's `if:` as the runner reads it for one mode, its tier (the mode's
    name when None) and one matrix row; the operators these steps use map one to
    one onto Python's."""
    text = re.sub(r"matrix\.(\w+)", lambda found: repr(str(row[found.group(1)])), condition or "true")
    for name, value in (("needs.plan.outputs.mode", repr(mode)), ("needs.plan.outputs.tier", repr(tier or mode)),
                        ("always()", "True"), ("&&", " and "), ("||", " or "), ("true", "True")):
        text = text.replace(name, value)
    return eval(text)


MUTATION_RELEASE = "mutation receipts cover the release"


def _release_cells():
    """(job, matrix row, sys.platform) for every cell a release run starts; each
    row names the Python minor the cell runs."""
    jobs = _jobs("accuracy.yml")
    for row in jobs["oracles"]["strategy"]["matrix"]["include"]:
        yield jobs["oracles"], row, "linux"
    for python in jobs["windows"]["strategy"]["matrix"]["python"]:
        yield jobs["windows"], {"python": python}, "win32"
    macos = step(jobs["macos"], "uses", "actions/setup-python@")["with"]["python-version"]
    yield jobs["macos"], {"python": macos}, "darwin"


def _cell_checks(job: dict, row: dict, platform: str, tier: str = "release") -> set:
    """The names of the checks the cell runs in `tier`, read by run.py's own
    parser and check selection."""
    command = job["steps"][_tier_index(job)]["run"].replace("\\\n", " ")
    command = command[command.index("python tools/accuracy/run.py"):].replace('"$TIER"', tier)
    command = rendered(command.replace("${{ needs.plan.outputs.tier }}", tier), row)
    args = arguments(RUN_TOOL["_run_parser"]().parse_args, command, "tools/accuracy/run.py")
    checks = RUN_TOOL["selected"](RUN_TOOL["load_checks"](), tier, args.shard, platform, args.os_sensitive,
                                  python=row["python"], local=args.local)
    return {check.name for check in checks}


def _runs_retro_release(job: dict, row: dict, platform: str) -> bool:
    return RETRO_RELEASE in _cell_checks(job, row, platform)


def test_one_release_cell_per_os_replays_the_past_bugs_and_none_reads_mutation_receipts():
    """`retro.py release` judges the same public rows on every Python of one OS,
    so a second Windows cell repeated each replay. `mutation.py covered` reads
    receipts no CI checkout holds and exits 3 there, so only the releasing
    machine runs it."""
    retro = [(platform, row["python"]) for job, row, platform in _release_cells()
             if _runs_retro_release(job, row, platform)]
    mutation = [row for job, row, platform in _release_cells()
                if MUTATION_RELEASE in _cell_checks(job, row, platform)]

    assert sorted(retro) == [("linux", "3.12"), ("win32", "3.13")]
    assert mutation == []


def _verdict_cache_steps(job: dict, row: dict, action: str, where) -> list[dict]:
    """The steps of `action` on the verdict cache that run at release for `row`,
    at an index `where` accepts."""
    return [item for index, item in enumerate(job["steps"])
            if str(item.get("uses", "")).startswith(action) and item.get("with", {}).get("path") == VERDICTS
            and where(index) and _holds(item.get("if"), "release", row)]


@pytest.mark.parametrize("field, problem", [
    ({"cells": ["windows-3.13"]}, "names cell 'windows-3.13'; a cell is <sys.platform>-<minor>"),
    ({"cells": ["win32"]}, "names cell 'win32'"),
    ({"local": "yes"}, "sets local to something other than True or False"),
    ({"local": True, "os": ["linux"]}, "is local and names an os"),
])
def test_a_check_that_names_a_cell_run_py_cannot_match_is_refused(field, problem):
    """A misspelled cell would leave the check out of every cell without a word."""
    row = {"name": "replays", "seconds": 0, "tiers": ["release"], "argv": ["python", "x.py"], **field}

    with pytest.raises(RUN_TOOL["CheckError"], match=re.escape(problem)):
        RUN_TOOL["_check"]("suite_strength", "suite-strength", row)


def test_a_check_with_cells_runs_only_in_them_and_a_local_one_only_on_the_releasing_machine():
    check = RUN_TOOL["_check"]("k", "s", {"name": "c", "seconds": 0, "tiers": ["release"], "argv": ["python", "x.py"],
                                          "cells": ["linux-3.12", "win32-3.13"]})
    local = RUN_TOOL["_check"]("k", "s", {"name": "l", "seconds": 0, "tiers": ["release"], "argv": ["python", "y.py"],
                                          "local": True})
    picked = {(platform, python, here): [found.name for found in RUN_TOOL["selected"](
        [check, local], "release", None, platform, python=python, local=here)]
        for platform in ("linux", "win32") for python in ("3.11", "3.12", "3.13") for here in (False, True)}

    assert {cell for cell, names in picked.items() if "c" in names} == {
        ("linux", "3.12", False), ("win32", "3.13", False), *((platform, python, True) for platform in ("linux", "win32")
                                                              for python in ("3.11", "3.12", "3.13"))}
    assert {cell for cell, names in picked.items() if "l" in names} == {cell for cell in picked if cell[2]}


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
        assert key == rendered(saves[0]["with"]["key"], row) and key.endswith("${{ github.run_id }}-${{ github.run_attempt }}")
        assert key.startswith(rendered(restores[0]["with"]["restore-keys"], row))
        assert "always()" in saves[0]["if"]
        keys.append(key)

    assert len(keys) == len(set(keys)) == 2


def test_a_rerun_attempt_saves_what_it_judged_under_a_key_of_its_own():
    """GitHub keeps run_id across a re-run, and actions/cache/save refuses a key
    that exists: a key of run_id alone dropped every verdict attempt 2 judged,
    so attempt 3 replayed them again. The attempt makes the key new, and the
    restore-keys prefix still finds the newest earlier entry."""
    saves = [(name, item) for name, job in _jobs("accuracy.yml").items() for item in job.get("steps", [])
             if str(item.get("uses", "")).startswith("actions/cache/save@")]
    for name, job in _jobs("accuracy.yml").items():
        for item in job.get("steps", []):
            if str(item.get("uses", "")).startswith("actions/cache/restore@"):
                key, prefix = item["with"]["key"], item["with"]["restore-keys"]
                assert key.endswith("-${{ github.run_id }}-${{ github.run_attempt }}") and key.startswith(prefix), name

    assert len(saves) >= 6
    for name, item in saves:
        assert item["with"]["key"].endswith("-${{ github.run_id }}-${{ github.run_attempt }}"), name



def test_the_docs_promise_no_verdict_reuse_across_releases():
    """docs/accuracy.md said the release cells' cache served the next release
    within GitHub's 7-day eviction. Each release dispatches on a branch of its
    own, a run restores only caches saved on its own branch or the default
    branch, and only a release-mode run saves a release key, so no run on main
    ever seeds one."""
    from test_release_tool import release

    saves = [item for job in _jobs("accuracy.yml").values() for item in job.get("steps", [])
             if str(item.get("uses", "")).startswith("actions/cache/save@")
             and item["with"]["key"].startswith("retro-verdicts-release-")]
    doc = " ".join((ROOT / "docs/accuracy.md").read_text(encoding="utf-8").split())
    said = doc[doc.index("The release tier runs `retro.py release`"):doc.index("Rows R01 to R12 are bundle rows")]

    assert release.accuracy_branch("0.9.0") != release.accuracy_branch("0.9.1") != "main"
    assert saves and all("needs.plan.outputs.mode == 'release'" in item["if"] for item in saves)
    assert "next release" not in said
    assert "first attempt replays every stale row" in said


def _tier_cell(tier: str) -> str:
    doc = (ROOT / "docs/accuracy.md").read_text(encoding="utf-8")
    return next(line for line in doc.splitlines() if line.startswith(f"| `{tier}` |")).split(" | ")[-1]


def test_the_tier_table_says_what_the_nightly_and_weekly_runs_judge():
    """The tier table still said the nightly mutated the functions changed since
    the weekly run and replayed a slice of the past bugs. The retro job judges
    every public row each night by the verdict kept for it, and both mutation
    runs judge every function whose stored verdicts do not carry, whatever the
    weekly run did."""
    retro = _jobs("accuracy.yml")["retro"]
    nightly, weekly = _tier_cell("nightly"), _tier_cell("weekly")

    assert retro["if"] == "needs.plan.outputs.mode == 'nightly'"
    assert "slice" not in nightly and "since the weekly run" not in nightly
    assert "every public past-bug row, judged by the verdict kept for it or replayed" in nightly
    for cell in (nightly, weekly):
        assert "whose stored verdicts do not carry" in cell

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


NODE_PUSH = "npm ci --prefix tools/accuracy/node/push"


def _native(job: dict) -> bool:
    """A cell that runs its tier on the runner itself; the accuracy image carries
    the node tools, a runner does not."""
    return not any("docker run" in str(item.get("run", "")) for item in job["steps"])


def test_every_native_cell_installs_the_push_node_tools_before_its_tier():
    """The change-control checks ask ESLint and SonarJS for each JS and TS column.
    The macOS nightly cell ran them with no node tools, and four rows failed in
    run 36940595657 with 'oracle eslint 10.11.0 is not installed'."""
    jobs = _jobs("accuracy.yml")
    native = [name for name, job in jobs.items()
              if any("tools/accuracy/run.py --tier" in str(item.get("run", "")) for item in job.get("steps", []))
              and _native(job)]
    assert sorted(native) == ["macos", "windows"]
    for name in native:
        install = next((index for index, item in enumerate(jobs[name]["steps"])
                        if NODE_PUSH in str(item.get("run", ""))), None)

        assert install is not None and install < _tier_index(jobs[name]), name
        assert step(jobs[name], "uses", "actions/setup-node@")["with"]["node-version"] == "22", name


def _tier_steps(job: dict) -> list[dict]:
    return [item for item in job["steps"]
            if "tools/accuracy/run.py --tier" in str(item.get("run", ""))]


def _linux_tier_steps() -> dict[str, list[dict]]:
    """Each Linux cell of accuracy.yml that runs a tier, with the steps that run it."""
    linux = {name: job for name, job in _jobs("accuracy.yml").items()
             if job["runs-on"] == "ubuntu-latest"}
    return {name: _tier_steps(job) for name, job in linux.items() if _tier_steps(job)}


def test_every_linux_cell_runs_its_tier_in_the_accuracy_image():
    """The nightly oracles (pylint, the Node tools, the Go, Rust, Java, C and
    Swift binaries, shellmetrics, bugspots) are installed in the accuracy image
    and nowhere else on Linux, and their tests carry platform("linux") for that
    reason. A Linux cell that runs a tier on the bare runner fails each of them
    as missing: lizard-edge's first nightly failed 16 of the 21 tests in
    tests/accuracy/kit/test_oracles_non_ascii.py that way."""
    jobs = _jobs("accuracy.yml")
    cells = _linux_tier_steps()

    assert {"oracles", "lizard-edge"} <= set(cells)
    for name, steps in cells.items():
        needs = jobs[name]["needs"]
        assert "image" in ([needs] if isinstance(needs, str) else needs), name
        for item in steps:
            assert item["env"]["REF"] == "${{ needs.image.outputs.ref }}", name
            assert "docker run" in item["run"] and '"$REF"' in item["run"], name


def test_the_image_cells_mount_the_fetched_corpus_where_the_image_looks():
    for name, steps in _linux_tier_steps().items():
        for item in steps:
            assert '${CRAPKIT_ACCURACY_CORPUS:+-v "$CRAPKIT_ACCURACY_CORPUS:/corpus:ro"}' in (
                item["run"]), name


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
