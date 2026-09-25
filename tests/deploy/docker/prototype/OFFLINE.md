# The harness probes with no network

`probe_offline.sh` reruns the checks of `probe.sh` to `probe7.sh` inside the
pinned `crapkit-deploy:full` image under `--network none`. The first runs
fetched crapkit from PyPI and the Cursor agent from its CDN with the network
on. Here crapkit 0.8.0 comes from the image's wheelhouse and every CLI from
the image, with these switches set: `DISABLE_AUTOUPDATER=1`,
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`,
`COPILOT_OFFLINE=true`, `GEMINI_TELEMETRY_ENABLED=false` and Gemini's
settings file with auto-update, usage statistics and telemetry off.

    docker run --rm --network none --user 1000:1000 -v <tree>:/src:ro \
        crapkit-deploy:full sh /src/tests/deploy/docker/prototype/probe_offline.sh

Measured on 2026-09-25 with Docker 29.8.0 and BuildKit 0.33.0, on the image
built from pins.toml at commit 4caef17a, while other builds and about 19
containers shared the host. Two runs from fresh containers gave the same
verdicts:

| Probe | Harness | Verdict | Run 1 (s) | Run 2 (s) |
|---|---|---|---|---|
| `claude mcp list` shows Connected | Claude Code 2.1.281 | pass | 1.61 | 1.99 |
| app-server `mcpServerStatus/list` names crapkit | Codex 0.156.1 | pass | 6.18 | 5.32 |
| `gemini mcp list` shows Connected | Gemini CLI 0.61.0 | pass | 4.87 | 4.65 |
| `opencode mcp list` shows connected | OpenCode 1.18.32 | pass | 4.83 | 4.49 |
| `copilot mcp get crapkit --json` | Copilot CLI 1.0.88 | pass | 5.09 | 5.90 |
| `cline mcp install crapkit` | Cline 3.0.65 | pass | 4.30 | 4.25 |
| `cline config mcp --json` | Cline 3.0.65 | pass | 3.40 | 4.34 |
| `cursor-agent mcp list-tools crapkit` lists get_next_item | Cursor agent 2026.09.23 | pass | 3.62 | 3.95 |
| `agent mcp list-tools crapkit` (the name Cursor's docs use) | Cursor agent 2026.09.23 | pass | 3.19 | 2.78 |
| `claude plugin install crapkit@crapkit` from a local marketplace | Claude Code 2.1.281 | pass | 1.02 | 0.56 |
| `crapkit doctor --plugin-root` | crapkit 0.8.0 | pass | 0.86 | 1.01 |
| `codex plugin add crapkit@crapkit` | Codex 0.156.1 | pass | 0.17 | 0.16 |
| app-server `hooks/list` returns the plugin's hooks | Codex 0.156.1 | pass | 1.79 | 1.78 |
| app-server `skills/list` returns the plugin's skills | Codex 0.156.1 | pass | 1.49 | 2.66 |

What the offline runs settle:

- None of these checks needs a login or the network. The Cursor agent lists
  crapkit's tools offline with no Cursor account, under both of its names.
- Every probe finished in under 6.2 s offline on a loaded host, against the
  online budget of under 8 s. On the first offline runs (commit 4b82ab1, a
  quieter host) the slowest probe took 5.6 s.
- Codex still loads the plugin's `hooks/hooks.json` offline: `hooks/list`
  returns `crapkit@crapkit:hooks/hooks.json:post_tool_use:...` handlers.

Raw output: `logs/offline-1.log` and `logs/offline-2.log`, one line per probe:
`<probe>|<exit>|<ms>|<pass or FAIL>|<last line of output>`.

## What each image serves a user offline

`probe_serves.sh` runs, in each image under `--network none`, the installs a
user types, against the image's wheelhouse and npm cache only: pipx installed
with pip, `crapkit[py]` and pytest into a venv of every CPython the image
holds (the 3.15 prerelease included), the pinned vitest and then the README's
`npm i -D "@vitest/coverage-v8@<major>"` line in a repo that holds only
vitest, and the Cursor agent under both names where the image holds it.

    docker run --rm --network none --user 1000:1000 -v <tree>:/src:ro \
        crapkit-deploy:<image> sh /src/tests/deploy/docker/prototype/probe_serves.sh

Same images and host as above. Seconds per probe; every probe passed:

| Probe | cells | core | ci | full | gui |
|---|---|---|---|---|---|
| `pip install pipx` | 4.3 | 4.6 | 4.0 | 5.3 | 4.8 |
| `pipx --version` | 1.4 | 1.4 | 2.4 | 2.1 | 1.1 |
| `pip install crapkit[py]==0.8.0 pytest` on 3.11 | 9.1 | 5.9 | 7.0 | 10.9 | 8.7 |
| the same on 3.12 | 7.7 | 5.6 | 6.1 | 11.1 | 8.3 |
| the same on 3.13 | 9.2 | 7.4 | 8.8 | 8.9 | 8.2 |
| the same on 3.14 | 8.9 | 6.9 | 8.2 | 7.8 | 9.0 |
| the same on 3.15.0rc2 | 7.5 | 7.5 | 10.5 | 8.8 | 8.7 |
| `crapkit --version` (each CPython, slowest) | 0.5 | 0.5 | 0.5 | 0.7 | 0.6 |
| `npm i -D vitest@5.0.2` | 4.6 | 5.1 | 5.8 | 5.6 | 5.3 |
| `npm i -D "@vitest/coverage-v8@5"` (the README line) | 3.9 | 4.2 | 5.2 | 3.2 | 4.6 |
| `cursor-agent --version` | | 1.0 | | 1.2 | 1.4 |
| `agent --version` | | 1.2 | | 0.8 | 1.1 |

Raw output: `logs/serves-<image>.log`, in the same line format.

## The arm64 cells image

`cells-arm64` is the cells target built for linux/arm64, for the weekly
lin-arm64 job. On an x86_64 host it runs under QEMU, registered once with
`docker run --privileged --rm tonistiigi/binfmt --install arm64`; Docker
Desktop drops that handler on some restarts, and `run.py` then names this
command before it builds or runs the image.

    docker run --rm --platform linux/arm64 --network none --user 1000:1000 \
        -v <tree>:/src:ro crapkit-deploy:cells-arm64 \
        sh /src/tests/deploy/docker/prototype/probe_serves.sh

The first run on 2026-09-25 failed one probe: the image holds the 3.15
prerelease like every image, but the wheelhouse had no aarch64 row for it,
so `pip install crapkit[py]` on 3.15 stopped at "No matching distribution
found for coverage>=7.10.6". With the `linux-aarch64-cp315` row every probe
passed, emulated, on the same host (`logs/serves-cells-arm64.log`):

| Probe | seconds |
|---|---|
| `pip install pipx` | 7.9 |
| `pipx --version` | 4.4 |
| `pip install crapkit[py]==0.8.0 pytest` on 3.11 / 3.12 / 3.13 / 3.14 / 3.15.0rc2 | 12.9 / 13.7 / 12.6 / 16.3 / 14.6 |
| `crapkit --version` (each CPython, slowest) | 2.0 |
| `npm i -D vitest@5.0.2` | 10.5 |
| `npm i -D "@vitest/coverage-v8@5"` (the README line) | 8.3 |

In the same image under `--network none`: the kit's own tests
(`run.py --packet deploy-kit --image cells-arm64 -n 4`) gave 48 pass and 1
skip (the Windows-only path test) in 335 s, and the transcripts name the
platform `Linux-...-aarch64-with-glibc2.41`. The lin-arm64 cell (the README
start on 3.12, then Route 1) passed in 105 s, and lin-pip-start-py311 and
lin-pip-start-py314 passed with the same strict xfail as on x86_64.
