"""mark-carry: a mark keeps its value when its key moves.

Push reads `rekey_marks` against hand_rekey.tsv's bytes and two pure
properties: the value strings stay one multiset, and a move set and its
inverse give back the file. Seconds are a serial ubuntu estimate: no git, no
CLI, a few hundred in-process rewrites of small marks files.
"""
SHARD = "history"
_HERE = "tests/accuracy/mark_carry/"
CHECKS = [
    {"name": "mark value kept across a re-key", "seconds": 2,
     "pytest": [_HERE + "test_rekey_values.py"]},
]
