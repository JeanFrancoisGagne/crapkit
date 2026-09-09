# Releasing crapkit

The version is always an explicit argument. Run one stage at a time from the release checkout. Stage 1 requires clean `main` that includes current `origin/main`; local preparation commits may remain unpublished. Stage 2a creates the local tag and runs the contract tests. Publishing requires that tag at the same clean HEAD and a new passing full `verify` row recorded by the verify stage. A zero process exit without that ledger row is refused. Nothing is pushed before that proof passes.

Stage 1 regenerates documentation after reinstalling the bumped version and before measuring coverage. It includes the generated `SECURITY.md` support table in the release commit. Stage 2a checks generated guidance against the tagged version.

The verification ledger must record passing tests for the full Python lane, which
runs both unit and end-to-end suites. Publication requires exit code zero, no test
failures, at least one executed test, valid skipped counts and both coverage and
test-result digests. A regression verdict that accepts unchanged failures does
not satisfy this release requirement.

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

Run `verify` through its stage, never as the bare command `plan` prints. The stage
stamps a run-id watermark before it starts and publication requires a passing run
above that watermark. `python -m crapkit verify` on its own stamps nothing, so a
run you watched pass is refused later with a message about test evidence.

## Preflight: prove the environment before anything is pushed

Every fault in the 0.7.2 release fired after PyPI and the GitHub release were
already public, because nothing checked the machine first.

`check VERSION` now refuses the first two rows below, and it is stage 1's first
command, so the chain stops before it builds or pushes anything. Confirm the other
two yourself. Each takes seconds; each cost a published half-release when skipped.

| Check | Command | Why it bites |
| --- | --- | --- |
| The release venv is ACTIVATED | `which python` names this repository's `.venv` | The py lane in `crapkit.toml` runs a bare `python`, taken from PATH, not the interpreter that launched this script. Launching by absolute path is not enough. |
| That venv owns its dev tools | `python -c "import pytest, coverage, build, twine"` resolves inside the venv | Three tests build a throwaway venv and rely on the dependency-venv bridge to carry the parent's `purelib` into it. A thin venv that resolves pytest from the base install carries nothing, and the child reports `No module named pytest`. |
| PyPI credentials reach Twine | `TWINE_USERNAME` and `TWINE_PASSWORD` are set, or the token is in keyring | Twine 7 skips the named `.pypirc` entry whenever `--repository-url` is passed, and that flag is a fixed anti-redirect control. A `.pypirc` alone authenticates nothing. |
| `gh` is authenticated | `gh auth status` | Publishing uses `gh`, and every readback now sends the same credential. GitHub's Pages API answers 404, not 403, to an anonymous reader. |

Set up the release venv once. `.venv/` is ignored by this repository:

```
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" build twine
```

On Windows use `.venv\Scripts\python.exe`, and activate the venv in the shell that
runs the release so PATH resolves `python` to it.

## Expect a rerun after each publication

Every publication action reads its own result back immediately, and PyPI's version
JSON and GitHub's release API both take seconds to serve what was just written. A
first read that misses is normal: the stage records the action as pending and stops.
Rerun the same stage once the surface answers. The 0.7.2 release needed six such
reruns, one per artifact and one for the release itself. A pending entry means
"unconfirmed", never "failed"; check the surface before reaching for recovery.


## Build once, then publish

Stage 2b builds a wheel and a source archive into `.crapkit/release-dist/` and runs `twine check` before any push. It records each filename and SHA256 digest in `.crapkit/release-receipt.json`, alongside the HEAD, version, contract proof and verification run. A retry rechecks those local bytes and never rebuilds a recorded pair. Missing, changed, extra or redirected artifacts refuse publication. Ordinary `dist/` output is separate.

Publication targets are fixed to PyPI and `github.com/JeanFrancoisGagne/crapkit`, matching recovery readback. The commands pass Twine's upload URL and gh's repository or hostname explicitly, so `TWINE_REPOSITORY_URL`, `.pypirc`, `GH_REPO` and `GH_HOST` cannot redirect them. Before publishing, both resolved `origin` fetch and push URLs must name that repository using ordinary HTTPS, `git@github.com:` or `ssh://git@github.com/` syntax. Multiple URLs and SSH host aliases refuse; set an explicit canonical URL before retrying. Git keeps the admitted transport and credentials.

The first push is confirmed only when remote `main` and the release tag both equal the receipt HEAD. After that proof is recorded, retries require the same remote tag and allow `main` to advance. It reads the version-specific PyPI JSON response and GitHub release asset digests before each upload, then sends only missing files. An existing filename with different bytes is an error; it never requests an overwrite. Older GitHub assets without a digest are downloaded and hashed in bounded chunks. After every publication command, including a failed command, the stage reads back the remote result. A failed command with confirmed publication stops safely; rerun the same stage to continue.

