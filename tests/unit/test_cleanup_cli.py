"""Cleanup previews and applies only the configured owned-artifact policy."""
import json

from crapkit.cli import main
from crapkit.retention import test_run_directory as evidence_directory


def test_clean_preview_and_apply_report_exact_owned_paths(tmp_path, capsys):
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'
        "[crapkit]\ntest_retention_count=1\ntest_retention_days=0\n", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (old, owner):
        (old / "result.txt").write_text("old", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (fresh, owner):
        (fresh / "result.txt").write_text("fresh", encoding="utf-8")
    assert main(["clean", "--repo", str(tmp_path), "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["schema"] == 1
    assert preview["test_runs"]["planned"] == [str(old)]
    assert preview["temporary_mutations"] == []
    assert old.exists()
    assert main(["clean", "--repo", str(tmp_path), "--json"]) == 0
    actual = json.loads(capsys.readouterr().out)
    assert actual["test_runs"]["removed"] == [str(old)]
    assert (fresh / "result.txt").read_text(encoding="utf-8") == "fresh"


def test_text_cleanup_names_removed_evidence_and_explains_preserved_mutations(tmp_path, capsys):
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'
        "[crapkit]\ntest_retention_count=1\ntest_retention_days=0\n", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (old, owner):
        (old / "result.txt").write_text("old", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (fresh, owner):
        (fresh / "result.txt").write_text("fresh", encoding="utf-8")
    unproven = tmp_path / ".crapkit/mutate-tmp" / ("a" * 32)
    unproven.mkdir(parents=True)
    (unproven / "owner.json").write_text(json.dumps({
        "version": 1, "root": str(tmp_path.resolve()), "run": unproven.name, "workers": 1,
    }), encoding="utf-8")
    (unproven / "notes.txt").write_text("keep without a lease", encoding="utf-8")
    refusal = f"temporary mutation unproven: {unproven} (temporary mutation lease is missing)"

    assert main(["clean", "--repo", str(tmp_path), "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"test evidence planned: {old}", refusal]
    assert (old / "result.txt").read_text(encoding="utf-8") == "old"
    assert main(["clean", "--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"test evidence removed: {old}", refusal]
    assert not old.exists()
    assert (fresh / "result.txt").read_text(encoding="utf-8") == "fresh"
    assert (unproven / "notes.txt").read_text(encoding="utf-8") == "keep without a lease"
