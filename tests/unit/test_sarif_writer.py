"""The SARIF writer emits the document one finding at a time.

json.dump falls back to CPython's pure-Python encoder whenever indent is set,
so a 36,767-finding document was encoded a character at a time in Python. The
writer here hands each finding to the C encoder and splices the results into a
skeleton the C encoder produced too, which is why the test that matters is
against json.dumps of the very same document rather than against a fixture.
"""
import json

import pytest

from crapkit.sarif import over_target_results, sarif_document
from crapkit.sarifio import write_sarif
from crapkit.score import ScoredRow


def scored(i: int) -> ScoredRow:
    return ScoredRow("src", f"src/f{i}.ts", f"f{i}( x, y )", i, i + 8, 8, 8, 8,
                     5, 1, 1, 0.0, "measured", 72.0, "decompose")


def _results(n: int) -> list:
    return over_target_results([scored(i) for i in range(1, n + 1)], {"src": 6}, 6)


@pytest.mark.parametrize("n", [0, 1, 19, 5000])
def test_the_written_file_is_the_document_the_builder_returns(tmp_path, n):
    results = _results(n)
    out = tmp_path / "out.sarif"

    write_sarif(out, results)

    assert json.loads(out.read_text(encoding="utf-8")) == sarif_document(results)


@pytest.mark.parametrize("n", [0, 1, 19, 5000])
def test_the_bytes_are_the_c_encoders_own_bytes(tmp_path, n):
    """Splicing per-finding encodings into a skeleton must land exactly where a
    single json.dumps of the whole document would have."""
    results = _results(n)
    out = tmp_path / "out.sarif"

    write_sarif(out, results)

    whole = json.dumps(sarif_document(results), sort_keys=True)
    assert out.read_bytes() == whole.encode("utf-8")


def test_findings_keep_the_order_they_were_handed_in(tmp_path):
    results = _results(50)
    out = tmp_path / "out.sarif"
    write_sarif(out, results)
    written = json.loads(out.read_text(encoding="utf-8"))["runs"][0]["results"]
    assert [r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in written] \
        == [r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results]


def test_two_writes_of_the_same_findings_are_the_same_bytes(tmp_path):
    results = _results(200)
    first, second = tmp_path / "a.sarif", tmp_path / "b.sarif"
    write_sarif(first, results)
    write_sarif(second, results)
    assert first.read_bytes() == second.read_bytes()


def test_the_writer_does_not_inherit_the_hosts_line_separator(tmp_path):
    out = tmp_path / "out.sarif"
    write_sarif(out, _results(5))
    assert b"\r\n" not in out.read_bytes()
