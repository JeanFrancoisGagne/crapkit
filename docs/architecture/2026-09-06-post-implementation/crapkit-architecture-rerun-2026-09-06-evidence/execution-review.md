# Fresh execution architecture review

Three strong candidates have public command reproductions: unsafe automatic reuse, conflicting artifact writers, and duplicate scope ownership rules in init. All are new findings against commit `499d9db4f9ff4d212fb94ea3975c7477b6b1c968` in `C:/Users/jfgag/crapkit`.

| Rank | Candidate | Observed result |
| --- | --- | --- |
| 1 | EX1: Account for test inputs before automatic artifact reuse | Reused verify exits 0 after a test edit; fresh verify on the same tree exits 8. |
| 2 | EX3: Own artifact paths through the whole measurement | Concurrent coverage commands both report B's load 2, although A wrote load 1. |
| 3 | EX2: Use one scope ownership rule for inventory and init | Root scope owns the child source, but init writes a shadowing config; named-prefix control refuses. |

The companion `execution-candidates.json` contains structured cards, exact source anchors, commands, costs, and coverage dispositions. Evidence files are beside this report. Source anchors are relative to the repository above. No production, test, or config file was changed; the final root status check was clean.

## EX1: automatic reuse ignores test inputs

The lane module decides freshness by filtering changed files through scored source scopes. That answers which source belongs to the lane; it does not answer whether its tests and runner inputs changed. Coverage and verify trust the result through their shared scoring path.

| Source | Role |
| --- | --- |
| `src/crapkit/lanes.py:443` | Stamp records commit, lane name, and duration. |
| `src/crapkit/lanes.py:577` | `_scope_changes` drops changes outside scored source scopes. |
| `src/crapkit/lanes.py:627` | `lane_unchanged` accepts that result as measurement freshness. |
| `src/crapkit/cli/scoring.py:129` | `_lane_reuse` enables reuse for coverage and verify. |

The disposable public CLI fixture first records a passing lane. Changing tracked `tests/state.txt` from `pass` to `fail` leaves `src` untouched. Reused verify records run 2 with `ok=true`, exits 0, and leaves the runner counter at 1. Fresh verify on the identical tree records run 3, exits 8, reports `tests.test_app::test_f`, and raises the counter to 2. This is a false passing measurement, not only a stale display.

| Before | After |
| --- | --- |
| Test input changes -> source filter drops it -> old coverage/JUnit -> passing verify | Measurement input changes or validity is unknown -> fresh lane -> current verdict |

Concentrate measurement validity in the lane module. Changed tests, runner configuration, commands, and other unproven inputs must block automatic reuse. Keep explicit `--reuse-artifacts` as a deliberate request to read an existing artifact. Remove the source-scope predicate as the authority for freshness; retain it for corpus ownership and warnings.

The gain is one correct decision for all callers. The cost is more executions when relevance is unknown. No speed gain was measured. Arbitrary shell commands can read external state, so the repair must state its input limits rather than promise complete dependency inference. A commit plus changed-path names also cannot distinguish repeated changes to already-dirty files; this follows from the stamp shape and was not separately replayed.

Validate test, source, runner-command, configuration, and repeated dirty-input edits through public commands. Assert the runner counter, verdict, and ledger together. Existing tests cover source edits and intentionally preserve docs-only reuse; a repair must explain when that optimization remains justified.

Evidence: `execution-reuse-probe.py`, `execution-reuse-probe.txt`, `execution-reuse-result.json`. The same probe's bare-string scope case belongs to the root reviewer's config admission candidate.

## EX2: init has a second scope meaning

Inventory recognizes a root scope. Init's private helper still says root scope claims nothing. Both paths interpret the same configuration, so a valid parent corpus and the nested-config guard disagree.

| Source | Role |
| --- | --- |
| `src/crapkit/cli/admin.py:553` | Ancestor guard before init writes. |
| `src/crapkit/cli/admin.py:570` | Duplicate scope rule with outdated root-path meaning. |
| `src/crapkit/cli/admin.py:579` | Private overlap/prefix interpretation. |
| `src/crapkit/universe.py:114` | Root-aware matcher used for real corpus ownership. |
| `src/crapkit/universe.py:168` | Ownership selection. |

With ancestor `paths=['.']`, public corpus assignment admits `child/src/app.py`, yet public `init --repo child` exits 0 and writes `child/crapkit.toml`. Changing only the ancestor scope to `paths=['child']` produces the same admitted source but init exits 3 and writes no nested config.

| Before | After |
| --- | --- |
| Inventory matcher and init prefix rule disagree -> child config shadows parent | Shared scope meaning -> overlap recognized -> refusal before writes |

Move the ownership question into the existing scope module and remove admin's duplicate root/prefix interpretation. Keep overlap in both directions: an ancestor scope can sit below the proposed config directory. This preserves ADR 0002; it does not ask to change that decision.

The gain is locality and less duplicated policy. The cost is small; no speed claim applies. Validate root paths, named prefixes, scopes below the proposed root, nested repositories, linked worktrees, explicit `--repo`, and no writes on refusal. Do not forbid independently owned nested configurations.

Evidence: `execution-init-probe.py`, `execution-init-probe.txt`, `execution-init-result.json`.

## EX3: separate invocations share measurement files

Configuration admission rejects duplicate artifact paths among lanes in one configuration. Separate commands still write the same declared coverage, JUnit, log, and stamp paths. The execute-to-parse interval has no cross-process owner.

| Source | Role |
| --- | --- |
| `src/crapkit/config.py:752` | Within-config artifact-path protection. |
| `src/crapkit/lanes.py:42` | Shared per-lane log path. |
| `src/crapkit/lanes.py:175` | Lane writes through that log and configured command. |
| `src/crapkit/lanes.py:411` | Freshness establishes a write occurred, not its author. |
| `src/crapkit/lanes.py:458` | Shared stamp update. |
| `src/crapkit/lanes.py:1068` | Execute then read configured artifact. |
| `src/crapkit/cli/scoring.py:268` | Shared measurement pipeline. |

