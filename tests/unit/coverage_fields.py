"""Every field each coverage adapter reads, and one minimal artifact per format.

FIELDS holds one row per field an adapter in coverage_format._FORMATS reads:

- format: the _FORMATS key.
- locator: where the field sits, spelled the way the format's builder reads it.
  A JSON format spells a dotted path of keys from the document root, where `*`
  stands for the first member of a map or array and any other segment is a key
  (a member id such as `0` picks that member). A line-based format such as lcov
  or a Go coverprofile spells a record tag and a field position, and an XML
  format such as Cobertura or JaCoCo an element and an attribute: each format's
  builder reads its own spelling, so the columns do not change.
- required: True when an artifact without the field is refused.
- type: the kind of value the producer writes there (a key of WRONG).
- absent: what the adapter makes of the field when it is not there. For a
  required field, the words the refusal prints besides the field and the file;
  for an optional one, a ReadsAs: the artifact reads the same with the field set
  to that value. None marks a member of a map or an array, which has no absence
  of its own: dropping one is another artifact, not a missing field.
- named: the words a refusal names the field by.

minimal_artifact(format, drop=None, retype=None) writes the format's smallest
valid artifact, with the field at `drop` left out or the field at retype's
locator set to retype's value. Every row's locator is present in it.

A new reader adds its rows here and its builder to _BUILDERS. Rules that tie
two fields together (covered at most total, a count without its partner, id
pairing, v8's branch clamp) stay in the reader's own test file.
"""
from __future__ import annotations

import copy
import json
from typing import NamedTuple


class ReadsAs(NamedTuple):
    """An optional field's reading: the artifact reads as if the field held `value`."""

    value: object
    means: str


class Field(NamedTuple):
    format: str
    locator: str
    required: bool
    type: str
    absent: object
    named: str


# The values a field of each type is retyped to. `{}` on an object that is not a
# map empties it; a map's `{}` is an empty map, which the producer writes.
WRONG = {
    "object": [None, [1], "2", 1.5, True, {}],
    "array": [None, {}, "2", 1.5, True],
    "integer": ["2", 1.5, 1.0, True, [1], {}, None],
    "line number": ["2", 1.5, 1.0, True, 0, -1, [1], {}, None],
    "count": ["2", 1.5, True, -1, [1], {}, None],
    "branch count": ["2", 1.5, True, [1], {}, None],
    "line numbers": [None, 5, "5", {"5": 1}, ["5"], [1.5], [True], [None]],
    "string": [5, 1.5, True, [1], {}, None],
    "boolean": ["2", 1, 1.5, [1], {}, None],
}

# Per format, the keys whose members are ids (a file, a function, a counter), not
# fields. "" is the document root.
MAPS = {
    "istanbul": {"", "fnMap", "f", "statementMap", "s", "branchMap", "b"},
    "coveragepy": {"files", "functions", "classes", "contexts"},
}

FILE_KEYS = {"istanbul": "src/app.ts", "coveragepy": "src/a.py"}

_FN = "*.fnMap.*"
_B0, _B1 = "*.branchMap.0", "*.branchMap.1"
_PY_FN = "files.*.functions.*"

