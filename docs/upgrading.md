# Upgrading Crapkit

Upgrade the CLI with the installer that owns it, then refresh the measurements in
each repository. Saved runs describe the rules and source they measured; an upgrade
does not turn those runs into measurements of the new reader.

| Installation | Upgrade command |
|---|---|
| pip in the active environment | `python -m pip install --upgrade crapkit` |
| pip with the Python coverage extra | `python -m pip install --upgrade "crapkit[py]"` |
| uv tool | `uv tool upgrade crapkit` |

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

## Measure before changing marks

0.8.0 moves the reader to analysis version 11, so a marks file stamped under 10
needs one re-seed; [analysis version 11](#analysis-version-11) says what moved.
The package upgrade rebuilds the versioned analysis cache automatically, and the
first `inventory` or `coverage` after it analyzes every file again. That run's
[twin-key note](ratchet.md#twins-one-name-several-functions) names the first five
files that give one name to several functions and ends with `... and N more file(s)
define a name more than once`. Restart each client's MCP session after upgrading so
its running server uses the new code.

Keep a copy of the committed ratchet and its diff before an upgrade. In each repo:

```sh
crapkit doctor
crapkit coverage --export .crapkit/current-functions.tsv
```

Resolve doctor failures, then inspect the fresh run. `coverage` writes a measurement
without applying the ratchet. Compare the saved marks with that measurement before
running `ratchet seed`, and finish with `crapkit verify` after reviewing and committing
any mark changes.

| What changed | Required action |
|---|---|
| Analysis or lizard stamp | Follow the [metric stamp rules](ratchet.md#the-metric-stamp). Comparisons refuse incompatible stamps. |
| Function membership or same-line identity | Review the [saved-mark mapping](ratchet.md#reconcile-saved-marks) before changing keys or stamps. |
| Coverage or JUnit producer | Run a fresh lane and resolve [artifact admission errors](lanes.md#a-junit-that-says-the-run-did-not-finish). |
| Shared exports or portable baselines | Upgrade readers before writing [encoded records](portable-records.md) for them. |

### Analysis version 11

0.8.0 reads Python defs in five new ways. Each one changes some functions' names or
numbers, and the stamp records the rules, so every marks file re-seeds once:

- A def with a PEP 695 type parameter list is named by its name. `def f[T](a: int):`
  read `]( a : int )`, and every generic def in a file that took the same parameters
  collided on that key. A file refused for a generic def with no annotated parameter
  now scores.
- A def nested three or more deep names each enclosing def once: `a.b.c( x )`, where
  it read `a.a.b.c( x )`.
- A def whose body sits on its colon line, such as `def one(x): return x`, is listed
  as its own function. Before, no report showed it and the lines after it counted
  toward it. A def that encloses one can gain conditions it had lost. A same-named
  def after it moves to the next twin key: where a one-line `f( x )` sits above a
  multi-line `f( x )`, the multi-line def is now `f( x )#2`, a mark recorded under
  `f( x )` binds the one-line def, and `ratchet seed` marks `f( x )#2` if it is over
  its ceiling.
- Cognitive complexity and nesting count a def's body from the colon that ends its
  signature, so a one-line body counts and a signature's continuation lines do not.
- A Python file that ends inside a def's signature is refused and names that def;
  before, only a nested def was, and the file scored without it.

The one-line change moves `ccn` for the def and for the defs whose lines it used to
take, and a generic def with a constrained bound and a line break after a default,
which read two lines at ccn 1, now reads its whole body. A newly listed def, or an
enclosing def that read short before, can be over its ceiling and fails the gate the
next time its file changes. Under a coverage.py lane a def whose body starts on the
line its signature ends, a one-line def or a body on the last line of a signature
that spans several lines, scores as uncovered with remedy `split-lines`, because
coverage.py reads that body as the `def` statement that runs at import.

Version 11 also reads JavaScript and TypeScript template literals whole. lizard ended
a template at the first backtick inside it, so a template nested in another's `${...}`,
an escaped backtick, or a brace inside a string within `${...}` hid every function
after it in the file: each was folded into the function around it or not listed at
all. Those functions are now listed, and the function that held them reads only its
own lines and branches. A newly listed function can be over its ceiling and fails the
gate the next time its file changes. A function written inside `${...}` is still not
listed.

After upgrading, in each repo:

```sh
crapkit coverage
crapkit ratchet prune
crapkit ratchet seed
```

`coverage` measures under version 11, and `ratchet prune` drops the marks left under
the old names. `ratchet seed` then stamps the marks with the metric of the run it
reads, so a seed from a run 0.7.x measured keeps the old stamp. Prune goes first
because a marks file with no `# crapkit-keys=1` line keeps the old key format while
any mark names a function the run lacks, and that format cannot key a function that
shares its start line with another, so seed refuses to add one. When a failed verify
pins the baseline, seed and prune both read the pinned run: pass the new run's id to
each, `crapkit ratchet prune --baseline N` then `crapkit ratchet seed --baseline N`;
their lines and verify's refusal name it. Review the diff and commit it before the
next `crapkit verify`.

### Analysis version 10

Analysis version 10, in 0.7.0, replaced version 9 from 0.6.0. It separates
JavaScript and TypeScript expression callbacks that older readers missed. Current
rows also carry an `occurrence` for functions sharing a start line. These changes can
shift anonymous ordinals even when functions begin on different lines. Fresh
coverage cannot prove which old function owned a mark. A refusal asks for that
review; replacing a stamp alone does not complete it.

Unambiguous legacy keys remain readable. Ambiguous groups and anonymous JS/TS marks
without current reader proof require the procedure in
[same-line function identity](ratchet.md#same-line-function-identity). Existing claims
without that proof continue to hold their whole name group until released,
removed by an explicit `runs prune` under its age rule, or the whole group becomes
healthy. Ordinary queue reads do not expire claims.

## Saved state and command behavior

Keep `.crapkit/crap.sqlite`: it holds run history, test baselines and override audits.
Commands manage disposable analysis and history caches themselves. Use
`crapkit runs list` to inspect the trusted baseline; a failed verify still prevents
a newer coverage run from silently becoming the baseline.

Automatic reuse requires the same clean HEAD and unchanged configuration,
environment and artifact bytes. Since 0.8.0 a lane can list the paths its command
reads as `inputs`, and `--reuse-unchanged` then reuses it across commits while
nothing under those paths, its lane table or its `env` changed. The reuse proof
covers that field, so the first `--reuse-unchanged` after upgrading to 0.8.0 reruns
every lane once, and an older stamp without the proof reruns its lane; each rerun
prints `lane 'x': rerunning:` and the reason. Ignored files, installed dependencies and
external services remain outside this proof. See
[artifact reuse](lanes.md#reusing-artifacts) before choosing an explicit
saved-artifact read.

`test_retention_days` and `test_retention_count` are ignored. `crapkit doctor` warns
once for each key a config sets; delete them. Test evidence retention is now the
development runner's `--retention-days` and `--retention-count`, and
`crapkit clean` recovers abandoned mutation checkouts only.

Every mutation worker uses a detached worktree, including a single worker.
The normal pool is retained; concurrent callers use temporary worktrees that
cleanup removes. Budget disk space for the pool and use
`crapkit mutate --drop-pool` to reclaim it.
[Mutation worktrees](configuration.md#mutation-worktrees) owns the input, link,
concurrency and cleanup rules. [Command cleanup](lanes.md#the-kill-takes-the-whole-process-tree)
describes Windows Jobs and POSIX process groups. These are process-lifetime controls,
not a sandbox for configured test commands.

Git filenames retain their literal identity through scoring and output. Coverage
paths still have to name the measured tree. Use the documented
[CLI path rules](configuration.md#file-paths-and-root-discovery) and
[portable record reader](portable-records.md) when automating around exports.
JSON stays at `schema: 1`; consumers must accept added fields.

## Missing values that 0.8.1 names

Before 0.8.1 some commands read a value nobody measured as zero, empty or passing.
0.8.1 names each one instead, and three of those changes can move a CI job's exit code.
The mutate counts below keep their meaning and gain two fields.

**Files no reader could read.** A file crapkit's readers refuse, such as a TypeScript
expression-arrow body with `<` before a comma, still scores as zero functions in a run.
Every gate read those zero records as nothing over the ceiling, so a function beside
the refused construct passed the commit hook, `rescore --gate`, `check_gate` and verify.
From 0.8.1 each of them exits 6 on such a file when a change touches it, and
`claude-hook` exits 2 after the edit. Before you upgrade the hook or CI, run
`crapkit coverage` and read the files it names in
`crapkit: N file(s) could not be tokenized; ...` (or the WARN lines of
`crapkit doctor`), then for each one change what the reason names so a reader can parse
it, or list it under `[exclude]` globs in `crapkit.toml` to leave it ungated. A file no
change touches blocks nothing.

**verify over a junit it cannot read.** `verify --reuse-artifacts` over a lane that
declares a `results_artifact` it finds missing or unreadable passed at exit 0 and stored
a trusted run. It now exits 5 and stores no run, as a real run of that lane does, and
the refusal ends `run verify without --reuse-artifacts so the lane writes it again`. A
CI job whose `verify --reuse-artifacts` found no readable junit for such a lane now
fails where it passed. `coverage --reuse-artifacts` over the same junit still warns and
scores on. A lane that declares no `results_artifact` still passes, with a stderr line
and the lane under `lanes_without_results` in `verify --json`.

**Shallow clones.** A depth-1 checkout holds one commit, so churn counts one commit per
file, every ratchet mark reads 0 days old and no repayment shows.
`ratchet report --enforce` with `debt_max_age_months` or `repayment_min_per_30d` set now
refuses there with exit 4, ending
`set fetch-depth: 0 on the checkout or run git fetch --unshallow`. A CI job that ran it
on a default `actions/checkout` judged its policy on those zeros: an age limit passed
and a repayment quota failed. Set `fetch-depth: 0` on the checkout. `worklist`,
`next-item`, `brief` and `ratchet report` without `--enforce` still answer, print one
stderr line that names what they counted, such as
`warning: churn counts read only the commits this clone holds` from `worklist`, and add
`shallow: true` to their JSON (`false` in a full clone).

**Mutants with no test verdict.** `mutate` counted a mutant whose suite exited 5, which
means no test ran, as killed, and printed nothing else. It still counts as killed, so
`killed`, `survived`, `mutants` and the rate read as they did in 0.8.0. The progress
line now says `no verdict: the suite ran no test (exit 5), counted killed`, the text
summary adds `no verdict: N of the K killed ran no test (exit 5), so no test caught
them`, and `--json` adds `no_verdict`, a count inside `killed`. A mutant whose suite
timed out is counted the same way under `timed_out`. A script that wants the rate over
the mutants a test judged divides `killed - no_verdict` by `mutants - no_verdict`. JSON
schema 2, in a later release, takes no-verdict mutants out of `killed` itself.

**Artifact stamps from 0.4.15 or older.** The record that stops `--reuse-artifacts` from
scoring the artifact a failed lane left behind lives in `.crapkit/artifacts.json`, and
crapkit writes it from 0.5.0 on. A stamps file written by 0.4.15 or older holds no such
record, and nothing else on disk says the last attempt failed, so the first
`coverage --reuse-artifacts` after the upgrade scores that leftover as a good run. After
upgrading from 0.4.15 or older, run `crapkit coverage` once without `--reuse-artifacts`
before any reuse. Every lane runs: one that works writes its artifact and stamp again, and
one that still writes nothing exits 5 and records the refusal the old release never
wrote, so the next reuse refuses it with `wrote no artifact on its last attempt`.

## Plugin and MCP clients

After upgrading the intended CLI, refresh Claude Code's marketplace before updating
its user-scope plugin:

```sh
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
crapkit doctor --plugin-root
```

Restart existing Claude Code sessions to apply the plugin update. The doctor check
compares the installed plugin with the `crapkit` launcher on PATH. It does not reload
an existing session. A failed, malformed or undecodable launcher probe is a failure,
not a version match.

For an installed Codex plugin, refresh its marketplace and install the current copy:

```sh
codex plugin marketplace upgrade crapkit
codex plugin add crapkit@crapkit
codex plugin list --marketplace crapkit --json
crapkit doctor --plugin-root PATH
```

Use the installed Codex plugin directory for `PATH`, not the marketplace's source
checkout. In the default cache this is
`~/.codex/plugins/cache/crapkit/crapkit/VERSION`, using the installed version from
the listing. With no explicit path, doctor checks Claude Code's cache instead.
Use the three skills and MCP server in Codex. The advisory hook instructions in
the README configure Claude Code's PostToolUse event. Start a new Codex task to
load updated plugin skills and tools.

Start fresh MCP sessions after upgrading so their server uses the installed code. Other
MCP clients use the [stdio setup](agent-json.md#mcp-server); skill copies and custom
hook entries need their own update. Run packet commands as supplied, in the
environment that owns the intended CLI, to retain literal arguments and exit codes.

## Windows launcher locks

A running `crapkit.exe mcp` can hold the console launcher open. An upgrade then
fails with Windows error 32 even if some package files were already updated.

1. Stop the Crapkit MCP server or the agent session that owns it.
2. Rerun the same upgrade command and require a successful installer result.
3. Check `crapkit --version`, restart the client, and check plugin compatibility.

Use the installer to repair the launcher instead of copying executables between
environments. The CLI version alone does not prove an interrupted install finished.

## Release evidence

The [implementation report](architecture/2026-09-07-implementation/REPORT.md)
records complete Windows source and Linux installed-wheel verification, independent
reviews and repeatable performance probes. Its benchmark tables distinguish
synthetic duplication input, fixture setup and a fixed unit subset from complete
suite runs. They do not claim a whole-suite speedup. Hosted CI dispatch and macOS
runtime execution were outside that local verification.
