"""An expired test-evidence directory the filesystem will not delete cannot stop
`crapkit clean` before it recovers abandoned mutation checkouts."""
import json
from pathlib import Path
import shutil
import time

from crapkit.cli import main


CONFIG = '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'


def _expired_run(root: Path) -> Path:
    parent = root / ".crapkit/test-runs"
    run = parent / "run-old"
    run.mkdir(parents=True)
    (parent / ".leases").mkdir()
    (parent / ".leases/run-old.lock").touch()
    finished = time.time() - 30 * 86400
    (run / ".crapkit-test-run.json").write_text(json.dumps({
        "kind": "crapkit-test-run", "schema": 1, "root": str(root.resolve()), "name": "run-old",
        "created_at": finished, "finished_at": finished}), encoding="utf-8")
    (run / "junit.xml").write_text("<x/>", encoding="utf-8")
    return run


def _abandoned_mutation(root: Path) -> Path:
    checkout = root / ".crapkit/mutate-tmp" / ("a" * 32)
    checkout.mkdir(parents=True)
    (checkout / "owner.json").write_text(json.dumps({
        "version": 1, "root": str(root.resolve()), "run": checkout.name, "workers": 1,
    }), encoding="utf-8")
    return checkout


def _refuse_to_delete(monkeypatch, locked: Path) -> None:
    """Windows refuses to unlink a read-only or open file; rmtree then raises."""
    delete = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path) == locked:
            raise PermissionError(13, "Access is denied", str(locked / "junit.xml"))
        return delete(path, *args, **kwargs)
    monkeypatch.setattr(shutil, "rmtree", rmtree)


def test_an_undeletable_run_still_lets_clean_recover_mutations(tmp_path, monkeypatch, capsys):
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    run = _expired_run(tmp_path)
    checkout = _abandoned_mutation(tmp_path)
    _refuse_to_delete(monkeypatch, run)

    code = main(["clean", "--repo", str(tmp_path), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert "temporary_mutations" in result, result
    assert result["temporary_mutations"] == [{
        "path": str(checkout), "status": "unproven",
        "reason": "temporary mutation lease is missing"}]
    assert result["test_runs"]["failed"] == [str(run)]
    assert code == 1
    assert (run / "junit.xml").is_file()


def test_text_clean_names_the_run_it_could_not_delete(tmp_path, monkeypatch, capsys):
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    run = _expired_run(tmp_path)
    _refuse_to_delete(monkeypatch, run)

    code = main(["clean", "--repo", str(tmp_path)])

    assert capsys.readouterr().out.splitlines() == [f"test evidence failed: {run}"]
    assert code == 1
