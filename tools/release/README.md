# Releasing crapkit

The version is always an explicit argument. Run one stage at a time from the release checkout. Stage 1 requires clean `main` already pushed to `origin`; stage 2a creates the local tag and runs the contract tests. Publishing requires that tag at the same clean HEAD and a new passing full `verify` row recorded by the verify stage. A zero process exit without that ledger row is refused.

```
python tools/release/release.py check VERSION
python tools/release/release.py plan VERSION
python tools/release/release.py run stage1 VERSION
python tools/release/release.py run stage2a VERSION
python tools/release/release.py run verify VERSION
python tools/release/release.py run stage2b VERSION
python tools/release/release.py run registry VERSION
python tools/release/release.py verify VERSION
```

Keep `run verify` in its own background process when the calling tool has a shorter deadline than the suite. `plan` and `run --dry-run` print commands without changing files or contacting publication services.

## Build once, then publish

Stage 2b builds a wheel and a source archive into `.crapkit/release-dist/` and runs `twine check` before any push. It records each filename and SHA256 digest in `.crapkit/release-receipt.json`, alongside the HEAD, version, contract proof and verification run. A retry rechecks those local bytes and never rebuilds a recorded pair. Missing, changed, extra or redirected artifacts refuse publication. Ordinary `dist/` output is separate.

The first push is confirmed only when remote `main` and the release tag both equal the receipt HEAD. After that proof is recorded, retries require the same remote tag and allow `main` to advance. It reads the version-specific PyPI JSON response and GitHub release asset digests before each upload, then sends only missing files. An existing filename with different bytes is an error; it never requests an overwrite. Older GitHub assets without a digest are downloaded and hashed in bounded chunks. After every publication command, including a failed command, the stage reads back the remote result. A failed command with confirmed publication stops safely; rerun the same stage to continue.

PyPI filename digests come from its [version JSON response](https://docs.pypi.org/api/json/). GitHub publishes [asset digests and download URLs](https://docs.github.com/en/rest/releases/assets). Pages completion uses the [latest build's commit and status](https://docs.github.com/en/rest/pages/pages#get-latest-pages-build), not the POST response alone. These readbacks describe the receipt's wheel and source archive; they do not audit unrelated release assets.

## Resume a partial stage

Keep the receipt and `.crapkit/release-dist/` together, then rerun:

```
python tools/release/release.py run stage2b VERSION
```

The command repeats all clean-tree, tag and ledger checks. It reuses matching local files, reads published files again, skips matching uploads, and continues with the next missing file. The local Claude plugin update is recorded after success. An interrupted local update can run again. Registry login/publish remains its own stage. Glama's Repository admin **Sync Server** action stays manual.

Pages can finish after the command stops. A `queued` or `building` status at the release commit asks you to wait and rerun; it does not send a second POST. A `built` result at that commit completes the stage. A later `errored` result at that commit proves the build ended and permits one new request on the next invocation. A build for a different commit cannot confirm this release.

## Resolve an unknown outcome

The receipt records a pending action before sending its command. If the command or readback fails and the remote result is still unknown, a retry reads again but does not repeat that action. A transient 404, empty result or timeout is not proof that the upload never happened.

If readback later finds the expected bytes, rerunning stage 2b clears that pending action and continues automatically. If the original request definitively failed before publishing, use this manual recovery procedure:

1. Confirm with the provider's upload or request history that the original request has ended and the named file or release is absent. Wait for pending requests and caches to settle. Preserve that evidence.
2. Back up `.crapkit/release-receipt.json`. Remove only the matching string from its `pending` array, such as `pypi:crapkit-VERSION-py3-none-any.whl` or `github:create`. Leave the HEAD, version, artifact digests and verification fields unchanged.
3. Rerun stage 2b. It reads the remote state again before issuing any missing upload. A digest mismatch still refuses.

Do not clear pending entries merely to suppress a refusal. Restore lost local artifacts from the original checked bytes; rebuilding an existing receipt's version is not a recovery path. Keep one release stage active per checkout. A new HEAD, changed tag or later failed verification requires fixing that condition and rerunning the relevant proof stage.
