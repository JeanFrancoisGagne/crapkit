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

## What crapkit spawns

Five things start a process, and all five come out of `crapkit.toml`. The shell
is the one that will run the command: `cmd.exe` on Windows, `sh` everywhere
else. When a `timeout_seconds` or a probe deadline expires, crapkit kills the
whole process tree, not just the shell it started (`taskkill /T` on Windows,
`killpg` on POSIX), so a suite cannot outlive the run that started it.

| What | When | What it runs |
| --- | --- | --- |
| Lane commands | `crapkit coverage` and `crapkit verify` | each `[[lane]] command`, in the repo root |
| Scoped tests | `crapkit test-scoped` | the `[crapkit.scoped_tests]` line for that scope |
| Mutation runs | `crapkit mutate` | `mutation_command`, once per mutant |
| Runner probes | `crapkit doctor` | `<first word of the lane> --version`, once per distinct word |
| The pytest-cov probe | `crapkit init` | `<the python that runs pytest> -c "import pytest_cov"` |

`crapkit override` runs a sixth, `alert_command`, and it is the only one that
gets data from your source: the override line reaches it on stdin, never
interpolated into the shell string, because function names are not shell-safe.

`mutate` writes mutated source. With one worker, the default, it writes into
your working tree and puts the original file back when the mutant finishes.
With `mutation_workers = N` it writes into the pool worktrees below instead. A
second `mutate` in the same repo finds the pool lock held and falls back to
throwaway worktrees under the system temp directory.

## What crapkit writes

Everything crapkit writes for itself lives under `.crapkit/` in the repo it
scores, which is why `init` adds that directory to `.gitignore`. The exceptions
are the three files you are meant to read and commit: `crapkit.toml`,
the ratchet TSV, and that `.gitignore` line.

| Under `.crapkit/` | What it is |
| --- | --- |
| `crap.sqlite` | the store: every scored run, its rows, its per-run rollups, and the override audit trail |
| `cov/` and `lane-*.log` | whatever your lanes write, plus the streamed log of each lane run |
| `churn-cache-v2.json`, `churn-log-v2.z` | the git churn walk, cached per format version |
| `coupling-cache-v1.json` | ranked coupling pairs, keyed on HEAD and the tracked set |
| `mutate-pool/` | one git worktree per mutation worker, each a full checkout of HEAD |

The pool is the part worth knowing about. Those worktrees are kept between runs
on purpose, because building them again costs 30 s on a large repo where
re-preparing them costs half a second. Nothing bounds their size: N workers cost
N checkouts of your repo, in your repo. `crapkit mutate --drop-pool` removes
them and their git registrations.

Nothing under `.crapkit/` is signed or checked for tampering. It is cache and
history, not a trust boundary. Anyone who can write there can write a scored
run, and they could already run the lane commands.
