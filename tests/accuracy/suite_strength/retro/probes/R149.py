"""R149: the override read crapkit-ratchet.tsv as strict UTF-8 while every other
reader of the file decodes utf-8-sig, so a marks file PowerShell 5.1 saved
(a byte-order mark in front) keyed its first mark with the BOM.

    <retro venv python> R149.py WORKTREE

verdict_model's check runs verify --override on a BOM file, and at the fix
commit verify's own stamp read still refused that file, a later fix. This
probe asks only this bug's question, of the function the fix changed:
crapkit.override._marks_by_key, which both commits have. Handed a marks file
that starts with a BOM, it must key the mark by the path the file names. A
read that raises instead is a wrong answer too, since the file is one every
other reader accepts.
"""
# source: Unicode 15.0 section 23.8: U+FEFF at the start of a UTF-8 stream is a byte-order mark, not part of the text, which Python's utf-8-sig codec drops; the marks line below names src/a.ts
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

MARKS = b"\xef\xbb\xbf" + b"src/a.ts\tf( )\t12.0\n"


def main(argv: list[str]) -> int:
    from crapkit.override import _marks_by_key

    with tempfile.TemporaryDirectory(prefix="crapkit-r149-") as scratch:
        path = Path(scratch) / "crapkit-ratchet.tsv"
        path.write_bytes(MARKS)
        try:
            keys = sorted(_marks_by_key(path))
        except ValueError as refused:
            keys = [f"ValueError: {refused}"]
    assert keys == [("src/a.ts", "f( )")], f"the override keyed the marks {keys!a}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
