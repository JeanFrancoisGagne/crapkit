"""runtime-guards: crapkit.invariants, the checks crapkit runs on its own numbers.

Push reads the checks against a model of the documented bounds (hand rows and
Hypothesis rows), replays R27 as a CLI run, finds zero stops on the seed
corpus, and times the row check at a large repo's size. Nightly adds the full
corpus, for zero stops and for the cost ratio. Seconds are serial ubuntu
estimates from a measured Windows run.
"""
SHARD = "corpus"
_HERE = "tests/accuracy/runtime_guards/"
CHECKS = [
    {"name": "invariant verdict against the documented bounds", "seconds": 6,
     "pytest": [_HERE + "test_invariant_verdict.py"]},
    {"name": "zero stops on measured corpora", "seconds": 9,
     "pytest": [_HERE + "test_zero_hits_on_corpus.py"]},
    {"name": "what the checks cost", "seconds": 5,
     "pytest": [_HERE + "test_guard_cost.py"]},
]
