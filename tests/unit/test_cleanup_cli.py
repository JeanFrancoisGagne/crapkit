"""Cleanup previews and applies only the owned-artifact policy: abandoned
temporary mutation checkouts."""
import json

from crapkit import mutate_pool
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


def _abandoned_run(root, workers):
    """A temporary run as crapkit writes one, with its lease left behind."""
    (root / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n',
        encoding="utf-8")
    run = root / ".crapkit/mutate-tmp" / ("b" * 32)
    run.mkdir(parents=True)
    mutate_pool._write_temporary_receipt(root, run, workers)
    lease = mutate_pool._temporary_lease(root, run)
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.touch()
    return run


def test_an_abandoned_run_of_more_than_100_workers_is_recognized(tmp_path, capsys):
    """`mutation_workers` has no upper bound, and a concurrent run records
    min(mutation_workers, mutants) workers. Recovery refused any count over 100,
    so an abandoned run of 101 workers read unproven and its worktrees stayed."""
    run = _abandoned_run(tmp_path, 101)

    assert main(["clean", "--repo", str(tmp_path), "--dry-run", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["temporary_mutations"]
    assert [(row["path"], row["status"], row["reason"]) for row in rows] == [
        (str(run), "planned", "")]


def test_a_recognized_run_prints_its_line_without_an_empty_reason(tmp_path, capsys):
    """Only a refusal carries a reason. A planned recovery printed `()` after
    its path, a pair of brackets with nothing to say."""
    run = _abandoned_run(tmp_path, 2)

    assert main(["clean", "--repo", str(tmp_path), "--dry-run"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"temporary mutation planned: {run}"]
