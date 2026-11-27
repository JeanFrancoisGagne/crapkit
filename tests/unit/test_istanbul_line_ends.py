"""An istanbul record lands on the lines the reader numbers, whatever rule its producer
numbered by.

Each case in tests/fixtures/recorded/vitest_line_ends.json is what vitest 4.1.10 wrote
to coverage-final.json for the source beside it, with `meta` and `path` dropped.
@vitest/coverage-v8 numbers a JavaScript file at LF only. A TypeScript file's
positions come back through its source map, and Babel (what @vitest/coverage-istanbul
instruments with) numbers the source itself; both end a line at every ECMAScript line
terminator: LF, CRLF, a lone CR, U+2028 and U+2029. The reader ends one at LF, CRLF and
a lone CR. In every source `f` runs both ways and `g` never runs.
"""
import json
import os
from pathlib import Path

import pytest

from crapkit import coverage_istanbul, istanbul_lines

CASES = json.loads((Path(__file__).parent.parent / "fixtures/recorded/vitest_line_ends.json")
                   .read_text(encoding="utf-8"))


def _parse(tmp_path, file: str, source: str, record: dict):
    """The record, parsed the way a lane reads it, with its source in the checkout."""
    (tmp_path / file).write_bytes(source.encode("utf-8"))
    artifact = tmp_path / "coverage-final.json"
    artifact.write_text(json.dumps({str(tmp_path / file): record}), encoding="utf-8")
    per_file, evidence, _ = coverage_istanbul.parse_istanbul_both_file(artifact,
                                                                      repo_root=str(tmp_path))
    missing = coverage_istanbul.parse_istanbul_missing_file(artifact, repo_root=str(tmp_path))
    assert evidence[file].hit_lines is None
    return per_file[file], set(evidence[file].missed_lines), missing[file]


def _spans(rows):
    return [(fn.name, fn.start, fn.end, fn.branches_total, fn.branches_covered) for fn in rows]


# (case, reader spans of f and g with their branches, reader lines no statement ran)
ON_READER_LINES = [
    # Five lone CRs in a comment: V8 counts one line where the reader counts six.
    ("v8_js_lone_cr", [("f", 7, 12, 2, 2), ("g", 14, 19, 2, 0)], {15, 16, 18}),
    # CR-only line ends: the whole file is line 1 to V8, and only columns tell f from g.
    ("v8_js_cr_only", [("f", 2, 7, 2, 2), ("g", 9, 14, 2, 0)], {10, 11, 13}),
    # Five U+2028 in a string: the source map counts six lines where the reader counts one.
    ("v8_ts_ls_string", [("f", 2, 7, 2, 2), ("g", 9, 14, 2, 0)], {10, 11, 13}),
    # The same source under Babel. This producer's record holds no branch and ends g on
    # its first line; those are its numbers, placed where the reader has them.
    ("istanbul_js_ls_string", [("f", 2, 7, 0, 0), ("g", 9, 9, 0, 0)], set()),
    # Numbered as the reader numbers it already: nothing moves.
    ("v8_js_ls_string", [("f", 2, 7, 2, 2), ("g", 9, 14, 2, 0)], {10, 11, 13}),
    ("istanbul_js_lone_cr", [("f", 7, 12, 0, 0), ("g", 14, 14, 0, 0)], set()),
]


@pytest.mark.parametrize(("case", "spans", "dead"), ON_READER_LINES,
                         ids=[case for case, _, _ in ON_READER_LINES])
def test_each_function_and_dead_line_lands_on_the_readers_lines(tmp_path, case, spans, dead):
    fixture = CASES[case]
    rows, both_dead, missing = _parse(tmp_path, fixture["file"], fixture["source"], fixture["record"])

    assert _spans(rows) == spans
    assert both_dead == missing == dead


