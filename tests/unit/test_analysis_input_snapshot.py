"""An analysis cache entry describes the bytes the parser consumed."""
from pathlib import Path

import pytest

from crapkit import analyze
from crapkit.errors import ToolError
from crapkit.merge import UnanalyzableFile


SOURCE = "def f(x):\n    return x\n"
EDITED = "def f(x):\n    if x:\n        return 1\n    return 0\n"


@pytest.mark.parametrize("change", ["edit", "delete"])
@pytest.mark.parametrize("pooled", [False, True])
def test_changed_input_cannot_publish_records_under_an_earlier_digest(tmp_path, monkeypatch, change, pooled):
    path = tmp_path / "source.py"
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(tmp_path / "slots"))
    path.write_text(SOURCE, encoding="utf-8")
    real_jobs = analyze.analyze_jobs
    (tmp_path / "other.py").write_text("def g(x):\n    return x + 1\n", encoding="utf-8")

    def change_before_parse(*args, **kwargs):
        if change == "edit":
            path.write_text(EDITED, encoding="utf-8")
        else:
            path.unlink()
        return real_jobs(*args, **kwargs, pool_threshold=1 if pooled else 64, chunksize=1)

    with monkeypatch.context() as patch:
        patch.setattr(analyze, "analyze_jobs", change_before_parse)
        with pytest.raises(ToolError, match="source.py.*changed|source.py.*read"):
            analyze.analyze_files(tmp_path, ["source.py", "other.py"], cache={}, workers=2 if pooled else 1)

    path.write_text(SOURCE, encoding="utf-8")
    records, hits, cache = analyze.analyze_files(tmp_path, ["source.py"], cache={}, workers=1)
    assert hits == 0
    assert [(r.long_name, r.start, r.end, r.ccn) for r in records["source.py"]] == [("f( x )", 1, 2, 1)]
    cache_path = tmp_path / "cache.json"
    analyze.save_cache(cache_path, cache)
    warm, hits, _ = analyze.analyze_files(tmp_path, ["source.py"], cache=analyze.load_cache(cache_path))
    assert hits == 1
    assert warm == records


def test_parser_uses_the_bytes_whose_digest_was_checked(tmp_path, monkeypatch):
    path = tmp_path / "source.py"
    path.write_text(SOURCE, encoding="utf-8")
    real_read = Path.read_bytes
    reads = 0

    def replace_after_read(file):
        nonlocal reads
        raw = real_read(file)
        if file == path:
            reads += 1
            if reads == 2:
                path.write_text(EDITED, encoding="utf-8")
        return raw

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", replace_after_read)
        records, _, cache = analyze.analyze_files(tmp_path, ["source.py"], cache={})
    assert records["source.py"][0].ccn == 1
    current, hits, _ = analyze.analyze_files(tmp_path, ["source.py"], cache=cache)
    assert hits == 0
    assert current["source.py"][0].ccn == 2


def test_a_file_changed_while_hashing_cannot_keep_a_stat_stamp(tmp_path, monkeypatch):
    path = tmp_path / "source.py"
    path.write_text(SOURCE, encoding="utf-8")
    real_read = Path.read_bytes

    def remove_after_read(file):
        raw = real_read(file)
        if file == path:
            path.unlink()
        return raw

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", remove_after_read)
        with pytest.raises(ToolError, match="source.py.*read"):
            analyze.analyze_files(tmp_path, ["source.py"], cache={})
    path.write_text(SOURCE, encoding="utf-8")
    records, hits, _ = analyze.analyze_files(tmp_path, ["source.py"], cache={})
    assert hits == 0
    assert records["source.py"][0].ccn == 1


def test_verified_input_keeps_reader_refusals(tmp_path, capsys):
    """A refused file is named and left uncached, and the run keeps going.

    Through 0.7.0 the first refusal raised, so one ambiguous arrow in a corpus
    ended `coverage`, which left the ratchet unseeded and refused every commit in
    the repo, in every language. The file now scores as the zero functions it
    honestly holds. Staying out of the cache is what keeps the refusal audible: a
    cached empty record set reads exactly like a real file of zero functions, so
    the warning would sound once and then go quiet while nothing was scored.
    """
    (tmp_path / "source.ts").write_text(
        "const callbacks = [(x: number) => x < 2, (y: number) => y];", encoding="utf-8")

    fresh, _, cache = analyze.analyze_files(tmp_path, ["source.ts"], cache={})

    assert isinstance(fresh["source.ts"], UnanalyzableFile)
    assert list(fresh["source.ts"]) == [], "an unread file holds zero functions"
    assert "source.ts" in fresh["source.ts"].reason
    assert cache["entries"] == {}, "a refusal cached as an empty file goes silent"
    assert "source.ts" in capsys.readouterr().err
