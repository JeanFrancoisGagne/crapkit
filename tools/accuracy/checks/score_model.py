"""score-model: the CRAP score and every number, label and ranking built on it.

Seconds are serial ubuntu estimates from measured Windows runs.
"""
SHARD = "verdict-score"
_SM = "tests/accuracy/score_model/"
CHECKS = [
    {"name": "CRAP score: grid, PHPUnit, published tables", "seconds": 9,
     "pytest": [_SM + "test_crap.py"]},
]
