"""Change control's checks: the rules on seeded repos, the tool around them, and the
in-tree rules on this checkout.

Seconds are serial ubuntu estimates. The seeded-repo tests are git-bound: on
Windows under Defender the same file takes about 160 s serial, on a Linux
runner a git call costs a few milliseconds.
"""
SHARD = "corpus"
_HOME = "tests/accuracy/change_control/"
CHECKS = [
    {"name": "the rules on seeded repos", "seconds": 9,
     "pytest": [_HOME + "test_change_control_rules.py"]},
    {"name": "the command line, the first lock, counts and the pre-push hook", "seconds": 7,
     "pytest": [_HOME + "test_change_control_tool.py"]},
    {"name": "this tree's lock, digests, goldens and counts", "seconds": 4,
     "pytest": [_HOME + "test_change_control.py"]},
]
