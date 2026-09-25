"""analysis-oracles: what crapkit reads off source before coverage joins.

The hand, equivalence and oracle checks read crapkit through its CLI, once
per session per file set (tests/accuracy/analysis_oracles/conftest.py).
Seconds are serial ubuntu estimates from measured Windows runs.
"""
SHARD = "analysis"
_PACKET = "tests/accuracy/analysis_oracles/"
CHECKS = [
    {"name": "hand probes in every language", "seconds": 8,
     "pytest": [_PACKET + "test_hand_probes.py"]},
    {"name": "one shape reads the same in every language", "seconds": 2,
     "pytest": [_PACKET + "test_equivalence.py"]},
    {"name": "Python reader against ast", "seconds": 8,
     "pytest": [_PACKET + "test_python_ast.py"]},
    {"name": "metamorphic source edits", "seconds": 6,
     "pytest": [_PACKET + "test_metamorphic_source.py"]},
    {"name": "ccn against radon, mccabe, ESLint and the gate rule", "seconds": 7,
     "pytest": [_PACKET + "test_complexity_oracles.py"]},
    {"name": "cognitive against the Sonar counter, complexipy and sonarjs", "seconds": 6,
     "pytest": [_PACKET + "test_cognitive_oracles.py"]},
    {"name": "nesting against the depth model, pylint and ESLint max-depth", "seconds": 3,
     "pytest": [_PACKET + "test_nesting_oracles.py"]},
    {"name": "JS and TS functions against the TypeScript compiler", "seconds": 5,
     "pytest": [_PACKET + "test_ts_compiler.py"]},
    {"name": "nloc against tokenize, params against ast", "seconds": 4,
     "pytest": [_PACKET + "test_nloc_params.py"]},
    {"name": "cold runs, hash seeds and pool vs serial give identical rows", "seconds": 5,
     "pytest": [_PACKET + "test_determinism.py"]},
    {"name": "byte encodings and line endings read as the same functions", "seconds": 2,
     "pytest": [_PACKET + "test_decode_matrix.py"]},
]
