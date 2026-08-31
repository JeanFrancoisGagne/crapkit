---
name: crapkit-recover
description: "Recover a crapkit run that refused, and tell a real refusal from a line that only looks like one: which exit code means what, the five causes behind \"produced no artifact\", the tainted-baseline escape, and why a crapkit-ratchet.tsv conflict goes to `crapkit ratchet merge` and never to hand-resolution. Use when a crapkit command exits 3/5/6/7/8/9, a lane reports \"produced no artifact\", doctor says a shell \"cannot run\" a lane's first word or that a lane \"declares no results_artifact\", a run \"cannot serve as a baseline\", marks \"were recorded under\" another metric version, a ratchet regression names a function you never touched, verify reports a tainted baseline, git conflicts crapkit-ratchet.tsv, `crapkit claude-hook` exits 2 with an advisory, or `crapkit doctor --plugin-root` reports drift."
---

# Recovering a refused run

Route by the string the command printed. Every row names the one command to run before you
decide anything. The links point at the crapkit repo on GitHub, because the repo this
session works in does not hold those pages.

## Lines that are not failures

Start here. Each of these reads like a refusal and none of them stopped anything.

| What printed | What it means | What to do |
|---|---|---|
| "crapkit advisory: N function(s) over ceiling C in PATH (the edit landed; nothing was blocked)", exit 2 from `crapkit claude-hook` | The PostToolUse hook judged a function the edit changed. PostToolUse runs after the write and cannot block | Decompose that function now. The commit gate refuses it later, with more work stacked behind it |
| "crapkit gate: N staged function(s) carry a ratchet mark and were not gated — `crapkit verify` fails a mark that rises" | The commit gate exempted debt the ratchet already signed for. The commit went through | Nothing. Only `crapkit verify` judges whether a mark rose |
| "crapkit doctor: the plugin at PATH is version X, this crapkit is Y", exit 1 | The plugin and the CLI ship as separate artifacts and drifted apart | Reinstall whichever is behind: `claude plugin install crapkit@crapkit`, or reinstall the CLI |
| "crapkit doctor: checking PATH", then nothing | You named a directory above the plugin root and doctor found the install under it. The line says which tree the verdict is about | Nothing. Exit 0 means the plugin and the CLI agree |
| "WARN lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it", from `crapkit doctor` | The lane measures coverage exactly as before. What it cannot feed are the two checks that read a test-results file | Add the junit flag and `results_artifact` the WARN prints. Until then exit 8 can never fire for that lane's scopes: [AGENTS: when a lane will not start](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#when-a-lane-will-not-start) |
| "warning: crapkit-ratchet.tsv carries no metric stamp (written before stamping)", from `crapkit verify` | The marks file predates stamping, so nothing can be compared against it | `crapkit ratchet seed` stamps it: [docs: the metric stamp](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-metric-stamp) |

