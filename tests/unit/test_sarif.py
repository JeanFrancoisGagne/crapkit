"""SARIF 2.1.0 emission: code-scanning UIs and PR annotation bots read this,
so ruleIds, levels, and locations are contract, not decoration."""
from fractions import Fraction

from accuracy.kit import exact

import json
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from crapkit.sarif import github_annotation, over_target_results, sarif_document
from crapkit.score import ScoredRow
from crapkit.gate import Unread
from crapkit.verify import GateViolation, Verdict, sarif_results


def holding(**fields) -> Verdict:
    return Verdict.passing()._replace(**fields)


def scored(path="src/a.ts", name="f( )", ccn=8, cov=0.0, scope="src"):
    c = float(exact.crap(ccn, Fraction(cov)))
    return ScoredRow(scope, path, name, 3, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "decompose")


def test_document_shape_and_rule_registration():
    doc = sarif_document(over_target_results([scored()], {"src": 6}, 6))
    assert doc["version"] == "2.1.0"
    (run,) = doc["runs"]
    assert run["tool"]["driver"]["name"] == "crapkit"
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert {"crapkit/over-target", "crapkit/gate", "crapkit/ratchet-regression",
            "crapkit/diff-uncovered", "crapkit/unread"} <= rule_ids


# The identifier the SARIF 2.1.0 schema document declares for itself: the OASIS
# errata01 location, which answered HTTP 200 on 2026-09-26. The oasis-tcs/sarif-spec
# master/Schemata path crapkit used to print answered 404.
OASIS_SCHEMA = ("https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/"
                "sarif-schema-2.1.0.json")


def test_the_schema_uri_is_where_oasis_publishes_the_schema():
    """SARIF 2.1.0 errata01 section 3.13.3: `$schema` is an absolute URI from
    which the schema can be obtained, so a validator that fetches it gets the
    schema and not a 404."""
    assert sarif_document([])["$schema"] == OASIS_SCHEMA


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
    (res,) = sarif_results(holding(gate_violations=[v]))
    assert res["ruleId"] == "crapkit/gate"
    assert res["level"] == "error"
    assert res["locations"][0]["physicalLocation"]["region"]["startLine"] == 3


def test_a_changed_file_no_reader_could_read_is_an_error_on_its_first_line():
    unread = Unread("src/a.ts", "src/a.ts:12: arrow refused")
    (res,) = sarif_results(holding(unread_files=(unread,)))
    assert res["ruleId"] == "crapkit/unread"
    assert res["level"] == "error"
    loc = res["locations"][0]["physicalLocation"]
    assert (loc["artifactLocation"]["uri"], loc["region"]["startLine"]) == ("src/a.ts", 1)
    assert "src/a.ts:12: arrow refused" in res["message"]["text"]


# --- diff-uncovered: the changed lines no lane ran -------------------------
# These findings used to reach stderr and nowhere else, so the one output a
# code-scanning UI reads dropped them on the floor.

def test_every_uncovered_changed_line_gets_its_own_located_finding():
    first, second = sarif_results(Verdict.passing(), [("src/a.py", 12), ("src/b.py", 4)])
    assert first["ruleId"] == "crapkit/diff-uncovered"
    assert first["level"] == "warning"
    loc = first["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/a.py"
    assert loc["region"]["startLine"] == 12
    assert second["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/b.py"
    assert second["locations"][0]["physicalLocation"]["region"]["startLine"] == 4


def test_a_fully_covered_diff_emits_nothing():
    assert sarif_results(Verdict.passing(), []) == []


# --- verify's own emission reads every finding kind's row ------------------

def _verify_sarif(tmp_path, uncovered: list) -> list[dict]:
    from crapkit.cli.verifying import _emit_verify_findings

    args = SimpleNamespace(sarif="out.sarif", github=False)
    _emit_verify_findings(tmp_path, args, Verdict.passing(), uncovered)
    doc = json.loads((tmp_path / "out.sarif").read_text(encoding="utf-8"))
    return doc["runs"][0]["results"]


def test_verify_writes_a_finding_per_uncovered_changed_line(tmp_path):
    results = _verify_sarif(tmp_path, [("src/a.py", 12), ("src/a.py", 13)])
    assert [r["ruleId"] for r in results] == ["crapkit/diff-uncovered"] * 2
    assert [r["locations"][0]["physicalLocation"]["region"]["startLine"] for r in results] \
        == [12, 13]


def test_verify_writes_no_diff_uncovered_finding_when_the_diff_is_covered(tmp_path):
    assert _verify_sarif(tmp_path, []) == []


# --- a path in every shape a consumer has to read back -------------------------
#
# Code scanning percent-decodes the SARIF uri, and the Actions runner unescapes
# `%`, `:` and `,` in an annotation's file property. Each path below comes back
# as the file it names from both.

@pytest.mark.parametrize("path", ["pkg/branchy.py", "pkg/café_世.py", "pkg/a, b.py",
                                  "pkg/100%25.py", "pkg/a:b.py"],
                         ids=["ascii", "non-ascii", "comma-and-space", "a-percent-sign", "a-colon"])
def test_a_finding_s_path_reads_back_as_the_file_from_the_uri_and_the_annotation(path):
    (result,) = sarif_results(holding(
        gate_violations=[GateViolation(path, "f( )", 3, 9, 0.0, 81.0, "decompose")]))

    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    prop = github_annotation(result).split("file=", 1)[1].split(",line=", 1)[0]

    assert unquote(uri) == path
    assert prop.replace("%2C", ",").replace("%3A", ":").replace("%25", "%") == path
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 3
