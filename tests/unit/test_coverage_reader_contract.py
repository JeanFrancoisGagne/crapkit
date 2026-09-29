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
import hashlib
import json
from pathlib import Path

import pytest

from crapkit import coverage_format
from crapkit.config import Lane
from crapkit.coverage_istanbul import FnCoverage
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

    per_file, dead, digest = _read(tmp_path, fmt, raw)

    assert per_file == {KEYS[fmt]: ROWS[fmt]}
    assert dead == {KEYS[fmt]: {5}}
    assert digest == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("layout", [{"indent": 2}, {"sort_keys": True}, {"ensure_ascii": False}],
                         ids=["indented", "sorted-keys", "raw-utf8"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_the_same_document_laid_out_another_way_reads_the_same(tmp_path, fmt, layout):
    raw = json.dumps(CONTROLS[fmt](tmp_path), **layout).encode("utf-8")

    assert _read(tmp_path, fmt, raw)[:2] == ({KEYS[fmt]: ROWS[fmt]}, {KEYS[fmt]: {5}})


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_utf8_byte_order_mark_is_read_past(tmp_path, fmt):
    """A JSON artifact is JSON: a BOM is read past, as a PowerShell
    `Out-File -Encoding utf8` copy or a Windows editor leaves one. The digest
    stays the file's own bytes, mark included."""
    raw = codecs.BOM_UTF8 + _bytes(tmp_path, fmt)

    per_file, dead, digest = _read(tmp_path, fmt, raw)

    assert per_file == {KEYS[fmt]: ROWS[fmt]}
    assert dead == {KEYS[fmt]: {5}}
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
