# Same-line identity repair

This is the accepted design record for the integrated occurrence repair. The implementation persists `occurrence` from parser creation order while retaining raw signatures and physical line numbers. A separate local reader repair now recovers expression-array callbacks under analysis version 10. [fresh-findings.json](fresh-findings.json) records final dispositions; [validation.json](validation.json) records current validation. The failures and source lines below describe the pre-repair snapshot.

## Failures observed before repair

| Fixture | Raw lizard result | Downstream result before repair |
| --- | --- | --- |
| Chained map/filter callbacks on line 1 | Two FunctionInfos, CCN 2 and 1 | One key and one handle position |
| Two block-bodied array callbacks on line 1 | Two FunctionInfos with identical record values | One key and one handle position |
| Nested block callbacks on line 1 | Creation order outer, inner; result list inner, outer | Enumerating the result list would reverse source order |
| Two expression-bodied array callbacks | One FunctionInfo, even with callbacks on separate lines | Analyzer receives only one function |

Raw parser evidence: installed `lizard.py:308` stores no column or offset. `try_new_function` creates the object at line 490. `end_of_function` appends it at line 521. Installed `lizard_languages/typescript.py:461` keeps an existing started function at the next arrow; the array fixture reaches `;` before closing its sole object. Adding identity metadata cannot recover a missing object.

Project evidence recorded before implementation:

| Source | Rule to repair |
| --- | --- |
| `src/crapkit/analyze.py:174`, `:246` | Record construction carries no discriminator; it does not deduplicate |
| `src/crapkit/keys.py:53`, `:76` | Identity map deduplicates start lines |
| `src/crapkit/packet.py:198`, `:203`, `:222` | Handles are keyed by start alone |
| `src/crapkit/store.py:39`, `:154` | Rows can coexist, but sorting has no occurrence |
| `src/crapkit/store.py:708`, `:728`, `:856`, `:1103`, `:1113` | Marks, twins, spans and history lose the distinction |
| `src/crapkit/cli/queue.py:444`, `:465`, `:505` | Numeric and ordinal selectors choose through start-only maps |
| `src/crapkit/score.py:76` | Portable row reader accepts exactly 16 fields |

The original independent state probe confirmed that storage kept both callback rows while `read_marks` returned one entry, `twin_key_names` returned an empty map, and `function_span('(anonymous)#2')` returned None. Current regressions require both callbacks to remain addressable.

## Chosen representation

Append `occurrence: int = 0` to FunctionRecord, InventoryRow, ScoredRow and the narrow CrapRow. New analysis emits positive values in source creation order among functions with the same start line, across raw names. Zero represents an old row whose within-line position was not recorded. An old unique-start row remains usable.

A small extension wraps `reader.context.try_new_function` on that reader instance, records a creation sequence on the new FunctionInfo, and restores the original method when its generator finishes. Rank only surviving functions by that sequence, then number each start-line group. Do not wrap the process-global lizard class. Do not search source text for token strings or invent columns. The prototype passes the existing token stream through unchanged with `yield from`.

Centralize the four-part lookup `(path, long_name, start, occurrence)` in keys.py. Keep final canonical keys `(path, raw_name)` and `(path, raw_name#N)`. Compute N from ordered `(start, occurrence)` positions. Scope copies share one position. Use the full lookup for packet handles too, so old same-line functions with different raw names remain distinct. Numeric selectors refuse multiple distinct candidates on one line and print the actual handles. Existing bare-name burn-down selection can still choose the highest-debt named twin; explicit `#N` selects one occurrence.

Rescore's named coverage overlay matches the saved occurrence before its nearest-start fallback and refuses ambiguous legacy groups. Full artifact coverage remains a separate limit: line spans cannot identify two distinct callbacks with identical spans. The integrated coverage reader refuses that attribution. Occurrence does not supply missing coverage columns or prove a correspondence between independent parser and Istanbul ordering.

Before: parser functions -> start-only maps -> one callback survives each identity lookup.

After: parser creation order -> record occurrence -> occurrence-aware store/maps -> one key and handle per parsed callback.

## Compatibility and debt preservation

