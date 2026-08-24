"""The gate analyzes staged blobs in memory; the records must not move.

The blobs are already in hand from `git cat-file --batch`, so writing them to a
temp tree only to have lizard read them back is a round trip through the disk.
Handing lizard the text directly skips it — but ONLY if the bytes are decoded
the way lizard's own auto_read decodes them, BOM and line endings included.

A naive `blob.decode()` is what makes this dangerous rather than merely wrong: a
CR-only file (classic Mac endings, and what a botched filter leaves behind)
reads as one enormous line, lizard finds no functions in it at all, and the gate
passes a file it never judged. Every case below is checked against the shipped
temp-tree path, which is the definition of the right answer.
"""
import codecs
import tempfile
from pathlib import Path

import pytest

from crapkit.analyze import analyze_jobs
from crapkit.hook import staged_records

TANGLED = ("def alpha(n):\n"
           "    if n > 1:\n"
           "        n = n + 1\n"
           "    if n > 2:\n"
           "        n = n + 1\n"
           "    return n\n")

TS = ("export function beta(n: number) {\n"
      "  if (n > 1) { n += 1; }\n"
      "  return n;\n"
      "}\n")

CASES = {
    "src/lf.py": TANGLED.encode("utf-8"),
    "src/crlf.py": TANGLED.replace("\n", "\r\n").encode("utf-8"),
    "src/cr_only.py": TANGLED.replace("\n", "\r").encode("utf-8"),
    "src/bom.py": codecs.BOM_UTF8 + TANGLED.encode("utf-8"),
    "src/bom_crlf.py": codecs.BOM_UTF8 + TANGLED.replace("\n", "\r\n").encode("utf-8"),
    # a byte no UTF-8 decoder accepts, inside a name the record carries
    "src/latin1.py": ("def café(n):\n    return n\n").encode("cp1252"),
    # 0x81 is undefined in cp1252 too, so both decoders have to give up together
    "src/undecodable.py": b"# \x81\n" + TANGLED.encode("utf-8"),
    "src/crlf.ts": TS.replace("\n", "\r\n").encode("utf-8"),
    "src/empty.py": b"",
}


def temp_tree_records(rel: str, blob: bytes) -> list:
    """The shipped path: the blob written under its repo-relative name, read by lizard."""
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / rel
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(blob)
        return analyze_jobs([(str(staged), rel)], pool_threshold=99)[rel]


@pytest.mark.parametrize("rel", sorted(CASES))
def test_blob_records_match_the_temp_tree_path(rel: str):
    assert staged_records({rel: CASES[rel]})[rel] == temp_tree_records(rel, CASES[rel])


def test_a_cr_only_file_still_shows_its_functions():
    """The trap, stated as the consequence: decoded naively this file has no
    functions, so the gate would wave through anything written in it."""
    records = staged_records({"src/cr_only.py": CASES["src/cr_only.py"]})["src/cr_only.py"]

    assert [r.long_name for r in records] == ["alpha( n )"]
    assert records[0].ccn == 3


def test_every_staged_blob_is_analyzed_once_under_its_own_path():
    records = staged_records({rel: CASES[rel] for rel in ("src/lf.py", "src/crlf.ts")})

    assert sorted(records) == ["src/crlf.ts", "src/lf.py"]
    assert [r.path for r in records["src/lf.py"]] == ["src/lf.py"]
