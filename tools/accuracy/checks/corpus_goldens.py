"""The corpus-goldens packet's checks: the small corpus, its goldens, cross-surface
agreement, SARIF and annotations, the MCP and CLI pairs, cross-platform digests,
printed commands, the Action scenarios, the wheel diff, the store upgrade and the
corpus tool.

Seconds are serial ubuntu estimates for the push tier; the shared small-corpus
measurement is paid once per session by whichever check reaches it first.
"""
SHARD = "corpus"
_PACKET = "tests/accuracy/corpus_goldens/"
CHECKS = [
    {"name": "small corpus and goldens", "seconds": 20,
     "pytest": [_PACKET + "test_goldens.py"]},
    {"name": "every surface prints the counts", "seconds": 10,
     "pytest": [_PACKET + "test_surfaces_agree.py"]},
    {"name": "SARIF schema, uris and annotation escapes", "seconds": 3,
     "pytest": [_PACKET + "test_sarif_annotations.py"]},
    {"name": "every MCP tool answers what its CLI command prints", "seconds": 3,
     "pytest": [_PACKET + "test_mcp_equals_cli.py"]},
    {"name": "the wheel diff's moved-row map and xplat rule", "seconds": 8,
     "pytest": [_PACKET + "test_wheel_diff_tool.py"]},
    {"name": "exports noted for the cross-platform receipts", "seconds": 1,
     "pytest": [_PACKET + "test_xplat_digest.py"]},
    {"name": "printed commands run in every shell", "seconds": 12,
     "pytest": [_PACKET + "test_printed_commands.py"]},
    {"name": "two runs normalize to one golden", "seconds": 15, "tiers": ["nightly"],
     "pytest": [_PACKET + "test_normalized_twice.py"]},
]
