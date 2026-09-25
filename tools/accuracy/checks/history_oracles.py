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
    {"name": "churn at the parser", "seconds": 2,
     "pytest": [_HISTORY + "test_churn_properties.py"]},
    {"name": "churn on git's clock and mid-walk", "seconds": 4,
     "pytest": [_HISTORY + "test_churn_clock.py", _HISTORY + "test_churn_concurrency.py"]},
    {"name": "coupling against the pair count", "seconds": 5,
     "pytest": [_HISTORY + "test_coupling_oracle.py"]},
    {"name": "coupling at the parser", "seconds": 3,
     "pytest": [_HISTORY + "test_coupling_properties.py"]},
    {"name": "changed ranges through the gates", "seconds": 4,
     "pytest": [_HISTORY + "test_changed_ranges.py"]},
    {"name": "changed ranges at the reader", "seconds": 3,
     "pytest": [_HISTORY + "test_diff_ranges_api.py"]},
    {"name": "marks across renames", "seconds": 4,
     "pytest": [_HISTORY + "test_renames.py"]},
    {"name": "burn-down against the history walk", "seconds": 4,
     "pytest": [_HISTORY + "test_burn_down.py"]},
    {"name": "explain's commit list against the objects", "seconds": 3,
     "pytest": [_HISTORY + "test_explain_commits.py"]},
]
