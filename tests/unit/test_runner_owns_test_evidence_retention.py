"""The development runner is the only producer of default test evidence, so it
alone applies retention, from its own flags."""
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import shutil
import time


SCRIPT = Path(__file__).resolve().parents[2] / "tools/testing/run.py"


@lru_cache(maxsize=1)
def runner():
    """tools/testing/run.py as a module; tools/ ships outside the wheel."""
    spec = importlib.util.spec_from_file_location("_crapkit_dev_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finished_run(root: Path, name: str, days_ago: float) -> Path:
    """A default run as the runner leaves it: receipt, lease, finish stamp."""
    parent = root / ".crapkit/test-runs"
    run = parent / name
    run.mkdir(parents=True)
    (parent / ".leases").mkdir(exist_ok=True)
    (parent / ".leases" / (name + ".lock")).touch()
    finished = time.time() - days_ago * 86400
    (run / ".crapkit-test-run.json").write_text(json.dumps({
        "kind": "crapkit-test-run", "schema": 1, "root": str(root.resolve()), "name": name,
        "created_at": finished, "finished_at": finished}), encoding="utf-8")
    (run / "junit.xml").write_text("<x/>", encoding="utf-8")
    return run


def preview(root: Path, *flags: str, capsys) -> dict:
    assert runner().main(["--repo", str(root), "--preview-retention", *flags]) == 0
    return json.loads(capsys.readouterr().out)


def test_preview_plans_the_runs_past_the_count_flag_and_removes_nothing(tmp_path, capsys):
    older = finished_run(tmp_path, "run-older", 2)
    newer = finished_run(tmp_path, "run-newer", 1)

    planned = preview(tmp_path, "--retention-count", "1", "--retention-days", "0", capsys=capsys)

    assert planned["planned"] == [str(older)]
    assert planned["removed"] == []
    assert older.is_dir() and newer.is_dir()


def test_preview_plans_the_runs_past_the_age_flag(tmp_path, capsys):
    old = finished_run(tmp_path, "run-old", 4)
    finished_run(tmp_path, "run-fresh", 1)

    planned = preview(tmp_path, "--retention-days", "3", "--retention-count", "0", capsys=capsys)

    assert planned["planned"] == [str(old)]


def test_the_default_limits_are_seven_days_and_ten_runs(tmp_path, capsys):
    runs = [finished_run(tmp_path, f"run-{index:02}", index + 0.5) for index in range(11)]

    planned = preview(tmp_path, capsys=capsys)

    # run-07 (7.5 days) is past the age limit; run-10 is also the eleventh run.
    assert sorted(planned["planned"]) == [str(run) for run in runs[7:]]


def test_retention_keys_in_crapkit_toml_no_longer_reach_the_runner(tmp_path, capsys):
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
        "[crapkit]\ntest_retention_count=1\ntest_retention_days=1\n", encoding="utf-8")
    finished_run(tmp_path, "run-older", 3)
    finished_run(tmp_path, "run-newer", 2)

    assert preview(tmp_path, capsys=capsys)["planned"] == []


def test_an_undeletable_run_is_reported_failed_and_the_next_one_still_goes(tmp_path, monkeypatch):
    stuck = finished_run(tmp_path, "run-stuck", 3)
    gone = finished_run(tmp_path, "run-gone", 4)
    delete = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path) == stuck:
            raise PermissionError(13, "Access is denied", str(stuck / "junit.xml"))
        return delete(path, *args, **kwargs)
    monkeypatch.setattr(shutil, "rmtree", rmtree)

    result = runner().prune_test_runs(tmp_path, keep=0, days=1)

    assert result["failed"] == [str(stuck)]
    assert result["removed"] == [str(gone)]
    assert (stuck / "junit.xml").is_file()
    assert not gone.exists()
