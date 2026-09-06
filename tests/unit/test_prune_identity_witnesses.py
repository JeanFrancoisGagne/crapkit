"""Pruning cannot remove the facts that keep legacy debt attached to its function."""
from contextlib import closing
import json
import subprocess
import sys

import pytest

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.ratchet import metric_version
from crapkit.score import score_rows
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


def _rows(path="app.ts", starts=(1, 2, 10), occurrence=1):
    rows = [InventoryRow("app", path, "(anonymous)", line, line, 9, 9, 9, 1, 1, 0,
                         occurrence=occurrence) for line in starts]
    return score_rows(rows, {}, lane_scopes={"app"}, target=6)


def _repo(root):
    (root / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="app"\npaths=["."]\n'
        'languages=["typescript"]\ncoverage_optional=true\n', encoding="utf-8")
    (root / ".crapkit").mkdir()
    return root / ".crapkit/crap.sqlite"


def _run(root, *args):
    return subprocess.run([sys.executable, "-B", "-m", "crapkit", *args, "--repo", str(root)],
                          capture_output=True, text=True, encoding="utf-8")


def _write(store, rows):
    return store.write_run(commit="fixture",
                           tool_versions={"analysis_version": str(ANALYSIS_VERSION)}, rows=rows)


def test_seed_refusal_survives_prune_without_rewriting_the_legacy_mark(tmp_path):
    store = SnapshotStore(_repo(tmp_path))
    marks = tmp_path / "crapkit-ratchet.tsv"
    marks.write_text(f"# {metric_version()}\n"
                     "path\tlong_name\tcrap\napp.ts\t(anonymous)#2\t99.0000\n", encoding="utf-8")
    before = marks.read_bytes()
    with closing(store._conn):
        _write(store, _rows(starts=(1, 1, 10), occurrence=0))
        _write(store, _rows())
        _write(store, _rows())
    initial = _run(tmp_path, "ratchet", "seed")
    assert initial.returncode == 3 and "legacy ratchet key identity" in initial.stderr
    pruned = _run(tmp_path, "runs", "prune", "--keep", "1", "--json")
    assert pruned.returncode == 0, pruned.stderr
    repeated = _run(tmp_path, "ratchet", "seed")
    assert repeated.returncode == 3, repeated.stdout + repeated.stderr
    assert "legacy ratchet key identity" in repeated.stderr
    assert marks.read_bytes() == before


def test_prune_keeps_one_recent_witness_per_group_and_reclaims_redundant_history(tmp_path):
    store = SnapshotStore(_repo(tmp_path))
    with closing(store._conn):
        for _ in range(8):
            _write(store, _rows(starts=(1, 1), occurrence=0))
        b = _write(store, _rows("b.ts", starts=(1, 1), occurrence=0))
        for _ in range(2):
            _write(store, _rows())
    for _ in range(2):
        result = _run(tmp_path, "runs", "prune", "--keep", "1", "--json")
        assert result.returncode == 0, result.stderr
        check = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
        with closing(check._conn):
            assert [run["id"] for run in check.list_runs()] == [8, b, 10, 11]
            assert check.historical_collision_groups() == {
                ("app.ts", "(anonymous)"), ("b.ts", "(anonymous)")}
    assert json.loads(result.stdout)["pruned_runs"] == 0


def test_one_witness_run_can_preserve_multiple_collision_groups(tmp_path):
    store = SnapshotStore(_repo(tmp_path))
    with closing(store._conn):
        old = _write(store, _rows(starts=(1, 1), occurrence=0))
        shared = _write(store, _rows(starts=(1, 1), occurrence=0)
                        + _rows("b.ts", starts=(1, 1), occurrence=0))
        recent = [_write(store, _rows()) for _ in range(2)]
    result = _run(tmp_path, "runs", "prune", "--keep", "1", "--json")
    assert result.returncode == 0, result.stderr
    check = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(check._conn):
        kept = {run["id"] for run in check.list_runs()}
        assert kept == {shared, *recent}
        assert old not in kept


@pytest.mark.parametrize("occurrences, scopes, collision", [
    ((0, 0), ("app", "app"), True),
    ((1, 2), ("app", "app"), True),
    ((0, 1), ("app", "app"), True),
    ((0, 0), ("app", "overlay"), False),
    ((1, 1), ("app", "overlay"), False),
])
def test_witness_retention_uses_the_same_identity_rule_as_migration(
        tmp_path, occurrences, scopes, collision):
    rows = [row._replace(scope=scope, occurrence=occurrence)
            for row, scope, occurrence in zip(_rows(starts=(1, 1)), scopes, occurrences)]
    store = SnapshotStore(_repo(tmp_path))
    with closing(store._conn):
        witness = _write(store, rows)
        expected = store.historical_collision_groups()
        for _ in range(2):
            _write(store, _rows())
    result = _run(tmp_path, "runs", "prune", "--keep", "1", "--json")
    assert result.returncode == 0, result.stderr
    check = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(check._conn):
        assert check.historical_collision_groups() == expected
        assert (witness in {run["id"] for run in check.list_runs()}) == collision


_WRITER = """
from contextlib import closing
import sys
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore
store = SnapshotStore(sys.argv[1])
with closing(store._conn):
    row = InventoryRow('app', 'concurrent.py', 'f()', 1, 2, 2, 2, 2, 2, 0, 0)
    run = store.write_run(commit='concurrent', tool_versions={}, rows=[row], kind=sys.argv[2])
    if sys.argv[2] == 'verify':
        store.set_verdict_ok(run, False, findings=1)
"""

_PRUNER = """
import subprocess
import sys
from crapkit.cli import main
from crapkit.store import SnapshotStore
original = SnapshotStore.size_bytes
written = False
def interleave(store):
    global written
    if not written:
        written = True
        subprocess.run([sys.executable, '-B', '-c', sys.argv[3], str(store._path), sys.argv[2]],
                       check=True)
    return original(store)
SnapshotStore.size_bytes = interleave
sys.exit(main(['runs', 'prune', '--keep', '1', '--json', '--repo', sys.argv[1]]))
"""


@pytest.mark.parametrize("kind", ["coverage", "verify"])
def test_prune_never_deletes_a_run_committed_after_its_retention_snapshot(tmp_path, kind):
    store = SnapshotStore(_repo(tmp_path))
    with closing(store._conn):
        for _ in range(3):
            _write(store, _rows())
    result = subprocess.run([sys.executable, "-B", "-c", _PRUNER, str(tmp_path), kind, _WRITER],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    check = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(check._conn):
        runs = check.list_runs()
        assert [run["id"] for run in runs] == [2, 3, 4]
        assert runs[-1]["commit"] == "concurrent"
        assert runs[-1]["kind"] == kind
        assert check.read_rows(4)[0].path == "concurrent.py"
    assert json.loads(result.stdout)["pruned_runs"] == 1
