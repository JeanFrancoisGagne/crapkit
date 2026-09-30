"""The R11 retro probe fails an id-keyed cognitive reader whatever CPython's allocator does.

R11: crapkit's cognitive reader kept each function's running total under
id(fn), and a later function given a finished one's number started from its
total. The probe (tests/accuracy/suite_strength/retro/probes/R11.py) once
waited for CPython to hand a freed FunctionInfo's address to the next one. In
the Linux accuracy image that happened in some runs and not in others (hash
randomization moves the heap), so the retro replay read the before commit
green and refused R11.

Each test runs the probe against a stand-in crapkit.lizardcognitive whose id()
never gives a number twice: each object gets the next integer, as on an
interpreter that never reuses a freed address. The reader keyed by id(fn), the
before commit's shape, must fail the probe with its AssertionError; the reader
keyed by the FunctionInfo itself, the fix's shape, must pass it.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import hang_guard

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "tests" / "accuracy" / "suite_strength" / "retro" / "probes" / "R11.py"
READER = '''
import itertools
import weakref

_numbers = weakref.WeakKeyDictionary()
_counter = itertools.count(1)


def id(obj):
    """A fresh number for each object, never one a dead object had."""
    if obj not in _numbers:
        _numbers[obj] = next(_counter)
    return _numbers[obj]


class LizardExtension:
    def __call__(self, tokens, reader):
        totals = {}
        for token in tokens:
            fn = reader.context.current_function
            key = KEY
            totals[key] = totals.get(key, 0) + (token == "if")
            fn.cognitive_complexity = totals[key]
            yield token
'''


def _probe(tmp_path: Path, key: str) -> subprocess.CompletedProcess:
    """The probe run against a stand-in reader whose running totals are keyed by `key`."""
    package = tmp_path / "stand-in" / "crapkit"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "lizardcognitive.py").write_text(READER.replace("KEY", key), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(package.parent)}
    return hang_guard.run([sys.executable, str(PROBE), str(tmp_path)], cwd=tmp_path, env=env,
                          text=True)


def test_the_probe_fails_a_reader_keyed_by_id_on_an_interpreter_that_never_reuses_one(tmp_path):
    done = _probe(tmp_path, "id(fn)")

    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr.strip().splitlines()[-1] == (
        "AssertionError: c holds no structure and reads cognitive 1, the total a left at its address")


def test_the_probe_passes_a_reader_keyed_by_the_function_itself(tmp_path):
    done = _probe(tmp_path, "fn")

    assert done.returncode == 0, done.stdout + done.stderr
