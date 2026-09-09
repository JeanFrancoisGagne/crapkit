"""Coverage artifacts must carry counts that can represent a measurement."""
import hashlib
import json

import pytest

from crapkit.covstream import parse_coveragepy_both_file, parse_istanbul_both_file
from crapkit.errors import ToolError


def python_report(**counts):
    summary = dict(num_branches=2, covered_branches=1, num_statements=2, covered_lines=1)
    summary.update(counts)
    return {"meta": {"branch_coverage": True}, "files": {"src/a.py": {
        "functions": {"work": {"start_line": 1, "executed_lines": [1],
                                "missing_lines": [2], "summary": summary}},
        "missing_lines": [2]}}}


@pytest.mark.parametrize("field", ["num_branches", "covered_branches", "num_statements", "covered_lines"])
@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_python_rejects_invalid_counts_with_artifact_and_function(tmp_path, field, bad):
    artifact = tmp_path / "measurement.json"
    artifact.write_text(json.dumps(python_report(**{field: bad})), encoding="utf-8")
    with pytest.raises(ToolError) as error:
        parse_coveragepy_both_file(artifact, path_prefix="", chunk=7)
    assert str(artifact) in str(error.value)
    assert "work" in str(error.value) and field in str(error.value)


@pytest.mark.parametrize("field", ["covered_branches", "covered_lines"])
def test_python_rejects_covered_above_total(tmp_path, field):
    artifact = tmp_path / "impossible.json"
    artifact.write_text(json.dumps(python_report(**{field: 4})), encoding="utf-8")
    with pytest.raises(ToolError, match=field):
        parse_coveragepy_both_file(artifact, path_prefix="")


def istanbul_report(group, bad):
    cov = {"fnMap": {"0": {"name": "work", "decl": {"start": {"line": 1}},
                            "loc": {"end": {"line": 3}}}}, "f": {"0": 1},
           "branchMap": {"0": {"loc": {"start": {"line": 2}}}}, "b": {"0": [1, 0]},
           "statementMap": {"0": {"start": {"line": 2}}}, "s": {"0": 1}}
    cov[group]["0"] = [bad, 0] if group == "b" else bad
    return {"/repo/src/a.js": cov}


@pytest.mark.parametrize(("group", "bad"), [
    (group, bad) for group in ("f", "s", "b") for bad in (-1, True, 1.5)
    # A negative `b` is the one pair that is not corruption: the producer derives
    # an else-path as parent - if and that subtraction underflows, so it clamps
    # to 0 and is counted. test_istanbul_negative_branches.py holds that
    # contract. `f` and `s` are measured hit counts, so a negative there stays a
    # refusal, and a bool or a fraction stays a refusal in every group.
    if not (group == "b" and bad == -1)])
def test_istanbul_rejects_invalid_hit_counts(tmp_path, group, bad):
    artifact = tmp_path / "istanbul.json"
    artifact.write_text(json.dumps(istanbul_report(group, bad)), encoding="utf-8")
    with pytest.raises(ToolError) as error:
        parse_istanbul_both_file(artifact, repo_root="/repo", chunk=7)
    assert str(artifact) in str(error.value)
    assert group in str(error.value)


@pytest.mark.parametrize("branches,statements,expected", [(2, 2, 0.5), (0, 2, 0.5), (0, 0, 0.0)])
def test_valid_fallbacks_keep_the_original_digest(tmp_path, branches, statements, expected):
    report = python_report(num_branches=branches, covered_branches=bool(branches) * 1,
                           num_statements=statements, covered_lines=bool(statements) * 1)
    artifact = tmp_path / "valid.json"
    raw = json.dumps(report).encode("utf-8")
    artifact.write_bytes(raw)
    measured, dead, digest = parse_coveragepy_both_file(artifact, path_prefix="", chunk=7)
    assert measured["src/a.py"][0].coverage == expected
    assert dead == {"src/a.py": {2}}
    assert digest == hashlib.sha256(raw).hexdigest()
