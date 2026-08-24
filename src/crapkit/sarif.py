"""SARIF 2.1.0 and GitHub workflow-command emission. Pure builders.

Code-scanning UIs and PR annotation bots consume this; ruleIds, levels, and
locations are contract. Every uri is repo-relative with forward slashes.
"""
from __future__ import annotations

from . import __version__

_RULES = (
    {"id": "crapkit/over-target",
     "shortDescription": {"text": "CRAP score above the scope ceiling"}},
    {"id": "crapkit/gate",
     "shortDescription": {"text": "touched function over the complexity gate"}},
    {"id": "crapkit/ratchet-regression",
     "shortDescription": {"text": "a recorded CRAP mark got worse"}},
)


def _result(rule_id: str, level: str, path: str, line: int, text: str) -> dict:
    return {
        "ruleId": rule_id, "level": level,
        "message": {"text": text},
        "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": path.replace("\\", "/")},
            "region": {"startLine": line},
        }}],
    }


def over_target_results(scored, scope_targets: dict, target: int) -> list[dict]:
    out = []
    for r in scored:
        ceiling = scope_targets.get(r.scope, target)
        if r.crap <= ceiling:
            continue
        out.append(_result(
            "crapkit/over-target", "warning", r.path, r.start,
            f"{r.long_name}: CRAP {r.crap:.1f} over ceiling {ceiling} "
            f"(ccn {r.ccn}, cov {r.cov:.0%}) -> {r.remedy}"))
    return out


def gate_results(violations) -> list[dict]:
    return [_result("crapkit/gate", "error", v.path, v.start,
                    f"{v.long_name}: CRAP {v.crap:.1f} (ccn {v.ccn}, cov {v.cov:.0%}) -> {v.remedy}")
            for v in violations]


def regression_results(regressions) -> list[dict]:
    # the ratchet stores no line numbers; line 1 anchors the file-level finding
    return [_result("crapkit/ratchet-regression", "error", r.path, 1,
                    f"{r.long_name}: recorded {r.recorded} -> fresh {r.fresh_crap}")
            for r in regressions]


def sarif_document(results: list[dict]) -> dict:
    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/"
                   "master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "crapkit",
                "version": __version__,
                "informationUri": "https://github.com/JeanFrancoisGagne/crapkit",
                "rules": [dict(r) for r in _RULES],
            }},
            "results": results,
        }],
    }


def _esc(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github_annotation(result: dict) -> str:
    loc = result["locations"][0]["physicalLocation"]
    return (f"::{result['level']} file={loc['artifactLocation']['uri']},"
            f"line={loc['region']['startLine']},title={result['ruleId']}"
            f"::{_esc(result['message']['text'])}")
