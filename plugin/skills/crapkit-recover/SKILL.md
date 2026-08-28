---
name: crapkit-recover
description: "Recover a crapkit run that refused: which exit code means what, the four causes behind \"produced no artifact\", the tainted-baseline escape, and why a crapkit-ratchet.tsv conflict goes to `crapkit ratchet merge` and never to hand-resolution. Use when a crapkit command exits 5/6/7/8/9, a lane reports \"produced no artifact\", a ratchet regression names a function you never touched, verify reports a tainted baseline, or git conflicts crapkit-ratchet.tsv."
---

# Recovering a refused run

Route by the string the command printed. Every row links the page that owns the failure and
names the one command to run before deciding anything. The links point into the crapkit
repo on GitHub, so they resolve from whatever repo this session is working in.

## By exit code

| Exit | What refused | Owner | First command |
|---|---|---|---|
| 3 | config: `crapkit.toml` unparseable, a metric-stamp mismatch, a `test-scoped` file under no templated scope | [docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | `crapkit doctor` |
| 4 | git: not a repository, or a baseline commit rewritten out of the history | [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) | `crapkit runs list` |
| 5 | a lane produced no artifact, timed out past its retries, or refused a container | [docs: what a failed lane does to scoring](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#what-a-failed-lane-does-to-scoring) | `crapkit coverage --lane NAME` |
| 6 | gate: a function the diff touched is over its ceiling | [AGENTS: gate the edit](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#3-gate-the-edit) | `crapkit rescore FILE --gate` |
| 7 | ratchet: a marked function scores worse than its recorded mark | [docs: how verify uses the ratchet](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#how-verify-uses-the-ratchet) | `crapkit explain PATH NAME` |
| 8 | a test that passed in the baseline fails now | [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) | `crapkit test-scoped FILE` |
| 9 | more uncovered changed lines than `diff_uncovered_max` | [docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | `crapkit verify --json` |

`verify` reports the first of 6, 7, 8, 9 that fires, so a fixed 6 can uncover a 7 underneath
it. Exit 1 is three unrelated things at once:
[README: exit 1 means one of three things](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-1-means-one-of-three-things)
splits them by command.

## "produced no artifact": four causes

The lane log names which one. It sits at `.crapkit/lane-<name>.log`, and the failure line
quotes its tail.

| Cause | Signature in the log | Owner |
|---|---|---|
| No coverage provider installed | `MISSING DEPENDENCY '@vitest/coverage-v8'`, or pytest rejecting `--cov` without pytest-cov | [docs: getting an artifact out of vitest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#getting-an-artifact-out-of-vitest) |
| Tests failed, so the runner wrote no report | a red suite and no file, vitest with `reportOnFailure` unset | [docs: reportOnFailure](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#reportonfailure) |
| The report landed somewhere the lane does not name | the suite passed and `artifact` still points at nothing | [docs: where artifacts live](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#where-artifacts-live) |
| Killed or refused before it could write | `timed out after Ns (attempt N)`, or `host-only (container runs OOM)` | [docs: timeouts and retries](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#timeouts-and-retries) |

This is tooling, not your code. The run still happened: the failed lane's scopes fall back
to `no-lane`, the run is typed `partial`, and `verify` refuses to conclude at all.

## Exit 7 on a function you never touched

Test rot, not code rot. A ratchet regression fires whether or not the diff touched the
function, which is the whole point: deleting the coverage behind an untouched function is
enough to raise its CRAP. Start at `crapkit explain PATH NAME`, look for the test that
stopped exercising it, and restore the coverage rather than the code.

## Tainted baseline

`run N is not the baseline: verify run M FAILED with K finding(s)` means the newest run
never cleared its findings, so an older one is being measured against. Two escapes, both
legitimate:

- Fix the findings the older baseline still shows, then rerun `crapkit verify`.
- Accept the newer run by name: `crapkit verify --baseline N`, a visible act somebody can audit later.

Owner: [README: the trusted baseline](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#the-trusted-baseline).
`crapkit runs list` prints `verdict=-` on runs that rendered no verdict.

## A conflicted crapkit-ratchet.tsv

The `resolving-merge-conflicts` skill's always-resolve rule does not apply to this file. Do
not resolve it by hand and do not take one side: hand-resolution is exactly where a mark
gets raised, which the ratchet exists to forbid. `crapkit ratchet merge` is the resolver,
and per key it takes the side that changed, or the lower value when both did.

A conflict here means the merge driver is not installed in this clone. Install it, then redo
the merge:

    git config merge.crapkit-ratchet.driver "python -m crapkit ratchet merge %O %A %B"

Owner: [docs: the git merge driver](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-git-merge-driver).
When the driver itself refuses (`marks from different metric versions cannot merge`),
re-baseline one side with `crapkit ratchet seed` and merge again.
