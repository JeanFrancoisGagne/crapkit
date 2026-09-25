# Analytical core architecture review

Four new candidates emerged: two demonstrated correctness defects and two measured performance opportunities. The strongest change is to make analysis records and their cache identity describe the same source bytes. None requires changing complexity conventions or function identity.

| Rank | Candidate | Strength | Evidence |
|---|---|---|---|
| 1 | AN1: Bind cache records to the bytes parsed | Strong | Restored source receives incorrect metrics, or zero functions, from a persisted cache hit |
| 2 | AN2: Preserve Git path spelling | Strong | A real tracked ` leading.py` becomes `leading.py` in dirty-state and history answers |
| 3 | AN3: Decode each large coverage member once | Worth exploring | A 16MiB member causes 142.6 million characters to be offered to repeated decodes |
| 4 | AN4: Parse distinct cold inputs once | Worth exploring | Twelve identical inputs invoke the parser twelve times and leave one cache entry |

The full candidate records, exact source locations, costs, deletion tests and proposed validation are in `analysis-candidates.json` beside this report. These are proposed changes, not implemented repairs.

## Baseline and scope

- Repository: `C:\Users\jfgag\crapkit`.
- Commit: `499d9db4f9ff4d212fb94ea3975c7477b6b1c968`.
- The checkout was clean before and after this review.
- This is an independent review of current code. Historical architecture reports were not used as a repair checklist.
- Read the architecture and codebase-design skills, the contributing instructions in `AGENTS.md`, `CONTEXT.md`, and both ADRs. Applied the module depth, interface, seam, adapter, leverage, locality and deletion checks.
- All writes were disposable evidence under the workspace or temporary fixture directories. No repository source, tests or configuration changed. No full suite, verify, install or publishing ran.

## AN1: Cache identity can outlive the wrong source

The public analysis operation hashes each file, then schedules a path for a later parser read. The cache updater trusts the first hash and the second read's records. A controlled write between those phases stores the wrong record under the original content identity.

| Replay | Current source | Later warm result |
|---|---|---|
| Edit between hash and parse, then restore | CCN 1, end line 2 | CCN 2, end line 4 |
| Delete between hash and parse, persist cache, then restore | One function | Zero functions, one cache hit |

Both restorations use normal file writes. Neither changes timestamps deliberately. The delete replay passes through the real parser's missing-file behavior and the real `save_cache`/`load_cache` pair.

| Caller flow | Source evidence |
|---|---|
| Inventory/coverage/verify build inventory through `_analyzed_corpus` | `cli/scoring.py:61-94` |
| Rescore uses the same analyzer | `cli/scoring.py:528` |
| Hash and parse read separately | `analyze.py:389-390`, `557-577`, `362-367` |
| Earlier identities own later records | `analyze.py:671-688`, `cache.py:31-40` |

The proposed change deepens the existing analysis module: own source bytes, their digest and their records together. It removes a cross-phase assumption, rather than adding another cache interface. Keep the fast path for settled files, reader-aware identity, deterministic ordering and path restamping. Invalidating existing cache fingerprints is necessary because valid-shaped wrong records cannot be detected on load.

The interleaving is demonstrated. Its frequency on normal repositories is not measured. Worker memory and IPC costs need comparison before implementation.

## AN2: Git records lose path identity

The real Git fixture contains ` leading.py` and `ordinary.py`. Git preserves the leading space. Three consumers remove it: the dirty-name reader and both analytical history parsers.

| Answer | Observed value |
|---|---|
| Tracked files | `[' leading.py', 'ordinary.py']` |
| Raw Git diff name | `' leading.py\n'` |
| `status_names` / `unstaged_paths` | `['leading.py']` |
| Churn key | `'leading.py'` |
| Coupling after tracked-file filter | `[]` |
| Mutation snapshot for the dirty dependency | `'leading.py': null`, actual dirty path absent |

| Owner | Source evidence |
|---|---|
| NUL-safe tracked-file spelling | `gitio.py:87-90` |
| Dirty paths trimmed as text | `gitio.py:152-155` |
| History consumers trim independently | `churn.py:85-104`, `coupling.py:22-46` |
| Wrong name becomes a captured deletion | `mutate_pool.py:130-155` |
| Scope-change joins use those names | `lanes.py:577-590` |

The proposed change puts record framing and path spelling at the Git boundary. Remove terminators without trimming path content. Use Git's NUL form for path lists and concentrate the shared history decoding rule. Keep churn weights and coupling counts in their separate modules. Existing derived caches need a fresh path contract marker after this correction.

