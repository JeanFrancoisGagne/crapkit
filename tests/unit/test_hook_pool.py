"""Where the hook stops analyzing staged blobs serially and pays for a pool.

Every worker a pool starts re-imports lizard, and that import is the whole cost
of a small pool. Measured on this box (9 reps per point, medians): 4 staged
files cost 17ms serial against 120ms pooled, 8 cost 52 against 158, 12 cost 112
against 175. Serial and pool only cross at 16 (178 against 191), so that is
where the threshold sits.

The pool is counted, never started: the point of the test is which arm the hook
takes, and a real ProcessPoolExecutor would add a second of spawn to prove it.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit import analyze
from crapkit.config import load_config_text
from crapkit.hook import gate_staged

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout


class _CountedPool:
    """A pool that maps serially, so taking this arm costs nothing but is visible."""

    def __init__(self, built: list, max_workers=None) -> None:
        built.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def map(self, fn, jobs, chunksize=1):
        return [fn(job) for job in jobs]


@pytest.fixture()
def counted_pool(monkeypatch) -> list:
    built: list = []
    monkeypatch.setattr(analyze, "ProcessPoolExecutor",
                        lambda max_workers=None: _CountedPool(built, max_workers))
    return built


def staged_repo(tmp_path: Path, files: int) -> Path:
    """A repo with one committed file and `files` staged one-function modules."""
    repo = tmp_path / f"repo{files}"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (repo / "src" / "base.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    for i in range(files):
        (repo / "src" / f"mod{i}.py").write_text(f"def fn{i}(n):\n    return n + {i}\n",
                                                 encoding="utf-8")
    git(repo, "add", "-A")
    return repo


def gate(repo: Path):
    cfg = load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8"))
    return gate_staged(repo, cfg)


def test_fifteen_staged_files_are_analyzed_without_a_pool(tmp_path, counted_pool):
    repo = staged_repo(tmp_path, 15)

    assert gate(repo).violations == []
    assert counted_pool == [], "15 files is still cheaper serially than one pool spawn"


def test_sixteen_staged_files_pay_for_the_pool(tmp_path, counted_pool):
    repo = staged_repo(tmp_path, 16)

    assert gate(repo).violations == []
    assert counted_pool == [8], "at the crossover the pool runs, capped at _HOOK_MAX_WORKERS"
