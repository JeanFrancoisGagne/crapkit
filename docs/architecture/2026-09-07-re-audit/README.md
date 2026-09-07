# Crapkit architecture re-audit

The review found **17 opportunities: 14 Strong and 3 Worth exploring**. Start with
Git diff format ownership: ordinary display preferences can make the staged gate
miss a changed function.

[Open the visual report](architecture-review.html). Each candidate includes source
locations, observed behavior, before/after diagrams, a deletion test, a verification
plan and compatibility costs. No proposed fix was applied in this audit.

| Record | Value |
|---|---|
| Reviewed source | `b11e2c165411b5ff9caa6b4b8486aafe477dfeb8` |
| Root tracked inputs preserved | 744 |
| Python modules assigned | 69 |
| Root run records preserved | 77 |
| Focused tests | 348 passed, 1 POSIX-only skip |
| Fresh self-analysis | 1,867 functions, 79 measured files |
| Isolated verification | Run 78, `ok=true`, zero dirty/committed findings |

The self-review ran inventory, worklist, duplication, coupling and verify in a
separate checkout. Verify reused the retained complete coverage artifacts; this
audit did not run another full suite. It reported 23 uncovered changed lines with
`diff_uncovered_max=null`. The new reproductions exercise cases those artifacts
and passing contract tests do not cover.

The review covers source modules, maintainer tools, configuration, CLI/MCP,
Action/plugin delivery, packaging, CI and current operating guidance. It does not
claim a line-by-line review of every test or historical document, a new hosted CI
run, or POSIX process execution. The source-path mismatch for a literal backslash
uses a pure seam on Windows; its filename requires POSIX for a filesystem replay.
The duplicate-allocation figures come from synthetic inputs.

## Files

| File | Purpose |
|---|---|
| `report-data.json` | Normalized candidates, 46 coverage groups and 28 dismissed ideas |
| `audit-facts.json` | Counts and evidence archive SHA-256 |
| `evidence.zip` | Exact probe scripts, raw results, focused test output and input hashes |
| `decisions.tsv` | Review decisions and probe corrections |
| `build_report.py`, `report-template.html`, `report.css` | Reproducible report source |

The ZIP includes `SHA256SUMS.json` for its entries. `root-before.json` and
`root-integrity.json` record the tracked-input and run-record comparison.
`execution/integration-review.json` independently checks the verdict, portable
record and packet evidence, including the failed run's later baseline admission.

## Reproduce

Run `python build_report.py` from this directory to regenerate the HTML. The
generator uses only Python's standard library. Open the result with network
access for Mermaid; the layout also has embedded CSS.

For a defect replay, extract `evidence.zip` into a new scratch directory and select
the script named by its candidate. The scripts preserve the executed local source
and interpreter paths. Point those constants at a checkout of the reviewed commit
and an environment containing its declared development dependencies before running
elsewhere. Keep each script's output root in scratch: several create Git fixtures,
launch bounded child processes, or write toy ledgers. Do not run the probes inside
the report's committed directory.

The project copy in `docs/architecture/2026-09-07-re-audit/` is the source. Desktop
output and OS temporary copies are generated from these files. No release, push,
installed-package update or root-ledger change belongs to this review.
