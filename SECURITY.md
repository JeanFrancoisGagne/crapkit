# Security

## Supported versions

Fixes land on the latest minor only. There are no maintenance branches.

<!-- generated:version-support -->
| Version | Supported |
| --- | --- |
| 0.8.x | Yes |
| < 0.8 | No. Upgrade. |
<!-- /generated:version-support -->

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

Configured commands run through `cmd.exe` on Windows and `sh` on POSIX,
with the working directory and environment selected by the operation.
crapkit also starts Git and internal process owners; the table below describes
the commands a project config supplies.

crapkit starts Git, and the other programs it runs on its own account (the
`claude` that `doctor` asks for its version, and on Windows `taskkill`, taken
from the system directory first), from an absolute PATH entry only. It never
starts one from the working directory or through an empty or relative PATH
entry, so a `git.exe` a repository holds is never run. `doctor` asks a launcher
it found for its version from that launcher's own folder, with `cmd.exe`'s
search of the current directory off, so an interpreter the launcher starts by
name, such as npm's `node`, never comes from the repository either. The Python
processes crapkit starts of itself (each MCP tool call, the rescore that
`crapkit watch` runs, the process owner and the analysis workers) start with
`-P` or with `PYTHONSAFEPATH=1` in their environment, so Python never puts
their working directory first on `sys.path` and a `crapkit.py` or
`json.py` a repository holds never runs in place of crapkit's own modules or the
standard library. The lines the docs give a reader, a git hook or a merge driver
to run in a repository's root, and the next step crapkit prints when no `crapkit`
command is on PATH, spell `python -P -m crapkit` for the same reason. A hook or
merge driver copied from an earlier page runs `python -m crapkit`, which runs a
`crapkit.py` at the repository's root in place of crapkit: add `-P` to it.
`python -m pip` reads `sys.path` the same way, so the pip lines crapkit prints
(the pytest-cov, coverage.py and lizard installs, and `doctor`'s upgrade and
reinstall of the CLI) spell `<python> -P -m pip install`, and the Action installs
crapkit with `python -P -m pip install` in the consumer's checkout: a `pip.py` at
the repository's root never runs in place of pip. The install, upgrade and
removal lines in the README, the handbook and the upgrade guide keep
`python -m pip`: crapkit neither prints nor runs them, and the page sets no
directory to run them in. A configured command is yours, and it finds its
programs the way its shell does.

| What | When | What it runs |
| --- | --- | --- |
| Lane commands | `crapkit coverage` and `crapkit verify` | each lane's `command`, using its configured `cwd` and `env` |
| Scoped tests | `crapkit test-scoped` | the scope's test command, in the project root with the caller's environment |
| Mutation runs | `crapkit mutate` | `mutation_command` for the baseline and each mutant, in an isolated worktree |
| Runner probes | `crapkit doctor` | version probes for the executables named by lane commands |
| The pytest-cov probe | `crapkit init` | an import check in the interpreter selected for the pytest lane |

Lane, mutation and probe commands own their descendants through
Windows Jobs or POSIX process groups. Command completion, timeout, interruption
and caller death stop the owned processes before releasing their resources.
Untimed commands have no deadline. On POSIX, commands must keep their inherited
process group; a daemon that explicitly calls `setsid` leaves this ownership.
This is process cleanup, not a sandbox for hostile commands.

Scoped tests have no configured timeout. They own their command descendants
through the same cleanup used by lanes.

An audited `crapkit verify --override REASON`, or a hook override through
`CRAPKIT_OVERRIDE_REASON`, runs `alert_command` with the override record on
stdin. `crapkit digest --alert` runs it with the digest body on stdin when
the digest has changes to report. Both use a separate shell subprocess in the
project root without a configured timeout. Source-derived names are never
interpolated into the command. A nonzero alert exit refuses the operation.

`mutate` writes mutated source in a detached worktree at every worker count,
including the default of one worker. It copies dirty, untracked and deleted
inputs into that worktree before applying mutants. A second `mutate` in the
same repo finds the pool lock held and uses temporary worktrees under
`.crapkit/mutate-tmp`. Each run holds its lease until owned commands stop.
The user's working tree stays unchanged.

[Mutation isolation tests](tests/e2e/test_mutate_e2e.py) exercise this behavior
through the CLI. [Pool tests](tests/unit/test_mutate_pool.py) check preparation
and restoration.

## What crapkit writes

Managed run state lives under `.crapkit/` in the project being scored;
`init` adds that directory to `.gitignore`. The config, ratchet TSV and
`.gitignore` are intended to be reviewed and committed. Commands that export a
baseline or report write to the output path you supply, and lane commands can
write to their own configured destinations.

| Under `.crapkit/` | What it is |
| --- | --- |
| `crap.sqlite` | the store: every scored run, its rows, its per-run rollups, and the override audit trail |
| `cov/` and `lane-*.log` | where the lanes `init` writes put their artifacts, plus the streamed log of each lane run. A lane you write can point its artifact anywhere |
| `churn-cache-v3.json`, `churn-commits-v1.json`, `churn-log-v3.z` | the git churn walk, cached per format version |
| `coupling-cache-v2.json` | ranked coupling pairs at the default thresholds, keyed on HEAD, the churn window, the UTC date, the path format and a digest of the tracked set |
| `mutate-pool/` | one git worktree per mutation worker, each a full checkout of HEAD |

Mutation worktrees remain between runs so later runs can reuse their checkouts.
Their disk cost grows with the worker count and repository size. Run
`crapkit mutate --drop-pool` to remove the pool and its Git registrations;
an active pool refuses removal. Input links and reparse points are refused
before a worker can write through them to another tree.

The local store and caches are not authenticated. Parsing, provenance and
content checks catch stale or malformed data; they do not establish a security
boundary against someone who can write to the project. That person can also
change the commands the project runs.
