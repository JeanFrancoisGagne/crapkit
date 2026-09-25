"""The definitions packet's checks: tests/accuracy/definitions.

Every field means one thing in README.md, CONTEXT.md, docs/agent-json.md, the
MCP output schemas and the SARIF rules, and crapkit's rows follow that
meaning on a repo whose values were worked by hand. Seconds are serial ubuntu
estimates: one `crapkit mcp` listing for the first check, and one git build,
one coverage.py run, one `crapkit coverage` and three briefs for the second.
Measured in the accuracy image on a copied tree (no bind mount), on a loaded
desktop: 0.5 s and 4.8 s.
"""
SHARD = "corpus"
_HERE = "tests/accuracy/definitions/"
CHECKS = [
    {"name": "field definitions in the five places", "seconds": 1,
     "pytest": [_HERE + "test_definitions.py"]},
    {"name": "rows follow the field definitions", "seconds": 4,
     "pytest": [_HERE + "test_definition_models.py"]},
]
