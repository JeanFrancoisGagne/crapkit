"""score-model: the CRAP score and every number, label and ranking built on it.

Seconds are serial ubuntu estimates from measured Windows runs. The corpus
join's seconds are the session's one measured corpus run, which it shares
with every packet that reads the same corpus.
"""
SHARD = "verdict-score"
_SM = "tests/accuracy/score_model/"
CHECKS = [
    {"name": "CRAP score: grid, PHPUnit, published tables", "seconds": 8,
     "pytest": [_SM + "test_crap.py"]},
    {"name": "function coverage ratio", "seconds": 1, "pytest": [_SM + "test_ratio.py"]},
    {"name": "corpus rows against their artifact counts", "seconds": 8,
     "pytest": [_SM + "test_counts_join.py"]},
    {"name": "remedy, grade and budget", "seconds": 3,
     "pytest": [_SM + "test_remedy_grade_budget.py"]},
]
CHECKS += [
    {"name": "ceiling per row", "seconds": 3, "pytest": [_SM + "test_ceiling.py"]},
    {"name": "rescore overlay", "seconds": 2, "pytest": [_SM + "test_overlay.py"]},
]
CHECKS += [
    {"name": "queue admission, worklist ranking, next-item", "seconds": 7,
     "pytest": [_SM + "test_queue.py"]},
]
CHECKS += [
    {"name": "batch split", "seconds": 3, "pytest": [_SM + "test_batch_split.py"]},
    {"name": "doctor --tune knobs and lane cost", "seconds": 3, "pytest": [_SM + "test_tune.py"]},
    {"name": "totals, trend, digest and the coverage summary", "seconds": 5,
     "pytest": [_SM + "test_rollups.py"]},
    {"name": "brief: today's ceiling, file totals, regrowth", "seconds": 2,
     "pytest": [_SM + "test_rejudge.py"]},
]
