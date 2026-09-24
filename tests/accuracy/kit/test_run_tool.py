"""tools/accuracy/run.py: tiers and shards of planted checks, their exit codes and receipts.

Each test plants a checks directory in tmp_path: modules declaring CHECKS over
planted test files, the way tools/accuracy/checks/<key>.py declares a packet's.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
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


def _run(checks: Path, receipt: Path, *args: str, env: dict | None = None):
    argv = [sys.executable, str(RUN), "--checks", str(checks), "--receipt", str(receipt), *args]
    done = hang_guard.run(argv, cwd=REPO, env=env, text=True, encoding="utf-8",
                          errors="replace")
    saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else None
    return done, saved


def _outcomes(receipt: dict) -> dict:
    return {(check["key"], check["name"]): check["outcome"] for check in receipt["checks"]}


@pytest.mark.process
def test_a_passing_tier_exits_0_and_writes_its_receipt(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 3)])})

    done, receipt = _run(checks, tmp_path / "r.json", "--tier", "push")

    assert done.returncode == 0, done.stdout + done.stderr
    assert receipt["tier"] == "push" and receipt["outcome"] == "pass"
    [check] = receipt["checks"]
    assert (check["key"], check["name"], check["declared"], check["tests"]) == (
        "alpha", "passes", 3, 1)
    assert check["seconds"] >= 0
    assert receipt["hypothesis_seed"] == "derandomized"


@pytest.mark.process
def test_a_planted_failing_check_exits_1_and_names_itself(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 1),
                                                 ("fails", "test_fail.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 1
    assert _outcomes(receipt) == {("alpha", "passes"): "pass", ("alpha", "fails"): "fail"}
    assert "alpha: fails" in done.stdout


@pytest.mark.process
def test_a_missing_tool_exits_3_after_one_retry(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("needs x", "test_infra.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 3, done.stdout + done.stderr
    assert receipt["outcome"] == "infra" and receipt["attempts"] == 2
    assert receipt["infra"] == ["oracle x 1.0 is not installed"]
    assert _outcomes(receipt) == {("alpha", "needs x"): "infra"}


@pytest.mark.process
def test_a_real_failure_outranks_an_infra_miss(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("needs x", "test_infra.py", 1),
                                                 ("fails", "test_fail.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 1 and receipt["attempts"] == 1
    assert receipt["outcome"] == "fail"


@pytest.mark.process
def test_notes_reach_the_receipt(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("notes", "test_notes.py", 1)])})

    done, receipt = _run(checks, tmp_path / "r.json")

    assert done.returncode == 0, done.stdout + done.stderr
    assert receipt["events"] == {"R28": 3}
    assert receipt["oracles"] == {"radon": "6.0.1"}
    assert receipt["skipped_files"] == {"ast": 2}


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


@pytest.mark.process
def test_shards_merge_to_the_receipt_of_the_whole_tier(tmp_path):
    checks = _plant(tmp_path, {"alpha": ("one", [("passes", "test_pass.py", 1)]),
                               "beta": ("two", [("notes", "test_notes.py", 2)])})
    _, whole = _run(checks, tmp_path / "whole.json")
    _, one = _run(checks, tmp_path / "one.json", "--shard", "one")
    _, two = _run(checks, tmp_path / "two.json", "--shard", "two")

    done = hang_guard.run([sys.executable, str(RUN), "merge", str(tmp_path / "two.json"),
                           str(tmp_path / "one.json"), "--out", str(tmp_path / "merged.json")],
                          cwd=REPO, text=True)

    assert done.returncode == 0, done.stderr
    merged = json.loads((tmp_path / "merged.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in one["checks"]] == ["passes"]
    assert _comparable(merged) == _comparable(whole)


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
    assert check.os == () and check.tiers == ()


@pytest.mark.parametrize("row, message", [
    ({"name": "x", "seconds": 1}, "names neither pytest targets nor argv"),
    ({"name": "x", "pytest": ["a"], "argv": ["b"], "seconds": 1}, "names both"),
    ({"name": "x", "pytest": ["a"]}, "declares no seconds"),
    ({"name": "x", "argv": ["b"], "seconds": 1}, "an argv check names its tiers"),
    ({"name": "x", "pytest": ["a"], "seconds": 1, "colour": "red"}, "unknown field colour"),
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
