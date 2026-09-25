"""suite-strength's checks: the retro ledger, the mutation tools, `crapkit mutate`'s
verdicts and the self-measurement floor.

Seconds are serial ubuntu estimates from measured runs; the mutate fixtures
spawn crapkit and git, which is where the time goes.
"""
SHARD = "verdict-score"
_SS = "tests/accuracy/suite_strength/"
CHECKS = [
    {"name": "retro ledger and triage", "seconds": 2,
     "pytest": [_SS + "test_retro_ledger.py"]},
    {"name": "retro tool", "seconds": 2,
     "pytest": [_SS + "test_retro_tool.py"]},
    {"name": "mutation tool and floors", "seconds": 1,
     "pytest": [_SS + "test_mutation_tool.py", _SS + "test_mutation_floors.py"]},
    {"name": "crapkit mutate verdicts", "seconds": 9,
     "pytest": [_SS + "test_mutate_results.py"]},
    {"name": "self-measurement floor", "seconds": 1,
     "pytest": [_SS + "test_self_measure_floor.py"]},
]
