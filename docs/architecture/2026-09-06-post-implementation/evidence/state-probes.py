"""Disposable current-state probes; never writes the checkout."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace

import crapkit
from crapkit.cli.queue import _worklist_ratchet
from crapkit.cli.reports import _runs_prune
from crapkit.override import record_override
from crapkit.packet import mark_age_days
from crapkit.ratchet_report import DAY, mark_events, report_from_events
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation

SOURCE = Path(os.environ["PYTHONPATH"]).resolve()
assert Path(crapkit.__file__).resolve().is_relative_to(SOURCE)


def alert(root: Path):
    (root / "alert-ready").write_text(sys.stdin.read(), encoding="utf-8")
    deadline = time.monotonic() + 15
    while not (root / "allow-alert").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("probe controller did not release alert")
        time.sleep(0.01)


def override_worker(root: Path):
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit="probe-hook", tool_versions={}, rows=[],
                             lanes={"_hook_override": {"staged": True}}, kind="hook")
    command = subprocess.list2cmdline([sys.executable, "-B", str(Path(__file__).resolve()),
                                      "alert", str(root)])
    record_override(store=store, run_id=run_id, root=root,
                    ratchet_file="crapkit-ratchet.tsv", alert_command=command,
                    violations=[GateViolation("a.py", "f( )", 1, 9, 0, 90, "decompose")],
                    reason="disposable concurrency probe", raise_marks=False, key_version=1)
    print(json.dumps({"run_id": run_id, "audit_rows_by_id": len(store.read_overrides(run_id))}))
    store._conn.close()


def prune_during_override(root: Path):
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    for commit in ("coverage-a", "coverage-b"):
        store.write_run(commit=commit, tool_versions={}, rows=[], kind="coverage")
    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                             "override-worker", str(root)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        deadline = time.monotonic() + 10
        while not (root / "alert-ready").exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("override worker did not reach alert")
            time.sleep(0.01)
        before = store.list_runs()
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            _runs_prune(store, keep=1, as_json=True)
        (root / "allow-alert").touch()
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, stderr
        cli = subprocess.run([sys.executable, "-B", "-m", "crapkit", "overrides", "--repo", str(root),
                              "--json"], capture_output=True, text=True,
                             encoding="utf-8", check=True)
        return {"before_runs": [(r["id"], r["kind"]) for r in before],
                "prune": json.loads(printed.getvalue()), "override_worker": json.loads(stdout),
                "ratchet_granted": "a.py\tf( )\t90.0000" in
                (root / "crapkit-ratchet.tsv").read_text(encoding="utf-8"),
                "public_overrides": json.loads(cli.stdout)}
    finally:
        (root / "allow-alert").touch()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        store._conn.close()


def packet_age():
    key = ("a.py", "f( )")
    patches = [(1000, "+a.py\tf( )\t50"),
               (1000 + 90 * DAY, "-a.py\tf( )\t50\n+a.py\tf( )\t20")]
    events = mark_events(patches)
    return {"events": events, "packet_age_days": mark_age_days(events, key),
            "ratchet_report": report_from_events(events, {key: 20})}


def full_worklist_read(root: Path):
    store = SnapshotStore(root / "large.sqlite")
    rows = [InventoryRow("src", f"src/p{i // 100}.py", f"f{i}( )", i + 1, i + 1,
                         1, 1, 1, 1, 0, 0, 0, 1) for i in range(100000)]
    run_id = store.write_run(commit="large", tool_versions={}, rows=rows)
    del rows
    queries = []
    store._conn.set_trace_callback(queries.append)
    tracemalloc.start()
    started = time.perf_counter()
    result = _worklist_ratchet(root, SimpleNamespace(ratchet_file="absent.tsv"), store, run_id)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    store._conn.close()
    return {"rows": 100000, "ratchet_exists": False, "result_marks": len(result.marks),
            "seconds_with_tracemalloc": elapsed, "peak_bytes": peak, "queries": queries}


def wait_file(path: Path):
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() > deadline:
            raise RuntimeError(f"probe controller did not create {path.name}")
        time.sleep(0.01)


def marks_worker(root: Path, fresh: int):
    from crapkit.cli.verifying import _write_marks_if_changed
    from crapkit.ratchet import load_ratchet, update_ratchet
    from crapkit.score import ScoredRow

    path = root / "marks.tsv"
    prior = load_ratchet(path.read_text(encoding="utf-8"))
    coverage = 1 - ((fresh - 4) / 16) ** (1 / 3)
    row = ScoredRow("src", "a.py", "f( )", 1, 2, 4, 4, 4, 2, 0, 0,
                    coverage, "measured", fresh, "add-tests", 0, 1)
    updated = update_ratchet(prior, [row], target=6)
    (root / f"marks-ready-{fresh}").touch()
    wait_file(root / f"marks-go-{fresh}")
    delta = _write_marks_if_changed(path, prior, updated, key_version=1)
    print(json.dumps({"read_mark": prior[0].crap, "fresh": fresh,
                      "receipt": delta._asdict()}))


def stale_ratchet_write(root: Path):
    from crapkit.ratchet import RatchetEntry, dump_ratchet, load_ratchet

    path = root / "marks.tsv"
    path.write_text(dump_ratchet([RatchetEntry("a.py", "f( )", 50)], key_version=1),
                    encoding="utf-8")
    procs = {fresh: subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                      f"marks-worker{fresh}", str(root)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8") for fresh in (10, 20)}
    states = []
    try:
        for fresh in (10, 20):
            wait_file(root / f"marks-ready-{fresh}")
        for fresh in (10, 20):
            (root / f"marks-go-{fresh}").touch()
            stdout, stderr = procs[fresh].communicate(timeout=10)
            assert procs[fresh].returncode == 0, stderr
            states.append({**json.loads(stdout),
                           "disk_mark": load_ratchet(path.read_text(encoding="utf-8"))[0].crap})
        return states
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def mixed_trend_read(root: Path):
    from crapkit.cli.reports import _trend_payload
    from crapkit.score import ScoredRow

    path = root / "trend.sqlite"
    store, writer = SnapshotStore(path), SnapshotStore(path)
    row = ScoredRow("src", "a.py", "f( )", 1, 2, 4, 4, 4, 2, 0, 0,
                    0.5, "measured", 6, "ok", 0, 1)
    store.write_run(commit="old", tool_versions={}, rows=[row])
    original = store.run_totals

    def interleaved_totals(**kwargs):
        totals = original(**kwargs)
        writer.write_run(commit="new", tool_versions={}, rows=[row])
        return totals

    store.run_totals = interleaved_totals
    result = _trend_payload(SimpleNamespace(target=6, scope_targets={}), store)
    store._conn.close()
    writer._conn.close()
    return result


def annotation_paths():
    from crapkit.sarif import diff_uncovered_results, github_annotation

    paths = ["src/a,b.py", "src/a%2Cb.py", "src/a.py\n::warning::injected.py"]
    return [{"path": path, "annotation": github_annotation(diff_uncovered_results([(path, 12)])[0])}
            for path in paths]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        {"alert": alert, "override-worker": override_worker,
         "marks-worker10": lambda root: marks_worker(root, 10),
         "marks-worker20": lambda root: marks_worker(root, 20)}[sys.argv[1]](Path(sys.argv[2]))
    else:
        with tempfile.TemporaryDirectory(prefix="crapkit-state-rerun-") as temp:
            root = Path(temp)
            print(json.dumps({"source": str(SOURCE), "packet_age": packet_age(),
                              "prune_override": prune_during_override(root),
                              "stale_ratchet_write": stale_ratchet_write(root),
                              "mixed_trend_read": mixed_trend_read(root),
                              "annotation_paths": annotation_paths(),
                              "full_worklist_read": full_worklist_read(root)}, indent=2))
