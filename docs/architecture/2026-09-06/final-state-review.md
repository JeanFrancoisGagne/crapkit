# Store, identity and retention reassessment

Review target: current integrated `<repo>`, read without edits. The review found two retention defects. Both have failing regressions and a verified incremental repair in `work/implementation/review-root`. Root applied the patch and passed 48 combined cache, numeric and retention checks.

## New findings and disposition

| Finding | Reproduction | Disposition |
|---|---|---|
| P1: pruning removes the only evidence that a legacy ordinal changed meaning | `ratchet seed` refuses a legacy `(anonymous)#2` mark; `runs prune --keep 1` removes the collision run; the same seed then succeeds and stamps the new key format | Fixed in `evidence/final-review/prune-retention.patch`: retain one recent witness run for every historical collision group |
| P1: pruning deletes a concurrently written run that it never considered | A second Python process commits coverage or a failed verify after the CLI reads history but before deletion; both disappear in the original code | Fixed in the same patch: deletion is limited to IDs observed by the retention decision |

The first reproduction uses three coverage runs. The first run has legacy callbacks at lines 1, 1 and 10. Later runs place them at 1, 2 and 10. Under the old start-only rule, ordinal 2 named the callback at line 10. Its current ordinal is 3. The history check correctly refuses reassignment until pruning erases the first run.

| Current-root source before this patch | Role |
|---|---|
| `src/crapkit/store.py:781` | Historical collisions come from surviving function rows |
| `src/crapkit/ratchet.py:87` | Legacy marks refuse a known collision group |
| `src/crapkit/cli/_shared.py:288` | Mark readers and writers obtain historical collision evidence |
| `src/crapkit/cli/reports.py:263` | Retention chooses IDs from one history read |
| `src/crapkit/store.py:1234` | Deletion reads live IDs again after that decision |
| `src/crapkit/store.py:1400` | Original keep-set preserves baseline trust but has no identity witnesses |

## Repair and simpler ownership

The store now supplies witness run IDs using the same collision query that migration already trusts. There is one predicate for legacy duplicate positions, mixed old/new positions, and fresh same-line occurrences. Copies across scopes remain one function and do not create a witness.

Retention keeps the newest run for each collision group. Eight redundant runs with the same collision shrink to one witness. One run can witness several groups. A second prune preserves the same groups and deletes nothing more. No evidence table or schema migration was added.

The CLI captures run history once and gives those IDs to `prune_runs`. A writer that commits afterward remains outside the deletion set. This avoids expanding lock duration or holding a write lock around VACUUM. Existing direct callers can omit `observed_ids` and retain the original explicit keep-set behavior.

The pure `prune_keep_set` still owns baseline trust, failure blockers, passing verify IDs, digest pairs and override anchors. It accepts the witness IDs as one additional protected set. The CLI only gathers the inputs and applies that decision.

| Repair source in review-root | Change |
|---|---|
| `src/crapkit/store.py:785` | `identity_witness_run_ids` selects one recent witness per group |
| `src/crapkit/store.py:798` | `_collision_rows` supplies both migration checks and retention |
| `src/crapkit/store.py:1245` | `_doomed_ids` intersects deletion with observed IDs |
| `src/crapkit/store.py:1414` | Keep-set includes supplied identity evidence |
| `src/crapkit/cli/reports.py:264` | One captured history determines deletion eligibility |
| `tests/unit/test_prune_identity_witnesses.py` | Ten public-command and concurrency regressions |

## Claim retention contract checked after integration

