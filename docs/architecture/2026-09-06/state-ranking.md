# State and ranking review

This historical review records the state and ranking defects found during integration on 2026-09-06. All five listed repairs are integrated, including churn publication. Disposable repositories and stores supplied the original probes. [fresh-findings.json](fresh-findings.json) records the final dispositions, including later migration and retention repairs; [validation.json](validation.json) records current whole-project validation. Source line numbers and focused counts below belong to the earlier review snapshot.

## Historical findings and final dispositions

| Rank | Finding | Evidence before repair | Disposition |
|---|---|---|---|
| P2 | Same-line callbacks share one stored lookup/key | `keys.py:53-78`, `packet.py:198-220`, `store.py:708-745`, `store.py:856-863` | Integrated occurrence identity, selectors, exports and guarded legacy migration. |
| P2 | Nonfinite ratchet marks suppress findings | `ratchet.py:103`, `verify.py:172-173`, `verify.py:205` | Integrated finite-value admission in the shared ratchet reader. |
| P2 | Concurrent churn writers stamp the wrong history | `churn_log.py:304-315` | Integrated private-scratch checksum and publication repair. |
| P2 | Committed tightening is reported as uncommitted and does not advance ages | `ratchet_report.py:40-51`, `ratchet_report.py:84-99`, `ratchet_report.py:132-136` | Integrated update events, preserved entry dates and committed history clock. |
| P2 | Override audit loses the selected twin identity | `override.py:39`, `override.py:75-80`, `store.py:888-899`, `cli/reports.py:279-287` | Integrated canonical-key audit identity. |

These are demonstrated defects. No new performance benchmark was run during this review. Performance figures in existing module comments remain prior measurements, not measurements made here.

## Same-line identity before repair

The real TypeScript fixture is:

```typescript
const a = values.map((x) => x > 0 ? x : 0).filter((x) => x > 1);
```

The pre-repair analyzer returned two records. Both carried path `app.ts`, raw name `(anonymous)`, start 1 and end 1. Their CCNs were 2 and 1. The store preserved both records, but the following lookups collapsed them:

| Read | Result before repair |
|---|---|
| `key_names(rows)` | One canonical key: `(anonymous)` |
| `packet.handles(rows)` | One handle: `(anonymous)#1` |
| `read_marks(run_id)` | One score and verdict entry |
| `twin_key_names(run_id)` | Empty map |
| `function_span(run_id, "app.ts", "(anonymous)#2")` | `None` |

The defect comes from treating start line as a unique location. `rows_by_key` then treats distinct callbacks as duplicate scope measurements. `next-item --claim`, `brief`, `explain`, ratchet lookup, and digest all inherit that collapse.

The array fixture `const f = [(x) => x + 1, (x) => x + 2];` loses its second callback inside lizard 1.24.0 before crapkit constructs records. `_file_records` does not deduplicate the analyzer output. That reader defect is separate from same-line identity and needs a reader regression, not fabricated records.

## Identity repair choice

| Choice | Benefit | Cost and failure mode |
|---|---|---|
| Persist an occurrence discriminator | Both callbacks remain addressable through storage, claims, gates and history. | Changes record tuples, storage reads/writes, selectors and portable exports. Legacy ambiguity needs explicit treatment. |
| Refuse ambiguous start-line selectors only | Small selector change. | Gates, marks, digest and scored lookup still merge callbacks. This does not repair the defect. |
| Reject files containing same-line functions | Avoids a false answer. | Stops normal TypeScript callback chains from being analyzed. It is too broad for new analysis. |

The integrated repair uses a persisted occurrence discriminator. Creation-order capture was verified on chain, block-body, nested, same-line and multiline fixtures, plus Python, C++, Rust, Swift, Kotlin, shell and PowerShell. Lizard carries no source column/offset to reuse.

Append `occurrence: int = 0` to `FunctionRecord`, `InventoryRow`, `ScoredRow` and `CrapRow`. New analysis assigns positive occurrence in creation order among functions opening on the same line. Nested completion order must not decide the number. Preserve the raw name and measured start/end lines.

Canonical lookup uses `(path, raw name, start, occurrence)`. Presentation handles use `(start, occurrence)` and still count all anonymous functions in file order. Duplicate scope copies of one occurrence take one key. Start-only selectors that match several occurrences must return a clear ambiguity with the available handles.

Before: source -> records -> start-line lookup -> one callback wins.
After: source -> records with occurrence -> stored occurrence -> separate keys, marks and selectors.

## Migration and contract costs

