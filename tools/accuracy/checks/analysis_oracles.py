"""analysis-oracles: what crapkit reads off source before coverage joins.

The hand, equivalence and oracle checks read crapkit through its CLI, once
per session per file set (tests/accuracy/analysis_oracles/conftest.py).
Seconds are serial ubuntu estimates from measured Windows runs.
"""
SHARD = "analysis"
_PACKET = "tests/accuracy/analysis_oracles/"
CHECKS = [
    {"name": "hand probes", "seconds": 8,
     "pytest": [_PACKET + "test_hand_probes.py"]},
    {"name": "Python reader against ast", "seconds": 8,
     "pytest": [_PACKET + "test_python_ast.py"]},
    {"name": "metamorphic source edits", "seconds": 6,
     "pytest": [_PACKET + "test_metamorphic_source.py"]},
]
