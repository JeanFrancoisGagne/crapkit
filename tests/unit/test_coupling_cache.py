"""The coupling cache: the window's pair ranking is built once per key.

The log seam is monkeypatched, so a served ranking is proven by the log never
being read, not by a stopwatch. Every rebuild path is a miss on one key field,
because a wrong key is the only way this cache can lie.
"""
import json

import pytest

from crapkit import coupling_cache
from crapkit.coupling import DEFAULT_MIN_CONFIDENCE, change_coupling_lines
from crapkit.errors import GitError
from crapkit.worklist import BATCH_CONTAINMENT

HEAD = "cafe1234" * 5
TRACKED = ["src/a.ts", "src/b.ts", "src/c.ts"]


def _log(*commits) -> str:
    blocks = []
    for i, files in enumerate(commits):
        blocks.append(f"\x01dev{i % 2}\x02{1000 + i}\n" + "\n".join(files) + "\n")
    return "\n".join(blocks)


# Six commits pair a.ts with b.ts, so the pair clears the default support of 5.
LOG = _log(*[["src/a.ts", "src/b.ts"]] * 6, ["src/c.ts"])


class FakeLog:
    """Counts the window reads the cache exists to save."""

    def __init__(self, text: str = LOG, head: str = HEAD):
        self.text = text
        self.head = head
        self.reads = 0

    def lines(self, root, months):
        self.reads += 1
        return iter(self.text.splitlines())


@pytest.fixture()
def log(monkeypatch) -> FakeLog:
    fake = FakeLog()
    monkeypatch.setattr(coupling_cache, "log_lines", fake.lines)
    monkeypatch.setattr(coupling_cache, "head_commit", lambda root: fake.head)
    return fake


def _fresh(text: str = LOG, tracked=TRACKED) -> list[dict]:
    """What a reader gets with no cache in the way."""
    return change_coupling_lines(iter(text.splitlines()), top=None, tracked=set(tracked))


def _cache_path(tmp_path):
    return tmp_path / ".crapkit" / coupling_cache.CACHE_NAME


def _doc(tmp_path) -> dict:
    return json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))


def _rewrite(tmp_path, doc) -> None:
    _cache_path(tmp_path).write_text(json.dumps(doc), encoding="utf-8")


def test_a_second_read_at_the_same_key_never_re_reads_the_log(tmp_path, log):
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert log.reads == 1, "the whole point: an unmoved key pays the walk once"
    assert _cache_path(tmp_path).is_file()


def test_the_warm_ranking_is_the_cold_one_pair_for_pair(tmp_path, log):
    cold = coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    warm = coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert log.reads == 1
    assert warm == cold == _fresh(), "the cache must not reshape the ranking"
    assert cold[0]["files"] == ["src/a.ts", "src/b.ts"]
    assert (cold[0]["support"], cold[0]["confidence"]) == (6, 1.0)


def test_the_stored_order_round_trips_byte_stably(tmp_path, log):
    """_rank_pairs sorts on (-support*confidence, files). A cache that reorders
    would hand brief a different top partner than the walk it replaced."""
    ranked = _log(*[["src/a.ts", "src/b.ts"]] * 6, *[["src/b.ts", "src/c.ts"]] * 8)
    log.text = ranked
    cold = coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    warm = coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert [p["files"] for p in cold] == [["src/b.ts", "src/c.ts"], ["src/a.ts", "src/b.ts"]]
    assert json.dumps(warm) == json.dumps(cold)
    assert json.dumps(warm) == json.dumps(_fresh(ranked))


def test_a_moved_head_rebuilds(tmp_path, log):
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    log.head = "beef" * 10
    log.text = _log(*[["src/a.ts", "src/c.ts"]] * 6)
    fresh = coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert log.reads == 2
    assert [p["files"] for p in fresh] == [["src/a.ts", "src/c.ts"]]


def test_a_different_window_rebuilds(tmp_path, log):
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    coupling_cache.load_coupling(tmp_path, 3, TRACKED)
    assert log.reads == 2, "--since=3 months is a different question"


