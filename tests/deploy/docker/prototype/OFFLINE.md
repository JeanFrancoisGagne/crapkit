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

Measured on 2026-09-24 with Docker 29.8.0 and BuildKit 0.33.0, on the image
built from pins.toml at commit 4b82ab1. Two runs from fresh containers gave the
same verdicts:

| Probe | Harness | Verdict | Run 1 (s) | Run 2 (s) |
|---|---|---|---|---|
| `claude mcp list` shows Connected | Claude Code 2.1.281 | pass | 0.97 | 1.11 |
| app-server `mcpServerStatus/list` names crapkit | Codex 0.156.1 | pass | 2.91 | 2.16 |
| `gemini mcp list` shows Connected | Gemini CLI 0.61.0 | pass | 2.77 | 3.19 |
| `opencode mcp list` shows connected | OpenCode 1.18.32 | pass | 4.18 | 4.25 |
| `copilot mcp get crapkit --json` | Copilot CLI 1.0.88 | pass | 5.07 | 5.62 |
| `cline mcp install crapkit` | Cline 3.0.65 | pass | 4.67 | 2.90 |
| `cline config mcp --json` | Cline 3.0.65 | pass | 3.56 | 3.88 |
| `cursor-agent mcp list-tools crapkit` lists get_next_item | Cursor agent 2026.09.23 | pass | 3.35 | 1.73 |
| `claude plugin install crapkit@crapkit` from a local marketplace | Claude Code 2.1.281 | pass | 0.43 | 0.31 |
| `crapkit doctor --plugin-root` | crapkit 0.8.0 | pass | 0.83 | 0.63 |
| `codex plugin add crapkit@crapkit` | Codex 0.156.1 | pass | 0.08 | 0.09 |
| app-server `hooks/list` returns the plugin's hooks | Codex 0.156.1 | pass | 0.93 | 0.35 |
| app-server `skills/list` returns the plugin's skills | Codex 0.156.1 | pass | 0.84 | 0.37 |

What the offline runs settle:

- None of these checks needs a login or the network. The Cursor agent lists
  crapkit's tools offline with no Cursor account.
- Every probe finished under 6 s offline, against the online budget of under 8 s.
- Codex still loads the plugin's `hooks/hooks.json` offline: `hooks/list`
  returns `crapkit@crapkit:hooks/hooks.json:post_tool_use:...` handlers.

Raw output: `logs/offline-1.log` and `logs/offline-2.log`, one line per probe:
`<probe>|<exit>|<ms>|<pass or FAIL>|<last line of output>`.