PyPI filename digests come from its [version JSON response](https://docs.pypi.org/api/json/). GitHub publishes [asset digests and download URLs](https://docs.github.com/en/rest/releases/assets). Pages completion uses the [latest build's commit and status](https://docs.github.com/en/rest/pages/pages#get-latest-pages-build), not the POST response alone. These readbacks describe the receipt's wheel and source archive; they do not audit unrelated release assets.

## Resume a partial stage

Keep the receipt and `.crapkit/release-dist/` together, then rerun:

```
python tools/release/release.py run stage2b VERSION
```

The command repeats all clean-tree, tag and ledger checks. It reuses matching local files, reads published files again, skips matching uploads, and continues with the next missing file. The local Claude plugin update is recorded after success. An interrupted local update can run again. Registry login/publish remains its own stage. Log in with `mcp-publisher login github --token "$(gh auth token)"`, which needs no device flow. Glama's Repository admin **Sync Server** action stays manual, and is the only step in the chain no command performs.

Pages can finish after the command stops. A `queued` or `building` status at the release commit asks you to wait and rerun; it does not send a second POST. A `built` result at that commit completes the stage. A later `errored` result at that commit proves the build ended and permits one new request on the next invocation. A build for a different commit cannot confirm this release.

## Resolve an unknown outcome

The receipt records a pending action before sending its command. If the command or readback fails and the remote result is still unknown, a retry reads again but does not repeat that action. A transient 404, empty result or timeout is not proof that the upload never happened.

If readback later finds the expected bytes, rerunning stage 2b clears that pending action and continues automatically. If the original request definitively failed before publishing, use this manual recovery procedure:

1. Confirm with the provider's upload or request history that the original request has ended and the named file or release is absent. Wait for pending requests and caches to settle. Preserve that evidence.
2. Back up `.crapkit/release-receipt.json`. Remove only the matching string from its `pending` array, such as `pypi:crapkit-VERSION-py3-none-any.whl` or `github:create`. Leave the HEAD, version, artifact digests and verification fields unchanged.
3. Rerun stage 2b. It reads the remote state again before issuing any missing upload. A digest mismatch still refuses.

Do not clear pending entries merely to suppress a refusal. Restore lost local artifacts from the original checked bytes; rebuilding an existing receipt's version is not a recovery path. Keep one release stage active per checkout. A new HEAD, changed tag or later failed verification requires fixing that condition and rerunning the relevant proof stage.
## Credentials and secondary listings

Install `build` and `twine` into the Python environment that runs the release
script. Check GitHub authentication with `gh auth status` and confirm access to
`JeanFrancoisGagne/crapkit` before the release starts.

Put `claude` on the release process's PATH. The script resolves each command
through a PATHEXT-aware lookup, so an npm `claude.CMD` shim now works where a bare
`claude` once died with WinError 2: Windows `CreateProcess` searches PATH but
appends only `.exe`. That failure used to land after PyPI and the GitHub release
were public. Confirm with `claude --version` from the same shell.

Twine receives an explicit PyPI upload URL. With Twine 7, that skips the named
`.pypirc` repository entry, including its credentials, so a populated `.pypirc`
authenticates nothing here. Supply credentials through Twine's supported
environment variables or keyring in the release process. Keep them out of command
arguments, logs and committed files; do not remove the fixed upload URL to make
authentication work.

The refusal reads `NonInteractive: Credential not found for API token`, and it
arrives after the push, with `main` and the tag already public. Check the
credential in preflight instead.

After stage 2b and the MCP Registry stage, check every distribution route:

| Route | Source and confirmation |
| --- | --- |
| PyPI | The release receipt's wheel and source archive hashes match the version JSON response. |
| GitHub release | The tag names the verified commit, notes match the changelog, and asset hashes match PyPI. |
| Website | `verify` compares the newest Pages build against the tagged commit and requires status `built`. Open the landing page and handbook. |
| GitHub Action and pre-commit | The release tag includes `action.yml` and `.pre-commit-hooks.yaml`; README examples use that tag. |
| Local CLI | Stage 1 updates only its selected Python environment. Upgrade the intended user CLI with its owning installer, read its resolved executable and version, and run `crapkit doctor --plugin-root` against installed plugins. |
| Claude Code plugin | Refresh its registered marketplace, update the user-scope plugin, read back its version and check `doctor --plugin-root`. Existing sessions need a restart to apply the update. |
| Codex plugin | Refresh its registered marketplace, install the current plugin with the supported manager, and check its listed version and explicit installed plugin root. Verify its three skills and MCP configuration. |
| MCP Registry | The canonical server name has the new version and matching PyPI package. |
| Glama | `verify` reads the server page and requires the README action pin to name this release's tag. Until the sync runs it reports an earlier revision. Use the Repository admin **Sync Server** action after the GitHub release exists, then confirm build and tool schema, and correct stale profile text separately. |

After publication, refresh the clients' marketplace snapshots before updating their
installed copies. Stage 2b invokes Claude's plugin update; it does not perform the
Codex commands below:

```sh
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
claude plugin list --json

codex plugin marketplace upgrade crapkit --json
codex plugin add crapkit@crapkit --json
codex plugin list --marketplace crapkit --json
```

The Codex marketplace is registered once with
`codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git`.
Use the supported managers to refresh installations; do not edit their caches.
Check that the registered source is the canonical repository and that its current
revision and installed version match the release. Run `crapkit doctor --plugin-root PATH`
against each installed plugin using the intended global CLI, then start a fresh MCP
session and confirm initialize reports the release version and tools/list returns
twelve tools. Doctor's no-path default checks Claude Code's cache, so pass the Codex
installed root explicitly. See the [client upgrade guide](../../docs/upgrading.md#plugin-and-mcp-clients).

These checks do not reload existing clients. Restart existing Claude Code sessions
to apply its plugin update, and start a new Codex task to load updated skills and
tools. The advisory hook instructions configure Claude Code's
PostToolUse event; the Codex installation check covers skills and MCP.

Glama profile text does not necessarily follow the README. Check its tool count
and write behavior against the current MCP definitions. Do not use **Build and
Release** to force an automatic version number while a synchronized release is
pending.

Finally, inspect the release commit's CI jobs and repository activity. A queued
job has not passed, and an upload command's exit code does not replace readback.
