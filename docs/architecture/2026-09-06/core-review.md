# Fresh analysis and core architecture review

This historical architecture review records why the implementation retained its existing module boundaries. Later integration repaired source-identity, reader-cache and numeric-admission defects. [fresh-findings.json](fresh-findings.json) records their final dispositions; [validation.json](validation.json) records the current whole-project test and verification status. Detailed caller checks and limits are in [final-analysis-review.md](final-analysis-review.md) and [final-state-review.md](final-state-review.md).

| Modules inspected | Decision and evidence |
|---|---|
| `analyze`, `cache`, `_pygdefer`, `merge` | Keep one analyzer and a disposable cache. Cache identity now includes the lizard reader, preprocessing chain and content hash. Reload validates stored shapes before constructing records. Same-reader moves restamp paths. `merge` now contains only the shared record type; moving that type would relocate imports without removing a decision. |
| `lizardcognitive`, `lizardpowershell`, `lizardrust`, `lizardshell`, `lizardtypescript` | Keep the language adapters. Their token, nesting and declaration rules differ by language and sit behind the existing analyzer. The new expression reader restores callback spans under analysis version 10. Recorded fixtures and cold/warm compatibility checks exercise its outputs and refusal cases. No additional generic adapter is justified. |
| `covstream`, `coverage_py`, `coverage_istanbul` | Keep the JSON walk in one module and file-level projections in the format modules. The walk now requires complete objects and document endings, including the contexts-only path. Independent whole-document oracles belong in tests. The file reader hashes the actual bytes consumed. |
| `uncovered`, lane/scoring call sites | Keep dead-line ownership per run. Lane workers add under one lock; the consumer takes the intersection once and re-reads artifacts whose recorded identity changed. This removes process-global lifecycle coordination. Synthetic folding time and peak allocation were unchanged, so the benefit claimed here is ownership, not measured speed. |
| `dup`, `snapshot`, `score` | Keep scoring and inventory records pure. Source occurrence distinguishes siblings across storage and projection. Twin candidates are filtered before result dictionaries are allocated. The existing inverted shingle index and exact-start coverage index avoid repeated whole-corpus scans. Measured coverage refuses distinct functions sharing one source span because the line projection cannot prove attribution. |
| `universe`, `diffparse`, `hook`, `verify` | Keep corpus membership, diff decoding and verdict rules shared by CLI adapters. The staged gate still analyzes index blobs. Its optional base changes the diff basis, while ordinary pre-commit behavior stays intact. Duplicate scope names now refuse at configuration admission, and a root scope claims loose repository files. |
| `errors`, `repotext`, `invocation`, `sarif`, `sarifio`, `ratchet_report` | Keep the existing error, repository text, command display and rendering modules. They own actual format or environment differences. No additional extraction passed the deletion test. |

## Measurements and limits

`measure-structure.py` compares the release baseline against the current source with AST parsing. Its line counts include comments and blanks. [structure-measurements.json](structure-measurements.json) contains the saved measurement inputs and outputs. This note does not repeat a moving final source total.

`analysis-measure.py` records synthetic first-invocation and warmed samples with `tracemalloc`. Selected contexts reduced median warm peak from 25.0934 MiB to 6.016 MiB. Twin search with no qualifying results reduced peak from 7.2681 MiB to 0.0033 MiB. Every measured output matched. These figures isolate the changed operations; they are not end-to-end production latency claims.

The first integrated unit run passed 3,106 tests before the later repairs. That is a historical checkpoint, not the final result. Use [validation.json](validation.json) for the final suites, configured coverage lane, hosted OS/Python jobs and platform-specific limits.

## Current regression replay

From the repository root with development dependencies installed:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -B -m pytest tests/unit/test_analysis_cache_identity.py tests/unit/test_analysis_typed_cache.py tests/unit/test_analysis_occurrence.py tests/unit/test_coverage_reader_contract.py tests/unit/test_coverage_finite_json.py tests/unit/test_uncovered_run_ownership.py tests/unit/test_lizardtypescript_expressions.py
```

The benchmark scripts and saved JSON remain the evidence for the isolated measurements above. The regression command checks current behavior; it does not rerun the performance measurements or publish anything.
