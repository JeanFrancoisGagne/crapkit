---
name: crapkit-onboard
description: Adopting crapkit in a repo for the first time.
disable-model-invocation: true
---

# Adopting crapkit

Read [docs: adoption](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adoption.md)
first: it carries the judgment the quickstarts leave out (how coarse to cut scopes, exclude
versus lane, where `scoped_tests` belongs, the first-verify taint).

Run [README: quickstart, Python](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-python)
or [README: quickstart, TypeScript](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#quickstart-typescript)
for the mechanics, in order, in the repo being adopted.

`crapkit init` writes the starter config, `crapkit doctor` says whether it still describes
the repo, and `crapkit coverage` produces the first scored run.

## Install the agent surface last, once the repo scores

Two commands, in this order:

```
claude plugin marketplace add JeanFrancoisGagne/crapkit
claude plugin install crapkit@crapkit
```

That one install carries all three skills, the read-side MCP server, and the advisory
PostToolUse hook. Installed earlier, `crapkit-onboard` points at a config that does not
exist yet and `crapkit` points at a store with no run in it.

Fallback, for a runtime that has no plugin marketplace: copy `plugin/skills/*` from a clone
into `~/.claude/skills`, or that runtime's equivalent. A copy gets the skills alone, never
the hook or the MCP server, and carries no version to compare against the CLI.
