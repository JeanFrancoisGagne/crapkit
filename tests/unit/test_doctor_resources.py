"""Doctor reports effective resource policy without allocating worker slots."""
import json
import subprocess

import pytest

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
    assert policy["test_retention_days"] == 0  # deprecated keys: crapkit applies neither
    assert policy["test_retention_count"] == 0
    assert not budget.exists()


# --- the environment a CI job or a shell profile hands over ---------------------

@pytest.mark.parametrize("value, read", [
    ("", None), ("four", None), ("\u00b2", None), ("-1", None), ("0", None),
    ("\u0663", 3), ("9" * 40, int("9" * 40)),
], ids=["empty", "a-word", "a-superscript-digit", "negative", "zero", "an-arabic-indic-digit",
        "40-digits"])
@pytest.mark.parametrize("name, field", [
    ("CRAPKIT_ANALYSIS_WORKERS", "inherited_analysis_workers"),
    ("CRAPKIT_ANALYSIS_MEMORY_MB", "memory_budget_mb"),
])
def test_a_worker_or_memory_limit_that_is_not_a_positive_count_reads_as_unset(
        monkeypatch, name, field, value, read):
    """Python's int() reads any Unicode decimal digit, so an Arabic-Indic three
    is a three; everything else that is not a positive count is no limit."""
    from crapkit.resources import resource_status

    monkeypatch.setenv(name, value)

    status = resource_status()

    assert status[field] == read
    assert status["pool_worker_limit"] >= 1


@pytest.mark.parametrize("value", ["", "Z:/no/such/dir", "r\u00e9s"],
                         ids=["empty", "missing", "non-ascii"])
def test_a_resource_dir_of_any_spelling_names_a_directory_and_creates_nothing(tmp_path, monkeypatch,
                                                                              value):
    from crapkit.resources import _budget_directory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", value)

    directory = _budget_directory()

    assert directory.is_absolute()
    assert (value == "") == (directory.parts[-4:-1] == (".cache", "crapkit", "workers"))
    assert not (tmp_path / value).exists() or value == ""


def test_an_inside_container_value_other_than_1_is_not_a_container(monkeypatch):
    from pathlib import Path

    from crapkit.lanes import _in_container

    if Path("/.dockerenv").exists():
        pytest.skip("needs a host without /.dockerenv, which every CI runner is")
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "yes")

    assert _in_container() is False


def test_an_empty_claude_config_dir_reads_as_the_home_default(monkeypatch):
    from pathlib import Path

    from crapkit.cli.admin import _plugins_dir

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")

    assert _plugins_dir() == Path.home() / ".claude" / "plugins"
