"""tools/accuracy/run.py: tiers and shards of planted checks, their exit codes and receipts.

Each test plants a checks directory in tmp_path: modules declaring CHECKS over
planted test files, the way tools/accuracy/checks/<key>.py declares a packet's.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import hang_guard

REPO = Path(__file__).resolve().parents[3]
RUN = REPO / "tools" / "accuracy" / "run.py"
TESTS = {
    "test_pass.py": "def test_ok():\n    assert True\n",
    "test_fail.py": "def test_bad():\n    assert 1 == 2\n",
    "test_infra.py": ("from accuracy.kit import runlog\n\n\ndef test_tool():\n"
                      "    runlog.note('infra', message='oracle x 1.0 is not installed')\n"
                      "    raise AssertionError('oracle x 1.0 is not installed')\n"),
    "test_notes.py": ("from accuracy.kit import runlog\n\n\ndef test_notes():\n"
                      "    runlog.note('events', counts={'R28': 3})\n"
                      "    runlog.note('oracle', name='radon', version='6.0.1')\n"
                      "    runlog.note('skipped_files', oracle='ast', count=2)\n"),
}


def _load_run():
    spec = importlib.util.spec_from_file_location("accuracy_run_tool", RUN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_tool = _load_run()


def _plant(tmp_path: Path, modules: dict) -> Path:
    """A checks directory: {module name: (shard, [(check name, test file, seconds)])}."""
    tests = tmp_path / "planted"
    tests.mkdir(exist_ok=True)
    for name, text in TESTS.items():
        (tests / name).write_text(text, encoding="utf-8")
    checks = tmp_path / "checks"
    checks.mkdir(exist_ok=True)
    for module, (shard, rows) in modules.items():
        declared = [{"name": name, "pytest": [(tests / test).as_posix()], "seconds": seconds}
                    for name, test, seconds in rows]
        (checks / f"{module}.py").write_text(
            f"SHARD = {shard!r}\nCHECKS = {declared!r}\n", encoding="utf-8")
    return checks


def _spawned(argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return hang_guard.run([sys.executable, str(RUN), *argv], cwd=REPO, env=env, text=True,
                          encoding="utf-8", errors="replace")


def _set_environ(values: dict) -> None:
    os.environ.clear()
    os.environ.update(values)


def in_process(argv: list[str], env: dict | None = None,
               where: Path = REPO) -> subprocess.CompletedProcess:
    """run.py's main in this process, as `python run.py ARGV` would run it from
    `where` with `env` as its whole environment: what it prints is caught, and the
    working directory and environment come back afterwards."""
    out, err = io.StringIO(), io.StringIO()
    saved, cwd = dict(os.environ), os.getcwd()
    try:
        _set_environ(env or saved)
        os.chdir(where)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_tool.main(argv)
    finally:
        _set_environ(saved)
        os.chdir(cwd)
    return subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())


def _run(checks: Path, receipt: Path, *args: str, env: dict | None = None, spawn: bool = False):
    """A tier over the planted checks; in process unless `spawn`, which starts
    run.py the way CI does. In process it starts outside the repo, where nothing
    it runs may depend on the working directory."""
    argv = ["--checks", str(checks), "--receipt", str(receipt), *args]
    done = _spawned(argv, env) if spawn else in_process(argv, env, checks.parent)
    saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else None
    return done, saved


def _outcomes(receipt: dict) -> dict:
    return {(check["key"], check["name"]): check["outcome"] for check in receipt["checks"]}


@pytest.mark.nightly
@pytest.mark.process
def test_a_passing_tier_exits_0_and_writes_its_receipt(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 3)])})

    done, receipt = _run(checks, tmp_path / "r.json", "--tier", "push", spawn=True)

    assert done.returncode == 0, done.stdout + done.stderr
    assert receipt["tier"] == "push" and receipt["outcome"] == "pass"
    [check] = receipt["checks"]
    assert (check["key"], check["name"], check["declared"], check["tests"]) == (
        "alpha", "passes", 3, 1)
    assert check["seconds"] >= 0
    assert receipt["hypothesis_seed"] == "derandomized"


@pytest.mark.nightly
@pytest.mark.process
def test_a_planted_failing_check_exits_1_and_names_itself(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 1),
                                                 ("fails", "test_fail.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 1
    assert _outcomes(receipt) == {("alpha", "passes"): "pass", ("alpha", "fails"): "fail"}
    assert "alpha: fails" in done.stdout


@pytest.mark.nightly
@pytest.mark.process
def test_a_missing_tool_exits_3_after_one_retry(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("needs x", "test_infra.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 3, done.stdout + done.stderr
    assert receipt["outcome"] == "infra" and receipt["attempts"] == 2
    assert receipt["infra"] == ["oracle x 1.0 is not installed"]
    assert _outcomes(receipt) == {("alpha", "needs x"): "infra"}


@pytest.mark.nightly
@pytest.mark.process
def test_a_real_failure_outranks_an_infra_miss(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("needs x", "test_infra.py", 1),
                                                 ("fails", "test_fail.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 1 and receipt["attempts"] == 1
    assert receipt["outcome"] == "fail"


@pytest.mark.nightly
@pytest.mark.process
def test_a_check_naming_a_missing_file_fails_under_xdist(tmp_path):
    """Under -n N pytest exits 5 with no message when one target is missing, so
    run.py checks each target before the session starts."""
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 1),
                                                 ("gone", "test_gone.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json", "-n", "2")

    gone = (tmp_path / "planted" / "test_gone.py").as_posix()
    assert done.returncode == 1, done.stdout + done.stderr
    assert _outcomes(receipt) == {("alpha", "passes"): "pass", ("alpha", "gone"): "fail"}
    assert [check.get("missing") for check in receipt["checks"]] == [[gone], None]
    assert (f"run.py: alpha: gone names {gone}, which is not there, so the check fails "
            "without running; restore the file or fix the check's row in "
            "tools/accuracy/checks/alpha.py") in done.stderr


@pytest.mark.nightly
@pytest.mark.process
def test_notes_reach_the_receipt(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("notes", "test_notes.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 0, done.stdout + done.stderr
    assert receipt["events"] == {"R28": 3}
    assert receipt["oracles"] == {"radon": "6.0.1"}
    assert receipt["skipped_files"] == {"ast": 2}


@pytest.mark.nightly
@pytest.mark.process
def test_receipt_digests_equal_hashlib_of_the_files(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 1)])})

    _, receipt = _run(checks, tmp_path / "r.json")

    assert receipt["digests"]
    for relative, digest in receipt["digests"].items():
        assert digest == hashlib.sha256((REPO / relative).read_bytes()).hexdigest(), relative


def _untimed(check: dict) -> dict:
    return {key: value for key, value in check.items() if key != "seconds"}


def _comparable(receipt: dict) -> dict:
    """What a shard split must not change: everything but timings and the shard name."""
    kept = {key: value for key, value in receipt.items() if key not in ("shard", "attempts")}
    kept["checks"] = list(map(_untimed, receipt["checks"]))
    return kept


@pytest.mark.nightly
@pytest.mark.process
def test_shards_merge_to_the_receipt_of_the_whole_tier(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 1)]),
                               "beta": ("two", [("notes", "test_notes.py", 2)])})
    _, whole = _run(checks, tmp_path / "whole.json")
    _, one = _run(checks, tmp_path / "one.json", "--shard", "one")
    _, two = _run(checks, tmp_path / "two.json", "--shard", "two")

    done = in_process(["merge", str(tmp_path / "two.json"), str(tmp_path / "one.json"),
                       "--out", str(tmp_path / "merged.json")])

    assert done.returncode == 0, done.stderr
    merged = json.loads((tmp_path / "merged.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in one["checks"]] == ["passes"]
    assert _comparable(merged) == _comparable(whole)


@pytest.mark.nightly
@pytest.mark.process
def test_the_time_table_goes_to_the_job_summary(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 7)])})
    summary = tmp_path / "summary.md"
    env = {**run_tool.os.environ, "GITHUB_STEP_SUMMARY": str(summary)}

    done, _ = _run(checks, tmp_path / "r.json", env=env)

    table = summary.read_text(encoding="utf-8")
    assert "| check | declared s | measured s | outcome |" in table
    assert "| alpha: passes | 7 |" in table
    assert "alpha: passes" in done.stdout


# --- the pieces, in process -------------------------------------------------------

def test_checks_load_with_their_module_and_shard(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 3)])})

    [check] = run_tool.load_checks(checks)

    assert (check.key, check.name, check.shard, check.seconds) == ("alpha", "passes", "one", 3)
    assert check.os == () and check.tiers == () and check.os_sensitive is False


@pytest.mark.parametrize("row, message", [
    ({"name": "x", "seconds": 1}, "names neither pytest targets nor argv"),
    ({"name": "x", "pytest": ["a"], "argv": ["b"], "seconds": 1}, "names both"),
    ({"name": "x", "pytest": ["a"]}, "declares no seconds"),
    ({"name": "x", "argv": ["b"], "seconds": 1}, "an argv check names its tiers"),
    ({"name": "x", "pytest": ["a"], "seconds": 1, "colour": "red"}, "unknown field colour"),
    ({"name": "x", "pytest": ["a"], "seconds": 1, "os_sensitive": "yes"},
     "sets os_sensitive to something other than True or False"),
])
def test_a_malformed_check_is_refused(tmp_path, row, message):
    (tmp_path / "bad.py").write_text(f"SHARD = 's'\nCHECKS = [{row!r}]\n", encoding="utf-8")

    with pytest.raises(run_tool.CheckError, match=message):
        run_tool.load_checks(tmp_path)


def test_two_checks_with_one_name_are_refused(tmp_path):
    rows = [{"name": "x", "pytest": ["a"], "seconds": 1}] * 2
    (tmp_path / "dup.py").write_text(f"SHARD = 's'\nCHECKS = {rows!r}\n", encoding="utf-8")

    with pytest.raises(run_tool.CheckError, match="dup: two checks are named x"):
        run_tool.load_checks(tmp_path)


def test_selection_follows_shard_os_and_tier():
    make = lambda **kw: run_tool.Check(**{"key": "k", "name": "n", "shard": "one",  # noqa: E731
                                          "seconds": 1, **kw})
    plain = make(pytest=("a",))
    windows = make(pytest=("a",), os=("win32",))
    nightly_argv = make(argv=("x",), tiers=("nightly",))

    assert run_tool.selected([plain, windows, nightly_argv], "push", None, "linux") == [plain]
    assert run_tool.selected([plain, windows, nightly_argv], "nightly", "one", "win32") == [
        plain, windows, nightly_argv]
    assert run_tool.selected([plain], "push", "two", "linux") == []


def test_the_os_sensitive_selection_keeps_only_the_marked_checks():
    """CI's Windows push job runs `--os-sensitive`: the checks whose answer can
    change with the OS, and none the Ubuntu job already answered for every OS."""
    plain = run_tool.Check("k", "plain", "one", 1, pytest=("a",))
    marked = run_tool.Check("k", "marked", "one", 1, pytest=("b",), os_sensitive=True)
    linux_only = run_tool.Check("k", "linux", "one", 1, pytest=("c",), os=("linux",),
                                os_sensitive=True)
    windows_only = run_tool.Check("k", "windows", "one", 1, pytest=("d",), os=("win32",))
    checks = [plain, marked, linux_only, windows_only]

    assert run_tool.selected(checks, "push", None, "win32", True) == [marked, windows_only]
    assert run_tool.selected(checks, "push", None, "win32") == [plain, marked, windows_only]
    assert run_tool._run_parser().parse_args(["--os-sensitive"]).os_sensitive is True
    assert run_tool._run_parser().parse_args([]).os_sensitive is False


def _session_argv(monkeypatch) -> list:
    """Every argv run.py hands subprocess.run, which runs nothing here."""
    started: list = []

    def run(argv, **_):
        started.append(argv)
        return run_tool.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(run_tool.subprocess, "run", run)
    return started


def _records(tmp_path, checks: list) -> dict:
    env = {run_tool.runlog.LOG_ENV: str(tmp_path / "notes.jsonl")}
    records = run_tool._pytest_records(checks, env, tmp_path, 2, "derandomized")
    return {record["name"]: (record["outcome"], record.get("missing")) for record in records}


def test_a_missing_target_fails_its_check_and_stays_out_of_the_session(tmp_path, monkeypatch):
    here, gone = tmp_path / "test_here.py", tmp_path / "test_gone.py::test_x"
    here.write_text("def test_ok():\n    pass\n", encoding="utf-8")
    started = _session_argv(monkeypatch)
    checks = [run_tool.Check("k", "here", "s", 1, pytest=(here.as_posix(),)),
              run_tool.Check("k", "gone", "s", 1, pytest=(gone.as_posix(),))]

    records = _records(tmp_path, checks)

    assert records == {"here": ("empty", None), "gone": ("fail", [gone.as_posix()])}
    [argv] = started
    assert here.as_posix() in argv and gone.as_posix() not in argv


def test_no_session_starts_when_every_target_is_missing(tmp_path, monkeypatch):
    """pytest handed no target would collect the whole repository."""
    started = _session_argv(monkeypatch)
    gone = (tmp_path / "tests_gone").as_posix()

    records = _records(tmp_path, [run_tool.Check("k", "gone", "s", 1, pytest=(gone,))])

    assert (records, started) == ({"gone": ("fail", [gone])}, [])


def test_an_argv_check_reads_its_exit_code():
    assert [run_tool.argv_outcome(code) for code in (0, 1, 3, 7)] == [
        "pass", "fail", "infra", "fail"]


def test_the_image_tag_is_12_hex_over_the_image_inputs():
    hashed = hashlib.sha256()
    for relative in run_tool.IMAGE_INPUTS:
        path = REPO / relative
        hashed.update(relative.encode("utf-8") + b"\0")
        hashed.update(path.read_bytes() if path.is_file() else b"<absent>")

    assert run_tool.image_tag(REPO) == hashed.hexdigest()[:12]
    assert "tools/accuracy/pins.toml" in run_tool.IMAGE_INPUTS
    assert "tests/accuracy/corpus_goldens/corpus.toml" in run_tool.IMAGE_INPUTS


def test_merge_refuses_receipts_of_two_tiers():
    first = {"tier": "push", "os": "linux", "python": "3.12", "checks": []}

    with pytest.raises(run_tool.CheckError, match="tier"):
        run_tool.merge([first, {**first, "tier": "nightly"}])


def test_image_tag_prints_the_tag(capsys):
    assert run_tool.main(["image-tag"]) == 0
    assert capsys.readouterr().out.strip() == run_tool.image_tag(REPO)


def test_a_refusal_exits_1_with_one_line(tmp_path, capsys):
    (tmp_path / "bad.py").write_text("SHARD = 's'\nCHECKS = [{'name': 'x'}]\n",
                                     encoding="utf-8")

    assert run_tool.main(["--checks", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "run.py: bad: check 'x' declares no seconds\n"


# --- what a receipt holds -------------------------------------------------------------------

OS_NAME = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform, sys.platform)
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
RECEIPT_KEYS = ["attempts", "checks", "digests", "events", "exports", "head", "hypothesis_seed",
                "image", "infra", "oracles", "os", "outcome", "python", "schema", "shard",
                "skipped_files", "tier"]


def _repo_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True).stdout.strip()


@pytest.mark.nightly
@pytest.mark.process
def test_the_receipt_names_the_run_it_came_from(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 3)])})
    image = {**os.environ, "CRAPKIT_ACCURACY_IMAGE": "acc:1", "CRAPKIT_ACCURACY_IMAGE_DIGEST": "d"}
    bare = {key: value for key, value in os.environ.items()
            if not key.startswith("CRAPKIT_ACCURACY_IMAGE")}

    _, tagged = _run(checks, tmp_path / "tagged.json", "--shard", "one", env=image)
    _, untagged = _run(checks, tmp_path / "untagged.json", env=bare)

    assert sorted(tagged) == RECEIPT_KEYS
    assert (tagged["schema"], tagged["tier"], tagged["shard"], tagged["os"], tagged["python"],
            tagged["head"], tagged["image"], tagged["exports"]) == (
        1, "push", "one", OS_NAME, PYTHON, _repo_head(), {"tag": "acc:1", "digest": "d"}, {})
    assert (untagged["shard"], untagged["image"]) == (None, {"tag": "", "digest": ""})
    assert tagged["checks"] == [{"key": "alpha", "name": "passes", "shard": "one", "declared": 3,
                                 "seconds": tagged["checks"][0]["seconds"], "outcome": "pass",
                                 "tests": 1}]


def test_the_default_receipt_is_named_after_the_tier_shard_os_and_python():
    home = REPO / ".crapkit" / "accuracy"

    assert run_tool.default_receipt("nightly", "one") == home / f"nightly-one-{OS_NAME}-{PYTHON}.json"
    assert run_tool.default_receipt("push", None) == home / f"push-{OS_NAME}-{PYTHON}.json"


SAVED = {"tier": "nightly", "shard": "one", "os": "linux", "python": "3.12", "outcome": "fail",
         "checks": [{"key": "a", "name": "x", "declared": 7, "seconds": 1.234, "outcome": "fail"},
                    {"key": "b", "name": "y", "declared": 0.5, "seconds": 0, "outcome": "pass"}]}
TABLE = ("### accuracy nightly one (linux, 3.12): fail\n\n"
         "| check | declared s | measured s | outcome |\n|---|---:|---:|---|\n"
         "| a: x | 7 | 1.23 | fail |\n| b: y | 0.5 | 0.00 | pass |\n")


def test_the_time_table_lists_each_check():
    assert run_tool.table(SAVED) == TABLE
    assert run_tool.table({**SAVED, "shard": None}).splitlines()[0] == (
        "### accuracy nightly (linux, 3.12): fail")


def test_publish_writes_the_receipt_prints_the_table_and_appends_to_the_summary(
        tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    summary.write_bytes(b"before\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    target = tmp_path / "two" / "deep" / "r.json"

    run_tool._publish(SAVED, target)

    text = target.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert json.loads(text) == SAVED and text.endswith("}\n")
    assert text.startswith('{\n "checks": [\n  {\n   "declared": 7,')
    assert capsys.readouterr().out == TABLE + "\n"
    assert summary.read_bytes().decode("utf-8").replace("\r\n", "\n") == "before\n" + TABLE + "\n"


def _shard(**fields) -> dict:
    common = {"tier": "nightly", "os": "linux", "python": "3.12", "head": "h",
              "hypothesis_seed": 7, "image": {"tag": ""}, "digests": {"a": "1"}}
    return {**common, **fields}


def test_merge_joins_the_shards_notes_and_counts():
    one = _shard(shard="one", first="kept", attempts=2, outcome="pass",
                 checks=[{"key": "b", "name": "y", "outcome": "pass"}], exports={"e": "1"},
                 oracles={"radon": "6"}, events={"R1": 2}, skipped_files={"ast": 1},
                 infra=["z"])
    two = _shard(shard="two", first="dropped", attempts=1, outcome="fail",
                 checks=[{"key": "a", "name": "x", "outcome": "fail"}], exports={"f": "2"},
                 oracles={"radon": "6", "lizard": "1"}, events={"R1": 3, "R2": 1},
                 skipped_files={}, infra=["a"])

    assert run_tool.merge([one, two]) == {
        **one, "shard": None, "attempts": 2, "outcome": "fail",
        "checks": [two["checks"][0], one["checks"][0]], "exports": {"e": "1", "f": "2"},
        "oracles": {"radon": "6", "lizard": "1"}, "events": {"R1": 5, "R2": 1},
        "skipped_files": {"ast": 1}, "infra": ["a", "z"]}
    bare = run_tool.merge([_shard(), _shard()])
    assert (bare["checks"], bare["attempts"], bare["outcome"], bare["infra"]) == ([], 1, "pass", [])


@pytest.mark.parametrize("field, clash, what", [
    ("exports", {"e": "9"}, "export e"), ("oracles", {"radon": "7"}, "oracle radon")])
def test_merge_refuses_two_values_for_one_note(field, clash, what):
    one = _shard(exports={"e": "1"}, oracles={"radon": "6"})

    with pytest.raises(run_tool.CheckError, match=f"^the receipts disagree on {what}$"):
        run_tool.merge([one, {**one, field: clash}])


# --- running a tier's pieces ------------------------------------------------------------------

def test_a_nightly_seed_is_a_random_32_bit_value_and_the_rest_are_derandomized():
    seeds = [run_tool._seed("nightly") for _ in range(200)]

    assert all(isinstance(seed, int) and 0 <= seed < 2 ** 32 for seed in seeds)
    assert max(seeds) >= 2 ** 31 and len(set(seeds)) > 190
    assert [run_tool._seed(tier) for tier in ("push", "weekly", "release")] == ["derandomized"] * 3


def test_the_child_env_leaves_out_the_parent_test_s_identity(monkeypatch, tmp_path):
    for name in run_tool.PARENT_ONLY:
        monkeypatch.setenv(name, "parent")
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    monkeypatch.setenv("CRAPKIT_KEPT", "1")

    env = run_tool._child_env("nightly", tmp_path / "log.jsonl")
    monkeypatch.delenv("PYTHONPATH")

    assert [name for name in run_tool.PARENT_ONLY if name in env] == [] and env["CRAPKIT_KEPT"] == "1"
    assert env["PYTHONPATH"] == os.pathsep.join([str(REPO / "tests"), "elsewhere"])
    assert (env[run_tool.tiers.TIER_ENV], env[run_tool.runlog.LOG_ENV]) == (
        "nightly", str(tmp_path / "log.jsonl"))
    assert run_tool._child_env("push", tmp_path)["PYTHONPATH"] == str(REPO / "tests")


def test_the_session_argv(tmp_path):
    junit = tmp_path / "j.xml"

    assert run_tool._pytest_argv(["a.py"], REPO, 0, junit, "derandomized") == [
        sys.executable, "-m", "pytest", "a.py", "--rootdir", str(REPO), "-q", "-p",
        "no:cacheprovider", "-p", "no:randomly", "-o", "junit_family=xunit1", "--junitxml",
        str(junit)]
    assert run_tool._pytest_argv(["a.py"], REPO, 3, junit, 12)[-3:] == [
        "-n", "3", "--hypothesis-seed=12"]


def test_only_the_push_tier_loads_the_push_lock_guard(tmp_path):
    junit = tmp_path / "j.xml"
    argv = {tier: run_tool._pytest_argv(["a.py"], REPO, 0, junit, 1, tier) for tier in run_tool.tiers.TIERS}

    assert [tier for tier, words in argv.items() if "accuracy.kit.push_only" in words] == ["push"]
    assert argv["push"][-2:] == ["-p", "accuracy.kit.push_only"]


JUNIT = ('<testsuites><testsuite>'
         '<testcase file="t/a.py" name="test_x" time="1.5"/>'
         '<testcase file="t/a.py" name="test_y" time="0.5"><failure/></testcase>'
         '<testcase file="t/b.py" name="test_z" time=""><error/></testcase>'
         '<testcase name="test_w" time="0.25"/><testcase file="t/c.py"/>'
         '</testsuite></testsuites>')


def test_the_junit_file_s_cases(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    Case = run_tool.Case

    assert run_tool._cases(junit, tmp_path) == [
        Case((tmp_path / "t" / "a.py").resolve(), "test_x", 1.5, False),
        Case((tmp_path / "t" / "a.py").resolve(), "test_y", 0.5, True),
        Case((tmp_path / "t" / "b.py").resolve(), "test_z", 0.0, True),
        Case(tmp_path.resolve(), "test_w", 0.25, False),
        Case((tmp_path / "t" / "c.py").resolve(), "", 0.0, False)]
    assert run_tool._cases(tmp_path / "none.xml", tmp_path) == []


@pytest.mark.parametrize("code, outcome, verdict", [
    (0, "pass", "pass"), (1, "pass", "pass"), (5, "empty", "empty"), (2, "pass", "fail"),
    (6, "empty", "fail"), (4, "infra", "infra"), (4, "fail", "fail")])
def test_a_session_that_broke_fails_the_checks_it_did_not_run(code, outcome, verdict):
    assert run_tool._broken(code, outcome) == verdict


def test_a_session_that_ended_with_exit_1_before_it_wrote_its_report_fails_every_check(
        tmp_path, monkeypatch):
    """A crapkit call stuck in C code ends a serial session on purpose
    (tests/e2e/cli_in_process.py) with exit 1, before pytest writes junit.xml.
    With no case to read, every check was `empty` and the tier read as a pass."""
    here = tmp_path / "test_here.py"
    here.write_text("def test_ok():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(run_tool.subprocess, "run",
                        lambda argv, **kw: run_tool.subprocess.CompletedProcess(argv, 1))

    records = _records(tmp_path, [run_tool.Check("k", "here", "s", 1, pytest=(here.as_posix(),))])

    assert records == {"here": ("fail", None)}


def test_a_record_rounds_its_seconds_to_the_millisecond():
    check = run_tool.Check("k", "n", "s", 3, pytest=("a",))

    assert run_tool._record(check, 1.23456, "pass", 2) == {
        "key": "k", "name": "n", "shard": "s", "declared": 3, "seconds": 1.235, "outcome": "pass",
        "tests": 2}


def _argv_checks(tmp_path: Path, code_for_push: int) -> Path:
    """One argv check: python from the repo, exiting `code_for_push` in the push tier
    when it runs in the repo with the attempt's notes log beside it, 9 otherwise."""
    script = ("import os, sys; log = os.environ.get('CRAPKIT_ACCURACY_LOG', ''); "
              f"sys.exit({code_for_push} if os.environ.get('CRAPKIT_ACCURACY_TIER') == 'push' "
              "and os.path.samefile(os.getcwd(), sys.argv[1]) "
              "and os.path.basename(log) == 'notes.jsonl' "
              "and os.path.basename(os.path.dirname(log)).startswith('crapkit-accuracy-run-') "
              "else 9)")
    row = {"name": "argv", "argv": ["python", "-c", script, str(REPO)], "tiers": ["push"],
           "seconds": 1}
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "beta.py").write_text(f"SHARD = 'two'\nCHECKS = [{row!r}]\n", encoding="utf-8")
    return checks


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("code, outcome, attempts", [(0, "pass", 1), (3, "infra", 2)])
def test_an_argv_check_runs_python_from_the_repo_in_its_tier(tmp_path, code, outcome, attempts):
    _, receipt = _run(_argv_checks(tmp_path, code), tmp_path / "r.json")

    [check] = receipt["checks"]
    assert (receipt["outcome"], receipt["attempts"]) == (outcome, attempts)
    assert (check["name"], check["outcome"], check["tests"]) == ("argv", outcome, None)
    assert 0 <= check["seconds"] < 60


