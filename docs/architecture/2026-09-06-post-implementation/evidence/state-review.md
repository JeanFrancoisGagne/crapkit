# Fresh persistence, domain and reporting review

Reviewed clean commit `499d9db4f9ff4d212fb94ea3975c7477b6b1c968`. Six new candidates survived caller tracing and disposable probes. Earlier repaired findings supplied context only; none is counted again here. Source, tests and configuration remain unchanged.

| Priority | Candidate | Result | Scope |
| --- | --- | --- | --- |
| 1 | ST1: Override publication and retention | A granted override loses its public audit record during prune | Store, override, hook and reports |
| 2 | ST4: Ratchet file mutation ownership | Two tightening writers change a mark from 50 to 10 to 20 | Verify and other TSV writers |
| 3 | ST5: One report snapshot | One trend row reports zero functions while its scope reports one | Store and report collection |
| 4 | ST2: One history replay | A 90-day mark becomes age `null` in its packet after a tightening | Packet and ratchet report |
| 5 | ST3: Optional marks evidence | An absent TSV still causes a full 100,000-row read | Worklist and HTML report |
| 6 | ST6: GitHub property encoding | Commas and percent sequences change the filename interpreted by the runner | Findings output adapter |

The full candidate record is in [state-candidates.json](state-candidates.json). It includes source lines, caller flow, proposed change, before/after schematic, deletion test, leverage, locality, validation needed, cost, risk and ADR disposition. No interfaces are designed in this review.

## Evidence and replay

[state-probes.py](state-probes.py) creates disposable SQLite stores and files under the operating system's temporary directory. It imports the checkout named by `PYTHONPATH`, asserts the import location, uses harmless local alert subprocesses, and removes its fixtures. It does not run a full suite, verify, installation, commit, staging operation or network write.

From the projectless workspace used for this review:

```powershell
$env:PYTHONPATH='C:\Users\jfgag\crapkit\src'
python -B work/architecture-rerun/state-probes.py
```

For another clone, set `PYTHONPATH` to that clone's absolute `src` directory and run the saved probe by its path. [state-probes.json](state-probes.json) records the latest result. Timings below are instrumented fixture measurements, not end-to-end command timings.

| Candidate | What the probe actually runs | Observed result | Limit |
| --- | --- | --- | --- |
| ST1 | Actual `record_override` in a subprocess, actual internal `_runs_prune` handler while the alert waits, then public `overrides --json` | Hook run 3 deleted; grant 90 persists; direct audit lookup returns one row; public audit returns zero rows | Does not run the full hook or Git staging |
| ST4 | Two subprocesses load the same prior mark, call real `update_ratchet`, then the actual verify writer in controlled order | Disk 50 -> 10 -> 20; both receipts report one tightening | Does not run two full verifies; controls the publication ordering |
| ST5 | Actual `_trend_payload`; a transparent read wrapper inserts a real run through a second SQLite connection between its reads | Run 2: whole functions 0/load 0; scope functions 1/load 6 | Deterministic internal-seam interleaving, not a timing-based CLI race |
| ST2 | Shared `mark_events` output fed to both existing pure consumers | Report age 90; packet age `null` | Does not create Git commits or run full packet commands |
| ST3 | Actual `_worklist_ratchet` on 100,000 synthetic ccn-1 rows with an absent marks file, SQLite tracing and tracemalloc | One unrestricted joined scan; 22,388,342 peak traced bytes; latest saved timing 0.660 seconds | No implementation or after-time measurement; instrumentation adds overhead |
| ST6 | Actual findings builder and annotation renderer on comma, literal-percent and newline paths | Raw filename enters workflow property syntax; newline makes two output lines | Strings captured locally; no GitHub runner or downstream exploit test |

ST6's interpretation follows the official [Actions toolkit property encoder](https://raw.githubusercontent.com/actions/toolkit/main/packages/core/src/command.ts) and [runner command parser](https://raw.githubusercontent.com/actions/runner/main/src/Runner.Common/ActionCommand.cs). The toolkit encodes percent, carriage return, newline, colon and comma in properties. The runner splits the property list before decoding values. The probe demonstrates the missing local encoding; the downstream parsing conclusion follows from those primary sources.

## Why these are new opportunities

