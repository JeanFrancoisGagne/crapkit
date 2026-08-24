# Changelog

## 0.2.0 — 2026-08-24

### The start-editing packet

`brief --json` is now step one of the burn-down loop, not step two. An agent reads
the payload instead of opening the file, grepping for callers and running `git log`.
Every field is additive and `schema` stays `1`; every existing text output and JSON
field is byte-identical.

- `brief --json` gains `source` (the function's own text), `params` (its parameter
  names), `file_functions` and `file_totals` (the rest of the file, and its rollup),
  `gate_rule` (`ceiling`, `binds`, `ratchet_mark`, `mark_age_days`,
  `diff_uncovered_max`: what the edit will be judged by), `commands` (`gate`,
  `scoped_tests`, `verify`, `refresh`, already written for this file and scope),
  `lane`, `stale`, `versions`, `attempts`, `regrowth` (whether an earlier
  decomposition of this function did not hold) and `notes`.
- `coupling[]` gains `is_test`; `duplication_twins[]` gains `contained`.
- `brief NAME` takes the function's start line as a third name form. It settles a bare
  name two functions share, and it is the only handle on a function printed
  `(anonymous)`.
- `brief --batch N --json` returns `{schema, run_id, commit, stale, packets[]}`: the
  top N of the queue as N packets from one read of the store, the churn log and the
  ratchet file, for an orchestrator dealing work to a fleet.
- `next-item` gains `stale`, the field `worklist` already carried.
- `explain --json` emits what the plain output prints, and `--history` commits now
  carry their message `body`.
- `crapkit.toml` gains `[crapkit] notes` and per-scope `notes`, free-text house rules
  that ride into every packet. `doctor` warns about a scope a lane measures with no
  `[crapkit.scoped_tests]` template behind it, which leaves `commands.scoped_tests`
  null and the loop's step 4 with nothing to run.

### Performance

31 measured, adversarially verified improvements. No
scoring change (ccn identical on every function by differential test), no
schema change on any JSON output, stdout byte-identical on every read command.

- Churn: the raw log is cached deflated (`.crapkit/churn-log.z`) and refreshed
  from `cached..HEAD` instead of rewalked; `brief` and `worklist --batches`
  drop 60-80% of their wall time, `coupling` and the per-file map read through
  the same log.
- Startup: one command family imported per invocation; ~35-40 ms off every
  command, which also multiplies through every MCP `tools/call`.
- Store: identity-led index layout, integer verdict codes, deflated lane
  records; about 30% smaller on disk with faster reads. Crash-safe migration
  on first open (the file grows until the next `runs prune` or a manual
  `VACUUM` reclaims the rewritten pages).
- Analysis: one lizard pass instead of two (-40% cold), cognitive complexity
  now deterministic (state keyed on the function, not a reused `id()`),
  streamed cache writes, opt-in `CRAPKIT_ANALYSIS_MEMORY_MB` pool bound.
- Memory: coverage artifacts parse in O(chunk) not O(file); `duplication`
  releases source texts after shingling; `watch` polls with scandir.
- Hook: serial below 16 staged files, staged blobs analyzed in memory,
  pygments kept out of the process, HEAD read from the ref files.
- `lane_order` re-keys stamps so a renamed artifact path no longer orphans its
  recorded duration (silently defeating longest-first lane scheduling).
- `doctor` warns when the repo's commit-graph lacks changed-path Bloom filters.
- SARIF output is compact now (decoded-equal, deterministic, ~30% smaller
  files, 5x faster to write).

## 0.1.0

First public release.

- Per-function CRAP scoring (`ccn^2 x (1-cov)^3 + ccn`, ccn = min of standard
  and modified cyclomatic complexity) for TypeScript, TSX, JavaScript, and
  Python via lizard, with Sonar-spec cognitive complexity as a reporting column.
- Coverage lanes (istanbul and coverage.py parsers) with timeouts, retries,
  flake re-test, artifact provenance, and parallel execution.
- Churn-weighted worklist, `next-item`, session claims, disjoint-file batch
  planning, and a one-call `brief` payload for coding agents.
- Hard complexity gate on touched functions (pre-commit hook and `verify`),
  committed ratchet with metric-version stamp, rename following, debt policy,
  and an audited override trail.
- `verify` with merge-base diff scoping, portable TSV baselines, dirty-tree
  attribution, and receipt fields; SARIF and GitHub annotations output.
- `doctor` (config, lanes, unclaimed files, unmeasured directories, committed
  hooks that are not executable in the index, `--json`, `--tune`), `init` with
  test-runner detection and a commented `[crapkit.scoped_tests]` stub,
  diff-scoped mutation testing, duplication and change-coupling analysis,
  `watch`, and a read-only MCP server.
- Lane artifacts live under `.crapkit/cov/`: `init` scaffolds them there using
  each runner's own flag (`--cov-report=json:`, `--coverage.reportsDirectory`,
  `--coverageDirectory`), and `doctor` warns about a lane that writes at the
  repo root instead.
- A failed `verify` holds the baseline: runs taken after it are skipped by the
  default baseline selection until some `verify` passes, so a `coverage` run on
  the refused tree can no longer retire the finding. `verify` names the run it
  refused and both escapes, and `runs list` marks the run it compares against.
- `worklist` and `next-item` are documented as two views of one run. The
  worklist row carries an `ok` or `no-lane` marker and its JSON entries carry
  `flag` and `remedy`, so a wiring gap and a finished repo are visible without
  a second call.
- A function's scope, path and long name are stored once, in an `identities`
  table, instead of on every row of every run. An existing store migrates on
  the first open — one transaction, the old table swapped in last, so an
  interrupt changes nothing — and `runs prune` hands the freed pages back. On a
  1.1M-row store: 246 MB down to 131 MB, and 32 MB down to 14 MB per run
  written. Every read returns what it returned before, in the same order.
