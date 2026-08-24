"""The baseline as a committable file: stamp line, then the scored export.

A fresh clone's .crapkit/ is gitignored, so the only baseline it can have is one
that lives in the repo. The codec is a determinism contract — emit, read, emit
again must produce the same bytes, or the file cannot be diffed or trusted.
"""
import pytest

from crapkit.score import ScoredRow, parse_scored_tsv, scored_tsv_lines
from crapkit.verify import baseline_tsv_lines, parse_baseline_tsv

ROW = ScoredRow("src", "src/a.ts", "f( )", 1, 9, 8, 8, 8, 5, 1, 1, 0.0, "measured", 72.0, "decompose", 3)
OTHER = ScoredRow("py", "pylib/b.py", "g( )", 4, 6, 2, 2, 2, 3, 0, 0, 1.0, "measured", 2.0, "ok", 0)

HEADER = ("scope\tpath\tlong_name\tstart\tend\tccn_std\tccn_mod\tccn\tnloc\tparams\t"
          "nesting\tcov\tflag\tcrap\tremedy\tcognitive\n")
ROW_LINE = "src\tsrc/a.ts\tf( )\t1\t9\t8\t8\t8\t5\t1\t1\t0.0\tmeasured\t72.0\tdecompose\t3\n"
DOC = "# commit=abc1234def run_kind=coverage\n" + HEADER + ROW_LINE


def test_a_baseline_file_is_a_commit_stamp_then_the_scored_export():
    assert "".join(baseline_tsv_lines("abc1234def", "coverage", [ROW])) == DOC


def test_reading_a_baseline_back_gives_the_stamp_and_the_rows():
    parsed = parse_baseline_tsv(DOC)

    assert parsed.commit == "abc1234def"
    assert parsed.kind == "coverage"
    assert parsed.rows == [ROW]


def test_emit_read_emit_is_byte_stable():
    once = "".join(baseline_tsv_lines("abc1234def", "verify", [ROW, OTHER]))
    parsed = parse_baseline_tsv(once)

    assert "".join(baseline_tsv_lines(parsed.commit, parsed.kind, parsed.rows)) == once


def test_a_baseline_of_no_rows_keeps_the_empty_header_line():
    doc = "".join(baseline_tsv_lines("abc1234def", "coverage", []))

    assert doc == "# commit=abc1234def run_kind=coverage\n\n"
    assert parse_baseline_tsv(doc).rows == []


def test_a_file_with_no_stamp_line_is_refused():
    with pytest.raises(ValueError) as exc:
        parse_baseline_tsv(HEADER + ROW_LINE)

    assert "commit=" in str(exc.value)


def test_a_stamp_missing_the_run_kind_is_refused():
    with pytest.raises(ValueError) as exc:
        parse_baseline_tsv("# commit=abc1234def\n" + HEADER)

    assert "run_kind" in str(exc.value)


def test_a_truncated_row_is_refused_rather_than_read_short():
    with pytest.raises(ValueError) as exc:
        parse_baseline_tsv("# commit=abc1234def run_kind=coverage\n" + HEADER + "src\tsrc/a.ts\tf( )\n")

    assert "3 fields" in str(exc.value)


def test_the_row_serializer_is_the_one_the_scored_export_uses():
    assert "".join(scored_tsv_lines([ROW])) == HEADER + ROW_LINE
    assert parse_scored_tsv(HEADER + ROW_LINE) == [ROW]
