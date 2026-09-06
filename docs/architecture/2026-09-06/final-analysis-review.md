# Final reassessment of analysis and execution

Root reviewed: `<repo>`. This was a read-only source review. Replays wrote only temporary fixtures and evidence. `evidence/evidence-analysis/final-reassessment-source.tsv` records the reviewed file hashes before the two final admission fixes were integrated. Every Python replay asserted the package came from the intended root or isolated worktree.

The integrated repairs passed 270 focused checks and six real mutation snapshot/lock checks. This review found two further admission defects, reproduced both, and fixed them in the isolated review-root worktree. `evidence/evidence-analysis/final-admission.patch` carries those fixes; its 307 focused checks pass. The state reviewer confirmed the separate version-upgrade ratchet defect. Its repair now refuses unproved anonymous marks and retains group-wide ownership for claims from old snapshots; no mark is remapped.

## Findings and disposition

| Finding | Reproduction and source | Disposition |
|---|---|---|
| JSX cache skips a TSX refusal | `evidence/evidence-analysis/final-reader-cache-probe.py` and `evidence/evidence-analysis/final-reader-cache-probe.txt`: same bytes at `case.jsx` return two rows. Cold `case.tsx` refuses the unparenthesized `<` before a comma. Warm TSX using the JSX cache accepts two rows with one hit. Root `analyze.py:397` identified reader class and extension chain; both suffixes use TSXReader. `lizardtypescript.py:34` selected typed mode from the filename. | Fixed in `evidence/evidence-analysis/final-admission.patch`. One `uses_type_syntax` predicate now owns the reader mode and cache-key field. Different modes miss; same-mode renames still hit. The analysis version remains 10 because this repairs cache identity rather than measurement rules. |
| Nonfinite JSON reaches scores | `evidence/evidence-analysis/final-root-probe.py` and `evidence/evidence-analysis/final-root-probe.txt`: coverage.py `covered_branches: NaN` yields cov=NaN and CRAP=NaN; `CRAP > 6` is false. Root `covstream.py:40` used the default JSONDecoder; `coverage_py.py:22` passed the summary values into FnCoverage. | Fixed in `evidence/evidence-analysis/final-admission.patch`. A decoder callback refuses NaN, Infinity, -Infinity, and finite-syntax exponent overflow such as 1e999. Coverage, missing-line and selected-context readers all enforce it. Ordinary finite exponents, string content and artifact digests stay unchanged. |
| Version 10 recovery can move legacy ordinal marks | `evidence/evidence-analysis/final-root-probe.py` and `evidence/evidence-analysis/final-root-probe.txt`: stock parser emits anonymous starts 2 and 6; version 10 emits 2, 3 and 6. No same-start collision exists. The state review's `evidence/evidence-execution/multiline-migration-probe.py` and `evidence/evidence-execution/multiline-migration-probe.txt` proves verify first refuses version 9, but a later seed preserves old #2 under the new line-3 callback. | Repaired through reader-version admission before seed, prune, override or mark comparison. Old or unproved JavaScript-family snapshots cannot supply a current anonymous ordinal; their claims hold the raw-name group. See `evidence/evidence-execution/reader-migration-handoff.md` and its paired red/green logs. |

The admission patch contains exactly `src/crapkit/analyze.py`, `src/crapkit/covstream.py`, `src/crapkit/lizardtypescript.py`, `tests/unit/test_analysis_typed_cache.py` and `tests/unit/test_coverage_finite_json.py`. It was built against the integrated source captured by `evidence/evidence-analysis/final-reassessment-source.tsv`, not against git HEAD. The patch is integrated into the repository. Red: 37 failed, one passed. Green: 307 passed. Changed production CCN is 1 or 2; reverse-apply and staged whitespace checks pass.

## Resolved behavior retained

