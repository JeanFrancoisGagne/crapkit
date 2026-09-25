"""coverage-oracles: every number crapkit reads off a coverage artifact.

The probes' recordings replay on push, so no test runner starts. The session's
probe run (one `crapkit coverage` over every recording) is charged to the
ground truth check, which runs first. Seconds are serial ubuntu estimates: each
check's share of a push run in the accuracy image (checkout on the
container's own disk), scaled to the 45 s the whole push selection took
serially on Windows, because the image run shared a host at 100% CPU and took
140 s. The Windows cell adds the drive-letter spellings of the admission
check. The Node and slipcover oracles, and the producer reruns, run nightly.
"""
SHARD = "coverage"
_CO = "tests/accuracy/coverage_oracles/"
CHECKS = [
    {"name": "ground truth: every probe function in every recording", "seconds": 10,
     "pytest": [_CO + "test_ground_truth.py"]},
    {"name": "producers agree, crap4py, parse consistency", "seconds": 3,
     "pytest": [_CO + "test_producers_agree.py", _CO + "test_parse_consistency.py"]},
    {"name": "streaming, digest, refusals and tree admission", "seconds": 10,
     "pytest": [_CO + "test_adversarial_artifacts.py"]},
    {"name": "coverage join by position, lane metamorphics", "seconds": 9,
     "pytest": [_CO + "test_join.py"]},
]
CHECKS += [
    {"name": "shared span and def-line floor", "seconds": 5,
     "pytest": [_CO + "test_shared_span.py"]},
    {"name": "coverage flag", "seconds": 3, "pytest": [_CO + "test_flags.py"]},
    {"name": "dark lines and the lane fold", "seconds": 4, "pytest": [_CO + "test_dark_lines.py"]},
    {"name": "diff coverage", "seconds": 3, "pytest": [_CO + "test_diffcov_oracle.py"]},
    {"name": "per-line test contexts", "seconds": 5, "pytest": [_CO + "test_contexts.py"]},
    {"name": "doctor unmeasured directories", "seconds": 6,
     "pytest": [_CO + "test_doctor_unmeasured.py"]},
]
CHECKS += [
    {"name": "istanbul-lib-coverage and crap-typescript", "seconds": 20, "tiers": ["nightly"],
     "pytest": [_CO + "test_istanbul_lib.py"]},
    {"name": "slipcover split by the ast", "seconds": 15, "tiers": ["nightly"],
     "pytest": [_CO + "test_slipcover.py"]},
]
