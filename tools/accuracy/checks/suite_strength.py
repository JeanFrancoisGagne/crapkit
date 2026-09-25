"""suite-strength's checks: the retro ledger and replay tool, the mutation tool,
floors and canary, `crapkit mutate`'s verdicts and their mutmut and Stryker
differentials, the self-measurement floor, the release gate's model, the
runner's check targets and the release-tier rows.

Seconds are serial ubuntu estimates for the push tier, rounded up from a run in
the accuracy image (13 s for the packet, 10.3 of them in tests); the mutate
fixtures spawn crapkit and git, which is where the time goes. A check whose
tests run only nightly or at release declares 0.
"""
SHARD = "verdict-score"
_SS = "tests/accuracy/suite_strength/"
CHECKS = [
    {"name": "retro ledger and triage", "seconds": 1,
     "pytest": [_SS + "test_retro_ledger.py"]},
    {"name": "retro tool", "seconds": 3,
     "pytest": [_SS + "test_retro_tool.py"]},
    {"name": "mutation tool and floors", "seconds": 3,
     "pytest": [_SS + "test_mutation_tool.py", _SS + "test_mutation_floors.py"]},
    {"name": "crapkit mutate verdicts", "seconds": 5,
     "pytest": [_SS + "test_mutate_results.py"]},
    {"name": "crapkit mutate against mutmut and Stryker", "seconds": 0,
     "pytest": [_SS + "test_mutate_differential.py"]},
    {"name": "self-measurement floor", "seconds": 0,
     "pytest": [_SS + "test_self_measure_floor.py"]},
    {"name": "release gate model", "seconds": 1,
     "pytest": [_SS + "test_release_gate_model.py"]},
    {"name": "runner check targets", "seconds": 0,
     "pytest": [_SS + "test_runner_targets.py"]},
    {"name": "no ruling waits on an answer", "seconds": 0,
     "pytest": [_SS + "test_release_rows.py"]},
    {"name": "retro replays for the release", "seconds": 0, "tiers": ["release"],
     "argv": ["python", "tools/accuracy/retro.py", "release"]},
    {"name": "mutation receipts cover the release", "seconds": 0, "tiers": ["release"],
     "argv": ["python", "tools/accuracy/mutation.py", "covered"]},
]
