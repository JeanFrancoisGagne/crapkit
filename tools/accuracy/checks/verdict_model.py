"""The verdict-model packet's checks: tests/accuracy/verdict_model.

Seconds are the push tier's serial seconds, each rounded up from a serial
Windows run, the slowest platform measured: 111.0 s of tests (112 s wall),
run back to back with the tree before the run history compared the store's
verdict with its record's after every step (96.4 s). The run history rose
from 9.7 to 19.8 s: two verifies on a copy after each scripted step, and ten
read commands around the prune. A serial WSL Ubuntu run of the same tree,
taken while a Windows nightly loaded the host, took 80.3 s; its path layouts
took 6.8 s against 6 declared, and every other check stayed under its value.
The packet's push share is 120 seconds (the plan's time budget); the gate
rule check added in 0.9.0 takes 1 more, so the checks declare 121, and the
push total stays inside the kit's 504 s budget.
Parametrized checks keep a few cases on push and run the rest nightly
(verdict_model/cadence.py), and the random run-history machine runs nightly
with `process` settings.
"""
SHARD = "verdict"
_VM = "tests/accuracy/verdict_model/"
CHECKS = [
    {"name": "verify exit, JSON and stored run over 16 finding subsets", "seconds": 5,
     "pytest": [_VM + "test_exit_and_settle.py"]},
    {"name": "run history: baseline choice, stamp, seed, override, explain", "seconds": 20,
     "pytest": [_VM + "test_history_machine.py"]},
    {"name": "commit hook, advisory, rescore --gate, verify gate and preview",
     "os_sensitive": True, "seconds": 14,
     "pytest": [_VM + "test_three_gates.py", _VM + "test_preview_vs_run.py"]},
    {"name": "ratchet keys and handles", "seconds": 5,
     "pytest": [_VM + "test_keys_handles.py"]},
    {"name": "marks file parse, dump and merge driver", "os_sensitive": True, "seconds": 5,
     "pytest": [_VM + "test_ratchet_file.py", _VM + "test_merge_driver.py",
                _VM + "test_marks_self_consistency.py"]},
    {"name": "JUnit parsing and test failure classes", "seconds": 10,
     "pytest": [_VM + "test_junit_parse.py", _VM + "test_failure_classes.py"]},
    {"name": "lane reuse, staleness and suite drop", "seconds": 19,
     "pytest": [_VM + "test_lanes_reuse.py"]},
    {"name": "lane command reading and the full-suite guard", "os_sensitive": True, "seconds": 6,
     "pytest": [_VM + "test_full_suite_guard.py"]},
    {"name": "path layouts and argument refusals", "os_sensitive": True, "seconds": 6,
     "pytest": [_VM + "test_layouts.py"]},
    {"name": "claims", "seconds": 8,
     "pytest": [_VM + "test_claim_close.py"]},
    {"name": "run retention keep set", "seconds": 8,
     "pytest": [_VM + "test_retention.py"]},
    {"name": "baseline record: --emit-baseline and --baseline-tsv", "seconds": 8,
     "pytest": [_VM + "test_baseline_tsv.py"]},
    {"name": "test-scoped routing", "seconds": 4,
     "pytest": [_VM + "test_scoped_routing.py"]},
    {"name": "doctor lane probes and plugin root (nightly)", "seconds": 1,
     "pytest": [_VM + "test_doctor_lanes.py"]},
    {"name": "push cadence helper", "seconds": 1,
     "pytest": [_VM + "test_cadence.py"]},
    {"name": "gate rules sit in a calc and under a floor", "seconds": 1,
     "pytest": [_VM + "test_gate_rules_are_calcs.py"]},
]
