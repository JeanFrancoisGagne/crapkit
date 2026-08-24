"""Streaming artifact readers: the same answer as the whole-document parsers,
without the whole document ever being in memory.

Every test here diffs a file-backed reader against the shipped text-backed one
on the same bytes. The chunk size is a parameter so the refill boundary lands
in the middle of a key, a value and a separator — a 1 MiB default hides those
seams on any fixture small enough to keep in a test.
"""
import hashlib
import json
from pathlib import Path

import pytest

from crapkit.coverage_istanbul import parse_istanbul, parse_istanbul_missing
from crapkit.covstream import parse_istanbul_file, parse_istanbul_missing_file
from crapkit.errors import ToolError

COMPOSITE = {
    'C:/repo/src/a "quoted" {braced}.ts': {
        "note": 'a { brace } a } close and an escaped \\" quote',
        "tail": "trailing backslash \\\\",
        "fnMap": {}, "f": {}, "branchMap": {}, "b": {}, "statementMap": {}, "s": {},
    },
    "C:/repo/src/\u00fcn\u00efcode-\u2603.ts": {
        "note": "emoji \U0001f3af and \"inner quotes\" and a , comma",
        "nested": {"deep": [1, 2, {"x": "}"}, None, True, -3.5e2]},
        "fnMap": {}, "f": {}, "branchMap": {}, "b": {}, "statementMap": {}, "s": {},
    },
    "C:/repo/src/plain.ts": {
        "fnMap": {"0": {"name": "go", "decl": {"start": {"line": 1}},
                        "loc": {"end": {"line": 4}}}},
        "f": {"0": 2}, "branchMap": {}, "b": {},
        "statementMap": {"0": {"start": {"line": 2}}, "1": {"start": {"line": 3}}},
        "s": {"0": 1, "1": 0},
    },
}

CHUNKS = [1, 2, 3, 7, 17, 64, 1 << 20]


def _write(tmp_path, obj, **dumps):
    path = tmp_path / "cov.json"
    path.write_bytes(json.dumps(obj, **dumps).encode("utf-8"))
    return path


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("indent", [None, 1])
def test_streamed_istanbul_equals_the_whole_document_parse(tmp_path, chunk, indent):
    path = _write(tmp_path, COMPOSITE, indent=indent)
    text = path.read_text(encoding="utf-8")

    per_file, digest = parse_istanbul_file(path, repo_root="C:/repo", chunk=chunk)

    assert per_file == parse_istanbul(text, repo_root="C:/repo")
    assert list(per_file) == list(parse_istanbul(text, repo_root="C:/repo"))
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("chunk", CHUNKS)
def test_streamed_missing_lines_equal_the_whole_document_parse(tmp_path, chunk):
    path = _write(tmp_path, COMPOSITE)
    text = path.read_text(encoding="utf-8")
    assert (parse_istanbul_missing_file(path, repo_root="C:/repo", chunk=chunk)
            == parse_istanbul_missing(text, repo_root="C:/repo"))


@pytest.mark.parametrize("chunk", CHUNKS)
def test_a_malformed_artifact_is_still_a_loud_error(tmp_path, chunk):
    path = tmp_path / "cov.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ToolError, match="istanbul"):
        parse_istanbul_file(path, repo_root="C:/repo", chunk=chunk)


@pytest.mark.parametrize("chunk", CHUNKS)
def test_an_artifact_measuring_nothing_is_still_a_loud_error(tmp_path, chunk):
    path = _write(tmp_path, {})
    with pytest.raises(ToolError, match="empty"):
        parse_istanbul_file(path, repo_root="C:/repo", chunk=chunk)


# --- the lane reads its artifact through the streaming reader --------------

def _lane(parser: str, artifact: str):
    from crapkit.config import Lane
    return Lane(name="ui", command="", artifact=artifact, parser=parser, scopes=())


def test_an_istanbul_lane_streams_its_artifact_off_the_file(tmp_path, monkeypatch):
    """The lane must hand the PATH to the streaming reader. Decoding the file
    into one str first is what put a second whole copy of a 150 MB artifact on
    the heap, and no equality test can see that."""
    from crapkit import lanes

    seen = []
    real = lanes.parse_istanbul_file

    def spy(path, **kwargs):
        seen.append(Path(path).name)
        return real(path, **kwargs)

    monkeypatch.setattr(lanes, "parse_istanbul_file", spy)
    _write(tmp_path, COMPOSITE)
    lanes.run_lane(tmp_path, _lane("istanbul", "cov.json"), reuse_artifact=True)
    assert seen == ["cov.json"]


