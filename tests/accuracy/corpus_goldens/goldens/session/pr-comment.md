<!-- crapkit-action -->

## crapkit

414 functions in 64 files, 33 over their ceilings (6; scripts 8, vendor 15), CRAP load 1711.21, grade C.

**verify failed, exit 6: complexity gate.**

- gate: `src/py/grades.py:8` `letter( score )` ccn 7, cov 50%, crap 13.1 -> decompose
- uncovered lines in `src/py/grades.py`: 13, 14, 15, 22, 26, 30, 51

Run 4 against baseline 3, 2 changed files: 1 gate violation, 0 ratchet regressions, 0 new test failures, 7 uncovered changed lines.

### Worklist: 2 changed files

| File | Function | ccn | risk | remedy |
|---|---|---:|---:|---|
| `src/py/grades.py:18` | `curve( scores , mode , floor , ceiling , skip_none )` | 8 | 8.0 | decompose |
