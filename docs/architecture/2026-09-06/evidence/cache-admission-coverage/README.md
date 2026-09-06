# Cache admission coverage

Only `tests/unit/test_analysis_cache_identity.py` changed. Its new changes remain unstaged. Production code, thresholds and ratchet marks did not change.

The existing nine tests passed, but the isolated file covered only 2 of 8 statements and 1 of 6 branches in `_cached_record`. The new 18 cases use a current cache produced by `analyze_files` and `save_cache`, corrupt its persisted JSON, and read it through `load_cache`. Every rejected cache rebuilds the same cold records with zero hits. Saving that rebuilt cache then gives one hit and identical records.

The cases cover string fields with wrong types, integer fields containing strings or floats, booleans in integer fields, a negative occurrence, non-list records, wrong record lengths and non-list row containers. A separate valid persisted cache case proves a current fingerprint still hits.

| Function | Statements after | Branches after | Missing lines or branches |
|---|---:|---:|---:|
| `_cached_record`, analyze.py:405 | 8/8, 100% | 6/6, 100% | 0 |
| `_cached_rows`, analyze.py:416 | 3/3, 100% | 2/2, 100% | 0 |

Focused result: **27 passed**. This is the focused cache test result; the parent task runs configured verification separately.

Evidence:

- `before.txt` and `coverage-before.json`: original nine tests and missing decoder branches.
- `after.txt` and `coverage-after.json`: all 27 tests and complete function coverage.
- `function-coverage.json`: exact function-region statements and branch arcs extracted from coverage.py JSON.
- `test-before.py`: target test file before these additions.
- `protected-before.json` and `protected-after.json`: matching SHA256 hashes for production `analyze.py`, root `.coverage`, `.crapkit/cov/py.json` and `.crapkit/cov/junit.xml`.

Replay from `<repo>` in PowerShell:

```powershell
$cacheEvidence = '<review-work>\cache-admission-coverage'
$env:PYTHONPATH = '<repo>\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:COVERAGE_FILE = "$cacheEvidence\.coverage-after"
python '<review-work>\evidence-analysis\final-root-run.py' tests/unit/test_analysis_cache_identity.py -o 'addopts=--tb=short -p no:cacheprovider' -q -n 0 -p no:randomly --cov=crapkit.analyze --cov-branch --cov-report="json:$cacheEvidence\coverage-after.json" --cov-report=term-missing
```

The runner asserts that `crapkit.__file__` points to the root checkout's `src/crapkit/__init__.py` before pytest starts. All coverage data and reports from this task live in this workspace directory. `git diff --check -- tests/unit/test_analysis_cache_identity.py` passes.
