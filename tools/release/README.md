# Releasing crapkit

The version is always an explicit argument. Run one stage at a time from the release checkout. Stage 1 requires clean `main` that includes current `origin/main`; local preparation commits may remain unpublished. Stage 2a creates the local tag and runs the contract tests. Publishing requires that tag at the same clean HEAD, a new passing full `verify` row recorded by the verify stage, and the accuracy and deploy stages' records at that commit. A zero process exit without that ledger row is refused. Nothing is pushed before that proof passes.

Stage 1 regenerates documentation after reinstalling the bumped version. It includes the generated `SECURITY.md` support table in the release commit. It measures nothing: the verify stage runs the one full py lane a release needs. Stage 2a checks generated guidance against the tagged version.

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
python tools/release/release.py run accuracy VERSION
python tools/release/release.py run deploy VERSION
python tools/release/release.py run stage2b VERSION
python tools/release/release.py run registry VERSION
python tools/release/release.py run glama VERSION
python tools/release/release.py run surfaces VERSION
```

`run surfaces` reads every surface back (`release.py verify VERSION`) and then
dispatches deploy.yml's published cadence, which installs vVERSION from PyPI, the
tag, pre-commit and the MCP registry the way a user does. It dispatches on the tag
and names the run `deploy published vVERSION FILES`, where FILES is the sha256 over
the sorted `<filename> <sha256>` lines of the files PyPI serves for the version. A
rerun of the stage dispatches nothing when a run by that name, at the commit
`git ls-remote origin refs/tags/vVERSION` returns, passed or is still running; a
red run, a moved tag or another PyPI file gets a new dispatch. Pushing the tag
starts no deploy run: deploy.yml has no tag trigger, because that run raced the
PyPI upload and repeated this one. The dispatch returns at once: find the run with
`gh run list --workflow deploy.yml --limit 1` and wait on it with
`gh run watch RUN_ID --exit-status`. A red run fails the release.

Preview the GitHub release body with `python tools/release/release.py notes VERSION > notes.md`.
It prints the changelog section as UTF-8 whatever the console's code page, and it is the
text stage 2b hands `gh release create`. GitHub refuses a body over 125,000 characters,
so `check` refuses a longer section before stage 1 bumps anything.

Keep `run verify` in its own background process when the calling tool has a shorter deadline than the suite. `plan` and `run --dry-run` print commands without changing files or contacting publication services.

Run `verify` through its stage, never as the bare command `plan` prints. The stage
stamps a run-id watermark before it starts and publication requires a passing run
above that watermark. `python -m crapkit verify` on its own stamps nothing, so a
run you watched pass is refused later with a message about test evidence.

After a passing verify, the stage runs `ratchet seed` and `ratchet prune` against
that verify run. A green verify also tightens and drops marks on its own, so the
stage compares against the marks it read before the verify started. When verify,
seed and prune change `crapkit-ratchet.tsv`, the release commit lacks marks its own
tree earns. The stage puts the committed file back, saves the computed one as
`.crapkit/release-marks-VERSION.tsv`, stops, and publication stays refused. The
refusal prints the commands that carry the saved file into the release commit:

```
git tag -d vVERSION
cp .crapkit/release-marks-VERSION.tsv crapkit-ratchet.tsv
git add -- crapkit-ratchet.tsv
git commit --amend --no-edit
```

Then rerun stage 2a and verify. `git add` comes first because `git commit -- PATH`
refuses a marks file the release commit does not track yet.

## The accuracy stage

A tree that holds `tools/accuracy/run.py` publishes only past its calculation-accuracy
suite. `run accuracy VERSION` comes after verify and does two things, both on the tag
commit. Keep it in its own background process: it can take two hours.

1. Here: `python tools/accuracy/run.py --tier release --local --receipt .crapkit/release-accuracy-VERSION.json`,
   about 30 minutes. It runs the push tier, both wheel diffs, the store upgrade check,
   the consumer replay, the retro replays and the mutation coverage check.
   `--local` selects the two rows no CI cell runs because they read this machine's
   state: the bundle replays and the mutation receipts. When the tag commit changes
   only the release files (stage 1's version bump), the stage sets
   `CRAPKIT_COVERED_AT=HEAD~1`, so `mutation.py covered` judges the code the release
   ships at the commit the mutation runs could judge; the bump alone would void every
   stored verdict.
   The retro replays include the bundle rows, whose commits live only in the
   pre-2026-08-24 history bundle, so the stage sets `CRAPKIT_RETRO_BUNDLE` for the tier:
   from the environment when it is set, else from the path
   `tools/release/retro-bundle.path` names (`~` is the home directory). When that path
   holds no file the stage asks `retro.py needs-bundle` for the stale bundle rows the
   tier could judge only from the bundle. With none, the tier runs without it, since
   `retro.py release` reads the bundle only for those rows. With one, the stage refuses
   before the tier starts, instead of retro.py exiting 3 inside the tier:

   ```
   the accuracy stage needs the retro history bundle: CRAPKIT_RETRO_BUNDLE names C:\gone.bundle, which is not a file. The release tier's retro row replays the bundle rows, whose commits live only in that bundle; put it there, then rerun `python tools/release/release.py run accuracy VERSION`
   ```
2. On GitHub: it pushes the tag commit to the scratch branch `accuracy-release/VERSION`,
   dispatches `accuracy.yml` with `mode=release` and `release_key=VERSION`, watches the run
   for up to 90 minutes, and deletes the branch. A dispatched run needs its commit on the
   remote, and the tag itself stays local until stage 2b pushes it.

A rerun reuses a receipt that still passes and a run that passed or is still running,
so it dispatches nothing new after a timeout. After a red or cancelled release run at
the tag commit it runs `gh run rerun ID --failed` and watches that run again, so one
red cell no longer redispatches all 14 jobs. It does so only while every `receipt-*`
artifact the run uploaded stays unexpired for longer than the 90-minute watch
(`expired` and `expires_at` in `gh api repos/OWNER/REPO/actions/runs/ID/artifacts`):
the receipts keep for one day, xplat downloads them only after the rerun cells finish,
and it compares the receipts it downloads with one another and passes on a single
one, so a receipt gone by then would leave the rerun cells checked against nothing.
A receipt that expired or expires within the watch, an artifact list GitHub cannot
answer (an HTTP or network error, or a body with no list), or a rerun gh refuses gets
a new dispatch instead. xplat itself refuses when any receipt the plan job names
(the four corpus shards, macOS and both Windows cells) is missing.

Stage 2b and the registry stage believe neither report. Before each publication they
read accuracy.yml's runs at the tag commit from GitHub and require one named
`accuracy release VERSION` that completed with success. They require the receipt's
head to be the release HEAD, its tier `release` and every row `pass` (or `empty`, a row
with no test in that tier), and its selection the stage's own: the whole tier with
`--local`, no shard and no `--os-sensitive`. A receipt made without `--local` holds no
retro or mutation row on the releasing machine. They also hash `tools/accuracy/pins.toml`,
`tests/accuracy/corpus_goldens/corpus.toml` and
`tests/accuracy/suite_strength/retro/ledger.tsv` themselves and compare the result with
the receipt. Each refusal names the row, the file or the run, and ends with the rerun:

```
release accuracy row `corpus_goldens: wheel diff vs 0.8.0` fail: fix what it names, then rerun `python tools/release/release.py run accuracy VERSION`
tests/accuracy/suite_strength/retro/ledger.tsv hashes to 3f1c09a2b7de here and the release accuracy receipt says 9a0e44c1d2f3; rerun `python tools/release/release.py run accuracy VERSION`
GitHub holds no successful accuracy.yml run named `accuracy release VERSION` at 8fb7b45c7248; rerun `python tools/release/release.py run accuracy VERSION`
```

## The deploy stage

A tree that holds `tools/deploy/candidate.py` publishes only past deploy.yml's release
cadence at the tag commit: the run that installs the candidate through every channel and
harness the deploy suite models, fresh and as an upgrade. `run deploy VERSION` comes
after verify. Keep it in its own background process: the run can take two and a half
hours.

1. Here: it reads the key, the tag commit's git tree id (`git rev-parse vVERSION^{tree}`).
2. On GitHub: it pushes the tag commit to the scratch branch `deploy-release/VERSION`,
   dispatches deploy.yml with `cadence=release` and `tree=` that id, watches the run for
   up to 150 minutes, and deletes the branch. The run is named `deploy release TREE`. Its
   scope job compares `git rev-parse HEAD^{tree}` with the input, and when the checkout's
   tree differs the run fails before any cell starts.

A tree id names the committed content, not the bytes a checkout writes. The key used to
be candidate.py's hash of the working-tree bytes, and a Windows checkout with
`core.autocrlf=true` holds two text fixtures with CRLF on disk while `git status` stays
clean: 0.8.1 hashed to 2d2b705b here and to fedbb54a on the runner, so the scope job
refused every run.

The stage then writes a deploy record into `.crapkit/release-receipt.json`: the tag
commit's sha and its tree id. A rerun of the stage reuses a run under that key that
passed or is still running. After a red or cancelled run under the key it runs
`gh run rerun ID --failed` and watches that run again: every deploy entry needs only the
scope job and downloads nothing another job wrote, so the green entries of the earlier
attempt still hold for the same tree. It reruns only a run that began less than 3 days
ago: the weekly entries install the harnesses at their newest release and read PyPI and
npm, which the tree id does not cover. An older red run, or one gh refuses to rerun (past
GitHub's 30-day window, no failed job, a startup_failure), gets a new dispatch under the
same key instead. A different tree id dispatches a new run.

Stage 2b and the registry stage require the record's sha to be the receipt's head. They
then read deploy.yml's runs at that commit from GitHub and require one named
`deploy release TREE` that completed with success. The record proves the suite tested
the release's source, not the bytes PyPI gets: the deploy kit builds its own wheel from
that tree. Each refusal ends with the rerun:

```
deploy gate: the release receipt holds no deploy record; rerun `python tools/release/release.py run deploy VERSION`
deploy gate: the deploy record was made at 0123456789ab and the release is at 8fb7b45c7248; rerun `python tools/release/release.py run deploy VERSION`
deploy gate: GitHub holds no successful deploy.yml run named `deploy release TREE` at 8fb7b45c7248; rerun `python tools/release/release.py run deploy VERSION`
```

The release cadence runs no cell that the published cadence runs: those cells copy the
README lines that name vVERSION, and the tag, the PyPI files and the registry entry reach
GitHub only when stage 2b pushes them. So `weekly-online` stays out of the release
cadence, and `latest-harnesses` runs only its harness cells (`--packet deploy-harnesses`).
`published-online` runs those cells after the push. `tests/unit/test_deploy_map.py`
checks every release entry against run.py's own selection.

`check` reads no deploy.yml run: before the bump there is no tag commit to test. It
refuses a machine whose gh cannot dispatch and read the run later:

```
deploy gate: gh is not on PATH, so the deploy stage cannot dispatch deploy.yml or read its runs; install the GitHub CLI and run gh auth login
deploy gate: gh auth token returned nothing, so the deploy stage cannot dispatch deploy.yml or read its runs; run gh auth login
```

## Advisory gates

A remote gate that cannot pass yet can be ruled advisory for one version. The ruling is
a row in `ADVISORY` in `release.py`, keyed by version and gate (`accuracy-remote` or
`deploy`) with the reason, and it lands through review like any other change. For a
listed gate, stage 2b and the registry stage print each problem as
`advisory (GATE): LINE` and write the gate, its reason and its problems into
`.crapkit/release-receipt.json` under `advisory`; they publish past it. A gate that the
table does not list for the version refuses as before, and the local accuracy receipt
is never advisory. The ruling goes into the gate functions themselves: stage 2b calls
the same `accuracy_gate` the accuracy stage ends on, and `deploy_gate`, so the
"Release accuracy gate" calc's mutants reach stage 2b's refusal too.

0.8.1 shipped with both gates advisory: accuracy.yml's release mode and deploy.yml's
release cadence had never run before it and could not pass at its tag. That ruling
first ran through a scratch wrapper that patched release.py's problem functions; the
table replaces it.

## Preflight: prove the environment before anything is pushed

Every fault in the 0.7.2 release fired after PyPI and the GitHub release were
already public, because nothing checked the machine first.

`check VERSION` is stage 1's first command, so the chain stops before it builds or
pushes anything. Besides the version surfaces, the changelog heading and the size of
that section, it reads the two rows marked `check` below, and it asks whether `gh` is on
PATH and hands out a token (the deploy stage's two lines above). Confirm the three rows
marked `you` yourself: `check` never looks at which python launched it, at what gh's
login may reach or at the accuracy corpus.
Each takes seconds. A missing credential or gh login shows up only after the push;
a python outside the release venv or a missing build or twine stops the release before it.

| Check | Checked by | Command | Why it bites |
| --- | --- | --- | --- |
| The release venv's python runs release.py | you | `.venv/bin/python tools/release/release.py ...` (`.venv\Scripts\python.exe` on Windows) | release.py starts every command with that interpreter's scripts directory first on PATH, so the py lane's bare `python` and a bare `crapkit` are the venv's. 0.8.1's stage 2a failed without it: the contract tests ran the `crapkit` that `<venv python> -m crapkit` hints stand for, and PATH did not reach the venv's. Another python without the dev extra fails the verify stage: nothing is pushed, and the release waits for a rerun. |
| The release interpreter imports build and twine | `check` | `python -c "import build, twine"` | Stage 2b runs `python -m build` and `python -m twine` through the interpreter that launched this script, and it builds before the push. |
| PyPI credentials reach Twine | `check` | `TWINE_USERNAME` and `TWINE_PASSWORD` are set, or the token is in keyring | Twine 7 skips the named `.pypirc` entry whenever `--repository-url` is passed, and that flag is a fixed anti-redirect control. A `.pypirc` alone authenticates nothing. |
| `gh` is authenticated | you | `gh auth status` | Publishing uses `gh`, and every readback now sends the same credential. GitHub's Pages API answers 404, not 403, to an anonymous reader. |
| The full accuracy corpus is cached | you | `%LOCALAPPDATA%/crapkit-accuracy/corpus` (`~/.cache/crapkit-accuracy/corpus` where LOCALAPPDATA is unset) holds the tree corpus.toml pins | The accuracy stage runs the release tier natively, so Docker is not needed here, but its wheel diff and consumer replay read the full corpus from that cache. Without it the tier reports an infra miss and the stage stops before anything is dispatched. |

A failed `check` row prints its line and `check` exits 1. The first line names only
the tools that are missing:

```
the release interpreter cannot import build, twine; stage 2b runs `python -m build` and `python -m twine` before the push, so install build, twine into the environment that runs release.py
no PyPI credential is reachable: twine ignores .pypirc when --repository-url is passed, so set TWINE_USERNAME and TWINE_PASSWORD, or store the token in keyring
```

Set up the release venv once. `.venv/` is ignored by this repository:

```
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" build twine
```

On Windows use `.venv\Scripts\python.exe`. Activating the venv is not needed: release.py
puts the venv's scripts directory first on PATH for every command it starts.

## Expect a rerun after each publication

PyPI's version JSON and GitHub's release API both take seconds to serve what was
just written, so the read straight after a publish usually misses. Each action now
re-reads for up to 55 seconds (12 reads, 5 seconds apart) before giving up, which
costs nothing and republishes nothing. PyPI outlasted the earlier 15-second window
on both 0.7.3 and 0.7.4.

If it still misses, the stage records the action as pending and stops. Rerun the
same stage once the surface answers. A pending entry means "unconfirmed", never
"failed", so check the surface itself before reaching for the recovery procedure.
The 0.7.2 release ran before this retry existed and paid a stage rerun for every
artifact, six in all.


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

The command repeats all clean-tree, tag and ledger checks. It reuses matching local files, reads published files again, skips matching uploads, and continues with the next missing file. The local Claude plugin update is recorded after success. An interrupted local update can run again. Registry login/publish remains its own stage. It logs in with `mcp-publisher login github --token` and the `gh auth token` value, so there is no device flow, and publishes straight after, because the registry session lasts only minutes. The echoed command shows `$(gh auth token)`, never the token. Glama's Repository admin **Sync Server** action stays manual, and is the only step in the chain no command performs: `run glama VERSION` prints that step and runs nothing.

Pages can finish after the command stops. A `queued` or `building` status at the release commit asks you to wait and rerun; it does not send a second POST. A `built` result at that commit completes the stage. A later `errored` result at that commit proves the build ended and permits one new request on the next invocation. A build whose commit does not carry the release commit cannot confirm this release; a build at a later commit on main that carries it does, by the same ancestry test `verify` applies.

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
| MCP Registry | The canonical server name has the new version and matching PyPI package. A search on a cold registry cache took 78 to 87 s, so `verify` gives registry reads 120 s where every other read gets 20 s. |
| Glama | `verify` reads the server page and requires the README action pin to name this release's tag. Until the sync runs it reports an earlier revision. Use the Repository admin **Sync Server** action after the GitHub release exists, then confirm build and tool schema, and correct stale profile text separately. |

After publication, refresh the clients' marketplace snapshots before updating their
installed copies. Stage 2b invokes Claude's plugin update; it does not perform the
Codex commands below:

```sh
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
claude plugin list --json

codex plugin marketplace remove crapkit
codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref vVERSION --sparse .claude-plugin --sparse plugin
codex plugin add crapkit@crapkit
codex plugin list --marketplace crapkit --json
```

The Codex marketplace is pinned to a release tag, and a marketplace added at a tag
stays there: `codex plugin marketplace upgrade` keeps it at that tag. Removing it and
adding it at the new tag moves the marketplace, and `codex plugin add` then installs the
new copy. Stage 1 rewrites the `--ref` in README.md, docs/adoption.md,
docs/upgrading.md, docs/handbook.html, plugin/skills/crapkit-onboard/SKILL.md and
docs/agent-json.md with the other version surfaces.
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