# --- the pieces' exact words and values ----------------------------------------------------

def _refusal(tmp_path: Path, row: dict) -> str:
    (tmp_path / "bad.py").write_text(f"SHARD = 's'\nCHECKS = [{row!r}]\n", encoding="utf-8")
    with pytest.raises(run_tool.CheckError) as refused:
        run_tool.load_checks(tmp_path)
    return str(refused.value)


@pytest.mark.parametrize("row, message", [
    ({"seconds": 1}, "check '?' names neither pytest targets nor argv"),
    ({"name": "x", "pytest": ["a"], "argv": ["b"], "seconds": 1},
     "check 'x' names both pytest targets and argv"),
    ({"name": "x", "argv": ["b"], "seconds": 1},
     "check 'x' is an argv check: an argv check names its tiers"),
    ({"name": "x", "pytest": ["a"], "seconds": 1, "os_sensitive": "yes"},
     "check 'x' sets os_sensitive to something other than True or False"),
    ({"name": "x", "pytest": ["t.py", "t.py::a", "t.py::b"], "seconds": 1},
     "check 'x' names node id t.py::a; a check names files or directories"),
])
def test_each_refusal_says_every_word(tmp_path, row, message):
    assert _refusal(tmp_path, row) == f"bad: {message}"


def test_a_check_keeps_the_os_tiers_and_os_sensitivity_it_declares(tmp_path):
    row = {"name": "x", "argv": ["python"], "seconds": 1, "os": ["win32"], "tiers": ["nightly"],
           "os_sensitive": True}
    (tmp_path / "m.py").write_text(f"CHECKS = [{row!r}]\n", encoding="utf-8")
    (tmp_path / "none.py").write_text("SHARD = 's'\n", encoding="utf-8")
    (tmp_path / "_shared.py").write_text(f"CHECKS = [{{**{row!r}, 'name': 'y'}}]\n",
                                         encoding="utf-8")

    [check] = run_tool.load_checks(tmp_path)

    assert (check.name, check.shard, check.os, check.tiers, check.os_sensitive) == (
        "x", "", ("win32",), ("nightly",), True)


