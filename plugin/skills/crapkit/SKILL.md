---
name: crapkit
description: "Work one crapkit debt item end to end: the packet that replaces reading the file, the ceiling the edit is judged against, the verdict that clears the commit. Use when the session already names crapkit (crapkit.toml, .crapkit/, crapkit-ratchet.tsv, a CRAP score, `crapkit next-item`, `crapkit brief`), when the user asks to burn down debt or lower complexity in a repo crapkit measures, when a commit is refused with \"crapkit: commit blocked by the complexity gate\", \"crapkit gate: N staged function(s) exceed the complexity ceiling\" or \"decompose before committing\", or when an edit draws the mid-session advisory \"crapkit advisory: N function(s) over ceiling C\". Not for eslint, husky, or any other pre-commit gate."
---

# Working a crapkit item

One item at a time. The packet carries the file so you never open it, `gate_rule.ceiling` is
the number the edit is judged against, and `crapkit verify` is the only authoritative
verdict. Every link below points at the crapkit repo on GitHub, because the repo this
session works in does not hold those pages.

## What answers each moment

| Moment | What answers it |
|---|---|
| Before opening the file | `crapkit brief PATH NAME --json`, or the MCP `brief` tool where the server is registered |
| Which house rules bind here | `notes.repo` and `notes.scope` in the packet |
| An advisory fired on the edit you just made | Decompose that function now, not at the commit wall. The edit landed and nothing was blocked, but the gate refuses the same function later |
| An advisory fired after a Bash command, not an edit | The same verdict, read off the working tree. See [After a shell write](#after-a-shell-write) |
| Before committing | `crapkit rescore FILE --gate` |
| The gate says a staged function carries a ratchet mark | Nothing. The repo signed for that function, so the commit gate skips it. `crapkit verify` still fails a mark that rises |
| Where does new code go | `crapkit worklist --top N`, `crapkit coupling`, `crapkit duplication` |
| Which branches need tests | `uncovered_lines` and `est_uncovered_paths` in the packet |
| Running the owning scope's tests | `commands.scoped_tests`, run verbatim, never retyped |
| Before a PR | `crapkit verify --reuse-artifacts`, or `crapkit verify --base REF` |
| Did the last refactor hold | `crapkit explain PATH NAME`, and the packet's `regrowth` |
| What this session changed | `crapkit digest` |
| Handing the state to a human who will not run a command | `crapkit report`, which writes `.crapkit/report.html` and prints the path |
| Boy-scout scan of the file | `file_functions` and `file_totals` in the packet |
| A lane command crapkit refuses on Windows | Write the value in double quotes. cmd.exe does not treat `'` as a quote, so a single-quoted value reaches the runner one word per space and the guard reads a positional |
| `crapkit doctor` WARNs that a lane declares no `results_artifact` | Coverage is measured either way. Two checks go dark for that lane's scopes until it names a junit file: the crashed-worker trust check, and exit 8, the no-new-failures check |
| `trend` or `report` writing in a checkout you meant to keep read-only | Both fill a per-run rollup in the store, once per run, best effort. It is a cache: no run, baseline, ratchet or mutant moves. `commands.refresh_writes_run` still marks the one command that lands a run |
| `mutate` left worktrees behind | `crapkit mutate --drop-pool` removes `.crapkit/mutate-pool/`, the worktrees `mutation_workers > 1` keeps between runs. Every run still re-prepares them, `git checkout --force` on HEAD then `git clean -xdff`; what keeping them saves is the build, 30.6 s down to 0.46 s on a 31,459-file repo |

Field semantics live in [docs: the brief payload](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md#brief);
the five-step loop lives in [AGENTS: the packet](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#1-the-packet).

## After a shell write

A `Bash` PostToolUse event carries the command and never a file path, so source written
through a heredoc or a `python - <<PY` block names no file for the hook to judge. It falls
back to the working tree: the `*.py` files git reports dirty or untracked whose mtime
lands inside a 12-second window, at most 25 of them, each through the same per-file ladder
an Edit takes. Scope, sequencing, ratchet marks and the untracked rule mean what they
already meant, and exit 2 means what it always meant, one advisory line about a function
the write pushed over its ceiling, with nothing blocked.

Only Python. Every other language stays the commit gate's business.

This fires only where the consumer registered a `Bash` matcher of their own; the shipped
plugin registers `Edit|Write`. A clean tree, a file older than the window, or a shell call
outside any repo all stay silent, so what an advisory names here is source written seconds
ago, never the tree's standing debt.

## Two fields decide whether a number is worth reading

Read `stale` and `uncovered_lines_note` first. `stale: true` means the run predates HEAD:
run `commands.refresh` before anything else. `uncovered_lines: null` means no artifact named
lines for this file, and `flag` says which case you are in:

- `untested`: write the first test at the public seam, then `crapkit coverage`. The lines appear.
- `measured`: the artifact no longer matches the tree. Commit or revert the edits, then `crapkit coverage`.
- `cc-only`: the scope sets `coverage_optional`, so only decompose clears it. Most languages land here, because only Python and JS/TS have coverage parsers.
- `[]`: the artifact answered and nothing is dark.

## Decompose: subtract first

A dead branch deleted drops ccn for free and needs no new name. Then extract private
helpers, picking names `file_functions` shows the file does not hold yet. Over the ceiling
and unsure where the cut goes: read [cuts.md](cuts.md).

One function that is a whole command handler rather than one decision is a redesign, not a
split. Say so and stop.

## With tdd

The packet supplies what `tdd` otherwise asks a user for. `params`, `file_functions` and the
module's exports are the pre-agreed seam. `gate_rule.ceiling` is the pre-agreed scope.
Everything else about writing the test first comes from `tdd`.

## One packet per function you actually edit

A packet is large and mostly source text. Trajectory questions (did this fall and come back,
when did it get worse, which commits touched it) go to `crapkit explain PATH NAME`, which
answers them far cheaper.

## Where a repo trap goes

A trap you learn about the repo goes into its `crapkit.toml` `notes`, repo-wide or under the
owning scope, where every future packet carries it. Keys are documented in
[docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md).
This file stays repo-free.

## Naming an anonymous function

The packet's `handle` field is the stable form: `(anonymous)#N` names the Nth anonymous
function in the file, counted by start line. `crapkit brief` and `crapkit claims` both take
it. The function's start line is the fallback handle.

## Routing

- A command refused (exit 5, 6, 7, 8 or 9, a lane with no artifact, a ratchet conflict), or printed a line that reads like a refusal and is not: the `crapkit-recover` skill.
- An unattended or multi-phase run: `show-me-your-work`, with `verify`'s `run_id` in the evidence cell.
