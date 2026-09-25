"""The history packet's checks: churn and its recency weight, change coupling,
changed line ranges, identity across renames, the ratchet burn-down and explain's
commit list (tests/accuracy/history_oracles).

Seconds are serial ubuntu estimates for the push selection of each check.
"""
SHARD = "history"
_HISTORY = "tests/accuracy/history_oracles/"
CHECKS = [
    {"name": "churn against the numstat walk", "seconds": 8,
     "pytest": [_HISTORY + "test_churn_oracle.py"]},
]
