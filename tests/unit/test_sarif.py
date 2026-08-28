"""SARIF 2.1.0 emission: code-scanning UIs and PR annotation bots read this,
so ruleIds, levels, and locations are contract, not decoration."""
import json
from types import SimpleNamespace

from crapkit.sarif import (diff_uncovered_results, gate_results, over_target_results,
                           sarif_document)
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
    assert {"crapkit/over-target", "crapkit/gate", "crapkit/ratchet-regression",
            "crapkit/diff-uncovered"} <= rule_ids


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


# --- diff-uncovered: the changed lines no lane ran -------------------------
# These findings used to reach stderr and nowhere else, so the one output a
# code-scanning UI reads dropped them on the floor.

def test_every_uncovered_changed_line_gets_its_own_located_finding():
    first, second = diff_uncovered_results([("src/a.py", 12), ("src\\b.py", 4)])
    assert first["ruleId"] == "crapkit/diff-uncovered"
    assert first["level"] == "warning"
    loc = first["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/a.py"
    assert loc["region"]["startLine"] == 12
    assert second["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/b.py"
    assert second["locations"][0]["physicalLocation"]["region"]["startLine"] == 4


def test_a_fully_covered_diff_emits_nothing():
    assert diff_uncovered_results([]) == []


# --- verify's own emission wires all three finding kinds -------------------

def _verify_sarif(tmp_path, uncovered: list) -> list[dict]:
    from crapkit.cli import _emit_verify_findings

    args = SimpleNamespace(sarif="out.sarif", github=False)
    verdict = SimpleNamespace(gate_violations=(), ratchet_regressions=())
    _emit_verify_findings(tmp_path, args, verdict, uncovered)
    doc = json.loads((tmp_path / "out.sarif").read_text(encoding="utf-8"))
    return doc["runs"][0]["results"]


def test_verify_writes_a_finding_per_uncovered_changed_line(tmp_path):
    results = _verify_sarif(tmp_path, [("src/a.py", 12), ("src/a.py", 13)])
    assert [r["ruleId"] for r in results] == ["crapkit/diff-uncovered"] * 2
    assert [r["locations"][0]["physicalLocation"]["region"]["startLine"] for r in results] \
        == [12, 13]


def test_verify_writes_no_diff_uncovered_finding_when_the_diff_is_covered(tmp_path):
    assert _verify_sarif(tmp_path, []) == []
