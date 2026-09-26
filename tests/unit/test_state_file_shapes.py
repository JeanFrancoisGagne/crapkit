"""crapkit's own state under .crapkit/, as an older, interrupted or hand-edited
crapkit leaves it.

Each reader is fed a JSON null, a list or a string where an object belongs, an
empty object or file, a byte that is not UTF-8, a UTF-16 file, and entries whose
inner fields are null or of another type. Every one answers as if there were no
state, so the next command rebuilds it, and none raises. The readers are called
the way their commands call them.
"""
import json

import pytest

from crapkit import analyze, churn_cache, churn_commits, churn_log, coupling_cache, lane_stamps, mutate_pool

_PAYLOADS = {
    "null": b"null",
    "a-list": b"[1, 2]",
    "a-string": b'"x"',
    "an-empty-object": b"{}",
    "an-empty-file": b"",
    "a-latin1-byte": b'{"k": "caf' + bytes([0xE9]) + b'"}',
    "utf16-with-bom": bytes([0xFF, 0xFE]) + "{}".encode("utf-16-le"),
}


def _shapes(inner: list) -> list:
    shapes = [pytest.param(data, id=name) for name, data in _PAYLOADS.items()]
    return shapes + [pytest.param(json.dumps(obj).encode(), id=f"inner-{i}")
                     for i, obj in enumerate(inner)]


def _file(tmp_path, rel: str, data: bytes):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("data", _shapes([{"fp": None, "entries": None}, {"fp": 1, "entries": []},
                                          {"entries": {"h": None}}, {"entries": {"h": [[None] * 12]}}]))
def test_the_analysis_cache_reads_as_empty(tmp_path, data):
    assert not analyze.load_cache(_file(tmp_path, ".crapkit/cache.json", data)).get("entries")


@pytest.mark.parametrize("data", _shapes([{"v": 1, "stamps": None}, {"v": 1, "stamps": []},
                                          {"v": 1, "stamps": {"a.py": None}}, {"v": None}]))
def test_the_stat_stamps_read_as_empty(tmp_path, data):
    assert analyze._load_stamps(_file(tmp_path, ".crapkit/stat-stamps.json", data)) == {}


@pytest.mark.parametrize("data", _shapes([{"key": None, "files": None}]))
def test_the_churn_cache_reads_as_cold(tmp_path, data):
    assert churn_cache._read_cache(_file(tmp_path, ".crapkit/churn.json", data), {"head": "x"}) is None


@pytest.mark.parametrize("data", _shapes([{"key": None, "pairs": None}]))
def test_the_coupling_cache_reads_as_cold(tmp_path, data):
    path = _file(tmp_path, ".crapkit/coupling.json", data)

    assert coupling_cache._read_cache(path, {"head": "x"}) is None


@pytest.mark.parametrize("data", _shapes([{"head": None, "size": None, "crc": None}]))
def test_the_churn_log_key_names_no_head(tmp_path, data):
    key = churn_log._read_key(_file(tmp_path, ".crapkit/churn-log.json", data).with_suffix(".bin"))

    assert key is None or not any(key.values())


@pytest.mark.parametrize("data", _shapes([{"head": None, "size": None, "crc": None}]))
def test_the_churn_commits_key_line_names_no_key(data):
    assert churn_commits._key(data.replace(b"\n", b" "), 12) is None


@pytest.mark.parametrize("data", _shapes([{"a.json": None}, {"a.json": []},
                                          {"a.json": {"seconds": None, "commit": None, "proof": None,
                                                      "artifacts": None}},
                                          {"a.json": {"seconds": "5", "commit": 5, "lane": None,
                                                      "refused_mtime_ns": "x"}}]))
def test_an_artifact_stamp_reads_as_no_stamp(tmp_path, data):
    _file(tmp_path, ".crapkit/artifacts.json", data)

    stamps = lane_stamps.read(tmp_path)
    entry = stamps.entry("a.json")

    assert (lane_stamps._recorded_seconds(entry), stamps.commit("a.json"),
            stamps.refusal("a.json").kind) == (None, "", "")


@pytest.mark.parametrize("data", _shapes([{"version": None}, {"version": 1, "workers": None},
                                          {"workers": "2"}]))
def test_a_mutation_receipt_is_unproven_and_left_where_it_is(tmp_path, data):
    _file(tmp_path, ".crapkit/mutate-tmp/" + "a" * 32 + "/owner.json", data)

    recovered = mutate_pool.recover_temporary(tmp_path, dry_run=True)

    assert [r.status for r in recovered] == ["unproven"]
