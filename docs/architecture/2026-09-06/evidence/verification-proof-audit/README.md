# Verification proof audit

Changed only the two report scripts under `docs/architecture/2026-09-06`: `capture-verification.py` and `verify-committed-inputs.py`. No production, test or config file changed. No root test suite, coverage command or verification ran during this audit.

## Gaps corrected

1. The capture script did not produce the artifact manifest consumed by the post-commit script. Finalization now binds the manifest to the source manifest, command, result, stdout receipt and captured ledger. It requires the configured lane artifact paths; an empty manifest cannot stand in for coverage and JUnit.
2. The post-commit check compared only names present in the captured manifest. It now requires exact working and committed path sets before checking file bytes. The only committed-byte exceptions remain `tests/fixtures/recorded/raw_mod.json` and `tests/fixtures/recorded/raw_std.json`, and only CRLF-to-LF conversion is accepted.
3. An exit code alone did not prove passing tests or identify the run whose coverage was reused. Finalization checks the stdout run ID against its captured ledger and SQLite verify row. Coverage must match the digest consumed by that run. JUnit must contain completed testcases, zero failures or errors, and the same total and skipped counts recorded by the run. Post-commit reuse checks HEAD, inputs and artifacts again after completion, including the reused run's recorded coverage digest.

## Current active capture

The already-running capture loaded the old script. Wait for that process to finish with return code 0 and no changed inputs, then run from the repository:

```powershell
$env:PYTHONPATH = '<repo>\src'
python docs/architecture/2026-09-06/capture-verification.py --finalize <raw-capture-directory>
```

This finalization command reads root inputs, configured artifacts and the store. It writes `artifacts-after.json` and `artifact-binding.json` only in the supplied raw capture directory. Include both in the portable evidence collection. Future captures finalize automatically after a passing run.

The original run did not record a JUnit digest. The new binding says exactly what can be proved: JUnit bytes are first hashed at finalization, and its completed zero-failure counts match the recorded run. Coverage has the stronger exact-byte link because its consumed digest already exists in the run ledger. The opt-in timing failure qualification in the report remains unchanged.

## Checks

`probe.py` uses disposable Git, SQLite, coverage and JUnit fixtures. It verifies 16 acceptance/refusal cases, including an added committed file that is absent from the matching working tree, the two explicit JSON exceptions, refusal of a third CRLF exception, stale receipts, an empty artifact manifest even with a matching manifest hash, and missing/failing/partial JUnit. `probe-result.json` and `probe.txt` contain the passing results.

The first probe exposed an open SQLite connection during Windows fixture cleanup. Both reader connections now close explicitly. The failing output remains in `probe-before.txt`; the paired green replay is `probe.txt`.

All 21 helper functions have CCN at most 5. `git diff --check` passes for both changed scripts. The probe asserts that `crapkit.__file__` resolves to the root checkout before it imports the report helpers. Root coverage artifacts were never written by this work.
