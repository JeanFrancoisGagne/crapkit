# Resource use and cleanup

Crapkit starts work on demand. An idle MCP server waits on its input; it does not
scan repositories or run analysis in the background. A client can start one MCP
session per task, so several processes with live client parents can be expected.
Count active work, memory and CPU before treating every repeated process name as
an orphan.

## Analysis workers

Small, cached and single-chunk analysis runs in the calling process. Automatic
pool sizing balances worker startup against the available work, within the CPU
ceiling. Set `analysis_workers` to request a specific ceiling; pools never start
more workers than runnable chunks. Processes running as the same user coordinate
pool slots through operating-system locks.
They take available slots without waiting and use the serial path if none are
free. A busy host therefore avoids another full-size analysis pool without
putting small edits behind a shared queue.

`analysis_worker_budget` narrows the shared slot range. Use the same setting in
repos that need a common host ceiling. Different settings share numbered slots;
the largest active ceiling determines the possible range, rather than the sum
of all configured ceilings. This policy bounds pool workers, not every caller
or the workers started by an external test runner.

The default coordination directory is under the user's cache, separated by host.
`CRAPKIT_RESOURCE_DIR` selects another directory. Processes must use the same
directory to share a budget; separate directories create independent budgets.

`CRAPKIT_ANALYSIS_WORKERS` can lower the inherited per-call worker limit.
`CRAPKIT_ANALYSIS_MEMORY_MB` also reduces the pool using an estimate of 35 MB per
worker. It is a sizing hint, not a hard operating-system memory limit. Invalid
values retain the unset behavior. Use lane `env` settings to constrain pytest,
Node or other test tools that create their own workers.

`crapkit doctor --json` reports the effective CPU and worker policy, memory
estimate, log limit and test retention settings. The reported pool limit is an
upper bound; work sizing and slot availability can reduce it. These settings leave
the scoring algorithm and analysis version 10 unchanged. A package upgrade
rebuilds the versioned analysis cache automatically; no manual deletion is needed.

## Command lifetime

Lane, mutation, scoped-test and watch commands own the work they start. The
development test runner follows the same rule. Completion, timeout and
cancellation stop owned descendants before releasing output and checkout leases.
Mutation cancellation closes admission before joining workers, so interruption
cannot start another mutant suite.

Measurement locks coordinate commands for the same user and host, including
different repositories that target one absolute artifact. Stable files under
`~/.cache/crapkit/measurements/<host-id>` stay outside report directories that
test runners delete and recreate. `TEMP`, `TMP` and `CRAPKIT_RESOURCE_DIR` do not
change this domain. Keep these small lease files as coordination state, not idle
test evidence.

The old adjacent locks could coordinate other users or hosts through a shared
filesystem. The new local domain does not preserve that behavior; those writers
need external serialization or distinct artifacts. Do not overlap old-version
and new-version measurements during an upgrade because their lock locations
differ.

MCP keeps reading protocol input while a tool runs. Cancellation stops that
request's CLI process tree. Input EOF closes the session and stops active work;
the caller must keep stdin open until it has read the replies it needs. Control
messages remain responsive during a tool call. MCP exposes the same twelve tools.
Each connection runs one tool call at a time and returns a retry message for an
overlapping tool call, keeping active work bounded.

Windows uses Job Objects. POSIX uses owned process groups and cleanup guardians.
A command that deliberately detaches into an unrelated session is outside the
ordinary inherited process-group contract. Start persistent services separately
from a command whose completion is supposed to release its resources.

## Logs and retained evidence

Each lane keeps its current log and one previous chunk at `.log.1`. The default
`log_max_bytes = 16777216` bounds each file to 16 MiB, up to 32 MiB per lane.
Small logs retain their bytes. Rotation keeps the newest output, including final
failure details; no-progress timeouts count bytes received across rotations.
Set `log_max_bytes = 0` when retaining the complete unbounded log is required.

The development runner marks default `.crapkit/test-runs/run-*` directories.
`test_retention_days = 7` and `test_retention_count = 10` expire idle runs when
either limit is exceeded. Zero disables that limit; setting both to zero keeps
all recognized runs. Explicit `--output` destinations remain caller-managed.
Explicitly selecting an existing retained run, or a directory inside it, removes
its retention receipt under the same lease and makes that run caller-managed.
Retention runs once at startup, outside individual tests and analysis calls.

Mutation retains only the requested number of canonical pool workers on reuse.
Concurrent temporary worktrees carry versioned ownership receipts. Recovery
checks repository identity and acquires the run's exclusive lease before
removing an abandoned checkout. Active runs, redirected paths, unknown evidence
and older unmarked temporary directories are preserved.

```sh
crapkit clean --dry-run --json
crapkit clean --json
crapkit mutate --drop-pool
```

`clean` applies configured test retention and recovers abandoned temporary
mutation runs. It does not remove intentional mutation pools, scored history or
ratchet files. Small stable lease files remain after cleanup so concurrent
processes continue to lock the same file. Repeating cleanup is safe.
