"""Export writers stream instead of building the whole document.

The exports are a determinism contract, so each test here pins the new file's
bytes against the list-and-join implementation it replaced. LF endings are part
of that contract: the writers must not inherit the host's line separator.
"""
import json

from crapkit.cli import _emit_findings, _export_scored, _write_tsv
from crapkit.merge import FunctionRecord
from crapkit.sarif import over_target_results, sarif_document
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow, build_inventory_rows, tsv_lines


def rec(i: int) -> FunctionRecord:
    return FunctionRecord(f"src/f{i}.ts", f"f{i}( x )", i, i + 8, i + 1, i, i, 8, 1, 2)


def scored(i: int) -> ScoredRow:
    return ScoredRow("src", f"src/f{i}.ts", f"f{i}( )", i, i + 8, 8, 8, 8,
                     5, 1, 1, 0.0, "measured", 72.0, "decompose")


def _legacy_inventory_tsv(rows) -> str:
    """snapshot.export_tsv as it stood before the streaming rewrite."""
    lines = ["\t".join(InventoryRow._fields)]
    for r in rows:
        lines.append("\t".join(str(v) for v in r))
    return "\n".join(lines) + "\n"


def _legacy_scored_tsv(rows) -> str:
    """cli._export_scored as it stood before the streaming rewrite."""
    header = "\t".join(rows[0]._fields) if rows else ""
    lines = [header] + ["\t".join(str(v) for v in r) for r in rows]
    return "\n".join(lines) + "\n"


def test_inventory_export_bytes_match_the_list_and_join_version(tmp_path):
    rows = build_inventory_rows({"src": [rec(i) for i in range(1, 40)]})
    out = tmp_path / "inv.tsv"
    _write_tsv(out, tsv_lines(rows))
    assert out.read_bytes() == _legacy_inventory_tsv(rows).encode("utf-8")


def test_inventory_export_of_no_rows_is_a_bare_header(tmp_path):
    out = tmp_path / "inv.tsv"
    _write_tsv(out, tsv_lines([]))
    assert out.read_bytes() == _legacy_inventory_tsv([]).encode("utf-8")


def test_exports_write_lf_on_every_host(tmp_path):
    out = tmp_path / "inv.tsv"
    _write_tsv(out, tsv_lines(build_inventory_rows({"src": [rec(1)]})))
    assert b"\r\n" not in out.read_bytes(), "an export must not carry the host's line separator"


def test_tsv_lines_streams_one_row_at_a_time():
    consumed = []

    def rows():
        for i in range(1, 4):
            consumed.append(i)
            yield InventoryRow("src", f"src/f{i}.ts", "f( )", i, i + 1, 7, 5, 5, 8, 1, 2)

    lines = tsv_lines(rows())
    assert next(lines).startswith("scope\t")
    assert consumed == [], "the header must not pull a single row"
    next(lines)
    assert consumed == [1], "one row in, one line out; the document is never a list"


def test_scored_export_bytes_match_the_list_and_join_version(tmp_path):
    rows = [scored(i) for i in range(1, 40)]
    _export_scored(tmp_path, "scored.tsv", rows)
    assert (tmp_path / "scored.tsv").read_bytes() == _legacy_scored_tsv(rows).encode("utf-8")


def test_scored_export_of_no_rows_keeps_its_empty_header_line(tmp_path):
    _export_scored(tmp_path, "scored.tsv", [])
    assert (tmp_path / "scored.tsv").read_bytes() == b"\n"


def test_sarif_file_bytes_are_the_c_encoders_own_bytes(tmp_path):
    """indent=1 forced json.dump onto the pure-Python encoder, which cost 718 ms
    on 36,767 findings. The document is compact now: the same document decoded,
    written by the C encoder, and still deterministic."""
    results = over_target_results([scored(i) for i in range(1, 20)], {"src": 6}, 6)
    _emit_findings(tmp_path, "out.sarif", False, results)
    expected = json.dumps(sarif_document(results), sort_keys=True)
    assert (tmp_path / "out.sarif").read_bytes() == expected.encode("utf-8")


def test_sarif_file_decodes_to_the_document_the_builder_returns(tmp_path):
    results = over_target_results([scored(i) for i in range(1, 20)], {"src": 6}, 6)
    _emit_findings(tmp_path, "out.sarif", False, results)
    doc = json.loads((tmp_path / "out.sarif").read_text(encoding="utf-8"))
    assert doc == sarif_document(results)


def test_sarif_file_stays_parseable_and_carries_every_result(tmp_path):
    results = over_target_results([scored(i) for i in range(1, 20)], {"src": 6}, 6)
    _emit_findings(tmp_path, "out.sarif", False, results)
    doc = json.loads((tmp_path / "out.sarif").read_text(encoding="utf-8"))
    assert len(doc["runs"][0]["results"]) == len(results) == 19


# --- the scored export's column contract -----------------------------------
# A fixed format template is faster than a per-row str() genexp (179 -> 116 ms
# on 140,922 rows) and can silently emit the wrong number of columns, which the
# genexp could not. These pin the arity and the round trip that depends on it.

def _awkward_rows():
    """Values that print differently than they look: repeating and exponent
    floats, a negative, a zero, and a name carrying non-ASCII."""
    from crapkit.score import ScoredRow
    return [
        ScoredRow("src", "src/a.ts", "f( x )", 1, 9, 8, 8, 8, 5, 1, 1,
                  1 / 3, "measured", 8.0 + 1e-05, "add-tests", 12),
        ScoredRow("ui", "ui/b.ts", "ünïcode( ☃ )", 40, 40, 1, 1, 1, 0, 0, -1,
                  1.0, "measured", 1.0, "ok", 0),
        ScoredRow("py", "scripts/c.py", "(anonymous)", 7, 700, 99, 99, 99, 500, 12, 9,
                  0.0, "no-lane", 970398.0, "decompose", 0),
    ]


def test_the_scored_export_emits_one_column_per_field():
    from crapkit.score import ScoredRow, scored_tsv_lines

    lines = list(scored_tsv_lines(_awkward_rows()))
    assert lines[0].rstrip("\n").split("\t") == list(ScoredRow._fields)
    for line in lines[1:]:
        assert len(line.rstrip("\n").split("\t")) == len(ScoredRow._fields)


def test_the_scored_export_round_trips_through_its_own_parser():
    from crapkit.score import parse_scored_tsv, scored_tsv_lines

    rows = _awkward_rows()
    text = "".join(scored_tsv_lines(rows))
    assert parse_scored_tsv(text) == rows
    assert "".join(scored_tsv_lines(parse_scored_tsv(text))) == text


def test_the_scored_export_bytes_do_not_move_for_awkward_values(tmp_path):
    from crapkit.score import scored_tsv_lines

    rows = _awkward_rows()
    _write_tsv(tmp_path / "scored.tsv", scored_tsv_lines(rows))
    assert (tmp_path / "scored.tsv").read_bytes() == _legacy_scored_tsv(rows).encode("utf-8")