The snapshot contents are demonstrated; a full mutation or verify verdict was not run. POSIX control-character filenames require separate platform fixtures.

## AN3: Bounded memory still permits repeated decoding

`covstream` owns one useful JSON window for both artifact formats. Its retry loop restarts decoding from the current member's beginning after every refill. This preserves memory bounds but repeats work inside a large member.

| Member size | Decode attempts | Characters offered to decoder |
|---|---:|---:|
| 1MiB | 1 | 1,048,543 |
| 4MiB | 4 | 10,485,648 |
| 16MiB | 16 | 142,606,116 |

The public coverage.py reader also reproduced the cost with a valid per-file contexts object. Its 16MiB fixture took a median 0.4825s across three runs; the same reader given one whole-file chunk took 0.0782s with identical coverage, missing lines and digest. The large-chunk run isolates repeated refill work; it is not the proposed fix because it gives up the intended window size.

Keep the public interface and make the window carry completion state until one full value can be decoded. The deletion test is removal of repeated prefix decoding. Strict separators, finite numbers, escaped strings, UTF-8 boundaries and end-of-document refusal must stay intact. Consumer artifact-size distributions were not measured, so this remains worth exploring rather than a claimed production speedup.

## AN4: Cold duplicates are discarded after doing the work

Equivalent reader/content identities share warm cache records. On a cold run, partitioning still sends every path to the parser, and only the final cache dictionary combines equal identities.

The replay used 12 identical Python files, each with 100 functions. It observed 12 real parser calls, 1200 output records and 1 cache entry. Cold elapsed time was 0.2122s; the equal-output warm read took 0.00274s. Those timings describe existing behavior, not the speed of an implemented optimization.

Group equivalent misses before parsing and reuse the existing path restamping. This removes parser jobs and serialization without adding a layer. Pair this with AN1 so grouping owns actual input bytes. Preserve separate identities for different readers, extension chains and typed syntax. Measure repeated-content prevalence before prioritizing this over other work.

## Complete source coverage map

All 24 assigned modules were read in full. The dispositions below distinguish candidates from structures that should stay.

| Module | Scope inspected | Disposition |
|---|---|---|
| `analyze.py` | Reader registration, extension placement, source decoding, occurrences, cache and stat stamps, workers, orchestration | AN1 and AN4; retain the single metric pass and process registration |
| `_pygdefer.py` | Temporary proxy modules, deferred lexer loading, module restoration | Retain; no demonstrated new import or ownership defect |
| `lizardcognitive.py` | Function state ownership, Python token timing, nesting, boolean runs, jumps, reader-specific rules | Retain metric conventions; no new repair proposed |
| `lizardpowershell.py` | Token additions, declaration states, switch arms, registration, documented limits | Retain; case-insensitivity and parameter limits are explicit existing scope |
| `lizardrust.py` | Match-arm attribution, wildcard handling, upstream replacement and retirement rule | Retain; upstream defect test provides a concrete deletion trigger |
| `lizardshell.py` | Heredoc stripping, tokenizer repairs, function states, registration and accepted limits | Retain; do not replace it with a general shell parser without need |
| `lizardtypescript.py` | Expression-arrow states, delimiter handling, typed ambiguity refusal and registration | Retain recovered functions and explicit refusal; no new finding |
| `covstream.py` | Window, framing, digest, strict JSON admission, both artifact formats and projections | AN3; retain one owner for the walk |
| `coverage_py.py` | Regions, branch/statement fallback, summaries, context cleanup and completeness | Retain format projection; no metric rewrite proposed |
| `coverage_istanbul.py` | Path projection, invocation fallback, span containment and branch/statement ownership | Retain indexed attribution and pure projection |
| `uncovered.py` | Run-local folds, cache keys, artifact readers, line intersections, staleness notes | Retain run-local ownership; AN3 benefits its file reads |
| `score.py` | Exact-start index, overlap fallback, ambiguous spans, stale overlay, score and row transport | Retain indexed join and conservative ambiguity handling |
| `universe.py` | Exclusions, test names, extension matching, longest-path ownership, size filters | Retain centralized scope ownership; no new split needed |
| `keys.py` | Full positions, canonical ordinals, ambiguous groups, legacy expression proof and claims | Retain separation of raw identity and presentation |
| `cache.py` | Partition, replacement and partial-run merge | AN1/AN4 at its caller; keep the pure functions |
| `churn.py` | Git path decoding, authors, counts, timestamps and recency weights | AN2; retain the weighting convention |
| `churn_log.py` | Compressed history, checksums, operation scratch paths, incremental refresh and cleanup | Retain publication ownership and ancestry checks |
| `churn_cache.py` | Identity, date/window invalidation, adoption, malformed reads and map-only operation | Retain disposable caching; AN2 needs derived-cache invalidation |
| `coupling.py` | Commit file sets, bulk-commit limits, support, confidence, live-path filtering and ranking | AN2; retain filtering before the top cut |
| `coupling_cache.py` | Tracked-set digest, thresholds, dates, malformed reads and persisted ordering | Retain owned ranking; AN2 needs derived-cache invalidation |
| `dup.py` | Normalization, shingles, index reuse, candidate counts, containment and identity | Retain existing index and deferred payload allocation; no measured new bottleneck |
| `diffparse.py` | Hunk body accounting, quoted new paths, deletion touch points and headers | Retain grammar-aware hunk handling; no additional defect established |
| `gitio.py` | Path lists, diffs, batch blobs, asynchronous reads, refs, worktrees, GitFacts | AN2; retain batched reads, root-relative output and safe worktree checks |
| `repotext.py` | Explicit encoding and repository-text errors | Retain the small adapter; it already hides a complete concern |

