"""Worker budgets preserve analysis records and keep small/cache hits cheap."""
import os
from pathlib import Path

import pytest

from crapkit import analyze
from crapkit._analysis_pool import analysis_pool

ANALYZE_ONE = analyze.analyze_one

def recording_analyzer(job):
    source = Path(job[0])
    (source.parent / f"measured-{os.getpid()}").touch()
    return ANALYZE_ONE(job)


def _jobs(tmp_path, count):
    jobs = []
    for index in range(count):
        source = tmp_path / f"f{index}.py"
        source.write_text(f"def f{index}(x):\n    return 1 if x else 2\n", encoding="utf-8")
        jobs.append((str(source), source.name))
    return jobs


@pytest.fixture(autouse=True)
def isolated_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(tmp_path / "slots"))
    monkeypatch.delenv("CRAPKIT_ANALYSIS_WORKERS", raising=False)
    monkeypatch.delenv("CRAPKIT_ANALYSIS_MEMORY_MB", raising=False)


def test_full_shared_budget_keeps_analysis_in_the_serial_caller(tmp_path, monkeypatch):
    jobs = _jobs(tmp_path, 32)
    expected = dict(map(analyze.analyze_one, jobs))
    monkeypatch.setattr(analyze, "analyze_one", recording_analyzer)
    with analysis_pool(workers=2, worker_budget=2) as occupied:
        assert occupied is not None
        actual = analyze.analyze_jobs(jobs, workers=2, worker_budget=2, chunksize=1)
    assert actual == expected
    assert [path.name for path in tmp_path.glob("measured-*")] == [f"measured-{os.getpid()}"]


def test_small_and_fully_warm_analysis_never_create_slot_files(tmp_path):
    jobs = _jobs(tmp_path, 3)
    paths = [job[1] for job in jobs]
    cold, hits, cache = analyze.analyze_files(tmp_path, paths, cache={})
    warm, warm_hits, _ = analyze.analyze_files(tmp_path, paths, cache=cache)
    assert cold == warm
    assert (hits, warm_hits) == (0, 3)
    assert not (tmp_path / "slots").exists()


def test_parallel_analysis_preserves_exact_records(tmp_path):
    jobs = _jobs(tmp_path, 36)
    expected = dict(map(analyze.analyze_one, jobs))
    assert analyze.analyze_jobs(jobs, workers=2, worker_budget=2) == expected

@pytest.mark.parametrize("count,chunksize,workers,expected_workers", [
    (32, 32, 8, None), (33, 32, 8, 2), (65, 32, None, 3),
    (65, 32, 2, 2), (65, 32, 1, None), (16, 1, 8, 8),
])
def test_pool_capacity_never_exceeds_parallel_chunks(tmp_path, monkeypatch, count, chunksize,
                                                    workers, expected_workers):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from crapkit import _analysis_pool, resources
    monkeypatch.setattr(resources, "default_chunks_per_worker", lambda: 1, raising=False)
    built = []
    @contextmanager
    def pool(**options):
        built.append(options)
        yield SimpleNamespace(map=lambda function, values, **kwargs: map(function, values))
    jobs = _jobs(tmp_path, count)
    expected = dict(map(analyze.analyze_one, jobs))
    monkeypatch.setattr(_analysis_pool, "analysis_pool", pool)
    assert analyze.analyze_jobs(jobs, workers=workers, chunksize=chunksize, worker_budget=11) == expected
    assert built == ([] if expected_workers is None else
                     [{"workers": expected_workers, "worker_budget": 11}])


def test_parallel_analysis_preserves_invalid_chunk_size_refusal(tmp_path):
    with pytest.raises(ValueError, match="chunksize"):
        analyze.analyze_jobs(_jobs(tmp_path, 33), chunksize=0)


@pytest.mark.parametrize("count,workers,source_bytes,expected_workers", [
    (32, None, 0, None), (65, None, 0, None), (129, None, 0, 2), (435, None, 0, 4),
    (1024, None, 0, 8), (4096, None, 0, 32), (65, 8, 0, 3), (435, 8, 0, 8),
    (1024, None, 16 * 1024 * 1024, 32),
])
def test_spawn_defaults_amortize_startup_without_capping_explicit_requests(
        monkeypatch, count, workers, source_bytes, expected_workers):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from crapkit import _analysis_pool, resources
    built = []
    @contextmanager
    def pool(**options):
        built.append(options["workers"])
        yield SimpleNamespace(map=lambda function, values, **kwargs: map(function, values))
    monkeypatch.setattr(resources, "default_chunks_per_worker", lambda: 4, raising=False)
    monkeypatch.setattr(analyze, "_source_bytes", lambda jobs: source_bytes, raising=False)
    monkeypatch.setattr(_analysis_pool, "analysis_pool", pool)
    monkeypatch.setattr(analyze, "analyze_one", lambda job: (job[1], []))
    jobs = [("", f"f{index}.py") for index in range(count)]
    assert analyze.analyze_jobs(jobs, workers=workers) == {path: [] for _, path in jobs}
    assert built == ([] if expected_workers is None else [expected_workers])


@pytest.mark.parametrize("count,workers,quantum,sized", [
    (3, None, 4, False), (32, None, 4, False), (33, None, 4, True),
    (65, None, 1, False), (65, 8, 4, False), (65, 1, 4, False),
])
def test_source_sizing_runs_only_for_eligible_automatic_spawn_work(
        monkeypatch, count, workers, quantum, sized):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from crapkit import _analysis_pool, resources
    observed = []
    @contextmanager
    def pool(**options):
        yield SimpleNamespace(map=lambda function, values, **kwargs: map(function, values))
    def source_bytes(jobs):
        observed.append(len(jobs))
        return 0
    monkeypatch.setattr(resources, "default_chunks_per_worker", lambda: quantum)
    monkeypatch.setattr(analyze, "_source_bytes", source_bytes)
    monkeypatch.setattr(_analysis_pool, "analysis_pool", pool)
    monkeypatch.setattr(analyze, "analyze_one", lambda job: (job[1], []))
    jobs = [("", f"f{index}.py") for index in range(count)]
    assert analyze.analyze_jobs(jobs, workers=workers) == {path: [] for _, path in jobs}
    assert observed == ([count] if sized else [])


def test_size_probe_cannot_replace_the_verified_source_reader_refusal(tmp_path, monkeypatch):
    import hashlib
    from crapkit import resources
    from crapkit.errors import ToolError
    jobs = _jobs(tmp_path, 33)
    hashes = {rel: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path, rel in jobs}
    Path(jobs[0][0]).unlink()
    monkeypatch.setattr(resources, "default_chunks_per_worker", lambda: 4)
    with pytest.raises(ToolError, match="f0.py"):
        analyze.analyze_jobs(jobs, hashes=hashes)