FIELDS = [
    Field("istanbul", "*", True, "object", None, "the file entry"),
    Field("istanbul", "*.fnMap", False, "object", ReadsAs({}, "no function is measured"),
          "`fnMap`"),
    Field("istanbul", _FN, True, "object", None, "fnMap['0']"),
    Field("istanbul", f"{_FN}.name", False, "string",
          ReadsAs("(anonymous)", "the function is named (anonymous)"), "name"),
    Field("istanbul", f"{_FN}.decl", True, "object", (), "decl.start.line"),
    Field("istanbul", f"{_FN}.decl.start", True, "object", (), "decl.start.line"),
    Field("istanbul", f"{_FN}.decl.start.line", True, "integer", (), "decl.start.line"),
    Field("istanbul", f"{_FN}.decl.start.column", False, "integer",
          ReadsAs(0, "the signature starts the line"), "column"),
    Field("istanbul", f"{_FN}.loc", True, "object", (), "loc.end.line"),
    Field("istanbul", f"{_FN}.loc.start", False, "object",
          ReadsAs({}, "the body starts at the signature"), "loc.start"),
    Field("istanbul", f"{_FN}.loc.start.line", False, "integer",
          ReadsAs(None, "the body starts at the signature"), "loc.start.line"),
    Field("istanbul", f"{_FN}.loc.start.column", False, "integer",
          ReadsAs(0, "the body starts the line"), "column"),
    Field("istanbul", f"{_FN}.loc.end", True, "object", (), "loc.end.line"),
    Field("istanbul", f"{_FN}.loc.end.line", True, "integer", (), "loc.end.line"),
    Field("istanbul", f"{_FN}.loc.end.column", False, "integer",
          ReadsAs(None, "the function ends with its last line"), "column"),
    Field("istanbul", "*.f", True, "object", (), "`f`"),
    Field("istanbul", "*.f.*", True, "count", None, "f['0']"),
    Field("istanbul", "*.statementMap", False, "object",
          ReadsAs({}, "no statement is measured, so the fnMap may not list every function"),
          "`statementMap`"),
    Field("istanbul", "*.statementMap.*", True, "object", None, "`statementMap['0']`"),
    Field("istanbul", "*.statementMap.*.start", False, "object",
          ReadsAs({}, "the statement is left out"), "`statementMap['0'].start`"),
    Field("istanbul", "*.statementMap.*.start.line", False, "integer",
          ReadsAs(None, "the statement is left out"), "`statementMap['0'].start.line`"),
    Field("istanbul", "*.statementMap.*.start.column", False, "integer",
          ReadsAs(0, "the statement starts the line"), "column"),
    Field("istanbul", "*.s", True, "object", (), "`s`"),
    Field("istanbul", "*.s.*", True, "count", None, "s['0']"),
    Field("istanbul", "*.branchMap", False, "object", ReadsAs({}, "no branch is measured"),
          "`branchMap`"),
    Field("istanbul", _B0, True, "object", None, "branchMap['0']"),
    Field("istanbul", f"{_B0}.loc", True, "object", (), "branchMap['0'] has no loc.start.line"),
    Field("istanbul", f"{_B0}.loc.start", True, "object", (),
          "branchMap['0'] has no loc.start.line"),
    Field("istanbul", f"{_B0}.loc.start.line", True, "integer", (),
          "branchMap['0'] has no loc.start.line"),
    Field("istanbul", f"{_B0}.loc.start.column", False, "integer",
          ReadsAs(0, "the branch starts the line"), "column"),
    Field("istanbul", f"{_B0}.locations", False, "array",
          ReadsAs(None, "the hit counts are not checked against the branch's paths"),
          "locations"),
    # A branch with no loc sits on the `line` producers write beside it.
    Field("istanbul", _B1, True, "object", None, "branchMap['1']"),
    Field("istanbul", f"{_B1}.line", True, "integer", (), "branchMap['1'] has no loc.start.line"),
    Field("istanbul", "*.b", True, "object", (), "`b`"),
    Field("istanbul", "*.b.*", True, "array", None, "`b['0']`"),
    Field("istanbul", "*.b.*.*", True, "branch count", None, "b['0'][0]"),

    Field("coveragepy", "meta", False, "object",
          ReadsAs({}, "branches are measured when a function carries branch counts"), "meta"),
    Field("coveragepy", "meta.branch_coverage", False, "boolean",
          ReadsAs(False, "branches are measured when a function carries branch counts"),
          "branch_coverage"),
    Field("coveragepy", "files", False, "object", ReadsAs({}, "no file is measured"), "'files'"),
    Field("coveragepy", "files.*", True, "object", None, "the file entry"),
    Field("coveragepy", "files.*.missing_lines", False, "line numbers",
          ReadsAs([], "no line of the file is dead"), "missing_lines"),
    Field("coveragepy", "files.*.functions", True, "object", ("coverage>=7.13.1",), "function"),
    Field("coveragepy", _PY_FN, True, "object", None, "f: no summary object"),
    Field("coveragepy", f"{_PY_FN}.start_line", True, "line number",
          ("no start_line", "coverage>=7.13.1"), "start_line"),
    Field("coveragepy", f"{_PY_FN}.executed_lines", False, "line numbers",
          ReadsAs([], "the region ends on its last missing or excluded line"), "executed_lines"),
    Field("coveragepy", f"{_PY_FN}.missing_lines", False, "line numbers",
          ReadsAs([], "the region ends on its last executed or excluded line"), "missing_lines"),
    Field("coveragepy", f"{_PY_FN}.excluded_lines", False, "line numbers",
          ReadsAs([], "the region ends on its last executed or missing line"), "excluded_lines"),
    Field("coveragepy", f"{_PY_FN}.summary", True, "object", (), "summary"),
    Field("coveragepy", f"{_PY_FN}.summary.num_statements", True, "count", (), "num_statements"),
    Field("coveragepy", f"{_PY_FN}.summary.covered_lines", True, "count", (), "covered_lines"),
    Field("coveragepy", f"{_PY_FN}.summary.num_branches", True, "count", (), "num_branches"),
    Field("coveragepy", f"{_PY_FN}.summary.covered_branches", True, "count", (),
          "covered_branches"),
    Field("coveragepy", f"{_PY_FN}.summary.excluded_lines", False, "count",
          ReadsAs(0, "no line of the function is excluded"), "excluded_lines"),
]