| Candidate | Existing repair retained | New seam checked |
| --- | --- | --- |
| ST1 | Completed overrides and runs added after retention selection remain protected | A run already observed by prune can still be waiting for its first audit |
| ST4 | Pure ratchet updates only lower marks against their supplied prior values | Separate writers share one mutable file, so each prior can be stale |
| ST5 | Store rollups avoid repeated function scans and tolerate cache-write contention | Three individually valid reads do not necessarily describe one run set |
| ST2 | Ratchet history now preserves age and value on committed updates | Packet still interprets the event stream with its own older state machine |
| ST3 | Worklist admission pushes complexity and scopes into SQL | Optional ratchet loading performs a second unrestricted inventory read |
| ST6 | Messages escape workflow control characters | Workflow properties use a different grammar and remain unescaped |

## Coverage map

Checked means the current implementation and its caller/test seams were inspected. It does not mean every existing test was executed. The review ran only its disposable probes.

| Checked area | Current files and caller seams | Disposition |
| --- | --- | --- |
| [x] Snapshot storage and row identity | `store.py`: schema, normalized identities, compressed lane provenance, code tables, insert/read paths; `snapshot.py`, `merge.py`; scoring and queue callers | Retain normalized identities, streamed inserts and deterministic row ordering. No file-size split. ST1 and ST5 concern ownership of multi-step operations. |
| [x] Schema admission and migrations | `store.py`: `_current`, `_prepare`, additive columns, identity migration, restacking, mixed-version text lanes | Retain shape detection, transaction-wrapped table replacement, legacy identity defaults and current-store no-write opening. No new migration candidate established. |
| [x] Run trust and retention | `store.py`: baseline selection, trusted/rowful distinctions, keep set, override and collision witnesses, observed run set; `reports._runs_prune`, ratchet baseline callers | Retain failed/crashed verify blockers, explicit passing baselines, digest pair and minimal collision witnesses. ST1 adds incomplete override publication. |
| [x] Claims and release | `store.record_claim`, conflict check, source-reader proof, attempts batching, idempotent closure; `worklist.closable_claims`; queue and verify calls | Retain short `BEGIN IMMEDIATE`, whole-group legacy reservations and exact modern keys. Explicit prune's age-based claim expiry is documented policy, not a new defect. |
| [x] Ratchet text and identity admission | `ratchet.py`: finite mark admission, metric/key stamps, old expression-reader refusals, group proof; `_shared` marks readers | Retain strict writes, lenient advisory reads and refusal of unprovable legacy mapping. ST3 moves evidence acquisition behind admission; it must not weaken these rules. |
| [x] Ratchet operations and publication | Pure seed, update, damping, move, rename-following, prune and three-way merge; `cli/ratchet_cmds.py`, verify settlement, override grant | Preserve each operation's distinct policy. ST4 removes separate filesystem coordination. A universal minimum merge is unsuitable for grants and explicit deletions. |
| [x] Override audit | `override.py`; verify and hook grant callers; store audit insert/read; prune protection | Retain alert-before-audit-before-grant, stdin alert text and canonical audit identity. ST1 demonstrates that successful insertion alone does not ensure a visible durable audit. |
| [x] Verdict and portable baseline | `verify.py`: touched rows, gate/ratchet/failure checks, dirty attribution, diff-uncovered, baseline TSV; actual CLI inputs and settlement | Retain the pure verdict seam and shared exact keys. No new verdict-policy candidate established. Execution and artifact freshness are assigned to another reviewer. |
| [x] Worklist admission and ranking | `worklist.py`: ceiling admission, floor, hot promotion, risk ordering, scored marks, dormant view, batch grouping; queue and report calls | Retain shared admission, deterministic ties, indivisible files and coupled groups. ST3 is a query cost outside the ranking policy. |
| [x] Function selection and packet identity | `store.py`: function lookup, line refusal, exact history and span queries; `packet.py`: handles, anonymous positions, matching names; `_BriefLoader` | Retain file-scoped proof, canonical keys, batched reads and explicit ambiguous-line refusals. Ordinals remain positional identities by design. |
| [x] Packet fields and debt age | `packet.py`: source, file totals, gate, commands, lane, params, budget, regrowth, coupling, version fields; queue packet collector | Retain pure shaping and one batch loader. ST2 deletes duplicate history interpretation; unrelated packet fields need no split. |
| [x] Ratchet analytics | `ratchet_report.py`: patch admission, events, replay, working marks, age, repayment and policy; `ratchet report` and packet age | Retain finite TSV admission, history clock and working-tree attribution. ST2 covers the cross-consumer inconsistency. |
| [x] Digest and trend | `digest.py`: comparable lane sets, skipped runs, exact-key changes, totals; store rollup and `cli/reports.py` | Retain comparable-pair semantics, exact keys, shared rounding and narrow CRAP reads. ST5 concerns snapshot consistency. Nested pair selection has no measured production cost in this review and is not promoted. |
| [x] HTML report | `report.py`, report collector, recorded-payload and identity tests | Retain pure rendering, HTML escaping, no network dependencies, row cap and explicit stale-data banner. ST5 applies to collection, ST3 to worklist input. No new rendering abstraction is warranted. |
| [x] SARIF and GitHub annotations | `sarif.py`, `sarifio.py`, shared emitter, scoring and verify callers | Retain stable rule IDs, pure builders and streamed JSON output. ST6 fixes GitHub property encoding. SARIF schema/consumer compliance was not revalidated. |
| [x] Error vocabulary | `errors.py` and shared CLI error handling used by these commands | Retain the small exit-code vocabulary and named failures. No new wrapper hierarchy. |
| [x] Test seams | Unit and CLI fixtures listed below; candidate-specific gaps matched against current caller paths | Keep pure tests plus combined caller regressions. No broad suite was run. |

