"""A large member needs at most one bounded prefix decode before completion."""
import hashlib
import json
from unittest.mock import patch

import pytest

from crapkit import covstream
from crapkit.errors import ToolError


def test_large_coverage_member_has_only_one_bounded_prefix_attempt(tmp_path):
    report = {"meta": {"branch_coverage": True}, "files": {"a.py": {
        "missing_lines": [2], "contexts": {"2": ["escaped \\\" } [ \u2603" * 40000]},
        "functions": {"f": {"start_line": 1, "executed_lines": [1], "missing_lines": [2],
                              "summary": {"num_branches": 2, "covered_branches": 1,
                                          "num_statements": 2, "covered_lines": 1}}}}}}
    raw = json.dumps(report).encode()
    path = tmp_path / "coverage.json"
    path.write_bytes(raw)
    with patch.object(covstream, "_DECODER", wraps=covstream._DECODER) as decoder:
        coverage, missing, digest = covstream.parse_coveragepy_both_file(path, path_prefix="", chunk=65536)
    # One complete meta decode; one bounded attempt and one final file decode.
    assert decoder.raw_decode.call_count == 3
    assert missing == {"a.py": {2}}
    assert coverage["a.py"][0].coverage == 0.5
    assert digest == hashlib.sha256(raw).hexdigest()


def test_complete_members_keep_the_direct_decode_fast_path(tmp_path):
    path = tmp_path / "coverage.json"
    path.write_text('{"files":{"a.py":{"missing_lines":[7]}}}', encoding="utf-8")
    with patch.object(covstream, "_ValueFrame", wraps=covstream._ValueFrame) as frames:
        assert covstream.parse_coveragepy_missing_file(path, path_prefix="") == {"a.py": {7}}
    assert frames.call_count == 0


@pytest.mark.parametrize("value", ['"end\\\\"', '["\\\"", {"x": [1, true, null, -2.4e-8]}]',
                                  '12.5e+12', 'true', 'null'])
@pytest.mark.parametrize("chunk", [1, 2, 7])
def test_ignored_members_keep_json_framing_and_digest(tmp_path, value, chunk):
    raw = ('{"note":' + value + ',"files":{"a.py":{"missing_lines":[7]}}}').encode()
    path = tmp_path / "coverage.json"
    path.write_bytes(raw)
    assert covstream.parse_coveragepy_missing_file(path, path_prefix="", chunk=chunk) == {"a.py": {7}}


@pytest.mark.parametrize("value", ['[1,]', '{"x":1,}', '"unfinished', '[1}', '01', '1e',
                                  'Infinity', 'NaN', '1e9999'])
def test_framing_never_admits_invalid_json(tmp_path, value):
    path = tmp_path / "coverage.json"
    path.write_text('{"note":' + value + ',"files":{"a.py":{"missing_lines":[]}}}', encoding="utf-8")
    with pytest.raises(ToolError):
        covstream.parse_coveragepy_missing_file(path, path_prefix="", chunk=2)
