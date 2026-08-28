---
name: crapkit-onboard
description: Adopting crapkit in a repo for the first time.
disable-model-invocation: true
---

# Adopting crapkit

Read `docs/adoption.md` first: it carries the judgment the quickstarts leave out (how coarse
to cut scopes, exclude versus lane, where `scoped_tests` belongs, the first-verify taint).

Run `README.md#quickstart-python` or `README.md#quickstart-typescript` for the mechanics, in
order, in the repo being adopted.

`crapkit init` writes the starter config, `crapkit doctor` says whether it still describes
the repo, and `crapkit coverage` produces the first scored run.

Install the skills last, once the repo scores: copy `skills/*` into `~/.claude/skills`, or
your agent runtime's equivalent.
