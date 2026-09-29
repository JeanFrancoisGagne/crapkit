"""The churn log at its edges: the commit a window is cut for, a key or a log
that does not hold, and the copy on its way to disk.

churn_log.py: a window's cutoff is `months` before the commit date of the
commit the caller names, not of HEAD; a HEAD that cannot be read has no window,
whatever repository the process runs in; a commit dated at the cutoff is in the
window; a header with no commit date reads as 0; batches close once they hold
`size` characters; a key without its log, a key that is no JSON object and a
log whose bytes changed at the same size all read as cold; the copy is written
under a part named after the log, and one that cannot be laid down leaves no
part behind; the key's date is the UTC one; and a compressed log longer than
one read chunk inflates whole.
"""
import json
import os
import random
import subprocess
import zlib
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path

from crapkit import churn_log
from hang_guard import HANG_SECONDS


def _git(repo: Path, *args: str, env=None) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True, env=env).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    return path


def _commit(repo: Path, rel: str, stamp: int) -> str:
    (repo / rel).write_text(f"{stamp}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    date = f"@{stamp} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", rel, env=env)
    return _git(repo, "rev-parse", "HEAD")


def test_a_window_is_cut_for_the_commit_named_not_for_head(tmp_path):
    root = _repo(tmp_path / "r")
    first = _commit(root, "a.py", 1_600_000_000)
    _commit(root, "b.py", 1_700_000_000)
    want = churn_log.months_before(1_600_000_000, 1)

    assert (churn_log.window_cutoff(root, 1, first),
            churn_log.walked_window(root, 1, first).cutoff,
            churn_log.stored_window(root, 1, first).cutoff) == (want, want, want)


def test_a_head_that_cannot_be_read_has_no_window_wherever_the_process_runs(tmp_path,
                                                                             monkeypatch):
    other = _repo(tmp_path / "other")
    _commit(other, "a.py", 1_600_000_000)
    monkeypatch.chdir(other)

    window = churn_log.stored_window(_repo(tmp_path / "empty"), 12, None)

    assert (list(window.lines), window.cutoff) == ([], None)


def test_a_commit_dated_at_the_cutoff_is_in_the_window():
    lines = ["\x01a\x021\x0210\n", "x.py\n", "\x01b\x021\x029\n", "y.py\n"]

    assert list(churn_log._within(iter(lines), 10)) == lines[:2]


def test_a_header_with_no_commit_date_reads_as_0():
    assert churn_log._commit_time("\x01a\x021\x02\n") == 0


def test_a_batch_closes_once_it_holds_size_characters():
    assert list(churn_log._batches(iter(["ab", "c", "de", "f"]), 3)) == [["ab", "c"], ["de", "f"]]


def test_a_key_without_its_log_is_no_cache(tmp_path):
    crapkit_dir = tmp_path / ".crapkit"
    crapkit_dir.mkdir()
    (crapkit_dir / "churn-log-v3.json").write_text("{}", encoding="utf-8")

    assert churn_log.has_cache(tmp_path) is False


def test_a_key_that_is_no_json_object_reads_as_no_key(tmp_path):
    (tmp_path / "churn-log-v3.json").write_text("[1]", encoding="utf-8")

    assert churn_log._read_key(tmp_path / "churn-log-v3.z") is None


def test_a_log_whose_bytes_changed_at_the_same_size_reads_as_cold(tmp_path):
    blob = zlib.compress(b"\x01a\x021\x022\nx.py\n")
    path = tmp_path / "churn-log-v3.z"
    path.write_bytes(blob[:-1] + bytes([blob[-1] ^ 1]))

    assert churn_log._read_log(path, {"size": len(blob), "crc": zlib.crc32(blob)}) is None


def test_the_copy_is_written_under_a_part_named_after_the_log(tmp_path):
    path = tmp_path / ".crapkit" / churn_log.LOG_NAME
    tee = churn_log._tee(iter(["a\n"]), path, {"head": "h"})

    assert next(tee) == "a\n"
    parts = [part.name for part in path.parent.glob("*.part")]
    assert list(tee) == []
    assert [part.startswith("churn-log-v3.") for part in parts] == [True]


def test_a_copy_that_cannot_be_laid_down_leaves_no_part(tmp_path):
    """The log's own name is taken by a directory, so the rename fails."""
    path = tmp_path / ".crapkit" / churn_log.LOG_NAME
    path.mkdir(parents=True)

    assert list(churn_log._tee(iter(["a\n"]), path, {"head": "h"})) == ["a\n"]
    assert list(path.parent.glob("*.part")) == []


class _Clock(datetime):
    """23:30 UTC on Jan 1, which is already Jan 2 in a zone 5 hours east."""

    @classmethod
    def now(cls, tz=None):
        moment = datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)
        return moment.astimezone(tz or timezone(timedelta(hours=5)))


def test_the_key_s_date_is_the_utc_one(monkeypatch):
    monkeypatch.setattr(churn_log, "datetime", _Clock)

    assert churn_log._utc_date() == "2026-01-01"


def test_a_log_longer_than_one_read_chunk_inflates_whole():
    rng = random.Random(0)
    lines = [f"{rng.getrandbits(128):032x} é\n" for _ in range(80_000)]
    comp = zlib.compressobj(1)
    blob = comp.compress("".join(lines).encode("utf-8")) + comp.flush()

    assert len(blob) > churn_log.CHUNK
    assert list(islice(churn_log._inflate(blob), len(lines) + 1)) == lines
