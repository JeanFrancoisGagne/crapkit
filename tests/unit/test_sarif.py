"""SARIF 2.1.0 emission: code-scanning UIs and PR annotation bots read this,
so ruleIds, levels, and locations are contract, not decoration."""
from crapkit.sarif import gate_results, over_target_results, sarif_document
from crapkit.score import ScoredRow
from crapkit.verify import GateViolation


def scored(path="src/a.ts", name="f( )", ccn=8, cov=0.0, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
    return ScoredRow(scope, path, name, 3, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "decompose")


def test_document_shape_and_rule_registration():
    doc = sarif_document(over_target_results([scored()], {"src": 6}, 6))
    assert doc["version"] == "2.1.0"
    (run,) = doc["runs"]
    assert run["tool"]["driver"]["name"] == "crapkit"
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert {"crapkit/over-target", "crapkit/gate", "crapkit/ratchet-regression"} <= rule_ids


def test_over_target_results_locate_the_function():
    (res,) = over_target_results([scored(ccn=8, cov=0.0)], {}, 6)
    assert res["ruleId"] == "crapkit/over-target"
    assert res["level"] == "warning"
    loc = res["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/a.ts"
    assert loc["region"]["startLine"] == 3
    assert "72" in res["message"]["text"]


def test_under_target_rows_emit_nothing():
    assert over_target_results([scored(ccn=2, cov=1.0)], {}, 6) == []


def test_gate_violations_are_errors():
    v = GateViolation("src/a.ts", "f( )", 3, 9, 0.0, 81.0, "decompose")
    (res,) = gate_results([v])
    assert res["ruleId"] == "crapkit/gate"
    assert res["level"] == "error"
    assert res["locations"][0]["physicalLocation"]["region"]["startLine"] == 3