def test_a_checks_module_is_a_module_its_own_classes_can_look_up(tmp_path):
    """dataclass reads a postponed annotation through sys.modules[__module__]."""
    (tmp_path / "typed.py").write_text(
        "from __future__ import annotations\nfrom dataclasses import dataclass\n\n\n"
        "@dataclass\nclass Row:\n    name: str\n\n\n"
        "CHECKS = [{'name': Row('x').name, 'pytest': ['a'], 'seconds': 1}]\n", encoding="utf-8")

    assert [check.name for check in run_tool.load_checks(tmp_path)] == ["x"]


def test_two_checks_naming_one_target_are_refused_by_name(tmp_path):
    for key, name in (("one", "x"), ("two", "y")):
        row = {"name": name, "pytest": ["tests/a.py"], "seconds": 1}
        (tmp_path / f"{key}.py").write_text(f"CHECKS = [{row!r}]\n", encoding="utf-8")

    with pytest.raises(run_tool.CheckError) as refused:
        run_tool.load_checks(tmp_path)

    assert str(refused.value) == "tests/a.py is named by both one: x and two: y"


def test_a_session_runs_in_the_repo_with_the_workers_and_seed_it_was_handed(tmp_path,
                                                                            monkeypatch):
    here = tmp_path / "test_here.py"
    here.write_text("def test_ok():\n    pass\n", encoding="utf-8")
    started = []
    monkeypatch.setattr(run_tool.subprocess, "run", lambda argv, **kw: started.append(
        (argv, kw["cwd"])) or run_tool.subprocess.CompletedProcess(argv, 0))
    env = {run_tool.runlog.LOG_ENV: str(tmp_path / "notes.jsonl")}

    run_tool._pytest_records([run_tool.Check("k", "here", "s", 1, pytest=(here.as_posix(),))],
                             env, tmp_path, 2, 12)

    [(argv, cwd)] = started
    assert (argv[-3:], cwd) == (["-n", "2", "--hypothesis-seed=12"], REPO)


