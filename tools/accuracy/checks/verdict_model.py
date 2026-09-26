"""The verdict-model packet's checks: tests/accuracy/verdict_model.

Seconds are serial ubuntu seconds for the push tier, measured in the accuracy
image with the repositories on the container's own disk (102 s for the
whole packet), then raised to a later serial Windows run where that run was
slower (92.5 s in total; lane reuse 17.3 s, the gates 11.3 s). The packet's
push share is 120 seconds (the plan's time budget). Parametrized checks keep a few
cases on push and run the rest nightly (verdict_model/cadence.py), and the
random run-history machine runs nightly, where `process` settings give it a
10-minute budget.
"""
SHARD = "verdict-score"
_VM = "tests/accuracy/verdict_model/"
CHECKS = [
    {"name": "verify exit, JSON and stored run over 16 finding subsets", "seconds": 6,
     "pytest": [_VM + "test_exit_and_settle.py"]},
    {"name": "run history: baseline choice, stamp, seed, override, explain", "seconds": 15,
     "pytest": [_VM + "test_history_machine.py"]},
    {"name": "commit hook, advisory, rescore --gate, verify gate and preview", "seconds": 12,
     "pytest": [_VM + "test_three_gates.py", _VM + "test_preview_vs_run.py"]},
    {"name": "ratchet keys and handles", "seconds": 5,
     "pytest": [_VM + "test_keys_handles.py"]},
    {"name": "marks file parse, dump and merge driver", "seconds": 4,
     "pytest": [_VM + "test_ratchet_file.py", _VM + "test_merge_driver.py",
                _VM + "test_marks_self_consistency.py"]},
    {"name": "JUnit parsing and test failure classes", "seconds": 14,
     "pytest": [_VM + "test_junit_parse.py", _VM + "test_failure_classes.py"]},
    {"name": "lane reuse, staleness and suite drop", "seconds": 18,
     "pytest": [_VM + "test_lanes_reuse.py"]},
    {"name": "lane command reading and the full-suite guard", "seconds": 10,
     "pytest": [_VM + "test_full_suite_guard.py"]},
    {"name": "path layouts and argument refusals", "seconds": 6,
     "pytest": [_VM + "test_layouts.py"]},
    {"name": "claims", "seconds": 8,
     "pytest": [_VM + "test_claim_close.py"]},
    {"name": "run retention keep set", "seconds": 6,
     "pytest": [_VM + "test_retention.py"]},
    {"name": "baseline record: --emit-baseline and --baseline-tsv", "seconds": 10,
     "pytest": [_VM + "test_baseline_tsv.py"]},
    {"name": "test-scoped routing", "seconds": 4,
     "pytest": [_VM + "test_scoped_routing.py"]},
    {"name": "doctor lane probes and plugin root (nightly)", "seconds": 1,
     "pytest": [_VM + "test_doctor_lanes.py"]},
    {"name": "push cadence helper", "seconds": 1,
     "pytest": [_VM + "test_cadence.py"]},
]
