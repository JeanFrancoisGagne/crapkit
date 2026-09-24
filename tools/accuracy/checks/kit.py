"""The kit's own checks: tests/accuracy/kit, which every packet imports.

The kit's process-heavy self-tests carry the nightly marker: its machinery
already runs inside every packet's push tests. Seconds are serial ubuntu
estimates from a measured Windows run (17 s wall for the push selection).
"""
SHARD = "corpus"
_KIT = "tests/accuracy/kit/"
CHECKS = [
    {"name": "exact values and strategies", "seconds": 2,
     "pytest": [_KIT + "test_exact.py", _KIT + "test_kit_strategies.py"]},
    {"name": "tiers, settings, guards and run log", "seconds": 4,
     "pytest": [_KIT + "test_kit_tiers.py", _KIT + "test_kit_settings.py",
                _KIT + "test_kit_guards.py", _KIT + "test_kit_runlog.py"]},
    {"name": "rulings and oracles", "seconds": 1,
     "pytest": [_KIT + "test_kit_rulings.py", _KIT + "test_kit_oracles.py"]},
    {"name": "repos, drive and surfaces", "seconds": 4,
     "pytest": [_KIT + "test_kit_repos.py", _KIT + "test_kit_drive.py",
                _KIT + "test_kit_surfaces.py"]},
    {"name": "corpus run, goldens and the lock", "seconds": 6,
     "pytest": [_KIT + "test_kit_corpus_run.py", _KIT + "test_kit_goldens.py"]},
    {"name": "the contract", "seconds": 4,
     "pytest": [_KIT + "test_kit_contract.py", _KIT + "test_kit_contract_rules.py",
                _KIT + "test_kit_closure.py", _KIT + "test_kit_calcs.py",
                _KIT + "test_kit_docrange.py", _KIT + "test_kit_reach.py"]},
    {"name": "the run tool", "seconds": 1,
     "pytest": [_KIT + "test_run_tool.py", _KIT + "test_run_tool_commands.py"]},
    {"name": "oracles on a non-ASCII path", "seconds": 0,
     "pytest": [_KIT + "test_oracles_non_ascii.py"]},
]