| Area | Required change |
|---|---|
| Analysis/cache | Carry occurrence through `_record`, cache validation and fingerprint. The identity-only design changed the cache format; the integrated expression-arrow extraction repair also bumps analysis version to 10. |
| Row records | Carry occurrence through inventory construction, scoring and narrow `CrapRow` reads. Keep ordinary raw names and all metric values unchanged. |
| SQLite | Add an occurrence column defaulting to 0. Include it in function writes, reads, order, marks, twin counts, span lookup and history window functions. Identity string table need not change. |
| Key consumers | Change keys, worklist `Marks`/`RatchetMarks`, packet handles and queue selectors together. Hook, digest, verify, rescore and claim completion must consume the same identity. |
| Exports | Root accepts an additive occurrence column/field. Always write the new shape; accept old and new exact TSV headers. Read old rows with occurrence 0. Portable baselines must retain occurrence. Avoid a different output shape depending on whether a file happens to contain a collision. |
| Ratchet compatibility | Add an identity-format marker separate from metric identity. Legacy marks in affected raw-name groups cannot be applied to new ordinals without reconciliation. Retain every unrelated mark and value. |
| Existing claims/history | Preserve old ambiguous ownership conservatively. Never infer lexical order from CCN, row order or source commit alone. |

A legacy collision shifts later ordinals in the same raw-name group. If line 1 held two collapsed callbacks and line 10 held one, old `#2` named line 10; new `#2` names the second callback at line 1. Every legacy mark in that affected raw-name group needs reconciliation, not just a mark at the colliding line.

Old stores can retain duplicate rows but cannot prove source creation order. Duplicate scopes alone remain valid: detect ambiguous legacy records within `(run, scope, path, raw name, start)` before collapsing scope copies. Existing run commits do not prove source bytes because runs may describe dirty files. Do not auto-remap debt from a matching commit alone, and do not require a blanket reseed of unaffected debt.

The coverage join remains a separate boundary. Rescore now prefers the saved occurrence and refuses ambiguous legacy overlays. Measured line-only coverage refuses distinct functions with identical source spans; occurrence does not invent missing coverage columns. Current cases live in `tests/unit/test_coverage_same_span.py`, `tests/unit/test_overlay_occurrence.py` and `tests/e2e/test_coverage_same_span_e2e.py`.

## Other failures observed before repair

**Nonfinite ratchet.** Loading a TSV mark `nan` succeeds. Evaluating an untouched CRAP 90 function against target 6 returns `ok=True` because a comparison against NaN cannot report a rise. A mark `inf` also forgives the same changed function. Reject nonfinite values at the one ratchet reader, retaining the existing salvage/read versus strict/write behavior.

**Override audit.** A real local override for `f( )#2` writes that precise mark to the ratchet, but its stored audit reports only `f( )`. The verify and hook paths already carry `key_name`; `record_override` discards it during the audit write. Store the granted canonical key in the audit. Historical rows cannot be reconstructed reliably.

**Ratchet history.** A clean disposable repository seeds mark 50 on January 1 and commits tightening to 20 on April 1. `ratchet report --json --enforce`, with maximum age 1 month, returns exit 0, `uncommitted=1`, `age_days=0`, a January anchor and no findings. The documented clock is the latest ratchet commit, so the expected committed count is 0 and age is 90 days. Keep value updates and their timestamp in the replay without counting them as new or repaid marks.

**Churn publication.** Two actual child processes read one disposable Git repository at 1-month and 12-month windows. A controlled pause after the first rename lets the second process replace the cache. The first process then reads the second process's bytes and stamps them with its own 1-month key and a matching checksum. The next 1-month read incorrectly contains January's `old.txt`.

The integrated fix reads and checksums the closed private scratch file before publishing it. Each operation owns a unique scratch file, including overlapping generators in one process. A mismatched published key/data pair reads as cold. No lock or new cache schema was added. Existing cleanup remains best effort through `_drop`.

## Coverage map

