"""A multi-megabyte member that fits inside the default window decodes in C.

A member that straddles the window's end falls into `_ValueFrame`, a Python
regex tokenizer that walks the member one structural token at a time, whatever
the member's size. With contexts recorded, one file's member reaches 1.5 MB,
and at a 1 MB window 9 of 81 members in crapkit's own report took that route:
half the file, parsed in Python. At 4 MB, 2 of them still do.
"""
import json
from unittest.mock import patch

from crapkit import coverage_py, covstream

MIB = 1 << 20


def _report_with_one_large_member(tmp_path):
    contexts = {str(line): [f"tests/unit/test_mod.py::test_case_{line}|run"]
                for line in range(1, 40000)}
    report = {"meta": {"branch_coverage": True}, "files": {"src/mod.py": {
        "missing_lines": [3, 5], "contexts": contexts}}}
    raw = json.dumps(report).encode("utf-8")
    path = tmp_path / "coverage.json"
    path.write_bytes(raw)
    return path, raw


def test_a_two_megabyte_member_decodes_whole_under_the_default_window(tmp_path):
    path, raw = _report_with_one_large_member(tmp_path)
    assert MIB < len(raw) < 3 * MIB, "the member must cross a 1 MB window and fit a 4 MB one"

    with patch.object(covstream, "_ValueFrame", wraps=covstream._ValueFrame) as frames:
        missing = coverage_py.parse_coveragepy_missing_file(path, path_prefix="")

    assert missing == {"src/mod.py": {3, 5}}
    assert frames.call_count == 0, "the member fit the window; the Python tokenizer never ran"


def test_the_default_window_reads_the_same_lines_a_small_window_does(tmp_path):
    path, _ = _report_with_one_large_member(tmp_path)

    whole = coverage_py.parse_coveragepy_contexts_file(path, path_prefix="",
                                                       source_path="src/mod.py")
    framed = coverage_py.parse_coveragepy_contexts_file(path, path_prefix="",
                                                        source_path="src/mod.py", chunk=MIB)

    assert whole == framed
    assert whole[39999] == ["tests/unit/test_mod.py::test_case_39999"]
