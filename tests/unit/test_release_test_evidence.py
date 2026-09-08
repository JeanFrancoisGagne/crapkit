"""A release needs passing tests, even when verify finds no new failures."""
import json
import sqlite3
import tomllib
import zlib
from contextlib import closing

import pytest

from crapkit.store import SnapshotStore
from crapkit.verify import evaluate
from test_release_guards import capture, contracts, git, ledger, passing_lanes, repo
from test_release_recovery import publish_adapter
from test_release_tool import ROOT, release


def write_test_run(root, lanes):
    failures = set(lanes["py"]["failures"])
    verdict = evaluate(fresh=[], changed_ranges={}, ratchet=[], target=6,
                       baseline_failures=failures, fresh_failures=failures)
    assert verdict.ok and verdict.new_failures == []
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    try:
        run_id = store.write_run(commit=git(root, "rev-parse", "HEAD"),
                                 tool_versions={}, rows=[], lanes=lanes, kind="verify")
        store.set_verdict_ok(run_id, verdict.ok, findings=0)
        assert store._conn.execute("SELECT typeof(lanes) FROM runs WHERE id=?",
                                   (run_id,)).fetchone() == ("blob",)
        return run_id
    finally:
        store._conn.close()


def old_release_receipt(root, run_id):
    path = root / ".crapkit/release-receipt.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt.update(verify_run=run_id, verify_after=0)
    path.write_text(json.dumps(receipt), encoding="utf-8")


@pytest.mark.parametrize("stage", ["verify", "stage2b", "registry"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_release_refuses_unchanged_test_failures(tmp_path, monkeypatch, stage, exit_code):
    root = repo(tmp_path, bumped=True)
    contracts(root, monkeypatch)
    lanes = passing_lanes()
    lanes["py"].update(exit_code=exit_code, failures=["tests.unit.test_known::test_existing_failure"])
    adapter = publish_adapter(root, monkeypatch)
    if stage == "verify":
        capture(monkeypatch, effect=lambda command, root: write_test_run(root, lanes))
    else:
        old_release_receipt(root, write_test_run(root, lanes))
    with pytest.raises(release.ReleaseError, match="passing test evidence"):
        release.run(stage, "0.5.2", root)
    assert adapter.events == []


def replace_evidence(root, stored):
    with closing(sqlite3.connect(root / ".crapkit/crap.sqlite")) as db, db:
        db.execute("UPDATE runs SET lanes=? WHERE id=(SELECT MAX(id) FROM runs)", (stored,))


@pytest.mark.parametrize("stored", [None, 0, "broken json", b"broken zlib",
                                   zlib.compress(b"\xff"), "null", "[]", "{}",
                                   '{"other": {}}', '{"py": {}}', '{"py": null}'])
def test_missing_or_unreadable_test_evidence_refuses(stored):
    with pytest.raises(release.ReleaseError, match="passing test evidence"):
        release._passing_test_evidence(stored)


@pytest.mark.parametrize("field,value", [
    ("exit_code", None), ("exit_code", False), ("exit_code", 1),
    ("failures", None), ("failures", {}),
    ("tests_total", None), ("tests_total", True), ("tests_total", "2"),
    ("tests_total", 0), ("tests_total", -1),
    ("tests_skipped", None), ("tests_skipped", False), ("tests_skipped", "0"),
    ("tests_skipped", -1), ("tests_skipped", 2), ("tests_skipped", 3),
    ("artifact_sha256", None), ("artifact_sha256", "bad"),
    ("results_artifact_sha256", None), ("results_artifact_sha256", "g" * 64),
])
def test_complete_passing_lane_fields_are_required(field, value):
    lanes = passing_lanes()
    lanes["py"][field] = value
    with pytest.raises(release.ReleaseError, match="passing test evidence"):
        release._passing_test_evidence(zlib.compress(json.dumps(lanes).encode("utf-8")))


@pytest.mark.parametrize("legacy_text", [False, True])
def test_release_accepts_recorded_passing_tests_with_skips(tmp_path, monkeypatch, legacy_text):
    root = repo(tmp_path, bumped=True)
    contracts(root, monkeypatch)
    lanes = passing_lanes()
    lanes["py"]["tests_skipped"] = 1

    def record(command, root):
        ledger(root, lanes=lanes)
        if legacy_text:
            replace_evidence(root, json.dumps(lanes))

    capture(monkeypatch, effect=record)
    release.run("verify", "0.5.2", root)
    adapter = publish_adapter(root, monkeypatch)
    release.run("stage2b", "0.5.2", root)
    assert "push" in adapter.events


def test_release_test_lanes_match_the_repository_full_runner():
    config = tomllib.loads((ROOT / "crapkit.toml").read_text(encoding="utf-8"))
    assert {lane["name"] for lane in config["lane"]} == release.RELEASE_TEST_LANES
    assert config["lane"][0]["command"] == (
        "python tools/testing/run.py --coverage --output .crapkit/cov")
    assert config["lane"][0]["results_artifact"] == ".crapkit/cov/junit.xml"