| Area | Integrated result and evidence |
|---|---|
| Reader identity and cache shape | Reader class, extension chain, typed mode and bytes define reusable records. Same-reader same-mode renames retain hits. Broken record lengths/types and malformed cache/stat-index shapes read as cold. `tests/unit/test_analysis_cache_identity.py`, `tests/unit/test_analysis_typed_cache.py`, `tests/unit/test_analysis_occurrence.py`. |
| Function identity | Records keep raw signatures and real line spans. Positive occurrence follows creation order at each start. Scope copies share identity; separate callbacks do not. Cache, inventory, scored rows and portable data carry the field. Legacy rows read occurrence 0. `tests/unit/test_analysis_occurrence.py`, `tests/unit/test_occurrence_transport.py`, `tests/unit/test_overlay_occurrence.py`. |
| Arrow extraction | Version 10 recovers comma-separated expression arrows in TS/TSX/JSX and the JavaScript reader's existing path. The extension changes only a fresh reader instance and preserves the token stream. `tests/unit/test_lizardtypescript_expressions.py`: sibling arrays, nesting, ternaries, typed parameters, multiline bodies, computed properties, block bodies and other language readers. |
| Coverage attribution | Distinct source identities with identical path/start/end and a matching artifact now refuse with an exact path and split-definitions remedy. Equal measurements and a single borrowed candidate also refuse. Scope copies, cc-only/no-lane rows and absent or nonmatching artifacts remain accepted. `tests/unit/test_coverage_same_span.py`; the real CLI regression is `tests/e2e/test_coverage_same_span_e2e.py`. |
| Coverage decoding | One streaming walker owns framing. Function coverage, missing lines and digest share the Python report pass. Selected contexts retain only the requested path and still validate the complete document. UTF-8 splits, separators, JSON whitespace and byte digests have independent oracle checks. |
| Missing lines | Each scored run owns its DeadLineFold. Lane workers share that collector, release each lane map after folding and keep an intersection. Ordinary readers cannot consume another run's state. `tests/unit/test_uncovered_run_ownership.py`. |
| Duplication and projections | Twins qualify before their payloads are built. Full function identity distinguishes nested functions that start together. Packet file rows and rescore rows include occurrence. `tests/unit/test_identity_presentations.py` and the existing duplication suite. |
| Mutation snapshot | One worker and several workers both use private trees seeded from one captured Git-root snapshot, including changed tests/config/dependencies, untracked files and deletions. Nested configured roots execute under the matching nested worker directory. Six real snapshot/lock checks passed in this reassessment. |
| Mutation eligibility | The lexer excludes comments, strings and longer operators. Clear type-angle syntax is protected; unresolved paired angles refuse affected lines. Type-only C++, Rust, Java and TS fixtures no longer create parser-error kills. |
| Template and retry evidence | Windows templates preserve literal metacharacters, quotes, empty strings, literal bangs and configured operators. Empty lists contribute no argument. Retry credit requires a fresh complete report, a passing requested case and a successful command; unrelated/skipped/missing/partial/stale evidence does not clear failures. |
| Configuration | Shared pytest configuration discovery preserves file precedence and root-relative scope normalization. Duplicate lane names/artifact destinations and invalid scalar values fail at admission. A declared root path `.` claims source and yields ownership to deeper scopes. |

## Measured benefits and costs

These are synthetic measurements captured during this implementation, not production workload forecasts. Each before/after comparison used a cold invocation and at least five warm samples, with traced Python allocation and output equality checks. Wall times ran beside other test work and vary with that load.

| Work | Fixture | Warm before -> after | Retained claim |
|---|---|---|---|
| Selected contexts | 5,000-file, 3,158,936-byte coverage.py report; one requested source | 25.0934 -> 6.0160 MiB; 0.644947 -> 0.203445 s | Fewer retained context objects; same selected contexts. |
| Qualifying twins before payloads | 25,000 indexed functions, zero qualifying twins | 7.2681 -> 0.0033 MiB; 0.225543 -> 0.093339 s | No per-candidate payload allocation when nothing qualifies. |
| Per-run missing-line ownership | 13 lanes, 1,500 files each, 80 missing lines/file | 24.3388 -> 24.3392 MiB; 0.372533 -> 0.375868 s | Shared-state removal preserves the existing fold's memory behavior. No speedup claimed. |
| Occurrence capture | 1,000 TS lines, 2,000 callbacks | 2.352492 -> 2.405350 MiB; 0.508146 -> 0.522093 s | About 0.053 MiB extra traced peak; every preexisting record field equal. |
| Same-span coverage guard | 100,000 distinct-span functions, 1,000 paths | 35.778038 -> 35.778038 MiB; 0.932981 -> 1.119132 s | One linear grouping pass. Peak unchanged in this fixture; extra work is measured and intentional. |

