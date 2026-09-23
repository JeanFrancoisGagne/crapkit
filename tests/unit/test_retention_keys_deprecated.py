"""`test_retention_days` and `test_retention_count` configure nothing in crapkit
any more. A config that sets them still loads, and doctor names what replaced
them instead of listing them as live keys."""
import json
import subprocess

from crapkit.cli import main
from crapkit.config import load_config_text
from crapkit.doctor import valid_keys


SCOPE = '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'
DEPRECATED = ("[crapkit]\ntest_retention_days=4\ntest_retention_count=5\n")


def _repo(tmp_path, crapkit_table: str):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "src/example.py"
    source.parent.mkdir()
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src"], check=True)
    (tmp_path / "crapkit.toml").write_text(SCOPE + crapkit_table, encoding="utf-8")
    return tmp_path


def test_a_config_setting_the_retention_keys_still_loads():
    load_config_text(SCOPE + DEPRECATED)
    load_config_text(SCOPE + "[crapkit]\ntest_retention_days=-1\ntest_retention_count=\"ten\"\n")


def test_unknown_key_messages_no_longer_offer_the_retention_keys():
    assert "test_retention_days" not in valid_keys("crapkit")
    assert "test_retention_count" not in valid_keys("crapkit")


def test_doctor_warns_each_retention_key_is_deprecated_and_names_its_flag(tmp_path, capsys):
    repo = _repo(tmp_path, DEPRECATED)

    assert main(["doctor", "--repo", str(repo), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert not [p for p in report["problems"] if "test_retention" in p]
    assert [w for w in report["warnings"] if "test_retention" in w] == [
        "crapkit.test_retention_days is deprecated and ignored: only crapkit's own "
        "development runner writes test evidence, and it takes --retention-days; "
        "delete the key",
        "crapkit.test_retention_count is deprecated and ignored: only crapkit's own "
        "development runner writes test evidence, and it takes --retention-count; "
        "delete the key"]


def test_doctor_json_keeps_the_retention_fields_at_zero(tmp_path, capsys):
    repo = _repo(tmp_path, DEPRECATED)

    assert main(["doctor", "--repo", str(repo), "--json"]) == 0

    policy = json.loads(capsys.readouterr().out)["resources"]
    assert (policy["test_retention_days"], policy["test_retention_count"]) == (0, 0)


def test_doctor_text_states_no_test_evidence_policy(tmp_path, capsys):
    repo = _repo(tmp_path, "")

    assert main(["doctor", "--repo", str(repo)]) == 0

    resources = [line for line in capsys.readouterr().out.splitlines()
                 if line.startswith("resources:")]
    assert len(resources) == 1
    assert "test evidence" not in resources[0]
    assert resources[0].endswith("bytes per file")
