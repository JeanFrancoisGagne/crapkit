## What changed

<!-- Two or three lines. What the code does now that it did not do before. -->

## The test that proves it

<!-- Path and test name. For a bug fix, that test has to fail on main. -->

`tests/unit/test_.py::test_`

## Checks

<!-- The rules these come from live in CONTRIBUTING.md. -->

- [ ] Every function I added or touched sits at ccn 6 or lower, and the gate ran on my commits (`git config core.hooksPath git-hooks`, so each commit ran `python -m crapkit hook-precommit` and came back clean)
- [ ] `python -m crapkit verify` is green on this branch
- [ ] `python -m pytest tests/unit` is green
- [ ] `python -m pytest -n 8 tests/e2e` is green (`-n 8` needs pytest-xdist, which the dev extra ships; it turns 8 minutes of e2e into about 2)
- [ ] Docs updated in the same commit if I renamed a subcommand or reworded a message a page quotes, so the docs contract tests stay green
- [ ] `CHANGELOG.md` has a line for this change under the unreleased heading, or nothing a user can see changed

## Worth a second look

<!-- Delete this section if there is nothing. -->