Sources: `analysis-measurements.json`, `evidence/evidence-analysis/identity-measurements-interleaved.txt`, `evidence/evidence-analysis/identity-followup-check.txt`. Existing exact-start coverage indexing, heap-based statement ownership, streamed exports, cache draining, unchanged-cache write avoidance and memory-bounded analysis workers remain in place. The final admission patch adds no extra source parse or artifact walk.

## Exact limits

- Line-only coverage cannot prove independent coverage for functions with identical path/start/end. Put those definitions on separate lines and regenerate coverage. The guard intentionally refuses even when reported values happen to match.
- TypeScript expression-arrow bodies with an unmatched `<` before a separating comma refuse because the reader cannot distinguish a comparison from type arguments at that point. Parentheses or a block around that body provide the needed delimiter. Generic arrow parameters remain supported.
- Occurrence is a source position within one start line, not a persistent semantic ID. Reader-version admission refuses affected anonymous marks with missing, malformed or pre-10 proof, including groups with no same-start collision or only one currently visible callback. Reconcile saved marks with their original functions before adding current proof. A fresh coverage run alone cannot prove that mapping. Scope duplication alone is not ambiguity.
- Mutation eligibility uses lexical and local delimiter evidence. It protects recognized type syntax and refuses unresolved paired angles; it does not promise a full compiler-level classification for every language construct.
- Windows templates reject multiline placeholder arguments combined with static percent environment expansion. Single-line arguments preserve the existing percent expansion; multiline mode protects static bangs. This refusal avoids silently changing command text.
- Streaming bounds retained decoded input by chunk plus largest file member. A single very large member can still consume memory. Selected contexts still read and validate the whole artifact.
- Missing-line fold reuse checks artifact path, size and mtime within the owning run. It does not retain maps across runs. Cache/stat reuse similarly trusts filesystem metadata under its existing same-tick rules; this review did not add a cross-process filesystem snapshot guarantee.
- These final replays ran on Windows. They did not rerun hosted CI, production coverage lanes, POSIX compilers, or a POSIX process-tree probe. Root owns the full integrated suites and verification.

## Coverage inventory and replay

Reviewed primary modules: `analyze`, `merge`, `snapshot`, `score`, `cache`, `lizardtypescript`, `coverage_istanbul`, `coverage_py`, `covstream`, `uncovered`, `dup`, `mutate`, `mutate_pool`, `procs`, `lanes`, `junitparse`, `config`, `universe`. Caller/dependency checks included `keys`, `gitio`, CLI scoring/queue paths and existing test-support oracles. This pass concentrated on the integrated changes and prior failure paths rather than rereading every unchanged function.

Root results are in `evidence/evidence-analysis/final-root-focused.txt` (270 passed) and `evidence/evidence-analysis/final-root-mutation.txt` (six passed, 13 deselected). Candidate-fix results are in `evidence/evidence-analysis/final-admission-red.txt`, `evidence/evidence-analysis/final-admission-green.txt` and `evidence/evidence-analysis/final-admission-complexity.txt`.

```powershell
# From the repository root. The probes assert this imported package path.
$env:PYTHONPATH=(Resolve-Path src).Path
python -B docs/architecture/2026-09-06/evidence/evidence-analysis/final-root-probe.py
python -B docs/architecture/2026-09-06/evidence/evidence-analysis/final-reader-cache-probe.py

# The maintained regression tests assert the repaired behavior.
python -B -m pytest tests/unit/test_analysis_typed_cache.py tests/unit/test_coverage_finite_json.py -o 'addopts=--tb=short -p no:cacheprovider' -q -p no:randomly
```

The saved probe outputs capture the historical defects. Public copies normalize local paths; the evidence index retains raw and public-copy hashes. Running either probe against repaired code stops at its expected refusal; this nonzero probe exit is not a regression. The maintained tests assert the refusal and preserved valid behavior. `evidence/evidence-analysis/final-admission-red.txt` (37 failures, one pass) pairs with `evidence/evidence-analysis/final-admission-green.txt` (307 passed).
