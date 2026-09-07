## What changed

<!-- Two or three lines. What the code does now that it did not do before. -->

## The test that proves it

<!-- Name the test and command you ran, with the result. For a bug fix, record
the failure before the fix. For a docs-only change, name the affected contracts. -->

## Checks

<!-- The rules these come from live in CONTRIBUTING.md. -->

- [ ] Every function I added or touched sits at ccn 6 or lower, and the gate ran on my commits (`git config core.hooksPath git-hooks`, so each commit ran `python -m crapkit hook-precommit` and came back clean)
- [ ] `python -m crapkit verify` is green on this branch
- [ ] `python tools/testing/run.py` is green (four unit workers, eight E2E workers; the dev extra includes pytest-xdist)
- [ ] Docs updated in the same commit if I renamed a subcommand or reworded a message a page quotes, so the docs contract tests stay green
- [ ] `CHANGELOG.md` has a line for this change under the unreleased heading, or nothing a user can see changed

## Worth a second look

<!-- Delete this section if there is nothing. -->
