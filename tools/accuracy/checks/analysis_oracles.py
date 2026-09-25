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
    {"name": "ccn against radon, mccabe and the gate rule", "seconds": 6,
     "pytest": [_PACKET + "test_complexity_oracles.py"]},
    {"name": "cognitive against the Sonar counter and complexipy", "seconds": 5,
     "pytest": [_PACKET + "test_cognitive_oracles.py"]},
    {"name": "nesting against the depth model and pylint", "seconds": 3,
     "pytest": [_PACKET + "test_nesting_oracles.py"]},
]