Explicit run pruning resets claims older than the oldest retained run, including open claims on functions that remain present. This is the existing documented behavior, not an additional defect. [the claims contract](../../agent-json.md#claims) lists pruning as one of the three release paths. `tests/e2e/test_worklist_surface_e2e.py:390` takes a claim on `core/alpha.py`, ages that claim, prunes, and verifies that the same still-present function is offered again. `tests/unit/test_claims.py:64` pins the timestamp boundary. Both exact tests passed against current root after the retention patch; see `evidence/final-review/claim-prune-contract.txt`.

The original comment on `SnapshotStore.prune_claims` said an old claim names a function that surviving history can never score. That explanation is false for the tested present-function case. Root corrected the comment to state the explicit ownership reset; the timestamp behavior still matches the published contract.

## Retained fixes checked across callers

| Area | Source at the review checkpoint | Checked invariant |
|---|---|---|
| Current-store admission | `store.py:415` | Schema, code tables and lane encoding decide whether preparation is required; current reads perform no setup writes |
| Legacy and mixed writers | `store.py:458`, `store.py:515` | Missing occurrence and claim key-version fields receive legacy defaults; old text lane rows trigger preparation |
| Atomic claim acquisition | `store.py:957`, `store.py:978` | Conflict check and insertion share a short `BEGIN IMMEDIATE`; a conflicting claim returns no ID |
| Queue refill after competition | `cli/queue.py:143` | A lost claim tries the next ranked candidate rather than reporting an item it did not acquire |
| Exact queue selectors | `cli/queue.py:70`, `cli/queue.py:99` | Handles and canonical keys come from complete file positions; ranking and scope cuts cannot renumber twins |
| Legacy ownership | `keys.py:136`, `keys.py:152` | Claims without proved identity hold the entire raw-name group |
| Claim completion | `worklist.py:207`, `worklist.py:217`, `cli/verifying.py:415` | Every scope must finish the exact function; legacy claims wait for every twin; absence alone cannot close a claim |
| Release storage | `store.py:1025`, `cli/queue.py:349` | Closed IDs stay closed; displayed handles resolve the intended claim |
| Ranked worklist | `worklist.py:245`, `cli/queue.py:1026` | Shared admission and pushed-down floor retain over-target debt; complete-file handles survive filtered reads |
| Per-function marks | `store.py:739`, `store.py:760`, `worklist.py:152` | Full positions separate scores and verdicts; committed marks use canonical keys counted over the whole run |
| Historical report identity | `store.py:1205` | History ranks each run by source position and takes one worst scope copy; ambiguous legacy rows refuse comparison |
| HTML drill-down | `report.py:269` | Commands use the reported handle, falling back to a line only when no handle exists |
| Ratchet key migration | `ratchet.py:69`, `ratchet.py:87`, `cli/ratchet_cmds.py:103` | Identity version is separate from metric version; unresolved old mappings and mixed-version merges refuse rewriting |
| Ratchet value admission | `ratchet.py:130`, `ratchet.py:165` | NaN and infinity never enter marks; writers reject malformed input while report readers report skipped rows |
| Ratchet history | `ratchet_report.py:12`, `ratchet_report.py:47`, `ratchet_report.py:84` | Shared TSV admission; updates retain entry dates and record new values; comment-only commits advance the clock |
| Baseline selection | `store.py:1302` | One chronological pass retains the failure blocker; crashed verifies do not clear it; explicit baselines remain a caller choice |
| Prior retention repairs | `store.py:1387`, `store.py:1400` | Prune preserves the chosen clean baseline and failures that still constrain future coverage |
| Digest | `digest.py:128` | Canonical keys separate twins; scope copies compare at their highest CRAP; output keeps the ordinal |

`src/crapkit/cli/reports.py` explain context and `src/crapkit/cli/claude_hook.py` final reader admission fixes belong to execution_delivery. Its source changes remain outside this patch. The separate reader migration repair now records analysis-version proof on runs, refuses unproved anonymous marks before writes, and makes claims from old or unproved JavaScript-family snapshots hold their raw-name group. No debt or claim is remapped. Its 196-check green run, claim rerun and corrected three-case e2e rerun are recorded in `evidence/evidence-execution/reader-migration-handoff.md`.

## Evidence and validation

| Check | Result | File |
|---|---|---|
| Cold current-root public CLI witness probe | Seed 3, prune 0, seed 0 before repair | `evidence/final-review/prune-key-history-probe.py`, `evidence/final-review/prune-key-history-probe.txt` |
| Tests before production edits | Five failures: witness, bounded history, shared witness, concurrent coverage, concurrent failed verify | `evidence/final-review/prune-regressions-red.txt` |
| Initial repaired slice | Five pass | `evidence/final-review/prune-regressions-green.txt` |
| Expanded store/ratchet/claims/identity sweep | 457 pass; one stale worktree fixture fails | `evidence/final-review/prune-focused.txt` |
| Current-root ratchet fixture plus ten new retention tests | 19 pass | `evidence/final-review/prune-current-tests.txt` |
| Affected worklist, claims competition and ratchet e2e | 59 pass | `evidence/final-review/prune-e2e.txt` |
| Changed production complexity | Seven functions, maximum CCN 5 | `evidence/final-review/prune-complexity.txt` |
| Staged hook | Exit 0 | `evidence/final-review/prune-hook.txt` |
| Whitespace and application check | Clean; incremental patch applies to frozen root | `evidence/final-review/prune-retention.patch` |

The stale failure is `test_update_compares_against_the_worst_twin` in review-root. Current root already tests this behavior as `test_update_compares_against_the_worst_scope_copy`, with a different scope for the second measurement. A byte copy of that current test ran from the evidence directory and passed against the repaired worktree. No existing test was changed to hide the failure.

Every Python command set `PYTHONPATH` to the source tree under test. The new concurrency cases use a real second Python process and SQLite connection. Their only instrumentation starts that writer after the CLI captures history, before it deletes runs; it does not fake a store result. Root source, tests and docs were not edited.

## Remaining limits

- Legacy rows without enough position evidence continue to refuse exact selection or comparison. Refreshing analysis does not authorize reassignment of unresolved committed marks.
- Ordinal keys survive line drift. Adding, deleting or renaming a sibling changes ordinal meaning by design; they are not permanent function IDs.
- Retention is a floor, not a disk-size cap. Passing verify baselines, override anchors, failure blockers and collision witnesses can keep more runs than `--keep` requests. Witness retention adds at most one selected run per distinct collision group, with shared runs counted once.
- The repair protects runs created after a retention snapshot. It does not claim to serialize every separate command that might update an older run while pruning occurs.
- No new throughput claim is made. The schema checks, history collision query and digest pair search remain visible costs. This reassessment measured correctness and bounded witness selection, not large-history latency.

## Patch boundary

`evidence/final-review/prune-retention.patch` contains only the incremental changes from captured before copies of `src/crapkit/store.py` and `src/crapkit/cli/reports.py`, plus the unique regression file. It excludes every prior staged change in review-root. `evidence/final-review/prune-retention-hashes.json` records before and after hashes. The patch and hashes preserve the captured before/after boundary; the original working copies remain in the local evidence workspace.


## Portable replay and paired outcomes

The saved red logs are historical results. `evidence/final-review/prune-regressions-red.txt` pairs with the initial green slice, `prune-current-tests.txt` and `prune-e2e.txt` in the same directory. The 457-pass run in `prune-focused.txt` also contains the stale fixture failure described above; the later 19-check current-fixture run resolves it without weakening the assertions.

From the repository root, set `PYTHONPATH` to `src`. The copied `evidence/final-review/prune-key-history-probe.py` locates this repository and writes only temporary fixture data. Current reader admission can reject its deliberately old marks before the historical prune path; the maintained `tests/unit/test_prune_identity_witnesses.py` tests assert retention independently of that earlier admission check.
