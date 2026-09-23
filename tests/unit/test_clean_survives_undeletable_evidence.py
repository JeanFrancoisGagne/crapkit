"""An expired test-evidence directory the filesystem will not delete cannot stop
`crapkit clean` before it recovers abandoned mutation checkouts: clean never
tries to delete test evidence, which belongs to the development runner."""
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


def _refuse_to_delete(monkeypatch, locked: Path) -> list:
    """Windows refuses to unlink a read-only or open file; rmtree then raises.
    Returns the list of attempts on `locked`."""
    attempts = []
    delete = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path) == locked:
            attempts.append(str(path))
            raise PermissionError(13, "Access is denied", str(locked / "junit.xml"))
        return delete(path, *args, **kwargs)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    return attempts


def test_clean_recovers_mutations_without_trying_to_delete_expired_test_evidence(
        tmp_path, monkeypatch, capsys):
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    run = _expired_run(tmp_path)
    checkout = _abandoned_mutation(tmp_path)
    attempts = _refuse_to_delete(monkeypatch, run)

    code = main(["clean", "--repo", str(tmp_path), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert "temporary_mutations" in result, result
    assert result["temporary_mutations"] == [{
        "path": str(checkout), "status": "unproven",
        "reason": "temporary mutation lease is missing"}]
    assert attempts == []
    assert not any(result["test_runs"].values())
    assert code == 0
    assert (run / "junit.xml").is_file()
