## What changed

<!-- Two or three lines. What the code does now that it did not do before. -->

## The test that proves it

<!-- Path and test name. For a bug fix, that test has to fail on main. -->

`tests/unit/test_.py::test_`

## Checks

- [ ] crapkit's own gate passed on my commits (`core.hooksPath` is set to `git-hooks`, so every commit ran `python -m crapkit hook-precommit` and came back clean)
- [ ] `python -m crapkit verify` is green on this branch
- [ ] `python -m pytest -q` is green, unit and e2e
- [ ] Docs updated, or nothing a user can see changed

## Worth a second look

<!-- Delete this section if there is nothing. -->
