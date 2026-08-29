---
name: crapkit-onboard
description: Adopting crapkit in a repo for the first time.
disable-model-invocation: true
---

# Adopting crapkit

Two artifacts, installed once each: the CLI scores the repo, the plugin gives an agent the
skills. Run them in that order, CLI first.

## The plugin

```
claude plugin marketplace add JeanFrancoisGagne/crapkit
claude plugin install crapkit@crapkit
```

One install carries all three skills, the read-side MCP server, and the advisory PostToolUse
hook, at a version that tracks the CLI's. `crapkit doctor --plugin-root PATH` reports drift
between the two later.

Install it after the repo scores, not before. Earlier, `crapkit-onboard` points at a config
that does not exist yet and `crapkit` points at a store with no run in it.

Fallback for a runtime with no plugin marketplace: copy `plugin/skills/*` from a clone into
`~/.claude/skills`, or that runtime's equivalent. A copy gets the skills alone, never the
hook or the MCP server, and carries no version to compare against the CLI.

## The repo

Install the CLI first ([README: install](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#install)).
Then, in the repo being adopted: `crapkit init` writes the starter config, `crapkit doctor`
says whether it still describes the repo, `crapkit coverage` produces the first scored run.

Read [docs: adoption](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adoption.md)
before the first `crapkit init`. It carries the judgment the quickstarts leave out: how
coarse to cut scopes, exclude versus lane, where `scoped_tests` belongs, the first-verify
taint.

Then run [README: quickstart, Python](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-python)
or [README: quickstart, TypeScript](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-typescript)
for the mechanics, in order.
