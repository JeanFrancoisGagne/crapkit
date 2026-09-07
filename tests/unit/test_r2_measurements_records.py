"""Portable records preserve repository identities, including delimiters."""
import pytest

from crapkit.ratchet import RatchetEntry, dump_ratchet, load_ratchet, read_key_version, read_ratchet, read_stamp
from crapkit.score import parse_scored_tsv, scored_tsv_lines
from crapkit.snapshot import InventoryRow, tsv_lines
from crapkit.verify import baseline_tsv_lines, parse_baseline_tsv
from test_portable_baseline import ROW

SEPARATORS = list("\t\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
PATHS = ["src/a" + char + "b.py" for char in SEPARATORS] + ["#source.py", r"src/a\b.py"]


@pytest.mark.parametrize("path", PATHS)
def test_scored_and_portable_records_roundtrip_exact_fields(path):
    row = ROW._replace(path=path, long_name="f( a\tb\nc )")
    text = "".join(scored_tsv_lines([row]))
    assert parse_scored_tsv(text) == [row]
    portable = "".join(baseline_tsv_lines("abc", "coverage", [row]))
    assert parse_baseline_tsv(portable).rows == [row]
    assert "".join(scored_tsv_lines(parse_scored_tsv(text))) == text


@pytest.mark.parametrize("path", PATHS)
def test_ratchet_roundtrip_preserves_stamps_and_identity(path):
    entries = [RatchetEntry(path, "f( a\tb\nc )", 12.5)]
    text = dump_ratchet(entries, stamp="crapkit-analysis=10 lizard=1.24.0", key_version=1)
    assert load_ratchet(text) == entries
    assert read_key_version(text) == 1
    assert read_stamp(text) == "crapkit-analysis=10 lizard=1.24.0"
    assert dump_ratchet(load_ratchet(text), stamp=read_stamp(text), key_version=1) == text


@pytest.mark.parametrize("path", ["#source.py", "src/a\u2028b.py", "src/a\x85b.py", r"src/a\b.py"])
def test_legacy_raw_identity_is_not_a_comment_or_a_physical_line(path):
    assert load_ratchet(path + "\tf( )\t12.5000\r\n") == [RatchetEntry(path, "f( )", 12.5)]
    assert read_stamp(path + "\tf( )\t12.5000\n") == ""


def test_stamped_encoded_row_can_be_read_alone_in_a_git_patch():
    entry = RatchetEntry("#a\nb.py", "f( )", 12.5)
    row = dump_ratchet([entry], stamp="").split("\n")[1]
    assert load_ratchet(row) == [entry]


@pytest.mark.parametrize("line", [
    '@crapkit-record-v2\t["a", "f", "12"]',
    '@crapkit-record-v1\tnot-json',
    '@crapkit-record-v1\t{"a": "b"}',
    '@crapkit-record-v1\t["a", "f", 12]',
])
def test_malformed_or_unknown_record_encoding_is_a_clean_refusal(line):
    with pytest.raises(ValueError):
        load_ratchet(line)
    entries, complaints = read_ratchet(line)
    assert entries == [] and len(complaints) == 1
    with pytest.raises(ValueError):
        parse_scored_tsv(line)


def test_inventory_uses_the_same_lossless_record_encoding():
    row = InventoryRow("src", "src/a\nb.py", "f( )", 1, 2, 1, 1, 1, 2, 0, 0)
    from crapkit.records import decode_record, record_lines

    lines = list(record_lines("".join(tsv_lines([row]))))
    assert decode_record(lines[1]) == list(map(str, row))


def test_valid_legacy_marker_named_path_is_not_reinterpreted():
    entry = RatchetEntry("@crapkit-record-v1", "f( )", 12.5)
    assert load_ratchet("@crapkit-record-v1\tf( )\t12.5000\n") == [entry]
