# Whole-project architecture implementation

Baseline: release commit 20f00e1371334f84aa70bba6f7b23bfc4bdae0f6. The unchanged review and candidate data live beside this file. completion.tsv tracks all 13 ranked opportunities and eight further checks or validation repairs.

The user authorized all opportunities, routine design choices, full verification and a fresh whole-project review before shipping. Publishing a release is outside this task.

## Execution order

1. Work in isolated store, analysis and execution worktrees. Root owns configuration, delivery and integration. Source ownership is disjoint; the integration owner must test every cross-owner call.
2. Reproduce each defect through an existing module or CLI interface before changing its implementation. Keep independent parser oracles and deterministic output contracts.
3. Integrate dependency-compatible changes in small commits after reviewing the actual diff and staged gate. Preserve the release baseline and accepted ADRs.
4. Run focused regressions, complete unit and e2e suites, coverage and verify. Exercise changed process, concurrency, migration and CLI paths with disposable repositories.
5. Independently inspect the integrated changes, then run improve-codebase-architecture again against the entire updated project. Save a new visual report and compare each original ledger item.

## Design constraints

Use the existing trust, identity, streaming and bounded-process modules where they already own the rule. Compare credible alternatives for migration, ownership, mutation input snapshots and reader projections before choosing one. Prefer removal of repeated decisions over extra abstraction. Every edited production function must pass the complexity ceiling of 6.

Do not treat synthetic allocations as production performance. Validate Windows behavior locally; run other supported environments where available and record any unavailable checks. Complete the current implementation and reassessment before any release decision.