| Modules | Read scope | Checks and retained strengths |
|---|---|---|
| `store.py` | Whole module, schema through retention helpers | Actual temporary-store identity/marks/audit probe; normalized strings/codes, narrow reads, current-schema open, transactional migrations and trusted-run retention remain useful. |
| `keys.py`, `snapshot.py`, `merge.py` | Whole modules | Actual analyzer-to-inventory fixture; distinct raw signatures and duplicate scope semantics preserved in design. |
| `worklist.py` | Whole module | Admission floor, marked debt, marks joins, claim closure and batching traced. Shared admission/SQL floor avoids conflicting queue decisions. |
| `packet.py` | Whole module | Handles, source windows, parameter parsing, mark ages, commands and context traced. Explicit absent-data notes and one loader per batch remain useful. |
| `digest.py` | Whole module | Comparable-run choice, key fold, deltas and totals traced. Narrow `CrapRow` read and explicit skipped runs remain useful. |
| `score.py` | Whole module | Coverage join, overlay fallback, row construction, exported headers and parsers traced. Distinct no-lane/untested/cc-only flags remain useful. |
| `ratchet.py`, `ratchet_report.py`, `override.py` | Whole modules | Real reader/gate, local audited override and clean-Git CLI history probes. Tighten-only marks, audit-before-grant ordering and source data on stdin remain useful. |
| `churn.py`, `churn_cache.py`, `churn_log.py` | Whole modules | Raw/parser/derived cache paths traced; real competing-process cache probe. Keep compressed streaming, CRC checks, ancestor refresh and relative-path keys. |
| `coupling.py`, `coupling_cache.py` | Whole modules | Pair formation, tracked-set key, default-threshold cache and batch caller paths traced. Keep tracked-path invalidation and default-threshold admission. |
| `cli/queue.py` | Whole module | Next-item, claim reservation/release, brief/batch, worklist, per-file memo and coupling callers traced. |
| `cli/ratchet_cmds.py` | Whole module | Trust selection, merge, move, seed/prune and enforced report traced. |
| `cli/reports.py`, `cli/verifying.py`, `verify.py` | Relevant complete caller sections | Audit output, explain selectors, gate/mark checks, verify override, hook override and claim closure traced. Overall command review remains with root and store_queue. |
| `analyze.py`, `hook.py`, `cli/scoring.py`, `cli/claude_hook.py` | Identity construction and callers | Actual lizard fixture, record/cache creation and key consumers inspected. Analyzer-wide review belongs to analysis reviewer. |

## Validation and replay

The table identifies historical files under the retained authoring archive's `work/implementation/` directory. The [public evidence index](evidence/index.json) lists included copies; these old scratch paths are not current replay commands.

| Evidence | Result |
|---|---|
| `final-review/state-probe.py` and `.txt` | Real analyzer/store collapse, local override audit mismatch, NaN/Infinity false pass. |
| `final-review/churn-race-probe.py` and `.txt` | Real Git plus competing processes reproduces wrong cached window. |
| `final-review/ratchet-history-probe.py` and `.txt` | Actual CLI on clean Git proves committed-tightening report defect. |
| `evidence-analysis/same-line-identity-probe.py` and `same-line-identity-before.txt` | Raw lizard evidence and occurrence prototype, owned by analysis reviewer. |
| `evidence-execution/churn-red.txt` | Both new publication/ownership regressions fail before repair. |
| `evidence-execution/churn-green.txt` | 48 cache unit tests pass in 2.03 seconds after repair. |
| `evidence-execution/churn-hook.txt` | Hook exit 0; no bypass. |
| `evidence-execution/churn-publication.patch` | Only churn source and unique new regression file. |

All original Python probes set the reviewed source tree on `PYTHONPATH` and asserted the imported package path. The publication regression runs two child processes plus real Git. The repair is integrated; the final suite and configured coverage/verify results belong to [validation.json](validation.json).

Run the maintained regressions from the repository root with development dependencies installed:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -B -m pytest tests/unit/test_identity_positions.py tests/unit/test_ratchet_finite_admission.py tests/unit/test_override_exact_audit.py tests/unit/test_ratchet_committed_updates.py tests/unit/test_churn_log_publication.py tests/unit/test_prune_identity_witnesses.py
```

Existing tests inspected include store/identity/read-open/trust/retention, twin/anonymous/claim ownership, worklist/packet/digest, override/ratchet/merge/seed/prune/tighten/report, and churn/coupling caches. The new tests cover the missing real interleaving, private scratch lifetime and clean committed history shapes rather than copying helper implementations.

## Limits and non-findings

No new state/ranking performance cost was measured beyond focused test durations. A shallow clone can keep a churn map stale under unchanged HEAD until the next UTC date; the module already documents that limit. Derived JSON caches keep key/data in one document and treat unreadable files as cold; this review did not prove an equivalent false-key publication bug in them. Further indexes, repository layers or cache frameworks have no measured justification from this review.

The stale anonymous-key paragraph and the start-only identity assumption were corrected during integration. Legacy records still require enough evidence to identify a function; the final migration limits are documented in [final-state-review.md](final-state-review.md).