# --- coverage.py: the "files" member split ---------------------------------

REPORT = {
    "meta": {"format": 3, "version": "7.13.1", "branch_coverage": True,
             "show_contexts": False, "note": 'braces { } and "quotes" and , commas'},
    "files": {
        # coverage.py spells a windows path with backslashes; both parsers fold them
        "scripts\\a.py": {
            "executed_lines": [1, 2, 4], "missing_lines": [5, 9],
            "summary": {"covered_lines": 3, "num_statements": 5, "num_branches": 4,
                        "covered_branches": 2},
            "functions": {
                "": {"executed_lines": [1], "missing_lines": [],
                     "summary": {"covered_lines": 1, "num_statements": 1,
                                 "num_branches": 0, "covered_branches": 0}},
                "go": {"start_line": 2, "executed_lines": [2, 4], "missing_lines": [5],
                       "summary": {"covered_lines": 2, "num_statements": 3,
                                   "num_branches": 4, "covered_branches": 2}},
            },
        },
        "scripts/ünïcode-☃.py": {
            "executed_lines": [], "missing_lines": [1],
            "summary": {"covered_lines": 0, "num_statements": 1, "num_branches": 0,
                        "covered_branches": 0},
            "functions": {"lonely": {"start_line": 1, "executed_lines": [],
                                     "missing_lines": [1],
                                     "summary": {"covered_lines": 0, "num_statements": 1,
                                                 "num_branches": 2, "covered_branches": 0}}},
        },
    },
    "totals": {"covered_lines": 3, "num_statements": 6, "percent_covered": 50.0},
}


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("indent", [None, 1])
def test_streamed_coveragepy_equals_the_whole_document_parse(tmp_path, chunk, indent):
    from crapkit.coverage_py import parse_coveragepy
    from crapkit.covstream import parse_coveragepy_file

    path = _write(tmp_path, REPORT, indent=indent)
    text = path.read_text(encoding="utf-8")

    per_file, digest = parse_coveragepy_file(path, path_prefix="", chunk=chunk)

    whole = parse_coveragepy(text, path_prefix="")
    assert per_file == whole
    assert list(per_file) == list(whole)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("chunk", CHUNKS)
def test_streamed_coveragepy_applies_the_lane_path_prefix(tmp_path, chunk):
    from crapkit.coverage_py import parse_coveragepy
    from crapkit.covstream import parse_coveragepy_file

    path = _write(tmp_path, REPORT)
    per_file, _ = parse_coveragepy_file(path, path_prefix="scripts/", chunk=chunk)
    assert per_file == parse_coveragepy(path.read_text(encoding="utf-8"),
                                        path_prefix="scripts/")


@pytest.mark.parametrize("chunk", CHUNKS)
def test_streamed_coveragepy_missing_equals_the_whole_document_parse(tmp_path, chunk):
    from crapkit.coverage_py import parse_coveragepy_missing
    from crapkit.covstream import parse_coveragepy_missing_file

    path = _write(tmp_path, REPORT)
    assert (parse_coveragepy_missing_file(path, path_prefix="py", chunk=chunk)
            == parse_coveragepy_missing(path.read_text(encoding="utf-8"), path_prefix="py"))


@pytest.mark.parametrize("chunk", CHUNKS)
def test_a_report_without_branch_data_is_still_refused(tmp_path, chunk):
    from crapkit.covstream import parse_coveragepy_file

    report = {**REPORT, "meta": {"branch_coverage": False}}
    path = _write(tmp_path, report)
    with pytest.raises(ToolError, match="branch data"):
        parse_coveragepy_file(path, path_prefix="", chunk=chunk)


@pytest.mark.parametrize("chunk", CHUNKS)
def test_a_file_without_function_regions_is_still_refused(tmp_path, chunk):
    from crapkit.covstream import parse_coveragepy_file

    report = {**REPORT, "files": {"a.py": {"executed_lines": []}}}
    path = _write(tmp_path, report)
    with pytest.raises(ToolError, match="function regions"):
        parse_coveragepy_file(path, path_prefix="", chunk=chunk)


