"""The MCP server reads each stdin frame's bytes leniently, one frame at a time.

The stdio loop reads the descriptor itself, past the UTF-8 reconfigure the CLI
gives sys.stdin, and it decoded each frame as strict UTF-8 inside the loop
that posts end of input on a ValueError. One frame holding a byte that is not
UTF-8 (a client writing its ANSI code page) ended the session in silence:
exit 0, nothing on stderr, every later request unanswered. A UTF-8 BOM in
front of the first frame made `initialize` go unanswered. A frame now decodes
through textcodec.lenient: the BOM is dropped, a stray byte reads as U+FFFD,
and a frame that is still not JSON gets no reply while the session reads on.
"""
import io
import json
import os
import sys

import pytest

from crapkit.mcp_server import serve


def _ping(msg_id: int, note: bytes = b"x") -> bytes:
    return b'{"jsonrpc":"2.0","id":%d,"method":"ping","params":{"note":"%s"}}' % (msg_id, note)


FRAMES = [
    # id, the frame's bytes, the id it must be answered under (None: no reply)
    ("invalid-utf8-inside-json-string", _ping(2, b"caf\xe9"), 2),
    ("invalid-utf8-junk-line", b"\xff\xfe junk", None),
    ("utf8-bom-before-first-request", b"\xef\xbb\xbf" + _ping(2), 2),
    ("crlf-line-endings", _ping(2) + b"\r", 2),
    ("valid-non-ascii-arguments", _ping(2, "café 渡辺 \U0001f680".encode()), 2),
    ("junk-ascii-line-control", b"junk", None),
]


def _serve_bytes(monkeypatch, tmp_path, frames: list[bytes]) -> dict:
    """Frames through a real pipe, so the loop takes its descriptor branch."""
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 6\n", encoding="utf-8")
    read_end, write_end = os.pipe()
    os.write(write_end, b"".join(frame + b"\n" for frame in frames))
    os.close(write_end)
    out = io.StringIO()
    with os.fdopen(read_end, "rb") as source:
        monkeypatch.setattr(sys, "stdin", source)
        monkeypatch.setattr(sys, "stdout", out)
        assert serve(tmp_path) == 0
    return {m["id"]: m for m in map(json.loads, out.getvalue().splitlines())}


@pytest.mark.parametrize("frame, answered", [row[1:] for row in FRAMES], ids=[row[0] for row in FRAMES])
def test_a_frame_in_any_bytes_leaves_the_session_reading(monkeypatch, tmp_path, frame, answered):
    replies = _serve_bytes(monkeypatch, tmp_path, [frame, _ping(9)])

    assert set(replies) == {9} | ({answered} if answered else set())
    assert all(reply["result"] == {} for reply in replies.values())
