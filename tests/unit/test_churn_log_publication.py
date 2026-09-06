"""Concurrent history readers keep each window bound to its own compressed bytes."""
import multiprocessing as mp
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crapkit import churn_log


def _git(root, *args, when=None):
    env = dict(os.environ)
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return subprocess.run(["git", "-c", "core.hooksPath=.no-hooks", *args], cwd=root,
                          env=env, capture_output=True, text=True, check=True).stdout


def _history(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Review Fixture")
    _git(root, "config", "user.email", "review@example.invalid")
    for name, age in [("old.txt", 200), ("fresh.txt", 1)]:
        (root / name).write_text(name, encoding="utf-8")
        _git(root, "add", name)
        when = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
        _git(root, "commit", "-qm", name, when=when)


def _publish_first(root, ready, done):
    path = root / ".crapkit" / churn_log.LOG_NAME
    replace = Path.replace

    def held_replace(part, destination):
        result = replace(part, destination)
        if destination == path:
            ready.set()
            assert done.wait(15), "second writer did not finish"
        return result

    Path.replace = held_replace
    assert "old.txt\n" not in list(churn_log.log_lines(root, 1))


def _publish_second(root, ready, done):
    assert ready.wait(15), "first writer did not publish its bytes"
    assert "old.txt\n" in list(churn_log.log_lines(root, 12))
    done.set()


def test_competing_process_never_stamps_another_windows_bytes(tmp_path):
    _history(tmp_path)
    ctx = mp.get_context("spawn")
    ready, done = ctx.Event(), ctx.Event()
    workers = [ctx.Process(target=fn, args=(tmp_path, ready, done))
               for fn in (_publish_first, _publish_second)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(30)
        assert [worker.exitcode for worker in workers] == [0, 0]
        key = churn_log._cache_key(tmp_path, 1)
        cached = churn_log._cached(tmp_path / ".crapkit" / churn_log.LOG_NAME, key)
        assert cached is None or "old.txt\n" not in list(cached)
        fresh = [churn_log._shipped(line) for line in churn_log._window_log(tmp_path, 1)]
        assert list(churn_log.log_lines(tmp_path, 1)) == fresh
        assert list((tmp_path / ".crapkit").glob("*.part")) == []
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)


def test_overlapping_reads_in_one_process_own_separate_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(churn_log, "head_commit", lambda root: "fixture-head")
    monkeypatch.setattr(churn_log, "_window_log",
                        lambda root, months: iter([f"window-{months}.txt\n"]))
    first = churn_log.log_lines(tmp_path, 1)
    second = churn_log.log_lines(tmp_path, 12)
    try:
        assert next(first) == "window-1.txt\n"
        assert next(second) == "window-12.txt\n"
        assert len(list((tmp_path / ".crapkit").glob("*.part"))) == 2
        first.close()
        assert list(second) == []
        assert list(churn_log.log_lines(tmp_path, 12)) == ["window-12.txt\n"]
        assert list((tmp_path / ".crapkit").glob("*.part")) == []
    finally:
        first.close()
        second.close()
