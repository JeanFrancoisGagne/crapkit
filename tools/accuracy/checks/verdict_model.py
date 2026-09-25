"""The verdict-model packet's checks: tests/accuracy/verdict_model.

Seconds are serial ubuntu estimates for the push tier, from runs in the
accuracy image with the repositories on local disk.
"""
SHARD = "verdict-score"
_VM = "tests/accuracy/verdict_model/"
CHECKS = [
    {"name": "verify exit, JSON and stored run over 16 finding subsets", "seconds": 12,
     "pytest": [_VM + "test_exit_and_settle.py"]},
]