Three more lines come out of `crapkit doctor --plugin-root`, same exit 1.
"crapkit doctor: the plugin at PATH asks for hook protocol N" means the plugin is ahead of
the CLI, so the advisory hook exits 0 in silence on every edit.
"crapkit doctor: the plugin at PATH has no .claude-plugin/plugin.json" means the path is not
a plugin root and holds no crapkit install below it. "crapkit doctor: no installed crapkit
plugin under DIR" means the bare flag found nothing in Claude Code's plugin directory: install
with `claude plugin install crapkit@crapkit`, or pass a PATH. `crapkit claude-hook` and
`crapkit doctor --plugin-root` are both specified in
[README: subcommands](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#subcommands).

Exit 2 from any other crapkit command is argparse: the subcommand or the flag does not exist
in this version.

## By exit code

| Exit | What refused | Owner | First command |
|---|---|---|---|
| 3 | config: `crapkit.toml` unparseable, a lane command the guard refuses, a metric-stamp mismatch, a `test-scoped` file under no templated scope | [docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | `crapkit doctor` |
| 4 | git: not a repository, or a baseline commit rewritten out of the history | [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) | `crapkit runs list` |
| 5 | a lane produced no artifact, timed out past its retries, or refused a container | [docs: what a failed lane does to scoring](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#what-a-failed-lane-does-to-scoring) | `crapkit coverage --lane NAME` |
| 6 | gate: a function the diff touched is over its ceiling and above any ratchet mark it carries | [AGENTS: gate the edit](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#3-gate-the-edit) | `crapkit rescore FILE --gate` |
| 7 | ratchet: a marked function scores worse than its recorded mark | [docs: how verify uses the ratchet](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#how-verify-uses-the-ratchet) | `crapkit explain PATH NAME` |
| 8 | a test that passed in the baseline fails now | [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) | `crapkit test-scoped FILE` |
| 9 | more uncovered changed lines than `diff_uncovered_max` | [docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | `crapkit verify --json` |

`verify` reports the first of 6, 7, 8, 9 that fires, so a fixed 6 can uncover a 7 underneath
it. Exit 1 is three unrelated things at once:
[README: exit 1 means one of three things](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-1-means-one-of-three-things)
splits them by command.

## Two exit-3 signatures worth naming

Exit 3 fires before any lane runs, so nothing was measured and nothing was written.

`ratchet marks were recorded under [crapkit-analysis=7 lizard=1.24.0] but this run
measures [crapkit-analysis=8 lizard=1.24.0]` is an upgrade, not a break. Shell cognitive
complexity nests since analysis 8, so shell numbers moved and CRAP scores from the two
versions are not comparable; ccn did not move. `crapkit ratchet seed` re-baselines the
marks under the running metric, and that is the whole fix:
[docs: the metric stamp](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-metric-stamp).

`lane 'py': positional argument 'slow'' narrows a full-suite coverage run ... (cmd.exe
does not treat ' as a quote: write the value in double quotes)` is the lane guard reading
the command the way the shell will. On Windows a single-quoted value reaches the runner
one word per space, so the guard sees a positional that would narrow the run. Rewrite the
value in double quotes:
[AGENTS: when a lane will not start](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#when-a-lane-will-not-start).

## "produced no artifact": five causes

The lane log names which one. It sits at `.crapkit/lane-<name>.log`, and the failure line
quotes its tail.

| Cause | Signature in the log | Owner |
|---|---|---|
| No coverage provider installed, vitest | `MISSING DEPENDENCY '@vitest/coverage-v8'` | [docs: getting an artifact out of vitest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#getting-an-artifact-out-of-vitest) |
| No coverage provider installed, pytest | `unrecognized arguments: --cov`, so pytest-cov is missing from the environment the SUITE runs in, which a pipx or uv-tool install of crapkit never shares | [docs: pytest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#pytest) |
| Tests failed, so the runner wrote no report | a red suite and no file, vitest with `reportOnFailure` unset | [docs: reportOnFailure](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#reportonfailure) |
| The report landed somewhere the lane does not name | the suite passed and `artifact` still points at nothing | [docs: where artifacts live](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#where-artifacts-live) |
| Killed or refused before it could write | `timed out after Ns (attempt N)`, or `host-only (container runs OOM)` | [docs: timeouts and retries](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#timeouts-and-retries) |

Before triaging any of the five, check that the command ran at all. `crapkit doctor` reads
each lane with the shell that will run it and FAILs one whose first word will not start:
`lane 'py': cmd.exe cannot run 'python' (exit 9009)`. On Windows that is usually the Store
`python.exe` alias a stock PATH carries with no Store app behind it, which resolves and
then refuses to run, so nothing that only reads PATH sees it. Point the lane at a python
that runs: [AGENTS: when a lane will not start](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#when-a-lane-will-not-start).

This is tooling, not your code. The run still happened: the failed lane's scopes fall back to
`no-lane`, the run is typed `partial`, and `verify` refuses to conclude at all.

## Exit 7 on a function you never touched

Test rot, not code rot. A ratchet regression fires whether or not the diff touched the
function, which is the whole point: deleting the coverage behind an untouched function is
enough to raise its CRAP. Start at `crapkit explain PATH NAME`, look for the test that
stopped exercising it, and restore the coverage rather than the code.

## Tainted baseline

`run N is not the baseline: verify run M FAILED with K finding(s)` means the newest run never
cleared its findings, so an older one is being measured against. Two escapes, both
legitimate:

- Fix the findings the older baseline still shows, then rerun `crapkit verify`.
- Accept the newer run by name: `crapkit verify --baseline N`, a visible act somebody can audit later.

Owner: [README: the trusted baseline](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#the-trusted-baseline).
`crapkit runs list` prints `verdict=-` on runs that rendered no verdict.

The second escape can itself be refused, at exit 1, and the refusal carries the answer:

    crapkit: run 1 is an inventory run (no coverage was measured) and cannot serve as a baseline; trusted runs: 2; pass `--baseline 2` for the newest

Read the middle clause. It names why that run cannot serve, and the four reasons are a
failed verify, a hook run, a partial run (a lane subset, or a lane that failed) and an
inventory run. Then take an id from `trusted runs`, or the one the line hands you. A run
id that is not in the store at all gets a different line naming `crapkit runs`.

## A conflicted crapkit-ratchet.tsv

The `resolving-merge-conflicts` skill's always-resolve rule does not apply to this file. Do
not resolve it by hand and do not take one side: hand-resolution is exactly where a mark gets
raised, which the ratchet exists to forbid. `crapkit ratchet merge` is the resolver, and per
key it takes the side that changed, or the lower value when both did.

A conflict here means the merge driver is not installed in this clone. Install it, then redo
the merge:

    git config merge.crapkit-ratchet.driver "crapkit ratchet merge %O %A %B"

Owner: [docs: the git merge driver](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-git-merge-driver).
When the driver itself refuses (`marks from different metric versions cannot merge`),
re-baseline one side with `crapkit ratchet seed` and merge again.
