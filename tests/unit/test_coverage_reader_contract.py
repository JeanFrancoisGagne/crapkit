"""Every coverage adapter keeps the same promises, checked through its public read().

coverage_format._FORMATS names each adapter a lane can pick with `parser`. This
suite builds one small artifact per format, the control, and changes one thing
at a time: the bytes the file arrives in, a count, the shape of one field. Each
change either reads the way the format means it or is refused, and a refusal
says what is wrong and what to do next. A format added to _FORMATS without a
control, or without a row for each common promise, fails
test_every_format_answers_every_common_promise.

Refill boundaries a small chunk reaches are covstream's seam and are tested in
test_covstream.py.
"""
import codecs
import copy
import hashlib
import json
from array import array
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.config import Lane
from crapkit.score import FileEvidence, FnCoverage
from crapkit.errors import ToolError

FORMATS = sorted(coverage_format._FORMATS)
REGENERATE = "regenerate the"
# Each refusal ends in one of these next steps.
FIXES = (REGENERATE, "save it as UTF-8", "rerun the lane", "coverage>=7.13.1")


# --- one control artifact per format ---------------------------------------------

def _istanbul(root: Path) -> dict:
    key = f"{root.as_posix()}/src/app.ts"
    return {key: {
        "path": key,
        "fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1, "column": 0}},
                        "loc": {"start": {"line": 1, "column": 0},
                                "end": {"line": 6, "column": 1}}, "line": 1}},
        "f": {"0": 1},
        "branchMap": {"0": {"loc": {"start": {"line": 2}, "end": {"line": 2}}, "type": "if",
                            "line": 2, "locations": [{"start": {"line": 2}},
                                                     {"start": {"line": 4}}]}},
        "b": {"0": [1, 0]},
        "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}},
                         "1": {"start": {"line": 3}, "end": {"line": 3}},
                         "2": {"start": {"line": 5}, "end": {"line": 5}}},
        "s": {"0": 1, "1": 1, "2": 0}}}


def _coveragepy(root: Path) -> dict:
    fn = {"start_line": 1, "executed_lines": [2, 3], "missing_lines": [5],
          "summary": {"num_statements": 3, "covered_lines": 2,
                      "num_branches": 2, "covered_branches": 1}}
    module = {"executed_lines": [1], "missing_lines": [],
              "summary": {"num_statements": 1, "covered_lines": 1}}
    return {"meta": {"version": "7.13.2", "branch_coverage": True},
            "files": {"src/a.py": {"executed_lines": [1, 2, 3], "missing_lines": [5],
                                   "functions": {"f": fn, "": module}}},
            "totals": {"covered_lines": 3}}


CONTROLS = {"istanbul": _istanbul, "coveragepy": _coveragepy}
KEYS = {"istanbul": "src/app.ts", "coveragepy": "src/a.py"}
# An istanbul entry with statements is an instrumenter's own output, whose fnMap
# lists every function it measured, so its rows carry full_listing
# (coverage_istanbul._instrumented; test_coverage_istanbul pins both readings).
ROWS = {"istanbul": [FnCoverage("f", 1, 6, True, 2, 1, 3, 2, full_listing=True)],
        "coveragepy": [FnCoverage("f", 1, 5, True, 2, 1, 3, 2)]}
# read()'s second value: the control's one file, its dead line 5 as missed_lines.
# Both formats keep their own function records, so hit_lines is None.
DEAD = FileEvidence(hit_lines=None, missed_lines=array("I", [5]))


def _ist(doc: dict) -> dict:
    """The one file entry of an istanbul control."""
    return next(iter(doc.values()))


def _py(doc: dict) -> dict:
    return doc["files"]["src/a.py"]


def _pyfn(doc: dict) -> dict:
    return _py(doc)["functions"]["f"]


def _file_field(field: str, value):
    """An edit that sets one field of the control's coverage.py file entry."""
    return lambda doc: _py(doc).update({field: value})


def _lane(fmt: str) -> Lane:
    return Lane(name="l", command="x", artifact="cov.json", parser=fmt, scopes=("src",),
                path_prefix="")


def _bytes(root: Path, fmt: str, edit=None) -> bytes:
    doc = CONTROLS[fmt](root)
    if edit is not None:
        edit(doc)
    return json.dumps(doc).encode("utf-8")


def _read(root: Path, fmt: str, raw: bytes):
    artifact = root / "cov.json"
    artifact.write_bytes(raw)
    return coverage_format._FORMATS[fmt].read(_lane(fmt), root, artifact)


def _refusal(root: Path, fmt: str, raw: bytes) -> str:
    with pytest.raises(ToolError) as refused:
        _read(root, fmt, raw)
    return str(refused.value)


def _holds(text: str, words) -> None:
    missing = [word for word in words if word not in text]
    assert not missing, f"{missing} not in: {text}"


# --- the control and the bytes it arrives in --------------------------------------

