"""`crapkit clean` recovers abandoned mutation checkouts and nothing else: test
evidence belongs to the development runner that writes it."""
import json

from crapkit.cli import main
from test_runner_owns_test_evidence_retention import finished_run


CONFIG = ('[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'
          "[crapkit]\ntest_retention_count=1\ntest_retention_days=1\n")
NOTHING = {"removed": [], "planned": [], "active": [], "unproven": [], "changed": []}


def test_clean_json_keeps_an_empty_test_runs_object_and_every_run(tmp_path, capsys):
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    old = finished_run(tmp_path, "run-old", 30)
    finished_run(tmp_path, "run-new", 10)

    assert main(["clean", "--repo", str(tmp_path), "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {"schema": 1, "dry_run": False, "test_runs": NOTHING, "temporary_mutations": []}
    assert (old / "junit.xml").is_file()


def test_text_clean_prints_nothing_for_test_evidence(tmp_path, capsys):
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    old = finished_run(tmp_path, "run-old", 30)

    assert main(["clean", "--repo", str(tmp_path), "--dry-run"]) == 0

    assert capsys.readouterr().out == ""
    assert old.is_dir()
