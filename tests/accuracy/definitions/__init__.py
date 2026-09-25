"""The definitions packet: every field crapkit prints means one thing in every place.

definitions.tsv holds one row per field (cov, crap, flag, remedy, nesting, nloc,
params, target): the definition, its outside source or README anchor, and the
phrases each of the five places must carry. Those places are README.md,
CONTEXT.md, docs/agent-json.md, the MCP tools' output schemas and the SARIF
rules. test_definitions reads the places without importing crapkit, and
test_definition_models checks crapkit's rows against each definition on a
repo whose expected values are worked by hand first.
"""
