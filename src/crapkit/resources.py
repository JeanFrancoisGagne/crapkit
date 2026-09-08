"""Analysis pool policy shared by execution and resource status.

Numbered slots coordinate pool workers across repositories for one user/host.
Serial callers remain outside this budget. Memory is an estimate, not an OS
limit. An explicit resource directory selects a separate coordination domain.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import socket

from .errors import ToolError
from .locks import exclusive_lock


WORKER_MEMORY_MB = 35
DEFAULT_SOURCE_BYTES_PER_WORKER = 512 * 1024
_POOL_WORKER_LIMIT = 61 if os.name == "nt" else None


def _windows_affinity() -> int:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE,
                                              ctypes.POINTER(ctypes.c_size_t),
                                              ctypes.POINTER(ctypes.c_size_t)]
    process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(),
                                        ctypes.byref(process_mask), ctypes.byref(system_mask)):
        raise ctypes.WinError(ctypes.get_last_error())
    return process_mask.value.bit_count()


def _affinity_cpus() -> int | None:
    if hasattr(os, "process_cpu_count"):
        return os.process_cpu_count()
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    if os.name == "nt":
        return _windows_affinity()
    return None


def available_cpus() -> tuple[int, str]:
    """Use process-visible CPUs where supported, then the host count, then one."""
    try:
        count = _affinity_cpus()
    except (OSError, NotImplementedError, AttributeError):
        count = None
    if count:
        return count, "process affinity"
    return os.cpu_count() or 1, "cpu_count"


def _positive_environment(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw.isdigit() or not raw.strip("0"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _budget_directory() -> Path:
    override = os.environ.get("CRAPKIT_RESOURCE_DIR")
    if override:
        return Path(override).resolve()
    host = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16]
    return Path.home() / ".cache" / "crapkit" / "workers" / host


def _worker_limit(requested: int, shared: int, memory: int | None, inherited: int | None) -> int:
    limit = min(requested or shared, shared, inherited or shared, _POOL_WORKER_LIMIT or shared)
    if memory is not None:
        limit = min(limit, max(1, memory // WORKER_MEMORY_MB))
    return max(1, limit)


def default_chunks_per_worker() -> int:
    """Amortize spawn startup without fixing the caller's start-method choice."""
    from multiprocessing import get_all_start_methods, get_start_method
    method = get_start_method(allow_none=True) or get_all_start_methods()[0]
    return 4 if method == "spawn" else 1


def resource_status(*, analysis_workers: int = 0, worker_budget: int = 0) -> dict:
    """Report effective ceilings without acquiring slots or creating files.

    Each positive budget selects the same slot prefix. Different budgets share
    slots; the largest active ceiling bounds aggregate pool capacity. Affinity
    always caps the local request. Status cannot promise later slot availability.
    """
    cpus, probe = available_cpus()
    quantum = default_chunks_per_worker()
    shared = min(worker_budget or cpus, cpus)
    memory = _positive_environment("CRAPKIT_ANALYSIS_MEMORY_MB")
    inherited = _positive_environment("CRAPKIT_ANALYSIS_WORKERS")
    limit = _worker_limit(analysis_workers, shared, memory, inherited)
    return {"available_cpus": cpus, "cpu_probe": probe,
            "requested_analysis_workers": analysis_workers, "shared_pool_limit": shared,
            "default_chunks_per_worker": quantum,
            "default_source_bytes_per_worker": DEFAULT_SOURCE_BYTES_PER_WORKER if quantum > 1 else None,
            "pool_worker_limit": limit, "estimated_pool_memory_mb": limit * WORKER_MEMORY_MB,
            "inherited_analysis_workers": inherited, "memory_budget_mb": memory,
            "worker_memory_estimate_mb": WORKER_MEMORY_MB, "memory_is_hard_limit": False,
            "budget_directory": str(_budget_directory()), "serial_fallback": True,
            "coordination": "per-user host primary locks (Windows caller, POSIX guardian) and worker-lifetime companion locks"}


def available_slots(status: dict) -> list[Path]:
    """Probe a shared slot prefix; the pool owner must acquire it before use."""
    paths = []
    directory = Path(status["budget_directory"])
    with ExitStack() as stack:
        for index in range(status["shared_pool_limit"]):
            path = directory / f"{index}.lock"
            if _take_slot(stack, path):
                paths.append(path)
            if len(paths) == status["pool_worker_limit"]:
                break
    return paths


def _take_slot(stack: ExitStack, path: Path) -> bool:
    try:
        stack.enter_context(exclusive_lock(path, label="analysis worker"))
        stack.enter_context(exclusive_lock(worker_lock(path), label="live analysis worker"))
    except (OSError, ToolError):
        return False
    return True


def worker_lock(path: Path) -> Path:
    """The actual worker keeps this companion lock if its owner dies first."""
    return path.with_suffix(".worker.lock")