def test_a_record_whose_source_is_missing_passes_through(tmp_path):
    fixture = CASES["v8_js_lone_cr"]
    artifact = tmp_path / "coverage-final.json"
    artifact.write_text(json.dumps({str(tmp_path / "gone.js"): fixture["record"]}), encoding="utf-8")

    per_file, _, _ = coverage_istanbul.parse_istanbul_both_file(artifact, repo_root=str(tmp_path))

    assert [(fn.name, fn.start, fn.end) for fn in per_file["gone.js"]] == [("f", 2, 7), ("g", 9, 14)]


def test_a_record_with_no_name_to_place_passes_through(tmp_path):
    """No named function, no evidence of the producer's rule: the record stays as it came."""
    fixture = CASES["v8_js_lone_cr"]
    record = json.loads(json.dumps(fixture["record"]))
    for index, fn in enumerate(record["fnMap"].values()):
        fn["name"] = f"(anonymous_{index})"

    rows, _, _ = _parse(tmp_path, fixture["file"], fixture["source"], record)

    assert [(fn.start, fn.end) for fn in rows] == [(2, 7), (9, 14)]


def test_a_crlf_file_is_read_as_it_came(tmp_path):
    """CRLF ends one line under every rule, so a CRLF file's record is never renumbered."""
    fixture = CASES["v8_js_lone_cr"]
    source = fixture["source"].replace("\r", " ").replace("\n", "\r\n")

    rows, _, _ = _parse(tmp_path, fixture["file"], source, fixture["record"])

    assert [(fn.start, fn.end) for fn in rows] == [(2, 7), (9, 14)]


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_a_column_counts_utf16_code_units(tmp_path):
    """JavaScript counts a column in UTF-16 code units, so each emoji above a function on
    V8's one line of a CR-only file is two units and one character. Read as characters,
    30 of them put `f` on its last line, 4, where it starts on 2."""
    head = "export const s = '" + "\U0001F600" * 30 + "';\r"
    source = head + "export function f(a) {\r  return a\r}\r"
    at = _utf16(head + "export function ")
    record = {"fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1, "column": at}},
                              "loc": {"end": {"line": 1, "column": _utf16(source) - 1}}}},
              "f": {"0": 1}, "statementMap": {}, "s": {}, "branchMap": {}, "b": {}}

    rows, _, _ = _parse(tmp_path, "m.js", source, record)

    assert [(fn.name, fn.start, fn.end) for fn in rows] == [("f", 2, 4)]


def test_a_malformed_member_is_left_for_the_parser_to_refuse(tmp_path):
    """A function with no declaration and a branch map that is no map move nothing
    and raise nothing here: refusing them is the parser's job, with its own message.
    The well-formed function still lands on the reader's lines."""
    fixture = CASES["v8_js_lone_cr"]
    (tmp_path / "m.js").write_bytes(fixture["source"].encode("utf-8"))
    record = json.loads(json.dumps(fixture["record"]))
    record["fnMap"]["2"] = {"name": "h", "decl": None}
    record["branchMap"] = []

    istanbul_lines.on_reader_lines(record, tmp_path / "m.js")

    assert record["fnMap"]["0"]["decl"]["start"]["line"] == 7
    assert record["fnMap"]["2"] == {"name": "h", "decl": None}


def test_a_record_that_is_no_object_comes_back_as_it_came(tmp_path):
    assert istanbul_lines.on_reader_lines([], tmp_path / "m.js") == []


def test_a_directory_reads_as_no_source(tmp_path):
    assert istanbul_lines._source_bytes(tmp_path) is None


def test_a_source_longer_than_its_stat_is_read_to_its_end():
    """A pipe stats at size 0, as a file that grew after its stat would: the read
    keeps going until the bytes stop. 600 bytes fit any pipe's buffer, so the
    write returns before anything reads."""
    reader, writer = os.pipe()
    os.write(writer, b"x = 1\r" * 100)
    os.close(writer)
    try:
        assert istanbul_lines._read_all(reader) == b"x = 1\r" * 100
    finally:
        os.close(reader)
