"""Doctor reports effective resource policy without allocating worker slots."""
import json
import subprocess

from crapkit.cli import main


def test_doctor_reports_configured_limits_without_starting_work(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "src/example.py"
    source.parent.mkdir()
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src"], check=True)
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'
        "[crapkit]\nanalysis_workers=3\nanalysis_worker_budget=2\n"
        "log_max_bytes=4096\ntest_retention_days=4\ntest_retention_count=5\n",
        encoding="utf-8")
    budget = tmp_path / "worker-slots"
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(budget))
    monkeypatch.setenv("CRAPKIT_ANALYSIS_WORKERS", "1")
    monkeypatch.setenv("CRAPKIT_ANALYSIS_MEMORY_MB", "70")
    assert main(["doctor", "--repo", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    policy = report["resources"]
    assert report["schema"] == 1
    assert policy["pool_worker_limit"] == 1
    assert policy["shared_pool_limit"] == min(2, policy["available_cpus"])
    assert policy["memory_budget_mb"] == 70
    assert policy["memory_is_hard_limit"] is False
    assert policy["log_max_bytes"] == 4096
    assert policy["test_retention_days"] == 4
    assert policy["test_retention_count"] == 5
    assert not budget.exists()
