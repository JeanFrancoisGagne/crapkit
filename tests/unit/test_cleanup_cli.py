"""Cleanup previews and applies only the owned-artifact policy: abandoned
temporary mutation checkouts."""
import json

from crapkit.cli import main


def test_text_cleanup_explains_preserved_mutations(tmp_path, capsys):
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n',
        encoding="utf-8")
    unproven = tmp_path / ".crapkit/mutate-tmp" / ("a" * 32)
    unproven.mkdir(parents=True)
    (unproven / "owner.json").write_text(json.dumps({
        "version": 1, "root": str(tmp_path.resolve()), "run": unproven.name, "workers": 1,
    }), encoding="utf-8")
    (unproven / "notes.txt").write_text("keep without a lease", encoding="utf-8")
    refusal = f"temporary mutation unproven: {unproven} (temporary mutation lease is missing)"

    assert main(["clean", "--repo", str(tmp_path), "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines() == [refusal]
    assert main(["clean", "--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == [refusal]
    assert (unproven / "notes.txt").read_text(encoding="utf-8") == "keep without a lease"
