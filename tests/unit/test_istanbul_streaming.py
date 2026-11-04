"""The streaming istanbul parser must be indistinguishable from the whole-document one.

Two seams here replace stdlib or straight-line code, and both are diffed against
what they replaced rather than against fixtures:

- split_top_level replaces json.loads on the outer object, so it has to agree
  with json.loads on keys and values carrying braces, escaped quotes, backslashes
  and non-ASCII — the characters a hand-rolled scanner gets wrong.
- _span_owners replaces a linear scan whose tie-break decided which of two
  equally sized spans owned a line, so the sweep is diffed against the scan
  itself on a dense file built with deliberate length ties. The tie decides 27
  of that file's 735 queried lines; resolving it the other way moves 30 line
  owners and 14 of the 200 FnCoverage rows, which is what these tests catch.
"""
import codecs
import hashlib
import json
import random

import pytest

from crapkit.config import Lane
from crapkit.coverage_istanbul import _SIGNATURE, _fn_spans
from crapkit.score import FnCoverage, span_owners as _span_owners
from coverage_readers import parse_istanbul, parse_istanbul_missing, split_top_level
from crapkit.errors import ToolError
from crapkit.lanes import run_lane

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
        "fnMap": {"0": {"name": "go", "decl": {"start": {"line": 1}}, "loc": {"end": {"line": 4}}}},
        "f": {"0": 2}, "branchMap": {}, "b": {},
        "statementMap": {"0": {"start": {"line": 2}}, "1": {"start": {"line": 3}}},
        "s": {"0": 1, "1": 0},
    },
}


# --- the top-level splitter agrees with json.loads -------------------------

@pytest.mark.parametrize("ensure_ascii", [True, False])
@pytest.mark.parametrize("indent", [None, 2])
def test_splitter_output_equals_json_loads_on_a_composite_artifact(ensure_ascii, indent):
    text = json.dumps(COMPOSITE, ensure_ascii=ensure_ascii, indent=indent)
    rebuilt = dict(split_top_level(text))
    whole = json.loads(text)
    assert rebuilt == whole
    assert list(rebuilt) == list(whole), "member order must survive the split"


def test_splitter_handles_scalar_array_and_null_values():
    text = json.dumps({"a": {"x": 1}, "b": [1, 2], "c": None, "d": 3.5, "e": "s", "f": True})
    assert dict(split_top_level(text)) == json.loads(text)


def test_splitter_keeps_the_last_value_of_a_duplicated_key_like_json_loads():
    text = '{"a": {"v": 1}, "a": {"v": 2}}'
    assert dict(split_top_level(text)) == json.loads(text) == {"a": {"v": 2}}


def test_splitter_yields_nothing_for_an_empty_object():
    assert list(split_top_level("{ }")) == []


def test_splitter_refuses_a_non_object_top_level():
    with pytest.raises(ValueError):
        list(split_top_level("[1, 2]"))


def test_splitter_refuses_content_trailing_the_object():
    with pytest.raises(ValueError):
        list(split_top_level('{"a": {}} and then some'))


def test_streaming_parse_still_reports_malformed_and_empty_artifacts():
    with pytest.raises(ToolError, match="istanbul"):
        parse_istanbul("{ not json", repo_root="C:/repo")
    with pytest.raises(ToolError, match="empty"):
        parse_istanbul("{}", repo_root="C:/repo")
    with pytest.raises(ToolError, match="istanbul"):
        parse_istanbul_missing("{ not json", repo_root="C:/repo")


def test_composite_artifact_parses_through_both_public_entry_points():
    text = json.dumps(COMPOSITE)
    per_file = parse_istanbul(text, repo_root="C:/repo")
    assert list(per_file) == ["src/a \"quoted\" {braced}.ts",
                              "src/\u00fcn\u00efcode-\u2603.ts", "src/plain.ts"]
    assert per_file["src/plain.ts"] == [
        FnCoverage("go", 1, 4, True, 0, 0, 2, 1, full_listing=True)]
    assert parse_istanbul_missing(text, repo_root="C:/repo")["src/plain.ts"] == {3}


# --- the span sweep reproduces the linear scan, tie-break included ---------

def _innermost_scan(fn_spans, line):
    """The pre-sweep linear scan, kept verbatim as the sweep's oracle."""
    best = None
    for span in fn_spans:
        if span[1] <= line <= span[2]:
            if best is None or (span[2] - span[1], -span[1]) < (best[2] - best[1], -best[1]):
                best = span
    return best


def _taken(hits):
    return sum(1 for h in hits if h > 0)


def _scan_add(spans, line, total_at, total, covered):
    best = _innermost_scan(spans, line)
    if best is not None:
        best[total_at] += total
        best[total_at + 1] += covered


def _scan_attribution(cov):
    """parse_istanbul's per-file result as the scan produced it."""
    spans = _fn_spans(cov)
    for bid, branch in cov["branchMap"].items():
        hits = cov["b"].get(bid, [])
        _scan_add(spans, branch["loc"]["start"]["line"], 4, len(hits), _taken(hits))
    for sid, stmt in cov["statementMap"].items():
        _scan_add(spans, stmt["start"]["line"], 6, 1, _taken([cov["s"].get(sid, 0)]))
    return [FnCoverage(*s[:8], full_listing=bool(cov["statementMap"])) for s in spans]


