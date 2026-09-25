"""Change control's checks: the rules on seeded repos, and the rules on this tree.

Seconds are serial ubuntu estimates. The seeded-repo tests are git-bound: on
Windows under Defender the same file takes about 160 s serial, on a Linux
runner a git call costs a few milliseconds.
"""
SHARD = "corpus"
_HOME = "tests/accuracy/change_control/"
CHECKS = [
    {"name": "the rules on seeded repos", "seconds": 14,
     "pytest": [_HOME + "test_change_control_rules.py"]},
    {"name": "this tree's lock, digests, goldens and counts", "seconds": 6,
     "pytest": [_HOME + "test_change_control.py"]},
]
