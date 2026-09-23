"""A default run the filesystem stops deleting partway keeps its receipt, so every
later prune reports it failed again and retries it, instead of forgetting it."""
import os
from pathlib import Path
import stat
import sys

import pytest

from test_runner_owns_test_evidence_retention import finished_run, runner


def test_a_run_whose_directory_is_held_open_is_retried_by_the_next_prune(tmp_path, monkeypatch):
    stuck = finished_run(tmp_path, "run-stuck", 3)
    remove_directory = os.rmdir

    def rmdir(path, *args, **kwargs):
        # Windows refuses to remove a directory another process holds open.
        # rmtree has deleted every file inside, the receipt too, by this point.
        if Path(os.fspath(path)) == stuck:
            raise PermissionError(32, "The process cannot access the file", os.fspath(path))
        return remove_directory(path, *args, **kwargs)
    monkeypatch.setattr(os, "rmdir", rmdir)

    first = runner().prune_test_runs(tmp_path, keep=0, days=1)
    second = runner().prune_test_runs(tmp_path, keep=0, days=1)

    assert first["failed"] == [str(stuck)]
    assert second["failed"] == [str(stuck)]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to delete a read-only file")
def test_a_run_holding_a_read_only_file_stays_listed_until_it_can_go(tmp_path):
    stuck = finished_run(tmp_path, "run-stuck", 3)
    junit = stuck / "junit.xml"
    os.chmod(junit, stat.S_IREAD)
    try:
        first = runner().prune_test_runs(tmp_path, keep=0, days=1)
        preview = runner().prune_test_runs(tmp_path, keep=0, days=1, dry_run=True)
    finally:
        os.chmod(junit, stat.S_IREAD | stat.S_IWRITE)
    last = runner().prune_test_runs(tmp_path, keep=0, days=1)

    assert first["failed"] == [str(stuck)]
    assert preview["planned"] == [str(stuck)]
    assert last["removed"] == [str(stuck)]
    assert not stuck.exists()
