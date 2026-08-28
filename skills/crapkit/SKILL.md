---
name: crapkit
description: "Work one crapkit debt item end to end: the packet that replaces reading the file, the ceiling the edit is judged against, the verdict that clears the commit. Use when the session already names crapkit (crapkit.toml, .crapkit/, crapkit-ratchet.tsv, a CRAP score, `crapkit next-item`, `crapkit brief`), when the user asks to burn down debt or lower complexity in a repo crapkit measures, or when a commit is refused with \"crapkit gate: ... exceed the complexity ceiling\". Not for eslint, husky, or any other pre-commit gate."
---

# Working a crapkit item

One item at a time. The packet carries the file so you never open it, `gate_rule.ceiling`
is the number the edit is judged against, and `crapkit verify` is the only authoritative
verdict. Doc anchors below name pages in the crapkit project.

## What answers each moment

| Moment | What answers it |
|---|---|
| Before opening the file | `crapkit brief PATH NAME --json`, or the MCP `brief` tool where the server is registered |
| Which house rules bind here | `notes.repo` and `notes.scope` in the packet |
| Before committing | `crapkit rescore FILE --gate` |
| Where does new code go | `crapkit worklist --top N`, `crapkit coupling`, `crapkit duplication` |
| Which branches need tests | `uncovered_lines` and `est_uncovered_paths` in the packet |
| Running the owning scope's tests | `commands.scoped_tests`, run verbatim, never retyped |
| Before a PR | `crapkit verify --reuse-artifacts`, or `crapkit verify --base REF` |
| Did the last refactor hold | `crapkit explain PATH NAME`, and the packet's `regrowth` |
| What this session changed | `crapkit digest` |
| Handing the state to a human who will not run a command | `crapkit report`, which writes one self-contained HTML page and prints its path |
| Boy-scout scan of the file | `file_functions` and `file_totals` in the packet |

Field semantics live in `docs/agent-json.md#brief`; the five-step loop lives in
`AGENTS.md#1-the-packet`.

## Open the packet dark-eyed

Read `stale` and `uncovered_lines_note` before trusting a number. `stale: true` means the
run predates HEAD: run `commands.refresh` first. `uncovered_lines: null` means no artifact
named lines for this file, and `flag` says which case you are in:

- `untested`: write the first test at the public seam, then `crapkit coverage`. The lines appear.
- `measured`: the artifact no longer matches the tree. Commit or revert the edits, then `crapkit coverage`.
- `cc-only`: the scope sets `coverage_optional`, so only decompose clears it.
- `[]`: the artifact answered and nothing is dark.

## The decompose branch

Subtract first: a dead branch deleted drops ccn for free and needs no new name. Then
extract private helpers, picking names `file_functions` shows the file does not hold yet.
Over the ceiling and unsure where the cut goes: read [cuts.md](cuts.md).

One function that is a whole command handler rather than one decision is a redesign, not a
split. Say so and stop.

## With tdd

The packet supplies what `tdd` otherwise asks a user for. `params`, `file_functions` and
the module's exports are the pre-agreed seam. `gate_rule.ceiling` is the pre-agreed scope.
Everything else about writing the test first comes from `tdd`.

## Packet economics

A packet is large and mostly source text, so take one per function you actually edit.
Trajectory questions (did this fall and come back, when did it get worse, which commits
touched it) go to `crapkit explain PATH NAME`, which answers them far cheaper.

## Where a repo trap goes

A trap you learn about the repo goes into its `crapkit.toml` `notes`, repo-wide or under
the owning scope, where every future packet carries it. Keys are documented in
`docs/configuration.md`. This file stays repo-free.

## Naming an anonymous function

The packet's `handle` field is the stable form: `(anonymous)#N` names the Nth anonymous
function in the file, counted by start line. `crapkit brief` and `crapkit claims` both take
it. The function's start line is the fallback handle.

## Routing

- A command refused (exit 5, 6, 7, 8 or 9, a lane with no artifact, a ratchet conflict): the `crapkit-recover` skill.
- An unattended or multi-phase run: `show-me-your-work`, with `verify`'s `run_id` in the evidence cell.
