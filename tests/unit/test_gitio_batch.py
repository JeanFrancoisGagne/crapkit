"""`git cat-file --batch` framing: records are sliced by the declared byte count.

Pure seam. Splitting the stream on newlines instead would hand the gate the
wrong bytes for the next file, and a path missing from the index arrives as a
record, not as a nonzero exit.
"""
import pytest

from crapkit.errors import GitError
from crapkit.gitio import _framed_blob


def record(oid: str, body: bytes) -> bytes:
    return f"{oid} blob {len(body)}\n".encode("utf-8") + body + b"\n"


def test_records_slice_by_declared_length_not_by_newlines():
    first_body = b"export function a() {\n  return 1;\n}\n"
    second_body = b"\x00\xffbinary, no trailing newline"
    stream = record("a" * 40, first_body) + record("b" * 40, second_body)

    first, pos = _framed_blob(stream, 0)
    second, end = _framed_blob(stream, pos)

    assert first == first_body
    assert second == second_body
    assert end == len(stream)


def test_an_empty_blob_is_a_record_not_a_gap():
    stream = record("c" * 40, b"") + record("d" * 40, b"x")

    empty, pos = _framed_blob(stream, 0)

    assert empty == b""
    assert _framed_blob(stream, pos)[0] == b"x"


def test_a_path_absent_from_the_index_raises_instead_of_shifting_every_later_frame():
    with pytest.raises(GitError) as exc:
        _framed_blob(b":src/gone.ts missing\n", 0)

    assert ":src/gone.ts" in str(exc.value)
