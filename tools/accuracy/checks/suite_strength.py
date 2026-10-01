"""suite-strength's checks: the retro ledger and replay tool, the mutation tool,
floors and canary, `crapkit mutate`'s verdicts and their mutmut and Stryker
differentials, the self-measurement floor, the release gate's model, the
runner's check targets and the release-tier rows.

Seconds are serial ubuntu estimates for the push tier: each check's larger JUnit
sum over two runs in the accuracy image, rounded up (14 s for the packet; the
latest run measured 9.3 s); the mutate fixtures spawn crapkit and git, which
is where the time goes. A check whose tests run only nightly or at release
declares 0.
"""
SHARD = "verdict-score"
_SS = "tests/accuracy/suite_strength/"
CHECKS = [
    {"name": "retro ledger and triage", "seconds": 1,
     "pytest": [_SS + "test_retro_ledger.py"]},
    {"name": "retro tool", "seconds": 2,
     "pytest": [_SS + "test_retro_tool.py"]},
    {"name": "mutation tool and floors", "seconds": 2,
     "pytest": [_SS + "test_mutation_tool.py", _SS + "test_mutation_floors.py"]},
    {"name": "crapkit mutate verdicts", "seconds": 4,
     "pytest": [_SS + "test_mutate_results.py"]},
    {"name": "mutant generation from source text", "seconds": 1,
     "pytest": [_SS + "test_mutant_generation.py", _SS + "test_mutate_command.py"]},
    {"name": "mutation worktree pool in-process", "seconds": 3,
     "pytest": [_SS + "test_mutate_pool_plumbing.py"]},
    {"name": "crapkit mutate against mutmut and Stryker", "seconds": 0,
     "pytest": [_SS + "test_mutate_differential.py"]},
    {"name": "self-measurement floor", "seconds": 0,
     "pytest": [_SS + "test_self_measure_floor.py"]},
    {"name": "calc functions run on the golden run or their independent tests", "seconds": 0,
     "pytest": [_SS + "test_calc_reach.py"]},
    {"name": "release gate model", "seconds": 1,
     "pytest": [_SS + "test_release_gate_model.py", _SS + "test_release_gate_refusals.py"]},
    {"name": "runner check targets", "seconds": 0,
     "pytest": [_SS + "test_runner_targets.py"]},
    {"name": "no ruling waits on an answer", "seconds": 0,
     "pytest": [_SS + "test_release_rows.py"]},
    # One CI cell per OS replays the public rows; the releasing machine also
    # replays the bundle rows, from the history bundle only it holds.
    {"name": "retro replays for the release", "seconds": 0, "tiers": ["release"],
     "cells": ["linux-3.12", "win32-3.13"],
     "argv": ["python", "tools/accuracy/retro.py", "release"]},
    # The receipts live on the releasing machine, and `covered` exits 3 until a
    # stored receipt carries verdicts, so no CI cell and no Windows release runs it.
    {"name": "mutation receipts cover the release", "seconds": 0, "tiers": ["release"],
     "os": ["linux"], "local": True,
     "argv": ["python", "tools/accuracy/mutation.py", "covered"]},
]
