"""The legacy memory knob remains an estimated worker cap.

Resource status exposes the same 35 MB divisor and invalid-as-unset rule.
Native shared-pool tests separately verify admission and process lifetime.
"""
import crapkit.resources as resources
from crapkit.analyze import analyze_jobs

ENV = "CRAPKIT_ANALYSIS_MEMORY_MB"


def _workers_asked_for(monkeypatch, tmp_path, *, workers, budget=None):
    monkeypatch.delenv(ENV, raising=False)
    if budget is not None:
        monkeypatch.setenv(ENV, budget)
    monkeypatch.delenv("CRAPKIT_ANALYSIS_WORKERS", raising=False)
    monkeypatch.setattr(resources, "available_cpus", lambda: (24, "fixture affinity"))
    return resources.resource_status(analysis_workers=workers or 0)["pool_worker_limit"]


def test_no_budget_leaves_the_worker_count_alone(tmp_path, monkeypatch):
    assert _workers_asked_for(monkeypatch, tmp_path, workers=24) == 24


def test_no_budget_and_no_worker_count_means_one_per_available_cpu(tmp_path, monkeypatch):
    assert _workers_asked_for(monkeypatch, tmp_path, workers=None) == 24


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
