"""Seed and prune pick their run through verify's trust rule.

Reported against 0.4.2 (#16). `_latest_full_run` took the newest run whose kind
was coverage or verify and never read the verdict, so `ratchet seed` signed
marks from a FAILED verify — the same scores `verify` refuses as a comparison
point, and a failed verify can carry them from a red tree.
"""
import argparse
from pathlib import Path

import pytest

from crapkit.cli.ratchet_cmds import _latest_full_run, cmd_ratchet
from crapkit.errors import CrapkitError
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

TRUSTED_SHA = "bb83d64fc19a7e2d4c5b60718293a4b5c6d7e8f9"
FAILED_SHA = "4f1c0aa9d3b2e5768190a2b3c4d5e6f70819a2b3"
CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n')


def scored(ccn: int = 8) -> ScoredRow:
    return ScoredRow("src", "src/a.py", "hot( n )", 1, 9, ccn, ccn, ccn, 5, 1, 1,
                     0.0, "measured", float(ccn * ccn + ccn), "decompose")


def coverage_run(store: SnapshotStore, commit: str = TRUSTED_SHA) -> int:
    return store.write_run(commit=commit, tool_versions={}, rows=[scored()],
                           kind="coverage", lanes={"unit": {}})


def verify_run(store: SnapshotStore, ok: bool, commit: str = FAILED_SHA) -> int:
    run_id = store.write_run(commit=commit, tool_versions={}, rows=[scored()],
                             kind="verify", lanes={"unit": {}})
    store.set_verdict_ok(run_id, ok, findings=0 if ok else 3)
    return run_id


def repo_with_store(tmp_path: Path) -> tuple[Path, SnapshotStore]:
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    return tmp_path, SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")


def seed(repo: Path) -> int:
    return cmd_ratchet(argparse.Namespace(action="seed", repo=str(repo)))


def test_seed_falls_back_past_a_failed_verify_to_the_newest_trusted_run(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    trusted = coverage_run(store)
    failed = verify_run(store, False)

    latest, skipped = _latest_full_run(store)

    assert latest["id"] == trusted
    assert [r["id"] for r in skipped] == [failed]


def test_a_passing_verify_is_still_the_run_to_seed_from(tmp_path):
    """Passing is what makes a verify trusted; the rule is not "never a verify"."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    coverage_run(store)
    passed = verify_run(store, True)

    latest, skipped = _latest_full_run(store)

    assert latest["id"] == passed and skipped == []


def test_a_store_whose_only_full_run_is_a_failed_verify_has_nothing_to_seed(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    verify_run(store, False)

    with pytest.raises(CrapkitError, match="failed verifies"):
        _latest_full_run(store)


def test_the_seed_line_names_the_run_it_used_and_the_verify_it_skipped(tmp_path, capsys):
    repo, store = repo_with_store(tmp_path)
    trusted = coverage_run(store)
    failed = verify_run(store, False)

    assert seed(repo) == 0

    line = capsys.readouterr().out.strip()
    assert f"vs run {trusted} ({TRUSTED_SHA[:11]})" in line
    assert line.endswith(f", skipped failed verify run {failed}")


def test_the_seed_line_says_nothing_extra_when_nothing_was_skipped(tmp_path, capsys):
    repo, store = repo_with_store(tmp_path)
    trusted = coverage_run(store)

    assert seed(repo) == 0

    assert capsys.readouterr().out.strip().endswith(f"vs run {trusted} ({TRUSTED_SHA[:11]})")


def test_prune_reads_the_same_trusted_run_as_seed(tmp_path, capsys):
    """One rule, both actions: pruning against a run verify refuses would drop
    marks on the strength of scores nothing trusts."""
    repo, store = repo_with_store(tmp_path)
    trusted = coverage_run(store)
    failed = verify_run(store, False)

    assert cmd_ratchet(argparse.Namespace(action="prune", repo=str(repo))) == 0

    line = capsys.readouterr().out.strip()
    assert f"vs run {trusted} ({TRUSTED_SHA[:11]})" in line
    assert line.endswith(f", skipped failed verify run {failed}")


def test_seeded_marks_come_from_the_trusted_run_not_the_failed_verify(tmp_path, capsys):
    """The failed verify measured a worse tree. Seeding from it would sign debt
    at a value verify would not accept as a comparison point."""
    repo, store = repo_with_store(tmp_path)
    store.write_run(commit=TRUSTED_SHA, tool_versions={}, rows=[scored(ccn=8)],
                    kind="coverage", lanes={"unit": {}})
    failed = store.write_run(commit=FAILED_SHA, tool_versions={}, rows=[scored(ccn=20)],
                             kind="verify", lanes={"unit": {}})
    store.set_verdict_ok(failed, False, findings=3)

    assert seed(repo) == 0

    marks = (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()
    assert marks[-1] == "src/a.py\thot( n )\t72.0000", "the trusted run's 8*8+8, not 20*20+20"
