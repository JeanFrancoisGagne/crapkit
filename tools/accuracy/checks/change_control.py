"""Change control's checks: the rules on seeded repos, the tool around them, and the
in-tree rules on this checkout.

Seconds are serial Linux seconds measured in the accuracy image from a copy of
the tree (one pytest process per file): 10.3 to 11.8 s for the rules, 3.5 to
4.8 s for the tool, 5.9 to 6.8 s for the in-tree rules and 1.9 to 2.2 s for the
edges. The push selection keeps every rule's seeded repo, the model and the
split-diff check; the tool's slower process tests (node, the hook's pytest run,
the command-line declares) run nightly. The seeded-repo tests are git-bound: on
Windows under Defender a git call costs far more than on a Linux runner.
"""
SHARD = "corpus"
_HOME = "tests/accuracy/change_control/"
CHECKS = [
    {"name": "the rules on seeded repos", "seconds": 10,
     "pytest": [_HOME + "test_change_control_rules.py"]},
    {"name": "the command line, the first lock, counts and the pre-push hook", "seconds": 4,
     "pytest": [_HOME + "test_change_control_tool.py"]},
    {"name": "this tree's lock, digests, goldens and counts", "seconds": 6,
     "pytest": [_HOME + "test_change_control.py"]},
    {"name": "missing cells, exact lines and the processes started", "seconds": 2,
     "pytest": [_HOME + "test_change_control_edges.py"]},
]