# --- the minimal artifacts ---------------------------------------------------------

def _istanbul() -> dict:
    """One function on lines 1-6: an if on line 2 (loc only) with one arm taken, a
    branch on line 3 (line only) with one arm taken, statements on lines 2, 3 and 5,
    the last never run."""
    return {FILE_KEYS["istanbul"]: {
        "path": FILE_KEYS["istanbul"],
        "fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1, "column": 0}},
                        "loc": {"start": {"line": 1, "column": 12},
                                "end": {"line": 6, "column": 1}}}},
        "f": {"0": 1},
        "branchMap": {"0": {"loc": {"start": {"line": 2, "column": 2}},
                            "locations": [{"start": {"line": 2}}, {"start": {"line": 4}}]},
                      "1": {"line": 3}},
        "b": {"0": [1, 0], "1": [0, 1]},
        "statementMap": {"0": {"start": {"line": 2, "column": 2}},
                         "1": {"start": {"line": 3, "column": 2}},
                         "2": {"start": {"line": 5, "column": 2}}},
        "s": {"0": 1, "1": 1, "2": 0}}}


def _coveragepy() -> dict:
    """One function on lines 1-5 with 2 of 3 statements and 1 of 2 branches run,
    and the module's own bucket, which no row reads."""
    fn = {"start_line": 1, "executed_lines": [2, 3], "missing_lines": [5], "excluded_lines": [],
          "summary": {"num_statements": 3, "covered_lines": 2, "num_branches": 2,
                      "covered_branches": 1, "excluded_lines": 0}}
    module = {"start_line": 1, "executed_lines": [1], "missing_lines": [],
              "summary": {"num_statements": 1, "covered_lines": 1}}
    return {"meta": {"version": "7.13.2", "branch_coverage": True},
            "files": {FILE_KEYS["coveragepy"]: {"executed_lines": [1, 2, 3], "missing_lines": [5],
                                                "functions": {"f": fn, "": module}}},
            "totals": {"covered_lines": 3}}


def _key(node: object, segment: str):
    """The key or index `segment` names in `node`: `*` is the first member."""
    if isinstance(node, list):
        return 0 if segment == "*" else int(segment)
    if segment == "*":
        return next(iter(node))
    return segment


def _parent(doc: object, locator: str):
    """(the object holding the field, the field's key in it)."""
    *path, last = locator.split(".")
    node = doc
    for segment in path:
        node = node[_key(node, segment)]
    return node, _key(node, last)


def _json_artifact(make):
    def build(drop: str | None = None, retype: tuple[str, object] | None = None) -> bytes:
        doc = make()
        if drop is not None:
            node, key = _parent(doc, drop)
            del node[key]
        if retype is not None:
            node, key = _parent(doc, retype[0])
            node[key] = copy.deepcopy(retype[1])
        return json.dumps(doc).encode("utf-8")
    return build


_BUILDERS = {"istanbul": _json_artifact(_istanbul), "coveragepy": _json_artifact(_coveragepy)}


def minimal_artifact(format: str, drop: str | None = None,
                     retype: tuple[str, object] | None = None) -> bytes:
    """The format's minimal valid artifact, with the field at `drop` left out or
    the field at retype[0] set to retype[1]."""
    return _BUILDERS[format](drop, retype)


# --- the fields a recorded artifact holds ------------------------------------------

def generic(format: str, locator: str) -> str:
    """The locator with every member id spelled `*`, the way field_paths spells it."""
    segments, parent = [], ""
    for segment in locator.split("."):
        segments.append("*" if parent in MAPS[format] else segment)
        parent = segments[-1]
    return ".".join(segments)


def field_paths(format: str, doc: object) -> dict[str, tuple]:
    """generic locator -> the concrete key path of its first occurrence in `doc`,
    for every field (a key of an object that is not a map), in document order.
    A path through the empty key, coverage.py's module bucket, is skipped: no row
    reads it."""
    found: dict[str, tuple] = {}
    _walk(format, doc, "", (), found)
    return found


def _walk(format: str, node: object, name: str, path: tuple, found: dict) -> None:
    """Record each field under `node`, whose own generic segment is `name`."""
    if isinstance(node, list):
        for index, item in enumerate(node):
            _walk(format, item, "*", path + ((index, "*"),), found)
        return
    if not isinstance(node, dict):
        return
    is_map = name in MAPS[format]
    for key, value in node.items():
        if key == "":
            continue
        here = path + ((key, "*" if is_map else key),)
        if not is_map:
            found.setdefault(".".join(label for _, label in here), tuple(k for k, _ in here))
        _walk(format, value, here[-1][1], here, found)