def test_a_new_utc_day_rebuilds(tmp_path, log, monkeypatch):
    """`--since=N months ago` is wall-clock relative, exactly as the churn map's
    key says: yesterday's ranking covers a window a day too wide."""
    monkeypatch.setattr(coupling_cache, "_utc_date", lambda: "2026-08-21")
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    monkeypatch.setattr(coupling_cache, "_utc_date", lambda: "2026-08-22")
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert log.reads == 2


def test_a_changed_tracked_set_at_an_unmoved_head_rebuilds(tmp_path, log):
    """`git rm --cached src/b.ts` moves the index and leaves HEAD alone. A key
    without the tracked set served the a.ts/b.ts pair on, naming a file git no
    longer has."""
    assert [p["files"] for p in coupling_cache.load_coupling(tmp_path, 12, TRACKED)] == \
        [["src/a.ts", "src/b.ts"]]

    after = coupling_cache.load_coupling(tmp_path, 12, ["src/a.ts", "src/c.ts"])

    assert log.reads == 2, "the index moved, so the ranking is stale"
    assert after == [], "an untracked file must not keep its pair"


def test_the_digest_ignores_the_order_ls_files_answers_in(tmp_path, log):
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    coupling_cache.load_coupling(tmp_path, 12, list(reversed(TRACKED)))
    assert log.reads == 1, "the same set is the same key, whatever order it arrives in"


def test_a_torn_cache_file_reads_as_cold(tmp_path, log):
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    _cache_path(tmp_path).write_text("{ truncated garbage", encoding="utf-8")
    assert coupling_cache.load_coupling(tmp_path, 12, TRACKED) == _fresh()
    assert log.reads == 2, "corrupt content is a miss, never a crash"


def test_a_cache_of_the_wrong_shape_reads_as_cold(tmp_path, log):
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    doc = _doc(tmp_path)
    doc["pairs"] = [["src/a.ts", "src/b.ts", "not a count", 1.0]]
    _rewrite(tmp_path, doc)
    assert coupling_cache.load_coupling(tmp_path, 12, TRACKED) == _fresh()
    assert log.reads == 2


def test_a_cache_written_before_the_tracked_digest_reads_as_cold(tmp_path, log):
    """The field this version added to the key. A file that predates it says
    nothing about the index, so it cannot be trusted at an unmoved HEAD."""
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    doc = _doc(tmp_path)
    del doc["key"]["tracked"]
    _rewrite(tmp_path, doc)
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert log.reads == 2


def test_a_cache_without_the_paths_marker_reads_as_cold(tmp_path, log):
    """Rankings built before `git log --relative` hold top-relative paths under
    a subdirectory root, and pair names that join against nothing."""
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    doc = _doc(tmp_path)
    del doc["key"]["paths"]
    _rewrite(tmp_path, doc)
    coupling_cache.load_coupling(tmp_path, 12, TRACKED)
    assert log.reads == 2


def test_an_unreadable_head_still_answers_and_writes_nothing(tmp_path, log, monkeypatch):
    def no_head(root):
        raise GitError("no HEAD commit")

    monkeypatch.setattr(coupling_cache, "head_commit", no_head)
    assert coupling_cache.load_coupling(tmp_path, 12, TRACKED) == _fresh()
    assert not _cache_path(tmp_path).exists(), \
        "nothing safe to key on means nothing to cache"


def test_a_read_only_crapkit_dir_costs_the_speedup_not_the_command(tmp_path, log,
                                                                  monkeypatch):
    def refuse(self, *args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr("pathlib.Path.write_text", refuse)
    assert coupling_cache.load_coupling(tmp_path, 12, TRACKED) == _fresh()


def test_the_batch_cut_asks_for_the_ranking_default(tmp_path):
    """`worklist --batches` reads the cache and truncates it. That is only sound
    while its containment threshold IS the ranking's default confidence: retune
    one and the batch cut has to go back to recomputing."""
    assert BATCH_CONTAINMENT == DEFAULT_MIN_CONFIDENCE
