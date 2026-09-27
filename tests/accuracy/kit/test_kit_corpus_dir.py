"""The full corpus is found in one order for every packet: the variable, the
image's /corpus, then the cache `corpus.py fetch` fills at today's digest."""
from pathlib import Path

import pytest

from accuracy.kit import corpus_dir, runlog


def _built(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "DIGEST").write_text("27bfc7258750\n", encoding="utf-8")
    return path


@pytest.fixture
def places(monkeypatch, tmp_path):
    """No variable, an image folder and a fetch cache under tmp_path, both empty."""
    monkeypatch.delenv(corpus_dir.ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(corpus_dir, "IMAGE", tmp_path / "image")
    tool = corpus_dir.corpus_tool()
    cache = tmp_path / "local" / "crapkit-accuracy" / "corpus" / tool.tag(tool.load())
    return tmp_path / "image", cache


def test_the_variable_is_the_only_place_looked_in_when_set(monkeypatch, tmp_path, places):
    named = _built(tmp_path / "named")
    _built(places[0])
    monkeypatch.setenv(corpus_dir.ENV, str(named))

    assert corpus_dir.candidates() == [named]
    assert corpus_dir.locate() == named


def test_the_image_folder_wins_over_the_fetch_cache(places):
    image, cache = places
    _built(image)
    _built(cache)

    assert corpus_dir.candidates() == [image, cache]
    assert corpus_dir.locate() == image


def test_an_empty_image_folder_falls_through_to_the_fetch_cache(places):
    image, cache = places
    image.mkdir()
    _built(cache)

    assert corpus_dir.locate() == cache


def test_a_fetch_of_another_digest_is_not_this_corpus(places):
    _, cache = places
    _built(cache.parent / "corpus-000000000000")

    assert corpus_dir.locate() is None


def test_a_missing_corpus_is_an_infra_miss_naming_every_place(monkeypatch, tmp_path, places):
    log = tmp_path / "run.jsonl"
    monkeypatch.setenv(runlog.LOG_ENV, str(log))

    with pytest.raises(pytest.fail.Exception) as failed:
        corpus_dir.require()

    message = str(failed.value)
    assert all(str(place) in message for place in places)
    assert corpus_dir.ENV in message and corpus_dir.FETCH in message
    assert runlog.summarize(runlog.read(log))["infra"] == [message]


def test_require_hands_back_the_corpus_it_found(places):
    image, _ = places
    _built(image)

    assert corpus_dir.require() == image