Two real coverage processes run a synthetic configured lane with a deterministic barrier. A writes a covered function with expected load 1 and waits. B writes an uncovered function with load 2 into the same path, then releases A. Both commands exit 0 and report load 2 and B's digest. A's own measurement record proves it wrote load 1. The fixture varies an environment value to make writer attribution visible; it does not claim all concurrent same-environment suites produce different data.

| Before | After |
| --- | --- |
| A writes -> B overwrites -> both parse B -> both persist B | Run owns artifact paths through parse/stamp -> conflict waits or refuses -> result belongs to its invocation |

Give one measurement ownership of conflicting artifact paths through execution, parsing, and stamping. Serialize or refuse the conflict, or use private outputs where the configured command supports them. Keep independent worktrees, read-only commands, and parallel lanes within one run independent.

The gain is reliable attribution for every scoring caller. The cost is waiting or refusal for simultaneous conflicting measurements. No timing gain was measured. Keep existing freshness checks for interrupted runs; removing them does not solve ownership. Avoid spreading separate lock checks across CLI commands or creating a general execution framework.

Validate with two real processes and a barrier, asserting result values and digests. Cover coverage/verify overlap, conflicting reuse, crash cleanup, and operating-system lock release. Shared-log and stamp interference follows from the same ownership gap but was not separately measured.

Evidence: `execution-concurrent-probe.py`, `execution-concurrent-probe.txt`, `execution-concurrent-result.json`.

## Reviewed areas and retained design

| Area | Inspection | Disposition |
| --- | --- | --- |
| CLI bootstrap/parser/shared | Lazy handlers, registration, error and output helpers | Retain lazy dispatch and public main. No split by file size. |
| CLI scoring/verifying | Inventory, reuse, lane collection, persistence, rescore, verdict callers | EX1/EX3. Retain typed scored-run result and declaration-order collection. |
| Queue/reports/ratchet commands | Entrypoints, batch loaders, full handles, targeted history/context reads, outputs | Retain existing owning modules and batch reads. State internals belong to parallel review. |
| Config/rootfind/scaffold/doctor/admin | Admission, init discovery, ancestor guard, pytest readers, interpreter probes, tune/watch | EX2. Config shape evidence passed to root's shared-rule candidate. Retain discovery ADR and bounded framed probe. Another admin module would move lines without proving new rule ownership. |
| procs/lanes | Capture, deadlines, transport, run/reuse/stamp/read/retry paths | EX1/EX3. Retain literal transport, timeout cleanup, bounded capture, and progress signals. |
| junitparse | Full module plus lane-result role | Retain pure reader, combined summary, unfinished-suite refusal, and positive retry evidence where failure/skip of an ID wins. No new change recommended. |
| mutate/mutate_pool/CLI analyses | Token eligibility, type-angle refusal, snapshots, locks, workers, restore/cleanup | Retain private worktrees for every worker count, dirty/untracked/deleted inputs, pool locking, and narrow ambiguity refusal. |
| watch/invocation | Full core files and watch command loop | Retain batched directory polling and documented start-time tracked-file set. |
| MCP | Registry, schemas, argument admission, root resolution, spawn, transport | Retain thin CLI adapter and ADR 0001. No new confirmed transport finding. |
| action/tools/action | Workflow steps and comment renderer's inputs/status/base-change/output core | Retain isolated action state and JSON body transport. Root owns CI, package, and release findings. |
| plugin/claude_hook | Manifest, MCP config, 50 PostToolUse handler entries, recovery guidance, early admission and staged diff path | Retain protocol guard, early exits, and store avoidance. No host installation performed. |

No new parser performance measurement was made. The timing comment in `junitparse.py` is historical source documentation, not a fresh result from this review. Reading the small module completed lane-results coverage without rerunning root tests.

## Limits and unpromoted leads

- Config admission is coordinated with root: bare `paths='src'` yields public inventory exit 0 with zero files/functions. Root has additional schema/runtime mismatches; a duplicate card would divide the same rule-ownership change.
- The cancellation probe did not prove a child survives after the API returns. Its delayed interrupt includes Windows and standard-library wait behavior. Keep `execution-cancel-probe.py`, its text output, and result JSON as inconclusive evidence, not an orphan-process finding.
- No full suite, configured root verify, install, commit, push, or network write ran during this review. Probes used temporary repositories and the root source through `PYTHONPATH`, with import guards.
- No cross-platform execution, external runner matrix, live MCP client, or installed plugin host conformance run was performed. Parallel reviewers own deeper state, analysis, packaging, and release review.
- These cards claim demonstrated behavior and concrete rule ownership gains. Costs are design tradeoffs; no speedup or universal dependency guarantee is claimed.

## Replay

Run one evidence script at a time from PowerShell. Each creates its own disposable fixture and invokes the public CLI with an import guard. These commands do not run the root suite or write root state.

```powershell
$env:PYTHONPATH = 'C:\Users\jfgag\crapkit\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
python 'C:\Users\jfgag\Documents\Codex\2026-09-06\c-users-jfgag-appdata-local-temp\work\architecture-rerun\execution-reuse-probe.py'
python 'C:\Users\jfgag\Documents\Codex\2026-09-06\c-users-jfgag-appdata-local-temp\work\architecture-rerun\execution-init-probe.py'
python 'C:\Users\jfgag\Documents\Codex\2026-09-06\c-users-jfgag-appdata-local-temp\work\architecture-rerun\execution-concurrent-probe.py'
```
