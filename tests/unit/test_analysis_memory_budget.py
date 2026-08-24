"""CRAPKIT_ANALYSIS_MEMORY_MB: cap the analysis pool by memory instead of cores.

Measured cold over the consumer repo's 14,152 files: 24 workers peak at 807 MB of tree
RSS in 11.8 s, 16 at 565 MB in 14.2 s. The default is unchanged behaviour (one
worker per core); a box that cannot spare the memory trades 2.4 s for 242 MB by
setting the budget. A mistyped budget must never silently serialize a run that
would otherwise take eleven seconds, so anything unparsable reads as unset.

The pool is stood in for here: what matters is the worker count the pool is
asked for, not the processes it would spawn.
"""
import crapkit.analyze as analyze
from crapkit.analyze import analyze_jobs

ENV = "CRAPKIT_ANALYSIS_MEMORY_MB"


class _RecordingPool:
    """Stands in for ProcessPoolExecutor: records max_workers, maps in-process."""

    def __init__(self):
        self.max_workers = "never asked"

    def __call__(self, max_workers=None):
        self.max_workers = max_workers
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, jobs, chunksize=1):
        return map(fn, jobs)


def _workers_asked_for(monkeypatch, tmp_path, *, workers, budget=None):
    monkeypatch.delenv(ENV, raising=False)
    if budget is not None:
        monkeypatch.setenv(ENV, budget)
    pool = _RecordingPool()
    monkeypatch.setattr(analyze, "ProcessPoolExecutor", pool)
    source = tmp_path / "a.ts"
    source.write_text("export function f(x: number) { return x; }\n", encoding="utf-8")

    analyze_jobs([(str(source), "a.ts")], workers=workers, pool_threshold=1)

    return pool.max_workers


def test_no_budget_leaves_the_worker_count_alone(tmp_path, monkeypatch):
    assert _workers_asked_for(monkeypatch, tmp_path, workers=24) == 24


def test_no_budget_and_no_worker_count_still_means_one_per_core(tmp_path, monkeypatch):
    assert _workers_asked_for(monkeypatch, tmp_path, workers=None) is None


def test_a_budget_caps_the_pool(tmp_path, monkeypatch):
    """565 MB is the 16-worker measurement: 565 // 35 MB per worker."""
    assert _workers_asked_for(monkeypatch, tmp_path, workers=24, budget="565") == 16


def test_a_budget_bigger_than_the_run_needs_changes_nothing(tmp_path, monkeypatch):
    assert _workers_asked_for(monkeypatch, tmp_path, workers=4, budget="64000") == 4


def test_a_budget_too_small_for_one_worker_still_gets_one(tmp_path, monkeypatch):
    assert _workers_asked_for(monkeypatch, tmp_path, workers=24, budget="5") == 1


def test_a_budget_that_is_not_a_positive_number_reads_as_unset(tmp_path, monkeypatch):
    for junk in ("", "  ", "plenty", "0", "-8", "1.5", "8mb"):
        assert _workers_asked_for(monkeypatch, tmp_path, workers=24, budget=junk) == 24, junk


def test_the_records_are_the_same_whatever_the_budget(tmp_path, monkeypatch):
    """A memory knob is not an analysis knob."""
    source = tmp_path / "a.ts"
    source.write_text("export function f(x: number) { if (x) { return 1; } return x; }\n",
                      encoding="utf-8")
    job = [(str(source), "a.ts")]
    monkeypatch.delenv(ENV, raising=False)
    plain = analyze_jobs(job, workers=2, pool_threshold=99)

    monkeypatch.setenv(ENV, "70")
    assert analyze_jobs(job, workers=2, pool_threshold=99) == plain
