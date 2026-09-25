"""runtime-guards: crapkit.invariants, the checks crapkit runs on its own numbers.

Push reads the checks against a model of the documented bounds (hand rows and
Hypothesis rows), replays R27 as a CLI run, pins the RG rulings, finds zero
stops on the seed corpus and on a dated history, and times the row check at a
large repo's size. Nightly adds the full corpus and its history bundles, for
zero stops and for the cost ratio. Seconds are serial ubuntu estimates from a
Windows run (12, 15 and 16 s on a loaded box); the seed corpus's session setup,
about 7 s, is shared with every check that measures it.
"""
SHARD = "corpus"
_HERE = "tests/accuracy/runtime_guards/"
CHECKS = [
    {"name": "invariant verdict against the documented bounds", "seconds": 8,
     "pytest": [_HERE + "test_invariant_verdict.py"]},
    {"name": "zero stops on measured corpora", "seconds": 10,
     "pytest": [_HERE + "test_zero_hits_on_corpus.py"]},
    {"name": "what the checks cost", "seconds": 4,
     "pytest": [_HERE + "test_guard_cost.py"]},
]