## Test coverage inspected

The tests already contain useful contracts. The new defects sit between those contracts rather than inside their ordinary happy paths.

| Existing tests inspected | Retained contract | Missing combined scenario |
| --- | --- | --- |
| `test_override.py`, `test_override_exact_audit.py`, `test_override_identity_admission.py`, `test_store_prune.py`, `test_prune_identity_witnesses.py` | Ordered grant records, exact identity, retained audited runs and post-selection new runs | Prune during an alert before the first audit exists |
| `test_ratchet.py`, `test_ratchet_merge.py`, `test_ratchet_move.py`, `test_ratchet_tighten_damping.py`, `test_ratchet_delta.py`, verify settlement tests | Pure tightening, merge rules, explicit moves and receipts | Two file writers whose pure updates share an old prior |
| `test_store_rollup.py`, `test_report_payload_marks.py`, `test_report_command.py`, report e2e fixtures | Cached aggregation, current ceilings, same worklist shaping and file output | New run inserted between aggregates and metadata reads |
| `test_packet.py`, `test_ratchet_report.py`, `test_ratchet_committed_updates.py` | Packet add/drop/re-add ages; report update ages and history clock | Feeding updated events to both age consumers |
| `test_store.py`, `test_store_scopes.py`, `test_worklist.py`, report marks tests | SQL pushdown, ranking and exact printed marks | Optional marks loading with no TSV must not scan the full run |
| `test_sarif.py`, `test_sarif_writer.py`, shared emission tests | Stable rule/line output and streamed JSON equivalence | Workflow property separators and literal escape sequences |
| `test_store_read_open.py`, `test_store_stack.py`, `test_store_identity.py`, `test_store_trust_retention.py`, `test_claims.py`, identity regressions | Current-store opening, migration, trust and claims | No additional candidate established beyond ST1/ST5 |
| `test_snapshot.py`, `test_verify_gate_marks.py`, `test_verify_dirty.py`, `test_verify_named_baseline.py`, `test_report_html.py`, `test_report_identity.py` | Determinism, gate marks, attribution, explicit baselines, escaped HTML and replayable handles | No additional candidate established |

## Limits and no-change recommendations

- Keep the accepted ADRs. ADR 0001's tool-result error handling and ADR 0002's nearest-config discovery do not conflict with any candidate.
- Keep the pure verdict, ratchet operations, ranking, packet shaping and report rendering modules. Their size does not justify another interface.
- Keep exact ordinal semantics and conservative refusal of ambiguous legacy data. This review does not propose permanent symbol IDs or a new key format.
- Keep the append-only scored rows and existing rollup cache. Fix transaction and read ownership at the seams that require it.
- Keep explicit claim expiry during `runs prune`. It is a documented ownership reset, already covered by tests.
- No failure frequency, production latency claim, complete GitHub runner behavior, filesystem crash matrix or full concurrent CLI suite was measured here. Those are validation work for an accepted implementation, not proof supplied by these probes.
- Source, tests, configuration and Git refs were not changed. Final `git status --short` was empty and HEAD remained the reviewed commit.