1. Add `functions.occurrence INTEGER NOT NULL DEFAULT 0`; include it in schema detection, table rewrites, writes, reads, SQL grouping and ordering. Keep the normalized identities table unchanged. Keep all old function rows.
2. Detect ambiguous legacy records within `(run, scope, path, raw_name, start)`. Two scope copies alone are not ambiguous. Reads that list history can show all rows; precise selection and ratchet identity operations refuse affected ambiguous groups. Never assign order from rowid, CCN or current SQL order.
3. Always append the occurrence field to new inventory/scored TSV and row JSON. Accept the exact old and new scored headers. Accept old 16-field scored records as occurrence 0 and new 17-field records with a nonnegative integer occurrence. Reject other shapes. Preserve the old field prefix. Round trips through portable baselines must keep the field. Parent approved additive output fields.
4. Bump the cache format fingerprint to invalidate old record tuples. The identity-only prototype retained the metric version because its counts and metrics did not change. The separate extraction repair is now integrated and raises `ANALYSIS_VERSION` to 10 because it recovers functions the stock reader omitted.
5. Give ratchet key identity its own version marker. Missing marker means legacy start-only keys. Preserve every unrelated mark and value. If either available legacy rows or fresh rows expose a same-start collision, treat the entire affected `(path, raw_name)` group as unresolved. A collision moves later ordinals too: an old `#2` on line 10 can become new `#3` after two callbacks on line 1.
6. Gate legacy comparisons and all mark writers through the same migration check. Safe groups keep their exact keys and values. Report unresolved groups and leave their marks intact; do not seed them from fresh scores. Write the new key marker only after every affected group has a proved mapping or explicit targeted reconciliation. Moves and merges preserve the marker and cannot silently upgrade an old file.
7. A stored commit SHA is not proof of source bytes because a run can include dirty inputs. Automatic remapping needs the actual old source or equivalent verified source digest. Without that proof, retain the ambiguous group and require targeted reconciliation. No blanket reseed or mark increase.

The migration checks are integrated across mark readers and writers. Later checks also require reader proof for anonymous JavaScript/TypeScript mappings, preserve conservative ownership for claims from older snapshots, and retain collision evidence across run pruning. Their final dispositions are recorded in [fresh-findings.json](fresh-findings.json); these additions are covered by maintained regression tests below.

## Rejected alternatives

| Design | Reason rejected |
| --- | --- |
| Enumerate final lizard function_list | Nested callbacks arrive in completion order |
| Sort by metric values or end line | Equal-metric callbacks collide; edits would change identity |
| Fake fractional lines or append text to raw signatures | Changes source locations or the reader's signature contract |
| Use only `(start, end)` | All demonstrated callbacks have the same span |
| Reconstruct legacy positions from SQL row order | Historical ordering was not recorded |
| Omit occurrence from portable exports | Export/import would recreate the collision |
| Add a full source-position parser | The creation counter solves parsed-function identity without another parse |

## Implemented ownership

| Front | Production files | Validation |
| --- | --- | --- |
| Analysis | `analyze.py`, `merge.py`, `snapshot.py`, `score.py` | Actual parser fixtures, old/new rows and cache shapes, same-reader rename cache, spawned workers, score/export round trips |
| State | `keys.py`, `packet.py`, `store.py`, `worklist.py` | Equal-metric callbacks, cross-scope copies, new/legacy SQLite, handles and history, marks, claims and overlays |
| Root integration | `ratchet.py`, `verify.py`, `cli/queue.py`, `cli/verifying.py`, `cli/ratchet_cmds.py`; output/docs contract | Targeted legacy migration refusal and preserved marks, CLI numeric/ordinal selectors, brief/next/claims, portable baseline and hook gates |

`digest.py`, `hook.py`, `cli/scoring.py`, and `cli/claude_hook.py` consume the shared key rules. Integration tests cover their direct identity assumptions. The independent review accepted the representation, scope-copy rule, additive exports, separate key version and migration refusal.

## Costs and checks

The representation adds one integer per persisted function row and one tuple slot per in-memory row. The creation counter runs once per parser function creation. Sorting covers surviving functions, while source tokens still pass through once. This design note makes no throughput claim. Later measured results and their limits are recorded in [final-analysis-review.md](final-analysis-review.md).

The historical prototype passed five TypeScript fixtures and seven language-reader checks: C++, Python, Rust, Swift, Kotlin, shell and PowerShell. It preserved raw records, metrics, signatures and real lines. The nested fixture retained outer-before-inner identity although the raw list returned inner first. Those counts describe the prototype only. The final reader has separate regressions and an [upstream fixture review](arrow-reader-review.md).

## Current replay

The original `same-line-identity-probe.py` and `same-line-identity-design-probe.py` commands were scratch diagnostics from the pre-repair checkout. Their outputs remain in the retained authoring archive; they are not required to run the integrated regressions. From the repository root with development dependencies installed:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -B -m pytest tests/unit/test_analysis_occurrence.py tests/unit/test_identity_positions.py tests/unit/test_occurrence_transport.py tests/unit/test_overlay_occurrence.py tests/unit/test_expression_identity_migration.py tests/unit/test_lizardtypescript_expressions.py
python -B -m pytest tests/e2e/test_same_line_identity_e2e.py tests/e2e/test_coverage_same_span_e2e.py
```

These tests cover actual parser order, old and new row shapes, storage, selectors, portable exports, legacy refusal and the extraction repair. Use [validation.json](validation.json) for the final run results.
