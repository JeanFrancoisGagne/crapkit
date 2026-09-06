# Reader identity migration

This integrated repair refuses unproved anonymous ordinals before a ratchet comparison or write. Claims taken from old snapshots hold their whole raw-name group. No stored mark, function span, or signed debt value is remapped.

## Reproduction and repair

| Case | Before | After |
| --- | --- | --- |
| Version 9 mark on anonymous #2, then coverage 10 and seed | Mark moved from line 6 to recovered line 3; seed stamped version 10 | Exit 3; ratchet bytes unchanged |
| Seed from an old stored run with no ratchet | Old ordinals gained a version 10 stamp | Exit 3; no ratchet created |
| Claim #2 from an old stored run, then coverage 10 | Claim held recovered line 3; original line 6 became available | Whole anonymous raw-name group remains held |
| Direct override with an old reader mark | Alert, audit, and restamp accepted the unproved mark | Refusal before all three side effects |

The real old reader reports anonymous functions at lines 2 and 6. Reader 10 reports lines 2, 3, and 6. No same-start collision exists in either result. Metric mismatch already refuses verify; the uncovered path was reseeding, which deliberately restamps measured values.

## Contracts and costs

- `keys.expression_group(path, name)` identifies anonymous groups in `.js`, `.cjs`, `.mjs`, `.ts`, `.tsx`, and `.jsx`. These are the existing JavaScript, TypeScript, and TSX reader suffixes. No compiler or parser dependency was added.
- `ratchet.check_reader_keys(text)` checks the existing analysis stamp, including when the key-format stamp is already 1. Missing, malformed, or pre-10 reader proof refuses affected anonymous marks. Named functions and other languages keep compatible reseed behavior.
- `ratchet.check_reader_version(entries, version)` checks proposed marks against the stored run that supplied them. Seed and prune call it before writing.
- Inventory and coverage runs now record `tool_versions.analysis_version` as a string. Existing metadata remains readable.
- `SnapshotStore.record_claim` accepts optional `source_run_id`. `_Handles.claim` supplies its actual run id. Anonymous claims without current reader proof use existing `key_version=0`, which holds the whole raw-name group. Each affected claim reads one run metadata row. Other claims skip that read.
- The private `_maybe_claim` no longer takes an unused store argument. The queue caller and competing-connection regression use the new signature.
- The override writer checks the old ratchet before its alert, audit, or grant, including callers that omit key-version arguments.

A source commit does not prove which dirty bytes a historical run analyzed. Reconstructing an old-to-new ordinal map from a commit, start line, or row id would assign ownership without proof. The repair therefore uses explicit refusal for marks and the existing group-wide claim rule. It adds no workflow framework or automatic debt rewrite.

The conservative mark refusal applies even if an affected file currently has only one anonymous function. The old reader may have omitted a sibling. A user must reconcile each saved mark with its original function before adding a current reader stamp; a fresh coverage run alone does not establish that mapping.

Old or unproved claims hold the group until released, expired by `runs prune`, or all functions in the group become healthy. Release uses the saved handle and preserves unrelated claims. The existing retention contract remains in the claims section of `docs/agent-json.md`.

## Validation

| Evidence | Result |
| --- | --- |
| `reader-migration-red.txt` | Four public-seam failures before the repair; named-mark compatible reseed passed |
| `reader-snapshot-red.txt` | Old stored run gained a false reader 10 stamp |
| `reader-stamp-red.txt` | An unrelated numeric comment incorrectly supplied reader proof |
| `reader-override-red.txt` | Direct override accepted old marks for omitted, 0, and 1 key versions |
| `reader-migration-focused-green.txt` | 196 passed |
| `reader-migration-claim-final.txt` | All 13 new cases and competing-connection test passed after caller cleanup |
| `reader-migration-e2e.txt` | 57 passed; one old fixture expected the key-collision message before reader admission |
| `reader-migration-e2e-rerun.txt` | All three same-line CLI tests passed after giving that fixture current reader proof |
| `reader-migration-static.txt` | Eight production modules compile; all 16 touched functions have CCN 1-6 |
| `reader-migration-hook.txt` | Staged hook passed with exit 0 |

Every Python invocation used this worktree's `src` and asserted the imported package path. No root source was edited, no commit or push was made, and no publishing command ran.

`reader-migration.patch` contains 13 paths and was built against exact integrated source copies rather than Git HEAD. The original before copies and export script remain in the local evidence workspace. The copied patch and `reader-migration-static.txt` record its scope and complexity checks. Store claim changes are separate from the other agent's prune and witness-retention changes.
