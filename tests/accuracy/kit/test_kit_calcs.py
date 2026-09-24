"""kit.calcs: every packet's calcs.tsv read as one table."""
import pytest

from accuracy.kit import calcs

HEADER = "\t".join(calcs.COLUMNS)


def _packet(root, name: str, rows: list[str]):
    (root / name).mkdir()
    (root / name / "calcs.tsv").write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")


def test_rows_load_with_their_packet_and_split_lists(tmp_path):
    _packet(tmp_path, "score_model", [
        "CRAP score\ttests/accuracy/score_model/test_crap.py::test_grid\t"
        "src/crapkit/score.py\tsrc/crapkit/score.py:crap, src/crapkit/score.py:grade"])

    [row] = calcs.load(tmp_path)

    assert (row.packet, row.calc, row.modules) == ("score_model", "CRAP score",
                                                   ("src/crapkit/score.py",))
    assert row.functions == ("src/crapkit/score.py:crap", "src/crapkit/score.py:grade")


def test_modules_union_and_touched_calcs(tmp_path):
    _packet(tmp_path, "a", ["X\tt::a\tsrc/crapkit/score.py\tsrc/crapkit/score.py:crap"])
    _packet(tmp_path, "b", ["Y\tt::b\tsrc/crapkit/digest.py, src/crapkit/score.py\t"
                            "src/crapkit/digest.py:totals"])
    rows = calcs.load(tmp_path)

    assert calcs.modules(rows) == ["src/crapkit/digest.py", "src/crapkit/score.py"]
    assert [row.calc for row in calcs.touched(rows, ["src/crapkit/digest.py"])] == ["Y"]


def test_a_calc_named_by_two_packets_is_refused(tmp_path):
    _packet(tmp_path, "a", ["X\tt::a\tm.py\tm.py:f"])
    _packet(tmp_path, "b", ["X\tt::b\tm.py\tm.py:f"])

    with pytest.raises(calcs.CalcsError, match="calc 'X' is named by a and b"):
        calcs.load(tmp_path)


@pytest.mark.parametrize("text, message", [
    ("calc\ttest\n", "the header must be"),
    (HEADER + "\nX\tt::a\tm.py\n", "a row needs all of"),
    (HEADER + "\nX\tt::a\t\tm.py:f\n", "a row needs all of"),
])
def test_a_malformed_table_is_refused(tmp_path, text, message):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "calcs.tsv").write_text(text, encoding="utf-8")

    with pytest.raises(calcs.CalcsError, match=message):
        calcs.load(tmp_path)


def test_the_tree_s_calcs_load():
    assert isinstance(calcs.load(), list)
