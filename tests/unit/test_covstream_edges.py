"""covstream at its edges: every value framed at every window size, in a window one chunk wide.

A value that straddles the window is framed token by token and handed to the C
decoder once it is whole, so the walk answers what json.loads answers at every
chunk size. When the walk hands out a member it has read at most one chunk past
that member's value. A value the window already holds whole, closed by its own
bracket at the window's edge, goes to the C decoder with no frame. A document
cut short says where, and one cut inside a character fails as a decode error.
"""
import io
import json

import pytest

from crapkit import covstream

DOCUMENT = {
    "meta": {"branch_coverage": True, "note": 'a "quoted" \\ back {brace} [bracket], comma'},
    "n": 1234567,
    "neg": -0.5e3,
    "t": True, "f": False, "z": None,
    "s": "plain words with spaces, and commas",
    "escaped": 'a\\"b' + "y" * 30,
    "brace": "open { brace",
    "nested": {"a": [1, [2, [3, {"b": "c}]"}]]], "d": {}},
    "e": [], "o": {},
    "tail": "x" * 40,
}
CHUNKS = [1, 2, 3, 5, 7, 11, 16, 64, 4096]


def window(data: bytes, chunk: int) -> covstream._Window:
    return covstream._Window(io.BytesIO(data), chunk)


def build(items) -> tuple[str, list[int]]:
    """An object's text from (key, value text) pairs, and where each value ends in it."""
    text, ends = "{", []
    for index, (key, value) in enumerate(items):
        text += (", " if index else "") + json.dumps(key) + ": " + value
        ends.append(len(text))
    return text + "}", ends


def failure(data: bytes, walk=covstream.split_window) -> str:
    with pytest.raises(ValueError) as caught:
        list(walk(window(data, 4096)))
    return str(caught.value)


def overreads(reach: list[int], ends: list[int], chunk: int) -> list[int]:
    """How far past a chunk beyond its value each member was handed out."""
    return [read - end for read, end in zip(reach, ends) if read > end + chunk]


def report() -> tuple[str, list, list[int]]:
    """A coverage.py report with a meta member longer than a small chunk ahead of its
    files, the items a walk hands out, and where each item's value ends."""
    meta, totals = {"note": "m" * 120}, {"n": 1}
    files = {f"f{index}.py": {"missing_lines": list(range(index * 7))} for index in range(8)}
    files_text, file_ends = build((key, json.dumps(value)) for key, value in files.items())
    text, ends = build([("meta", json.dumps(meta)), ("files", files_text), ("totals", json.dumps(totals))])
    base = ends[1] - len(files_text)
    items = [("meta", meta, "member"), *[(key, value, "sub") for key, value in files.items()],
             ("totals", totals, "member")]
    return text, items, [ends[0], *[base + end for end in file_ends], ends[2]]


@pytest.mark.parametrize("indent", [None, 1])
@pytest.mark.parametrize("chunk", CHUNKS)
def test_every_window_size_splits_the_document_as_json_loads_reads_it(chunk, indent):
    data = json.dumps(DOCUMENT, indent=indent).encode("utf-8")

    assert list(covstream.split_window(window(data, chunk))) == list(DOCUMENT.items())


@pytest.mark.parametrize("chunk", CHUNKS)
def test_the_split_reads_no_more_than_a_chunk_past_each_member(chunk):
    text, ends = build((key, json.dumps(value)) for key, value in DOCUMENT.items())
    handle = io.BytesIO(text.encode("utf-8"))
    reach = [handle.tell() for _ in covstream.split_window(covstream._Window(handle, chunk))]

    assert overreads(reach, ends, chunk) == []


@pytest.mark.parametrize("chunk", [1, 3, 8, 50])
def test_a_report_walk_reads_no_more_than_a_chunk_past_each_file(chunk):
    text, items, ends = report()
    handle = io.BytesIO(text.encode("utf-8"))
    walked, reach = [], []
    for item in covstream.walk_report(covstream._Window(handle, chunk), "files"):
        walked.append(item)
        reach.append(handle.tell())

    assert walked == items
    assert overreads(reach, ends, chunk) == []


def test_a_value_closed_at_the_windows_edge_is_decoded_without_a_frame(monkeypatch):
    data = b'{"a": {"b": 1}, "c": 2}'
    frames, real = [], covstream._ValueFrame
    monkeypatch.setattr(covstream, "_ValueFrame", lambda text, start: frames.append(start) or real(text, start))

    split = list(covstream.split_window(window(data, data.index(b"}") + 1)))

    assert (split, frames) == ([("a", {"b": 1}), ("c", 2)], [])


def test_a_cut_document_says_where_and_a_cut_character_fails_to_decode():
    def walk(w):
        return covstream.walk_report(w, "files")

    assert failure(b'{"a": 1} ' + b"x" * 100) == "unexpected content at " + repr("} " + "x" * 78)
    assert failure(b'{"a": 1, "b": ') == "unexpected content at " + repr(', "b": ')
    assert failure(b"[1]") == "istanbul artifact is not a JSON object"
    assert [failure(data, walk) for data in (b"[1]", b'{"files": [1]}', b'{"files": {"a": 1')] == [
        "coverage.py report is not a JSON object", "coverage.py report: 'files' is not a JSON object",
        "unterminated coverage.py report: 'files' object"]
    with pytest.raises(UnicodeDecodeError):
        list(covstream.split_window(window('{"a": "é'.encode("utf-8")[:-1], 4096)))


def test_a_window_reads_one_byte_per_refill_at_chunk_zero_and_compacts_after_a_chunk():
    small, compacted = window(b"abcdef", 0), window(b"abcdef", 2)
    small.refill()
    compacted.refill()
    compacted.refill()
    compacted.drop(2)

    assert (small.buf, compacted.buf, compacted.pos) == ("a", "cd", 0)
