r"""SARIF 2.1.0 and GitHub workflow-command emission. Pure builders.

Code-scanning UIs and PR annotation bots consume this; ruleIds, levels, and
locations are contract. Every uri is repo-relative with forward slashes, each
byte of the name percent-encoded as git gives it: a name that is not UTF-8
keeps its own bytes (`src/caf%E9.py`), and an annotation names that file the
way every crapkit message does, `src/caf\xe9.py`.
"""
from __future__ import annotations

from urllib.parse import quote, unquote_to_bytes

from . import __version__
from .repotext import backslashed
from .score import over_ceiling

_RULES = (
    {"id": "crapkit/over-target",
     "shortDescription": {"text": "CRAP score above the scope ceiling"}},
    {"id": "crapkit/gate",
     "shortDescription": {"text": "touched function over the complexity gate"}},
    {"id": "crapkit/ratchet-regression",
     "shortDescription": {"text": "a recorded CRAP mark got worse"}},
    {"id": "crapkit/diff-uncovered",
     "shortDescription": {"text": "a changed line no lane ever ran"}},
    {"id": "crapkit/unread",
     "shortDescription": {"text": "a changed file no reader could read, so the gate judged none of it"}},
    {"id": "crapkit/unreadable-name",
     "shortDescription": {"text": "a file a scope takes whose name is not UTF-8, so no gate can judge it"}},
)


def _result(rule_id: str, level: str, path: str, line: int, text: str) -> dict:
    return {
        "ruleId": rule_id, "level": level,
        "message": {"text": text},
        "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": quote(_name_bytes(path), safe="/")},
            "region": {"startLine": line},
        }}],
    }


def _name_bytes(path: str) -> bytes:
    """The bytes git names the file in: each surrogate a name that is not UTF-8
    holds goes back to its byte. A lone surrogate outside that range, which
    only a Windows command line hands over, stays the broken UTF-16 it is."""
    try:
        return path.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return path.encode("utf-8", "surrogatepass")


def over_target_results(scored, scope_targets: dict, target: int) -> list[dict]:
    out = []
    for r in scored:
        ceiling = scope_targets.get(r.scope, target)
        if not over_ceiling(r.crap, ceiling):
            continue
        out.append(_result(
            "crapkit/over-target", "warning", r.path, r.start,
            f"{r.long_name}: CRAP {r.crap:.1f} over ceiling {ceiling} "
            f"(ccn {r.ccn}, cov {r.cov:.0%}) -> {r.remedy}"))
    return out


def finding_result(form, entry) -> dict:
    """One verdict finding as a result, in the SARIF form its kind's row gives
    (`verify.FINDING_KINDS`): its rule, level, anchor and message."""
    path, line = form.place(entry)
    return _result(form.rule, form.level, path, line, form.message(entry))


# Where OASIS publishes the SARIF 2.1.0 schema, and the id the schema declares for
# itself. SARIF 3.13.3 asks for a URI the schema can be obtained from; the
# oasis-tcs/sarif-spec master/Schemata path crapkit printed before answers 404.
SCHEMA_URI = ("https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/"
              "sarif-schema-2.1.0.json")


def sarif_document(results: list[dict]) -> dict:
    return {
        "$schema": SCHEMA_URI,
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


def _property(text: str) -> str:
    """Workflow properties also escape the separators that the runner parses."""
    return _esc(text).replace(":", "%3A").replace(",", "%2C")


def github_annotation(result: dict) -> str:
    loc = result["locations"][0]["physicalLocation"]
    named = backslashed(unquote_to_bytes(loc["artifactLocation"]["uri"]))
    return (f"::{result['level']} file={_property(named)},"
            f"line={loc['region']['startLine']},title={_property(result['ruleId'])}"
            f"::{_esc(result['message']['text'])}")