@pytest.mark.parametrize("chunk", CHUNKS)
def test_a_malformed_report_is_still_a_loud_error(tmp_path, chunk):
    from crapkit.covstream import parse_coveragepy_file

    path = tmp_path / "cov.json"
    path.write_text('{"files": ', encoding="utf-8")
    with pytest.raises(ToolError, match="coverage.py"):
        parse_coveragepy_file(path, path_prefix="", chunk=chunk)


def test_a_coveragepy_lane_streams_its_artifact_off_the_file(tmp_path, monkeypatch):
    from crapkit import lanes

    seen = []
    real = lanes.parse_coveragepy_file

    def spy(path, **kwargs):
        seen.append(Path(path).name)
        return real(path, **kwargs)

    monkeypatch.setattr(lanes, "parse_coveragepy_file", spy)
    _write(tmp_path, REPORT)
    lanes.run_lane(tmp_path, _lane("coveragepy", "cov.json"), reuse_artifact=True)
    assert seen == ["cov.json"]


# --- the line-level truth reads its artifacts the same way -----------------

def test_missing_by_path_streams_every_lane_artifact(tmp_path, monkeypatch):
    """`explain` and `worklist` ask every lane for its dead lines. Reading each
    artifact whole put all of them on the heap one after another."""
    from types import SimpleNamespace

    from crapkit import covstream, uncovered

    seen = []
    for name in ("parse_istanbul_missing_file", "parse_coveragepy_missing_file"):
        real = getattr(covstream, name)
        monkeypatch.setattr(covstream, name,
                            lambda path, _r=real, _n=name, **kw: (seen.append(_n), _r(path, **kw))[1])

    (tmp_path / "ist.json").write_text(json.dumps(COMPOSITE), encoding="utf-8")
    (tmp_path / "py.json").write_text(json.dumps(REPORT), encoding="utf-8")
    cfg = SimpleNamespace(lanes=[_lane("istanbul", "ist.json"), _lane("coveragepy", "py.json")])

    missing = uncovered.missing_by_path(tmp_path, cfg)

    assert seen == ["parse_istanbul_missing_file", "parse_coveragepy_missing_file"]
    # a path outside the repo root keeps its own spelling, as it always has
    assert missing["C:/repo/src/plain.ts"] == {3}
    assert missing["scripts/a.py"] == {5, 9}


@pytest.mark.parametrize("chunk", CHUNKS)
def test_a_report_written_with_sorted_keys_still_finds_its_branch_flag(tmp_path, chunk):
    """json.dump(sort_keys=True) writes "files" BEFORE "meta", so a reader that
    decides at the first file refuses a perfectly good report."""
    from crapkit.coverage_py import parse_coveragepy
    from crapkit.covstream import parse_coveragepy_file

    path = _write(tmp_path, REPORT, sort_keys=True)
    per_file, _ = parse_coveragepy_file(path, path_prefix="", chunk=chunk)
    assert per_file == parse_coveragepy(path.read_text(encoding="utf-8"), path_prefix="")


@pytest.mark.parametrize("chunk", CHUNKS)
def test_no_branch_data_outranks_a_bad_file_whatever_order_they_are_written_in(tmp_path, chunk):
    """The whole-document parser read meta before any file, so this report has
    always complained about branch data first. Member order must not flip it."""
    from crapkit.coverage_py import parse_coveragepy
    from crapkit.covstream import parse_coveragepy_file

    report = {"files": {"a.py": {"executed_lines": []}}, "meta": {"branch_coverage": False}}
    path = _write(tmp_path, report, sort_keys=True)
    with pytest.raises(ToolError, match="branch data"):
        parse_coveragepy(path.read_text(encoding="utf-8"), path_prefix="")
    with pytest.raises(ToolError, match="branch data"):
        parse_coveragepy_file(path, path_prefix="", chunk=chunk)


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("text", ["{ truncated", '{"files": {}} and then some', "[1, 2]"])
def test_a_truncated_report_is_unreadable_not_empty(tmp_path, chunk, text):
    """A walk that stops at the first thing it cannot read reports NO dark lines,
    which reads exactly like a fully covered repo. It has to fail instead."""
    from crapkit.coverage_py import parse_coveragepy_missing
    from crapkit.covstream import parse_coveragepy_missing_file

    path = tmp_path / "cov.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ToolError, match="coverage.py"):
        parse_coveragepy_missing(text, path_prefix="")
    with pytest.raises(ToolError, match="coverage.py"):
        parse_coveragepy_missing_file(path, path_prefix="", chunk=chunk)
