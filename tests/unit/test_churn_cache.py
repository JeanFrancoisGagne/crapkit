"""The churn cache: git's tree walk runs once per (HEAD, window, UTC day).

Both git calls are monkeypatched, so a served cache is proven by the git seam
never firing — not by a stopwatch. Every rebuild path is a miss on one key
field, because a wrong key is the only way this cache can lie.
"""
import json

import pytest

from crapkit import churn_cache
from crapkit.churn import FileChurn, parse_git_log
from crapkit.errors import GitError

LOG = ("\x01alice\x021000000000\nsrc/a.ts\nsrc/b.ts\n\n"
       "\x01bob\x022000000000\nsrc/a.ts\n")
HEAD = "cafe1234cafe1234cafe1234cafe1234cafe1234"


class FakeGit:
    """Counts the log calls the cache does not save."""

    def __init__(self, head: str = HEAD, log: str = LOG):
        self.head = head
        self.log = log
        self.log_calls = 0

    def lines(self, root, months):
        self.log_calls += 1
        return iter(self.log.splitlines())


@pytest.fixture()
def git(monkeypatch) -> FakeGit:
    fake = FakeGit()
    monkeypatch.setattr(churn_cache, "churn_log_lines", fake.lines)
    monkeypatch.setattr(churn_cache, "head_commit", lambda root: fake.head)
    return fake


def _dump(churn: dict) -> str:
    return json.dumps({p: list(c) for p, c in sorted(churn.items())})


def test_a_second_read_at_the_same_head_never_shells_git(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    churn_cache.load_churn(tmp_path, 12)
    assert git.log_calls == 1, "the whole point: an unmoved HEAD pays git once"
    assert (tmp_path / ".crapkit" / "churn-cache.json").is_file()


def test_the_warm_read_is_byte_identical_to_the_cold_one(tmp_path, git):
    cold = churn_cache.load_churn(tmp_path, 12)
    warm = churn_cache.load_churn(tmp_path, 12)
    assert git.log_calls == 1
    assert _dump(warm) == _dump(cold)
    assert _dump(cold) == _dump(parse_git_log(LOG)), "the cache must not reshape the parse"


def test_a_moved_head_rebuilds(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    git.head = "beef" * 10
    git.log = "\x01carol\x023000000000\nsrc/c.ts\n"
    fresh = churn_cache.load_churn(tmp_path, 12)
    assert git.log_calls == 2
    assert set(fresh) == {"src/c.ts"}, "a new commit must not be answered from the old map"


def test_a_different_window_rebuilds(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    churn_cache.load_churn(tmp_path, 3)
    assert git.log_calls == 2, "--since=3 months is a different question"


def test_a_new_utc_day_rebuilds(tmp_path, git, monkeypatch):
    """`--since=N months ago` is wall-clock relative: yesterday's map is a day too wide."""
    monkeypatch.setattr(churn_cache, "_utc_date", lambda: "2026-08-21")
    churn_cache.load_churn(tmp_path, 12)
    monkeypatch.setattr(churn_cache, "_utc_date", lambda: "2026-08-22")
    churn_cache.load_churn(tmp_path, 12)
    assert git.log_calls == 2


def test_a_torn_cache_file_reads_as_cold(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    (tmp_path / ".crapkit" / "churn-cache.json").write_text("{ truncated garbage",
                                                            encoding="utf-8")
    assert churn_cache.load_churn(tmp_path, 12) == parse_git_log(LOG)
    assert git.log_calls == 2, "corrupt content is a miss, never a crash"


def test_a_cache_of_the_wrong_shape_reads_as_cold(tmp_path, git):
    path = tmp_path / ".crapkit" / "churn-cache.json"
    path.parent.mkdir(parents=True)
    key = {"head": HEAD, "months": 12, "date": churn_cache._utc_date()}
    path.write_text(json.dumps({"key": key, "files": {"src/a.ts": "not a row"}}),
                    encoding="utf-8")
    assert churn_cache.load_churn(tmp_path, 12) == parse_git_log(LOG)
    assert git.log_calls == 1


def test_an_unreadable_head_still_answers_and_writes_nothing(tmp_path, git, monkeypatch):
    def no_head(root):
        raise GitError("no HEAD commit")

    monkeypatch.setattr(churn_cache, "head_commit", no_head)
    assert churn_cache.load_churn(tmp_path, 12) == parse_git_log(LOG)
    assert not (tmp_path / ".crapkit" / "churn-cache.json").exists(), \
        "nothing safe to key on means nothing to cache"


def test_weights_survive_the_json_round_trip_exactly(tmp_path, git):
    cold = churn_cache.load_churn(tmp_path, 12)
    warm = churn_cache.load_churn(tmp_path, 12)
    assert [type(v) for v in warm["src/a.ts"]] == [int, int, float]
    assert warm["src/a.ts"] == cold["src/a.ts"] == FileChurn(2, 2, cold["src/a.ts"].weight)


def test_a_map_rebuild_without_a_log_cache_stays_off_the_log(tmp_path, git):
    """load_churn walks git directly when no churn-log has been laid down:
    building the deflated log belongs to the commands that need its per-commit
    structure (brief, batches, coupling), never to a map-only rebuild."""
    assert churn_cache.load_churn(tmp_path, 12) == parse_git_log(LOG)
    assert git.log_calls == 1
    assert list((tmp_path / ".crapkit").glob("churn-log*")) == []
