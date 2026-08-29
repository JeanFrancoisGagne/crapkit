# Security

## Supported versions

Fixes land on the latest minor only. There are no maintenance branches.

| Version | Supported |
| ------- | --------- |
| 0.4.x   | Yes       |
| < 0.4   | No. Upgrade. |

## Reporting a vulnerability

Use GitHub private vulnerability reporting:
[open an advisory](https://github.com/JeanFrancoisGagne/crapkit/security/advisories/new).
That thread is visible to the maintainers and to you, nobody else. Do not open a
public issue for a security bug.

You get a first reply within a week. If the report holds, the fix ships in the
next patch release and the advisory credits you unless you would rather it did not.

## What crapkit touches

- **It runs your own commands.** Every lane `command` in `crapkit.toml` is executed as you, in your shell, in your repo. A hostile `crapkit.toml` is a hostile shell script. Read one before running crapkit in a repo you did not write.
- **It never phones home.** No telemetry, no update check, no network call anywhere in the analysis path. Scoring works with the interface down.
- **The MCP server is stdio only.** `crapkit mcp` speaks newline-delimited JSON-RPC on stdin and stdout. It opens no port and accepts no remote connection. It reaches exactly what the agent that spawned it can already reach.
