"""Only idle, recognized test evidence participates in configured retention."""
from pathlib import Path
import json
import time

import pytest

from crapkit.retention import prune_test_runs, test_run_directory as evidence_directory


def test_count_retention_removes_only_old_finished_owned_runs(tmp_path):
    unrelated = tmp_path / ".crapkit/test-runs/user-evidence"
    unrelated.mkdir(parents=True)
    (unrelated / "notes.txt").write_text("keep this evidence", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (first, owner):
        (first / "result.txt").write_text("older result", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (second, owner):
        (second / "result.txt").write_text("latest result", encoding="utf-8")
    result = prune_test_runs(tmp_path, keep=1, days=0)
    assert result["removed"] == [str(first)]
    assert not first.exists()
    assert (second / "result.txt").read_text(encoding="utf-8") == "latest result"
    assert (unrelated / "notes.txt").read_text(encoding="utf-8") == "keep this evidence"
    assert prune_test_runs(tmp_path, keep=1, days=0)["removed"] == []


def test_active_run_is_preserved_even_when_over_retention_count(tmp_path):
    with evidence_directory(tmp_path, keep=0, days=0) as (active, owner):
        with evidence_directory(tmp_path, keep=0, days=0) as (newer, second_owner):
            (newer / "result.txt").write_text("new result", encoding="utf-8")
        result = prune_test_runs(tmp_path, keep=1, days=0)
        assert result["active"] == [str(active)]
        assert active.exists()
    assert prune_test_runs(tmp_path, keep=1, days=0)["removed"] == [str(newer)]


def test_dry_run_and_age_limit_preserve_fresh_and_explicit_evidence(tmp_path):
    with evidence_directory(tmp_path, keep=0, days=0) as (old, owner):
        (old / "result.txt").write_text("old result", encoding="utf-8")
    receipt = old / ".crapkit-test-run.json"
    record = json.loads(receipt.read_text(encoding="utf-8"))
    record["finished_at"] = time.time() - 8 * 86400
    receipt.write_text(json.dumps(record), encoding="utf-8")
    with evidence_directory(tmp_path, selected=Path(".crapkit/test-runs/run-manual")) as (manual, owner):
        (manual / "result.txt").write_text("manual result", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (fresh, owner):
        (fresh / "result.txt").write_text("fresh result", encoding="utf-8")
    preview = prune_test_runs(tmp_path, keep=0, days=7, dry_run=True)
    assert preview["planned"] == [str(old)]
    assert old.exists()
    assert prune_test_runs(tmp_path, keep=0, days=7)["removed"] == [str(old)]
    assert (manual / "result.txt").read_text(encoding="utf-8") == "manual result"
    assert (fresh / "result.txt").read_text(encoding="utf-8") == "fresh result"


def test_invalid_receipt_and_redirected_run_are_not_deleted(tmp_path):
    parent = tmp_path / ".crapkit/test-runs"
    parent.mkdir(parents=True)
    invalid = parent / "run-invalid"
    invalid.mkdir()
    (invalid / ".crapkit-test-run.json").write_text('{"kind": "user-data"}', encoding="utf-8")
    assert prune_test_runs(tmp_path, keep=1, days=1)["removed"] == []
    assert invalid.exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("keep", encoding="utf-8")
    try:
        (parent / "run-link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks requires host permission")
    assert prune_test_runs(tmp_path, keep=1, days=1)["removed"] == []
    assert (outside / "important.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("change", [
    {"schema": True}, {"schema": "1"}, {"root": "another-repository"},
    {"name": "run-something-else"}, {"created_at": -1}, {"created_at": float("nan")},
    {"created_at": True}, {"created_at": "1"}, {"finished_at": 0}, {"finished_at": None},
    {"created_at": 10 ** 1000},
])
def test_cleanup_requires_exact_receipt_identity_and_valid_timestamp(tmp_path, change):
    parent = tmp_path / ".crapkit/test-runs"
    run = parent / "run-old"
    run.mkdir(parents=True)
    leases = parent / ".leases"
    leases.mkdir()
    (leases / "run-old.lock").touch()
    value = {"kind": "crapkit-test-run", "schema": 1, "root": str(tmp_path.resolve()),
             "name": "run-old", "created_at": time.time() - 8 * 86400}
    value.update(change)
    (run / ".crapkit-test-run.json").write_text(json.dumps(value), encoding="utf-8")
    assert prune_test_runs(tmp_path, keep=0, days=7)["removed"] == []
    assert run.is_dir()


@pytest.mark.parametrize("suffix", ["", "manual-subdirectory"])
def test_explicit_reuse_removes_retention_eligibility_under_the_same_lease(tmp_path, suffix):
    with evidence_directory(tmp_path, keep=0, days=0) as (old, owner):
        (old / "result.txt").write_text("keep old evidence", encoding="utf-8")
    with evidence_directory(tmp_path, keep=0, days=0) as (fresh, owner):
        (fresh / "result.txt").write_text("new default evidence", encoding="utf-8")
    with evidence_directory(tmp_path, selected=(old / suffix).relative_to(tmp_path)) as (manual, owner):
        (manual / "explicit.txt").write_text("caller-owned", encoding="utf-8")
        assert prune_test_runs(tmp_path, keep=1, days=0)["removed"] == []
        assert (old / "result.txt").read_text(encoding="utf-8") == "keep old evidence"
    assert not (old / ".crapkit-test-run.json").exists()
    assert prune_test_runs(tmp_path, keep=1, days=0)["removed"] == []
    assert (manual / "explicit.txt").read_text(encoding="utf-8") == "caller-owned"


def test_the_forms_resolve_returns_mid_race_are_not_redirects(tmp_path, monkeypatch):
    """Two direct runners sharing a repository create and delete test-runs at
    once. While one does, Windows resolve() names the same directory in its
    extended-length form, then as the NTFS tombstone of a directory whose last
    handle is still open. Both were refused as redirects; neither is one."""
    from crapkit import retention
    expected = tmp_path / ".crapkit" / "test-runs"
    expected.mkdir(parents=True)
    forms = iter([Path("\\\\?\\" + str(expected)),
                  Path("\\\\?\\C:\\$Extend\\$Deleted\\00990000003EC3A2")])
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: next(forms))

    assert retention._parent(tmp_path) == expected
    assert retention._parent(tmp_path) == expected


def test_a_test_runs_directory_resolving_elsewhere_is_still_refused(tmp_path, monkeypatch):
    from crapkit import retention
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: tmp_path / "elsewhere" / "test-runs")

    with pytest.raises(Exception, match="redirected"):
        retention._parent(tmp_path)
