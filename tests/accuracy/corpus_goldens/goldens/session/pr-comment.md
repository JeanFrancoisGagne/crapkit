<!-- crapkit-action -->

## crapkit

414 functions in 64 files, 33 over their ceilings (6; scripts 8, vendor 15), CRAP load 1711.21, grade C.

**verify failed, exit 6: complexity gate.**

- gate: `src/py/fresh.py:4` `route( a , b , c , d )` ccn 9, cov 0%, crap 90.0 -> decompose
- gate: `src/py/grades.py:8` `letter( score )` ccn 7, cov 50%, crap 13.1 -> decompose
- uncovered lines in `src/py/fresh.py`: 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16

Run 4 against baseline 3, 3 changed files: 2 gate violations, 0 ratchet regressions, 0 new test failures, 13 uncovered changed lines.

### Worklist: 2 changed files

| File | Function | ccn | risk | remedy |
|---|---|---:|---:|---|
| `src/py/grades.py:18` | `curve( scores , mode , floor , ceiling , skip_none )` | 8 | 8.0 | decompose |
