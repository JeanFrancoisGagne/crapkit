"""Zero-branch functions: statement coverage beats the binary invoked guess.
Branch coverage stays authoritative whenever branches exist — the coverage term
must measure the same structure the complexity term counts."""
import json

from crapkit.coverage_istanbul import FnCoverage, parse_istanbul
from crapkit.coverage_py import parse_coveragepy


def test_property_prefers_branches_then_statements_then_invoked():
    assert FnCoverage("f", 1, 9, True, 4, 1, 10, 10).coverage == 0.25, "branches win when present"
    assert FnCoverage("f", 1, 9, True, 0, 0, 4, 1).coverage == 0.25, "statements next"
    assert FnCoverage("f", 1, 9, True, 0, 0, 0, 0).coverage == 1.0, "invoked is the last resort"
    assert FnCoverage("f", 1, 9, False, 0, 0, 0, 0).coverage == 0.0


def test_istanbul_zero_branch_function_takes_statement_fraction():
    artifact = {
        "C:/r/src/a.ts": {
            "fnMap": {"0": {"name": "straight", "decl": {"start": {"line": 1}},
                            "loc": {"start": {"line": 1}, "end": {"line": 6}}}},
            "f": {"0": 2},
            "branchMap": {}, "b": {},
            "statementMap": {
                "0": {"start": {"line": 2}, "end": {"line": 2}},
                "1": {"start": {"line": 3}, "end": {"line": 3}},
                "2": {"start": {"line": 4}, "end": {"line": 4}},
                "3": {"start": {"line": 5}, "end": {"line": 5}},
            },
            "s": {"0": 2, "1": 2, "2": 0, "3": 0},
        }
    }
    (fn,) = parse_istanbul(json.dumps(artifact), repo_root="C:/r")["src/a.ts"]
    assert fn.coverage == 0.5, "2 of 4 statements ran; invoked-binary would have said 1.0"


def test_istanbul_statements_attach_to_the_innermost_span():
    artifact = {
        "C:/r/src/a.ts": {
            "fnMap": {
                "0": {"name": "outer", "decl": {"start": {"line": 1}},
                      "loc": {"start": {"line": 1}, "end": {"line": 10}}},
                "1": {"name": "inner", "decl": {"start": {"line": 3}},
                      "loc": {"start": {"line": 3}, "end": {"line": 6}}},
            },
            "f": {"0": 1, "1": 1},
            "branchMap": {}, "b": {},
            "statementMap": {
                "0": {"start": {"line": 2}, "end": {"line": 2}},   # outer's own
                "1": {"start": {"line": 4}, "end": {"line": 4}},   # inner's
                "2": {"start": {"line": 5}, "end": {"line": 5}},   # inner's
            },
            "s": {"0": 1, "1": 0, "2": 0},
        }
    }
    fns = {f.name: f for f in parse_istanbul(json.dumps(artifact), repo_root="C:/r")["src/a.ts"]}
    assert fns["outer"].coverage == 1.0, "outer answers only for its own statement"
    assert fns["inner"].coverage == 0.0, "inner's dead statements are inner's"


def test_coveragepy_zero_branch_function_takes_statement_fraction():
    report = {
        "meta": {"branch_coverage": True},
        "files": {"pylib/mod.py": {"functions": {
            "straight": {"start_line": 1, "executed_lines": [2, 3], "missing_lines": [4, 5],
                         "summary": {"covered_lines": 2, "num_statements": 4,
                                     "num_branches": 0, "covered_branches": 0}},
        }}},
    }
    (fn,) = parse_coveragepy(json.dumps(report), path_prefix="")["pylib/mod.py"]
    assert fn.coverage == 0.5