@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_reads_its_control_in_one_walk_with_the_digest_of_its_bytes(tmp_path, fmt):
    raw = _bytes(tmp_path, fmt)

    per_file, evidence, digest = _read(tmp_path, fmt, raw)

    assert per_file == {KEYS[fmt]: ROWS[fmt]}
    assert evidence == {KEYS[fmt]: DEAD}
    assert digest == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("layout", [{"indent": 2}, {"sort_keys": True}, {"ensure_ascii": False}],
                         ids=["indented", "sorted-keys", "raw-utf8"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_the_same_document_laid_out_another_way_reads_the_same(tmp_path, fmt, layout):
    raw = json.dumps(CONTROLS[fmt](tmp_path), **layout).encode("utf-8")

    assert _read(tmp_path, fmt, raw)[:2] == ({KEYS[fmt]: ROWS[fmt]}, {KEYS[fmt]: DEAD})


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_utf8_byte_order_mark_is_read_past(tmp_path, fmt):
    """A JSON artifact is JSON: a BOM is read past, as a PowerShell
    `Out-File -Encoding utf8` copy or a Windows editor leaves one. The digest
    stays the file's own bytes, mark included."""
    raw = codecs.BOM_UTF8 + _bytes(tmp_path, fmt)

    per_file, evidence, digest = _read(tmp_path, fmt, raw)

    assert per_file == {KEYS[fmt]: ROWS[fmt]}
    assert evidence == {KEYS[fmt]: DEAD}
    assert digest == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_lines_no_test_ran_read_past_a_byte_order_mark_too(tmp_path, fmt):
    artifact = tmp_path / "cov.json"
    artifact.write_bytes(codecs.BOM_UTF8 + _bytes(tmp_path, fmt))

    assert coverage_format._FORMATS[fmt].missing(_lane(fmt), tmp_path, artifact) == {KEYS[fmt]: {5}}


def _utf16(order: str):
    mark = codecs.BOM_UTF16_LE if order == "le" else codecs.BOM_UTF16_BE
    return lambda raw: mark + raw.decode("utf-8").encode(f"utf-16-{order}")


def _latin1_name(raw: bytes) -> bytes:
    return raw.replace(b'"f"', b'"caf\xe9"', 1)


NOT_UTF8 = {
    "utf-16-le": (_utf16("le"), "first bytes ff fe = UTF-16"),
    "utf-16-be": (_utf16("be"), "first bytes fe ff = UTF-16"),
    "latin-1-byte": (_latin1_name, "byte e9 at offset"),
}


@pytest.mark.parametrize("shape", list(NOT_UTF8))
@pytest.mark.parametrize("fmt", FORMATS)
def test_text_that_is_not_utf8_is_refused_naming_the_bytes(tmp_path, fmt, shape):
    recode, cause = NOT_UTF8[shape]

    text = _refusal(tmp_path, fmt, recode(_bytes(tmp_path, fmt)))

    _holds(text, ["cov.json is not UTF-8", cause, "save it as UTF-8"])


def test_the_offset_a_refusal_names_is_the_byte_in_the_file(tmp_path):
    raw = codecs.BOM_UTF8 + _latin1_name(_bytes(tmp_path, "istanbul"))

    text = _refusal(tmp_path, "istanbul", raw)

    assert f"byte e9 at offset {raw.index(bytes([0xE9]))}" in text, text


@pytest.mark.parametrize("raw", [b"", b"  \r\n\t", codecs.BOM_UTF8],
                         ids=["zero-bytes", "whitespace", "byte-order-mark-alone"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_artifact_with_nothing_in_it_is_refused_as_empty(tmp_path, fmt, raw):
    _holds(_refusal(tmp_path, fmt, raw), ["cov.json", "is empty", "rerun the lane"])


@pytest.mark.parametrize("raw", [b"[]", b'"cov"', b"null", b"{", b'{"a": {}} {}'],
                         ids=["an-array", "a-string", "null", "truncated", "two-documents"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_a_document_that_is_not_one_json_object_is_refused(tmp_path, fmt, raw):
    _holds(_refusal(tmp_path, fmt, raw), ["cov.json", REGENERATE])


# --- the common promises every format answers -------------------------------------
#
# Each promise names a change every format can suffer. The row for a format
# spells that change in the format's own fields.

def _counted_as(fmt: str, value):
    if fmt == "istanbul":
        return lambda doc: _ist(doc)["s"].update({"0": value})
    return lambda doc: _pyfn(doc)["summary"].update(covered_lines=value)


PROMISES = {
    "dropped-counter": {
        "istanbul": (lambda d: _ist(d)["s"].pop("1"), ["statement '1'", "`s`"]),
        "coveragepy": (lambda d: _pyfn(d)["summary"].pop("covered_lines"),
                       ["num_statements without covered_lines"]),
    },
    "negative-count": {
        "istanbul": (_counted_as("istanbul", -1), ["s['0']", "nonnegative", "-1"]),
        "coveragepy": (_counted_as("coveragepy", -1), ["covered_lines", "nonnegative", "-1"]),
    },
    "count-a-string": {
        "istanbul": (_counted_as("istanbul", "1"), ["s['0']", "'1'"]),
        "coveragepy": (_counted_as("coveragepy", "2"), ["covered_lines", "'2'"]),
    },
    "counter-group-null": {
        "istanbul": (lambda d: _ist(d).update(s=None), ["src/app.ts", "`s` holds null"]),
        "coveragepy": (lambda d: _pyfn(d).update(summary=None), ["f: no summary object"]),
    },
    "file-entry-null": {
        "istanbul": (lambda d: d.update({next(iter(d)): None}),
                     ["src/app.ts: the file entry holds null"]),
        "coveragepy": (lambda d: d["files"].update({"src/a.py": None}),
                       ["src/a.py: the file entry holds null"]),
    },
    "missing-required-field": {
        "istanbul": (lambda d: _ist(d)["fnMap"]["0"]["loc"].pop("end"), ["loc.end.line"]),
        "coveragepy": (lambda d: _pyfn(d).pop("start_line"),
                       ["no start_line", "coverage>=7.13.1"]),
    },
}


def test_every_format_answers_every_common_promise():
    assert set(CONTROLS) == set(coverage_format._FORMATS) == set(KEYS) == set(ROWS)
    for promise, rows in PROMISES.items():
        assert set(rows) == set(coverage_format._FORMATS), promise


@pytest.mark.parametrize("promise", list(PROMISES))
@pytest.mark.parametrize("fmt", FORMATS)
def test_a_common_promise_is_refused_naming_the_field_and_the_fix(tmp_path, fmt, promise):
    edit, words = PROMISES[promise][fmt]

    text = _refusal(tmp_path, fmt, _bytes(tmp_path, fmt, edit))

    _holds(text, ["cov.json", *words])
    assert any(fix in text for fix in FIXES), text


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_a_count_that_is_not_a_finite_number_is_refused(tmp_path, fmt, number):
    raw = _bytes(tmp_path, fmt, _counted_as(fmt, "__COUNT__")).replace(b'"__COUNT__"',
                                                                      number.encode())

    _holds(_refusal(tmp_path, fmt, raw),
           ["cov.json", f"non-finite JSON number {number}", REGENERATE])


# --- what each format refuses in its own fields -----------------------------------

REFUSED = [
    ("istanbul", "s-count-null", lambda d: _ist(d)["s"].update({"0": None}), ["s['0']", "None"]),
    ("istanbul", "b-hits-null", lambda d: _ist(d)["b"].update({"0": None}),
     ["src/app.ts", "`b['0']` holds null"]),
    ("istanbul", "f-count-a-string", lambda d: _ist(d)["f"].update({"0": "1"}), ["f['0']"]),
    ("istanbul", "fnMap-null", lambda d: _ist(d).update(fnMap=None),
     ["src/app.ts", "`fnMap` holds null"]),
    ("istanbul", "fnMap-a-list", lambda d: _ist(d).update(fnMap=[]),
     ["src/app.ts", "`fnMap` holds an array"]),
    ("istanbul", "decl-absent-as-istanbul-0x-wrote", lambda d: _ist(d)["fnMap"]["0"].pop("decl"),
     ["src/app.ts", "fnMap['0'] has no decl.start.line"]),
    ("istanbul", "decl-start-line-null",
     lambda d: _ist(d)["fnMap"]["0"]["decl"]["start"].update(line=None), ["src/app.ts"]),
    ("istanbul", "statement-start-null",
     lambda d: _ist(d)["statementMap"]["0"].update(start=None), ["src/app.ts"]),
    ("istanbul", "s-absent", lambda d: _ist(d).pop("s"), ["statement '0'", "`s`"]),
    ("istanbul", "f-absent", lambda d: _ist(d).pop("f"), ["function '0'", "`f`"]),
    ("istanbul", "branch-with-no-line",
     lambda d: _ist(d)["branchMap"]["0"].update(loc={}, line=None), ["branchMap['0']"]),
    ("istanbul", "no-file-at-all", lambda d: d.clear(), ["empty (zero files)"]),
    ("coveragepy", "executed_lines-null", lambda d: _pyfn(d).update(executed_lines=None),
     ["src/a.py", "f"]),
    ("coveragepy", "missing_lines-null", lambda d: _pyfn(d).update(missing_lines=None),
     ["src/a.py", "f"]),
    ("coveragepy", "num_branches-null", lambda d: _pyfn(d)["summary"].update(num_branches=None),
     ["num_branches must be a nonnegative integer count, got None"]),
    ("coveragepy", "num_branches-a-string",
     lambda d: _pyfn(d)["summary"].update(num_branches="2"),
     ["num_branches must be a nonnegative integer count, got '2'"]),
    ("coveragepy", "function-entry-null", lambda d: _py(d)["functions"].update(f=None),
     ["src/a.py", "f: no summary object"]),
    ("coveragepy", "files-null", lambda d: d.update(files=None), ["'files' is not a JSON object"]),
    ("coveragepy", "functions-null-in-every-file", lambda d: _py(d).update(functions=None),
     ["no function regions for any", "coverage>=7.13.1"]),
    ("coveragepy", "summary-absent", lambda d: _pyfn(d).pop("summary"), ["f: no summary object"]),
    ("coveragepy", "start_line-a-string", lambda d: _pyfn(d).update(start_line="1"),
     ["start_line must be a line number"]),
    ("coveragepy", "covered-over-total", lambda d: _pyfn(d)["summary"].update(covered_lines=9),
     ["covered_lines exceeds num_statements"]),
    ("coveragepy", "executed_lines-a-string-entry",
     lambda d: _pyfn(d).update(executed_lines=["2", 3]),
     ["src/a.py: f: executed_lines[0] holds a string, not a line number"]),
    ("coveragepy", "executed_lines-a-float-entry",
     lambda d: _pyfn(d).update(executed_lines=[2, 3.0]),
     ["src/a.py: f: executed_lines[1] holds a decimal number, not a line number"]),
    *[("coveragepy", f"file-missing_lines-{shape}", _file_field("missing_lines", value), words)
      for shape, value, words in [
          ("null", None, ["src/a.py: missing_lines holds null, not a list of line numbers"]),
          ("a-number", 5, ["src/a.py: missing_lines holds a number, not a list"]),
          ("a-string", "5", ["src/a.py: missing_lines holds a string, not a list"]),
          ("an-object", {"5": 1}, ["src/a.py: missing_lines holds an object, not a list"]),
          ("a-string-entry", ["5"],
           ["src/a.py: missing_lines[0] holds a string, not a line number"]),
          ("a-null-entry", [5, None], ["src/a.py: missing_lines[1] holds null"]),
          ("a-boolean-entry", [True], ["src/a.py: missing_lines[0] holds a boolean"])]],
    *[("coveragepy", f"functions-{shape}", _file_field("functions", value), words)
      for shape, value, words in [
          ("a-list", ["f"], ["src/a.py: functions holds an array, not an object"]),
          ("an-empty-list", [], ["src/a.py: functions holds an array, not an object"]),
          ("a-string", "f", ["src/a.py: functions holds a string, not an object"]),
          ("an-empty-string", "", ["src/a.py: functions holds a string, not an object"]),
          ("a-number", 0, ["src/a.py: functions holds a number, not an object"])]],
]


@pytest.mark.parametrize("fmt, edit, words", [row[:1] + row[2:] for row in REFUSED],
                         ids=[f"{row[0]}-{row[1]}" for row in REFUSED])
def test_a_needed_field_in_the_wrong_shape_refuses_the_artifact(tmp_path, fmt, edit, words):
    text = _refusal(tmp_path, fmt, _bytes(tmp_path, fmt, edit))

    _holds(text, words)
    assert any(fix in text for fix in FIXES), text
    for python_error in ("NoneType", "not iterable", "has no attribute"):
        assert python_error not in text, text


# The dead-line read and the contexts read take one file entry at a time on
# walks of their own, so a file-level field is refused on each of them too.
FILE_LEVEL = {
    "file-entry-null": (lambda d: d["files"].update({"src/a.py": None}),
                        "src/a.py: the file entry holds null"),
    "missing_lines-null": (_file_field("missing_lines", None), "src/a.py: missing_lines holds null"),
    "missing_lines-a-number": (_file_field("missing_lines", 5),
                               "src/a.py: missing_lines holds a number"),
    "missing_lines-a-string-entry": (_file_field("missing_lines", ["5"]),
                                     "src/a.py: missing_lines[0] holds a string"),
}


@pytest.mark.parametrize("shape", list(FILE_LEVEL))
def test_the_dead_line_read_refuses_a_file_level_field_naming_the_file(tmp_path, shape):
    edit, words = FILE_LEVEL[shape]
    artifact = tmp_path / "cov.json"
    artifact.write_bytes(_bytes(tmp_path, "coveragepy", edit))

    with pytest.raises(ToolError) as refused:
        coverage_format._FORMATS["coveragepy"].missing(_lane("coveragepy"), tmp_path, artifact)

    _holds(str(refused.value), ["cov.json", words, REGENERATE])


CONTEXTS = {
    "contexts-null": (None, "src/a.py: contexts holds null, not an object"),
    "contexts-a-list": (["test_f"], "src/a.py: contexts holds an array, not an object"),
    "line-contexts-null": ({"2": None}, "src/a.py: contexts['2'] holds null, not a list"),
    "line-contexts-a-string": ({"2": "test_f"}, "src/a.py: contexts['2'] holds a string"),
    "a-context-a-number": ({"2": [7]}, "src/a.py: contexts['2'][0] holds a number, not a context name"),
    "line-not-a-number": ({"two": ["test_f"]}, "src/a.py: contexts key 'two' is not a line number"),
}


@pytest.mark.parametrize("shape", list(CONTEXTS))
def test_the_contexts_read_refuses_a_field_of_the_wrong_shape_naming_the_file(tmp_path, shape):
    value, words = CONTEXTS[shape]
    artifact = tmp_path / "cov.json"
    artifact.write_bytes(_bytes(tmp_path, "coveragepy", _file_field("contexts", value)))

    with pytest.raises(ToolError) as refused:
        coverage_format._FORMATS["coveragepy"].contexts(_lane("coveragepy"), tmp_path, artifact,
                                                        "src/a.py")

    _holds(str(refused.value), ["cov.json", words, REGENERATE])


# --- what each format reads as it means it -----------------------------------------

def _ist_row(*fields, full_listing: bool = True) -> dict:
    return {"src/app.ts": [FnCoverage(*fields, full_listing=full_listing)]}


READ_AS = [
    ("istanbul", "name-null", lambda d: _ist(d)["fnMap"]["0"].update(name=None),
     _ist_row("(anonymous)", 1, 6, True, 2, 1, 3, 2)),
    ("istanbul", "statementMap-absent", lambda d: _ist(d).pop("statementMap"),
     _ist_row("f", 1, 6, True, 2, 1, 0, 0, full_listing=False)),
    ("istanbul", "end-column-null",
     lambda d: _ist(d)["fnMap"]["0"]["loc"]["end"].update(column=None), None),
    ("istanbul", "path-field-absent", lambda d: _ist(d).pop("path"), None),
    ("istanbul", "statement-start-empty", lambda d: _ist(d)["statementMap"]["0"].update(start={}),
     _ist_row("f", 1, 6, True, 2, 1, 2, 1)),
    ("istanbul", "fnMap-empty", lambda d: _ist(d).update(fnMap={}, f={}), {"src/app.ts": []}),
    ("istanbul", "branch-line-beside-loc",
     lambda d: _ist(d)["branchMap"]["0"].update(loc={}), None),
    ("coveragepy", "meta-absent", lambda d: d.pop("meta"), None),
    ("coveragepy", "meta-null", lambda d: d.update(meta=None), None),
    ("coveragepy", "totals-null", lambda d: d.update(totals=None), None),
    ("coveragepy", "covered_lines-a-whole-float",
     lambda d: _pyfn(d)["summary"].update(covered_lines=2.0), None),
    ("coveragepy", "no-files", lambda d: d.update(files={}), {}),
    ("coveragepy", "function-name-not-ascii",
     lambda d: _py(d)["functions"].update({"café_世界": _py(d)["functions"].pop("f")}),
     {"src/a.py": [FnCoverage("café_世界", 1, 5, True, 2, 1, 3, 2)]}),
]


@pytest.mark.parametrize("fmt, edit, expected", [row[:1] + row[2:] for row in READ_AS],
                         ids=[f"{row[0]}-{row[1]}" for row in READ_AS])
def test_a_field_the_format_leaves_open_reads_as_it_means_it(tmp_path, fmt, edit, expected):
    """None as the expectation reads as the control: a field the scores do not
    read changes nothing."""
    per_file = _read(tmp_path, fmt, _bytes(tmp_path, fmt, edit))[0]

    assert per_file == ({KEYS[fmt]: ROWS[fmt]} if expected is None else expected)


def test_a_non_ascii_istanbul_path_keys_its_file_as_written(tmp_path):
    def rename(doc):
        doc[f"{tmp_path.as_posix()}/src/café_世界.ts"] = doc.pop(next(iter(doc)))

    assert list(_read(tmp_path, "istanbul", _bytes(tmp_path, "istanbul", rename))[0]) == [
        "src/café_世界.ts"]


def test_a_coveragepy_lane_reads_the_contexts_of_one_file_through_its_adapter(tmp_path):
    def with_contexts(doc):
        _py(doc)["contexts"] = {"2": ["test_f|run", "", "test_f|setup"]}

    artifact = tmp_path / "cov.json"
    artifact.write_bytes(codecs.BOM_UTF8 + _bytes(tmp_path, "coveragepy", with_contexts))
    adapter = coverage_format._FORMATS["coveragepy"]

    assert adapter.contexts(_lane("coveragepy"), tmp_path, artifact, "src/a.py") == {2: ["test_f"]}
    assert adapter.contexts(_lane("coveragepy"), tmp_path, artifact, "absent.py") == {}


# --- every field each format reads: coverage_fields.FIELDS ------------------------
#
# One row per field an adapter reads (tests/unit/coverage_fields.py). Each field
# is dropped and retyped in turn on the format's minimal artifact, and every
# field a recorded artifact holds is removed once, so a reader added to _FORMATS
# is checked field by field the day it lands.

from coverage_fields import FIELDS, MAPS, WRONG, field_paths, generic, minimal_artifact  # noqa: E402
from crapkit.repotext import json_kind  # noqa: E402

RECORDED = Path(__file__).parents[1] / "accuracy" / "coverage_oracles" / "recorded"
RECORDINGS = {"istanbul": ["c8-12.0.0", "nyc-18.0.0", "jest-babel-30.5.2", "jest-v8-30.5.2",
                           "vitest-istanbul-5.0.1", "vitest-v8-5.0.1"],
              "coveragepy": ["coveragepy-7.10.6", "coveragepy-7.13.0", "coveragepy-7.16.1"]}

# What read() does today where the ticket's lines ask for more. Each set is a
# strict xfail on the one claim it breaks, never on the whole case, so a case
# keeps every claim that does hold and turns red the day an adapter changes.
# test_a_listed_shortfall_reads_as_it_does_today holds each listed case to the
# reading or the refusal read() gives instead, so it turns red when anything
# else about the case changes. The wording is the adapters' own
# (coverage_istanbul._decl_line, _fn_end, _branch_line and _fn_span;
# coverage_py's _admit_summary and judge_regions; covstream's framing of 'files').
#
# Retyped, these fields are refused as if a field were absent: the refusal
# names the field but not the type it found. Each maps to the field whose drop
# gives that same refusal. 'files' maps to None: no drop of it refuses, and it
# refuses every wrong value in the words it gives null.
TYPE_UNNAMED = {
    **{("istanbul", locator): locator for locator in (
        "*.fnMap.*.decl", "*.fnMap.*.decl.start", "*.fnMap.*.decl.start.line",
        "*.fnMap.*.loc", "*.fnMap.*.loc.end", "*.fnMap.*.loc.end.line",
        "*.branchMap.0.loc", "*.branchMap.0.loc.start", "*.branchMap.0.loc.start.line",
        "*.branchMap.1.line")},
    ("istanbul", "*.fnMap.*"): "*.fnMap.*.decl",
    ("istanbul", "*.branchMap.0"): "*.branchMap.0.loc",
    ("istanbul", "*.branchMap.1"): "*.branchMap.1.line",
    ("coveragepy", "files"): None,
    ("coveragepy", "files.*.functions.*"): "files.*.functions.*.summary",
    ("coveragepy", "files.*.functions.*.summary"): "files.*.functions.*.summary",
}
# Retyped to anything but null, these optional fields read as if they were left
# out, with no refusal.
READ_AS_ABSENT = {
    ("istanbul", locator) for locator in (
        "*.fnMap.*.decl.start.column", "*.fnMap.*.loc.start", "*.fnMap.*.loc.start.line",
        "*.fnMap.*.loc.start.column", "*.fnMap.*.loc.end.column",
        "*.statementMap.*.start.column", "*.branchMap.0.loc.start.column",
        "*.branchMap.0.locations")} | {
    ("coveragepy", locator) for locator in ("meta", "meta.branch_coverage")}
# null on these optional fields reads as the field left out, with no refusal:
# its row's `reads`. null on any other optional field must be refused.
NULL_READS_AS_LEFT_OUT = {
    ("istanbul", locator) for locator in (
        "*.fnMap.*.name", "*.fnMap.*.decl.start.column", "*.fnMap.*.loc.start",
        "*.fnMap.*.loc.start.line", "*.fnMap.*.loc.start.column", "*.fnMap.*.loc.end.column",
        "*.statementMap.*.start.line", "*.statementMap.*.start.column",
        "*.branchMap.0.loc.start.column", "*.branchMap.0.locations")} | {
    ("coveragepy", locator) for locator in ("meta", "meta.branch_coverage")}
# `{}` on these optional fields reads as the field left out too: an empty start
# has no line, which leaves its statement out, and an empty name is (anonymous).
EMPTY_READS_AS_LEFT_OUT = {("istanbul", "*.statementMap.*.start"),
                           ("istanbul", "*.fnMap.*.name")}
# A name that is not a string is kept as the function's name (coverage_istanbul._fn_span).
KEPT_AS_WRITTEN = {("istanbul", "*.fnMap.*.name")}
# null on these required fields is refused in the words of the field left out,
# which do not say null.
NULL_AS_ABSENT = {("coveragepy", locator) for locator in (
    "files.*.functions", "files.*.functions.*.start_line")}
# Dropped, refused for the whole report in these words, which name neither the
# artifact nor a file key (coverage_py.judge_regions).
FILE_UNNAMED = {("coveragepy", "files.*.functions"):
                "coverage.py report has no function regions for any of its 1 file(s)"
                " - needs coverage>=7.13.1"}


def _row_id(row) -> str:
    return f"{row.format}:{row.locator}"


def _outcome(root: Path, fmt: str, raw: bytes):
    """read()'s function coverage and per-file FileEvidence, or its refusal's text."""
    try:
        return _read(root, fmt, raw)[:2]
    except ToolError as refused:
        return str(refused)


def _kinds(value) -> set[str]:
    """Each way a refusal can name what it found: the JSON kind or the value,
    and for an array, the kind of each entry."""
    found = {json_kind(value), repr(value)}
    for entry in value if isinstance(value, list) else ():
        found.add("a decimal number" if type(entry) is float else json_kind(entry))
    return found


def _xfail(gap: bool, reason: str) -> list:
    return [pytest.mark.xfail(strict=True, reason=reason)] if gap else []


def _emptied(row, value) -> bool:
    """`{}` on an object row empties it and keeps its type, so a refusal has no
    wrong type to name. On a row of any other type `{}` is a retype."""
    return row.type == "object" and value == {}


def _retypes(row) -> list:
    """Every wrong value for the row's type. An object member or a map emptied
    is a valid artifact (no function, an empty map), so `{}` is no retype for
    it. A count or an array member set to `{}` is retyped like any other row."""
    is_map = row.locator.split(".")[-1] in MAPS[row.format]
    valid_empty = row.absent is None or is_map
    return [value for value in WRONG[row.type] if not (valid_empty and _emptied(row, value))]


def _left_out(row, value) -> bool:
    """A retype the lists above hold to the reading of the field left out, the
    `reads` its row writes."""
    key = (row.format, row.locator)
    if value is None:
        return key in NULL_READS_AS_LEFT_OUT
    return key in READ_AS_ABSENT or (value == {} and key in EMPTY_READS_AS_LEFT_OUT)


def _unrefused(row, value) -> bool:
    """A retype the lists above hold to a reading with no refusal."""
    return _left_out(row, value) or (row.format, row.locator) in KEPT_AS_WRITTEN


def _refused_case(row, value):
    return pytest.param(row, value, id=f"{_row_id(row)}={value!r}",
                        marks=_xfail(_unrefused(row, value), "reads with no refusal"))


def _typed_case(row, value):
    key = (row.format, row.locator)
    gap = (key in TYPE_UNNAMED or _unrefused(row, value)
           or (key in NULL_AS_ABSENT and value is None))
    return pytest.param(row, value, id=f"{_row_id(row)}={value!r}",
                        marks=_xfail(gap, "not named by the type it found"))


REQUIRED = [row for row in FIELDS if row.absent is not None and row.required]
DROPPED = [pytest.param(row, id=_row_id(row)) for row in REQUIRED]
DROPPED_IN_A_FILE = [pytest.param(row, id=_row_id(row), marks=_xfail(
    (row.format, row.locator) in FILE_UNNAMED, "names no file")) for row in REQUIRED]
OPTIONAL = [pytest.param(row, id=_row_id(row))
            for row in FIELDS if row.absent is not None and not row.required]
RETYPED = [_refused_case(row, value) for row in FIELDS for value in _retypes(row)]
RETYPED_TO_A_KIND = [_typed_case(row, value)
                     for row in FIELDS for value in _retypes(row) if not _emptied(row, value)]


def test_every_format_has_rows_in_the_field_table():
    rowless = sorted(set(coverage_format._FORMATS) - {row.format for row in FIELDS})

    assert not rowless, f"coverage_fields.FIELDS has no rows for {rowless}"


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_minimal_artifact_reads_one_function_with_its_branches_and_a_dead_line(tmp_path, fmt):
    per_file, evidence = _outcome(tmp_path, fmt, minimal_artifact(fmt))

    (row,) = per_file[KEYS[fmt]]
    assert (row.name, row.start, row.invoked, row.statements_covered) == ("f", 1, True, 2)
    assert row.branches_total > row.branches_covered > 0
    assert evidence == {KEYS[fmt]: DEAD}


@pytest.mark.parametrize("row", [pytest.param(row, id=_row_id(row)) for row in FIELDS])
def test_every_row_names_a_field_the_minimal_artifact_holds(row):
    """A builder that leaves the field in, or a locator that names nothing,
    writes the control: its drop and retype cases would test nothing."""
    control = minimal_artifact(row.format)

    assert b'"__retyped__"' in minimal_artifact(row.format, retype=(row.locator, "__retyped__"))
    if row.absent is not None:
        assert minimal_artifact(row.format, drop=row.locator) != control


@pytest.mark.parametrize("row", DROPPED)
def test_dropping_a_required_field_refuses_naming_it(tmp_path, row):
    text = _refusal(tmp_path, row.format, minimal_artifact(row.format, drop=row.locator))

    _holds(text, [row.named, *row.absent])
    assert any(fix in text for fix in FIXES), text


@pytest.mark.parametrize("row", DROPPED_IN_A_FILE)
def test_dropping_a_required_field_names_the_artifact_and_the_file_key(tmp_path, row):
    text = _refusal(tmp_path, row.format, minimal_artifact(row.format, drop=row.locator))

    _holds(text, ["cov.json", KEYS[row.format]])


@pytest.mark.parametrize("row", OPTIONAL)
def test_dropping_an_optional_field_reads_as_its_row_documents(tmp_path, row):
    """The field left out, and the field set to the value its row documents,
    each read as the row's written `reads`, never as another read."""
    dropped = _outcome(tmp_path, row.format, minimal_artifact(row.format, drop=row.locator))
    documented = _outcome(tmp_path, row.format,
                          minimal_artifact(row.format, retype=(row.locator, row.absent.value)))

    assert dropped == row.absent.reads, row.absent.means
    assert documented == row.absent.reads, row.absent.means


@pytest.mark.parametrize("row, value", RETYPED)
def test_a_retyped_field_is_refused_naming_it(tmp_path, row, value):
    """Every wrong value, null included, is refused naming the field. A case
    that reads instead is listed above and held to its reading by
    test_a_listed_shortfall_reads_as_it_does_today."""
    retyped = _outcome(tmp_path, row.format,
                       minimal_artifact(row.format, retype=(row.locator, value)))

    assert isinstance(retyped, str), f"{row.locator} = {value!r} read as {retyped}"
    _holds(retyped, [row.named])


@pytest.mark.parametrize("row, value", RETYPED_TO_A_KIND)
def test_a_retyped_fields_refusal_names_what_it_found(tmp_path, row, value):
    """The refusal names the type or the value found after the file, null
    included."""
    retyped = _outcome(tmp_path, row.format,
                       minimal_artifact(row.format, retype=(row.locator, value)))

    assert isinstance(retyped, str), f"{row.locator} = {value!r} read as {retyped}"
    _holds(retyped, ["cov.json"])
    said = retyped.split("cov.json", 1)[1]
    assert any(kind in said for kind in _kinds(value)), retyped


def _listed(row, value) -> bool:
    """A retype the lists above hold short of a claim. An emptied object has no
    type to name, so it is held here only where it reads as the field left out."""
    key = (row.format, row.locator)
    if _left_out(row, value):
        return True
    return not _emptied(row, value) and (key in TYPE_UNNAMED or key in KEPT_AS_WRITTEN
                                         or (key in NULL_AS_ABSENT and value is None))


LISTED = [pytest.param(row, value, id=f"{_row_id(row)}={value!r}")
          for row in FIELDS for value in _retypes(row) if _listed(row, value)]


def _today(root: Path, row, value):
    """What read() gives today for a listed retype: the field left out's written
    reading, that reading with the name kept as written, or a refusal built from
    another artifact (a drop's, or null's)."""
    key = (row.format, row.locator)
    if _left_out(row, value):
        return row.absent.reads
    if key in KEPT_AS_WRITTEN:
        per_file, dead = row.absent.reads
        return {path: [fn._replace(name=value) for fn in fns] for path, fns in per_file.items()}, dead
    if key in TYPE_UNNAMED and TYPE_UNNAMED[key] is None:
        return _outcome(root, row.format, minimal_artifact(row.format, retype=(row.locator, None)))
    return _outcome(root, row.format,
                    minimal_artifact(row.format, drop=TYPE_UNNAMED.get(key, row.locator)))


@pytest.mark.parametrize("row, value", LISTED)
def test_a_listed_shortfall_reads_as_it_does_today(tmp_path, row, value):
    """Outside any xfail: a listed case gives the reading or the refusal it gives
    today, so a change to anything but its one short claim turns the suite red."""
    retyped = _outcome(tmp_path, row.format,
                       minimal_artifact(row.format, retype=(row.locator, value)))

    assert retyped == _today(tmp_path, row, value)


@pytest.mark.parametrize("row", [pytest.param(row, id=_row_id(row)) for row in FIELDS
                                 if (row.format, row.locator) in FILE_UNNAMED])
def test_a_listed_drop_is_refused_as_it_is_today(tmp_path, row):
    text = _refusal(tmp_path, row.format, minimal_artifact(row.format, drop=row.locator))

    assert text == FILE_UNNAMED[(row.format, row.locator)]


def _at_path(doc: dict, path: tuple, value=None, drop: bool = False) -> bytes:
    """`doc` with the field at `path` dropped or set to `value`, as bytes."""
    copied = copy.deepcopy(doc)
    node = copied
    for key in path[:-1]:
        node = node[key]
    if drop:
        del node[path[-1]]
    else:
        node[path[-1]] = copy.deepcopy(value)
    return json.dumps(copied).encode("utf-8")


def _unexplained(root: Path, fmt: str, doc: dict, locator: str, path: tuple, base) -> str | None:
    """Why removing one field breaks the table's promise, or None when it keeps it."""
    rows = [row for row in FIELDS if row.format == fmt and generic(fmt, row.locator) == locator]
    removed = _outcome(root, fmt, _at_path(doc, path, drop=True))
    if removed == base:
        return None
    if isinstance(removed, str):
        if any(row.named in removed for row in rows):
            return None
        return f"{locator}: refused with no row naming it: {removed}"
    if any(removed == _outcome(root, fmt, _at_path(doc, path, row.absent.value))
           for row in rows if not row.required):
        return None
    return f"{locator}: removing it changed the reading, and no optional row documents it"


RECORDED_CASES = [pytest.param(fmt, name, id=name)
                  for fmt, names in RECORDINGS.items() for name in names]


@pytest.mark.parametrize("fmt, name", RECORDED_CASES)
def test_removing_each_field_a_recorded_artifact_holds_refuses_or_reads_as_documented(
        tmp_path, fmt, name):
    """One removal per distinct field, from the first file entry that holds it."""
    doc = json.loads((RECORDED / name / "call.json").read_text(encoding="utf-8"))
    base = _outcome(tmp_path, fmt, json.dumps(doc).encode("utf-8"))

    broken = [why for locator, path in field_paths(fmt, doc).items()
              if (why := _unexplained(tmp_path, fmt, doc, locator, path, base))]

    assert not broken, "\n".join(broken)


def test_every_recording_is_one_of_its_formats():
    """A recording added under recorded/ in a format that has rows joins the
    completeness check; its producer's name says which format it is."""
    listed = {name for names in RECORDINGS.values() for name in names}

    assert listed <= {path.name for path in RECORDED.iterdir()}
