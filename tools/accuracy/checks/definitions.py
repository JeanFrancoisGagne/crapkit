"""The definitions packet's checks: tests/accuracy/definitions.

Every field means one thing in README.md, CONTEXT.md, docs/agent-json.md, the
MCP output schemas and the SARIF rules, and crapkit's rows follow that
meaning on a repo whose values were worked by hand. Seconds are serial ubuntu
estimates: one `crapkit mcp` listing for the first check, and one git build,
one coverage.py run, one `crapkit coverage` and three briefs for the second.
"""
SHARD = "corpus"
_HERE = "tests/accuracy/definitions/"
CHECKS = [
    {"name": "field definitions in the five places", "seconds": 2,
     "pytest": [_HERE + "test_definitions.py"]},
    {"name": "rows follow the field definitions", "seconds": 3,
     "pytest": [_HERE + "test_definition_models.py"]},
]