## Tests, formats and retained contracts

Selected test bodies were read for candidate seams, and related test names were checked across every module group. This review did not execute the test suite.

| Group | Existing tests used for contract review | New validation needed |
|---|---|---|
| Analysis/cache | `test_analysis_cache_identity.py`, `test_analysis_typed_cache.py`, `test_analyze_warm_path.py`, `test_analyze_one_pass.py`, `test_cache.py`, `test_cache_stream.py` | Edit/deletion interleaving and distinct cold-job count |
| Custom readers | `test_lizard*.py`, `test_cognitive*.py`, `test_pygments_deferral.py` | Preserve current records and spawned registration when changing analysis ownership |
| Coverage | `test_covstream.py`, `test_coverage_reader_contract.py`, `test_coverage_finite_json.py`, `test_coverage_same_span.py`, `coverage_oracles.py` | Large-member work scaling with unchanged output and refusal behavior |
| Score/uncovered | `test_score_join.py`, `test_score.py`, `test_uncovered_run_ownership.py`, `test_uncovered*.py`, `test_occurrence_transport.py` | No new metric/identity behavior requested |
| Universe/keys | `test_universe.py`, `test_universe_scan.py`, `test_twin_keys.py`, `test_shared_trust_and_twin_keys.py` | Preserve exact-file ownership and full-position identity |
| Git/history | `test_gitio_*.py`, `test_diffparse.py`, `test_churn*.py`, `test_coupling*.py` and corresponding e2e fixtures | Real whitespace-path transport and downstream joins |
| Duplication | `test_duplication.py`, `test_duplication_containment.py`, `test_duplicate_records.py` | No demonstrated new candidate |

Formats inspected include FunctionRecord occurrence transport, cached record validation/fingerprints/stat stamps, coverage.py regions and contexts, Istanbul function/branch maps, scored TSV16/17 transport, raw compressed Git history/key pairs, and derived churn/coupling dictionaries.

Keep these boundaries: a single parse computes metrics, coverage projects from a shared JSON walk, universe owns scope matching, keys own full positions, caches fail cold when malformed, and Git batches blob reads. Neither ADR conflicts with the proposals. The nearest-config behavior in ADR0002 must continue to work under nested roots.

## Replay and limits

Run each script from PowerShell with the explicit import root:

```powershell
$env:PYTHONPATH='C:\Users\jfgag\crapkit\src'
python 'C:\Users\jfgag\Documents\Codex\2026-09-06\c-users-jfgag-appdata-local-temp\work\architecture-rerun\analysis-probe.py'
python 'C:\Users\jfgag\Documents\Codex\2026-09-06\c-users-jfgag-appdata-local-temp\work\architecture-rerun\git-path-probe.py'
```

Both scripts assert that `crapkit.__file__` resolves under the reviewed checkout. Raw results are `analysis-probe.json` and `git-path-probe.json`. The Git fixture creates an ordinary temporary repository and makes one fixture commit without changing hook settings.

Performance samples are synthetic, local and short. The two scripts ran concurrently during the final measurement capture, so timings are descriptive rather than isolated benchmark claims; deterministic decoder-work and parser-call counts provide the stronger evidence. No estimate of repository-wide savings is claimed. No full verify, suite, compiler matrix or consumer corpus benchmark ran. Existing documented reader limits and large-output pair-count costs were not promoted into speculative candidates.

---
✅ **DONE**: All assigned analytical modules dispositioned; four new candidates recorded; checkout unchanged.
---