def test_an_argv_check_s_python_is_this_interpreter(monkeypatch):
    started = []
    monkeypatch.setattr(run_tool.subprocess, "run", lambda argv, **kw: started.append(
        argv) or run_tool.subprocess.CompletedProcess(argv, 0))

    run_tool._argv_record(run_tool.Check("k", "a", "s", 1, argv=("python", "-c", "pass")), {})

    assert started == [[sys.executable, "-c", "pass"]]


def test_a_target_s_node_id_is_left_off_before_its_path_is_looked_for(tmp_path):
    here = tmp_path / "test_here.py"
    here.write_text("", encoding="utf-8")

    assert run_tool.missing_targets(
        run_tool.Check("k", "a", "s", 1, pytest=(f"{here.as_posix()}::test_x",))) == []


def test_an_infra_miss_inside_a_class_keys_as_its_file_and_test_name(tmp_path):
    notes = [{"kind": "infra", "test": "t/a.py::TestC::test_x"}]

    assert run_tool._infra_keys(notes, tmp_path) == {((tmp_path / "t" / "a.py").resolve(),
                                                     "test_x")}


def test_a_session_root_is_the_repo_or_the_directory_a_target_names(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()

    assert run_tool.session_root(["tests/accuracy"]) == REPO
    assert run_tool.session_root([str(suite)]) == suite.resolve()


def test_the_image_tag_of_a_tree_without_its_inputs_hashes_each_as_absent(tmp_path):
    hashed = hashlib.sha256()
    for relative in run_tool.IMAGE_INPUTS:
        hashed.update(relative.encode("utf-8") + b"\0" + b"<absent>")

    assert run_tool.image_tag(tmp_path) == hashed.hexdigest()[:12]


def test_a_failed_head_read_is_no_head_whatever_git_printed(monkeypatch):
    """git rev-parse HEAD on an unborn branch prints HEAD itself and exits 128."""
    monkeypatch.setattr(run_tool.subprocess, "run", lambda argv, **kw: (
        run_tool.subprocess.CompletedProcess(argv, 128, "HEAD\n", "fatal: ambiguous argument")))

    assert run_tool._head() == ""


@pytest.mark.parametrize("platform, name", [("win32", "windows"), ("freebsd14", "freebsd14")])
def test_a_receipt_names_its_os_the_way_ci_does(monkeypatch, platform, name):
    monkeypatch.setattr(run_tool, "_head", lambda: "h")
    monkeypatch.setattr(run_tool.sys, "platform", platform)
    notes = {"exports": {}, "oracles": {}, "events": {}, "skipped_files": {}, "infra": []}

    saved = run_tool.receipt("push", None, [], notes, "derandomized", 1)
    path = run_tool.default_receipt("push", None)

    assert (saved["os"], path.name) == (name, f"push-{name}-{PYTHON}.json")


def test_a_tier_hands_both_attempts_its_workers_and_one_seed(monkeypatch):
    calls = []
    outcomes = iter(["infra", "pass"])
    monkeypatch.setattr(run_tool, "_attempt", lambda checks, tier, workers, seed: calls.append(
        (workers, seed)) or ([{"key": "k", "name": "n", "outcome": next(outcomes)}], {
            "exports": {}, "oracles": {}, "events": {}, "skipped_files": {}, "infra": []}))
    monkeypatch.setattr(run_tool, "_head", lambda: "h")

    saved = run_tool.run_tier([], "nightly", None, 3)
    outcomes = iter(["pass"])
    run_tool.run_tier([], "push", None)

    seed = calls[0][1]
    assert isinstance(seed, int) and calls == [(3, seed), (3, seed), (0, "derandomized")]
    assert (saved["hypothesis_seed"], saved["attempts"]) == (seed, 2)


def test_an_attempt_hands_the_session_its_workers_and_seed(monkeypatch):
    seen = []
    monkeypatch.setattr(run_tool, "_pytest_records", lambda checks, env, scratch, workers, seed: (
        seen.append((workers, seed)) or []))

    run_tool._attempt([run_tool.Check("k", "a", "s", 1, pytest=("a",))], "push", 2, 7)

    assert seen == [(2, 7)]


def test_receipts_agree_whatever_order_a_field_s_keys_were_written_in():
    first = _shard(image={"tag": "t", "digest": "d"}, checks=[])
    second = _shard(image={"digest": "d", "tag": "t"}, checks=[])

    assert run_tool.merge([first, second])["image"] == {"tag": "t", "digest": "d"}


def test_the_summary_is_utf8_whatever_the_locale(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    saved = {**SAVED, "checks": [{**SAVED["checks"][0], "name": "café"}]}

    run_tool._publish(saved, tmp_path / "r.json")

    assert "| a: café |".encode("utf-8") in summary.read_bytes()