def _dense_file(n_fns=200, seed=20260822):
    """Nested and overlapping spans with deliberate length ties: several spans
    share a length so the (length, -start) key alone cannot order them, which is
    exactly where an index-free heap key diverged from the scan."""
    rnd = random.Random(seed)
    fn_map, f_hits = {}, {}
    for i in range(n_fns):
        start = rnd.randint(1, 600)
        end = start + rnd.choice([0, 2, 2, 2, 8, 8, 30, 30, 150, 400])
        fn_map[str(i)] = {"name": f"fn{i}", "decl": {"start": {"line": start}},
                          "loc": {"end": {"line": end}}}
        f_hits[str(i)] = i % 3
    branch_map = {f"b{j}": {"loc": {"start": {"line": rnd.randint(1, 800)}}} for j in range(500)}
    stmt_map = {f"s{j}": {"start": {"line": rnd.randint(1, 800)}} for j in range(1500)}
    return {"fnMap": fn_map, "f": f_hits,
            "branchMap": branch_map, "b": {k: [j % 2, (j + 1) % 3] for j, k in enumerate(branch_map)},
            "statementMap": stmt_map, "s": {k: j % 4 for j, k in enumerate(stmt_map)}}


def test_sweep_picks_the_same_owner_as_the_scan_for_every_line():
    cov = _dense_file()
    spans = _fn_spans(cov)
    lines = set(range(0, 1100))
    owners = _span_owners(spans, {(ln, 0) for ln in lines}, _SIGNATURE)
    mismatches = [ln for ln in sorted(lines)
                  if owners.get((ln, 0)) is not _innermost_scan(spans, ln)]
    assert mismatches == [], f"{len(mismatches)} lines got a different owner, first {mismatches[:5]}"


def test_dense_artifact_attributes_identically_to_the_scan():
    cov = _dense_file()
    text = json.dumps({"C:/repo/src/dense.ts": cov})
    assert parse_istanbul(text, repo_root="C:/repo") == {"src/dense.ts": _scan_attribution(cov)}


def test_ties_on_span_length_keep_the_first_span_in_start_order():
    # Two spans of identical length starting on the same line: the scan kept the
    # first it met, so the sweep must too.
    cov = {"fnMap": {"0": {"name": "first", "decl": {"start": {"line": 10}}, "loc": {"end": {"line": 20}}},
                     "1": {"name": "second", "decl": {"start": {"line": 10}}, "loc": {"end": {"line": 20}}}},
           "f": {"0": 1, "1": 1},
           "branchMap": {"b0": {"loc": {"start": {"line": 15}}}}, "b": {"b0": [1, 0]},
           "statementMap": {}, "s": {}}
    spans = _fn_spans(cov)
    assert _span_owners(spans, {(15, 0)}, _SIGNATURE)[(15, 0)] is _innermost_scan(spans, 15)
    assert parse_istanbul(json.dumps({"C:/repo/a.ts": cov}), repo_root="C:/repo") == {
        "a.ts": _scan_attribution(cov)}


# --- the lane digest is the artifact's own bytes ---------------------------

def _istanbul_lane():
    return Lane(name="ui", command="", artifact="cov.json", parser="istanbul", scopes=())


@pytest.mark.parametrize("ensure_ascii", [True, False])
@pytest.mark.parametrize("indent", [None, 1])
def test_lane_provenance_hashes_the_artifact_bytes(tmp_path, ensure_ascii, indent):
    artifact = tmp_path / "cov.json"
    artifact.write_bytes(json.dumps(COMPOSITE, ensure_ascii=ensure_ascii,
                                    indent=indent).encode("utf-8"))

    prov = run_lane(tmp_path, _istanbul_lane(), reuse_artifact=True).provenance

    assert prov["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    # The pre-change formula was sha256(read_text(...).encode("utf-8")). Decoding
    # UTF-8 and encoding it back is the identity, so the recorded provenance value
    # for a given file does not move.
    round_trip = artifact.read_text(encoding="utf-8").encode("utf-8")
    assert prov["artifact_sha256"] == hashlib.sha256(round_trip).hexdigest()


def test_a_bom_prefixed_artifact_reads_past_the_mark_and_hashes_the_mark_too(tmp_path):
    """A JSON artifact is read past its UTF-8 BOM, as every other JSON file an
    outside tool writes is. The digest stays the file's own bytes, mark and all,
    so a copy saved with and without the mark reads the same and hashes apart."""
    artifact = tmp_path / "cov.json"
    body = json.dumps(COMPOSITE).encode("utf-8")
    artifact.write_bytes(body)
    plain = run_lane(tmp_path, _istanbul_lane(), reuse_artifact=True)
    artifact.write_bytes(codecs.BOM_UTF8 + body)

    marked = run_lane(tmp_path, _istanbul_lane(), reuse_artifact=True)

    assert marked.coverage == plain.coverage
    assert marked.provenance["artifact_sha256"] == hashlib.sha256(
        codecs.BOM_UTF8 + body).hexdigest()


def test_lane_digest_is_stable_across_two_reads_of_the_same_file(tmp_path):
    (tmp_path / "cov.json").write_text(json.dumps(COMPOSITE), encoding="utf-8")
    lane = _istanbul_lane()
    first = run_lane(tmp_path, lane, reuse_artifact=True)[1]["artifact_sha256"]
    second = run_lane(tmp_path, lane, reuse_artifact=True)[1]["artifact_sha256"]
    assert first == second
