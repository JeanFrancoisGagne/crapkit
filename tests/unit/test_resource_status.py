"""Resource status reports policy and probe fallbacks without opening a pool."""
import json
import os
from types import SimpleNamespace

import pytest

from crapkit import resources


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("CRAPKIT_ANALYSIS_MEMORY_MB", raising=False)
    monkeypatch.delenv("CRAPKIT_ANALYSIS_WORKERS", raising=False)


def test_status_reports_effective_limits_and_the_memory_estimate(monkeypatch):
    monkeypatch.setattr(resources, "available_cpus", lambda: (24, "test affinity"))
    monkeypatch.setenv("CRAPKIT_ANALYSIS_MEMORY_MB", "565")
    status = resources.resource_status(analysis_workers=20, worker_budget=18)
    assert status["available_cpus"] == 24
    assert status["shared_pool_limit"] == 18
    assert status["pool_worker_limit"] == 16
    assert status["memory_budget_mb"] == 565
    assert status["worker_memory_estimate_mb"] == 35
    assert status["memory_is_hard_limit"] is False
    assert status["serial_fallback"] is True
    json.dumps(status)


def test_inherited_limit_caps_explicit_parallelism_without_waiting(monkeypatch):
    monkeypatch.setenv("CRAPKIT_ANALYSIS_WORKERS", "1")
    assert resources.resource_status(analysis_workers=24)["pool_worker_limit"] == 1


@pytest.mark.parametrize("raw", ["", "0", "bad", "-1", "1.5", "²"])
def test_invalid_legacy_memory_value_remains_unset(monkeypatch, raw):
    monkeypatch.setenv("CRAPKIT_ANALYSIS_MEMORY_MB", raw)
    assert resources.resource_status()["memory_budget_mb"] is None


def test_affinity_probe_failures_fall_back_to_cpu_count(monkeypatch):
    def unsupported():
        raise OSError("probe unsupported")
    monkeypatch.setattr(resources, "_affinity_cpus", unsupported)
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    assert resources.available_cpus() == (12, "cpu_count")


def test_unknown_cpu_count_still_permits_serial_analysis(monkeypatch):
    monkeypatch.setattr(resources, "_affinity_cpus", lambda: None)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert resources.available_cpus() == (1, "cpu_count")


def test_status_does_not_create_the_budget_directory(tmp_path, monkeypatch):
    destination = tmp_path / "absent"
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(destination))
    assert resources.resource_status()["budget_directory"] == str(destination)
    assert not destination.exists()


def test_pool_backend_ceiling_preserves_available_shared_capacity(monkeypatch):
    monkeypatch.setattr(resources, "available_cpus", lambda: (96, "test affinity"))
    monkeypatch.setattr(resources, "_POOL_WORKER_LIMIT", 61)
    status = resources.resource_status(analysis_workers=90)
    assert status["shared_pool_limit"] == 96
    assert status["pool_worker_limit"] == 61


def test_affinity_limits_an_explicit_request(monkeypatch):
    monkeypatch.setattr(resources, "available_cpus", lambda: (3, "test affinity"))
    assert resources.resource_status(analysis_workers=24, worker_budget=24)["pool_worker_limit"] == 3


@pytest.mark.parametrize("system,probes,expected", [
    ("posix", {"process_cpu_count": lambda: 7}, (7, "process affinity")),
    ("posix", {"sched_getaffinity": lambda pid: {2, 4, 6}}, (3, "process affinity")),
    ("nt", {}, (5, "process affinity")),
    ("posix", {}, (12, "cpu_count")),
])
def test_cpu_probe_adapters_preserve_the_available_capacity(monkeypatch, system, probes, expected):
    host = SimpleNamespace(name=system, cpu_count=lambda: 12, **probes)
    monkeypatch.setattr(resources, "os", host)
    monkeypatch.setattr(resources, "_windows_affinity", lambda: 5)
    assert resources.available_cpus() == expected


@pytest.mark.parametrize("method,chunks", [("spawn", 4), ("fork", 1), ("forkserver", 1), (None, 4)])
def test_status_distinguishes_default_work_sizing_from_the_worker_ceiling(monkeypatch, method, chunks):
    import multiprocessing
    def configured_method(*, allow_none):
        assert allow_none, "status must not fix the caller's multiprocessing context"
        return method
    monkeypatch.setattr(multiprocessing, "get_start_method", configured_method)
    monkeypatch.setattr(multiprocessing, "get_all_start_methods", lambda: ["spawn", "fork"])
    monkeypatch.setattr(resources, "available_cpus", lambda: (24, "test affinity"))
    status = resources.resource_status()
    assert status["default_chunks_per_worker"] == chunks
    assert status["default_source_bytes_per_worker"] == (512 * 1024 if chunks == 4 else None)
    assert status["pool_worker_limit"] == 24
