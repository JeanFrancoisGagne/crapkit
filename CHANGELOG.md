# Changelog

## 0.4.5 — unreleased

An audit of 0.4.4 (six lenses, every finding reproduced twice) plus issues #24
and #25. No new capability.

### The guard reads cmd.exe the way cmd.exe does
0.4.4 taught the lane guard cmd.exe's quoting, with two gaps: a quote that opens
mid-token (`--cov-report=json:"a b\py.json"`) was two tokens here and one
argument to cmd.exe, so a good lane was refused; a caret escape (`-k ^"not slow^"`)
stayed in the token and split the value. The cmd.exe reading is now a character
walk that toggles on every quote and honours `^` outside quoted runs, checked
against real `cmd.exe` argv on thirty command shapes. A chained command
(`cd tests && python -m pytest --cov ...`, `... --cov && echo done`) is read one
argv per `&&`/`||`/`&`/`|` segment and every segment that runs the runner is
checked, so a second narrowing run after the operator is still refused and a
refusal never names a word from the next command. An empty quoted argument
(`-k ""`) stays an empty argument instead of shifting the next path onto the
flag; a quoted or caret-escaped operator is a word, not a separator; words break
on space, tab and line endings only, as cmd.exe and sh do, so a pasted
non-breaking space no longer splits a value; redirections (`> nul`, `2>&1`) are
the shell's and never a positional; on sh a trailing `;` ends the command. The
vitest guard licenses 25 value-taking options (`--workspace`, `--diff`,
`--snapshotEnvironment`, `--coverage.extension`, `--typecheck.tsconfig` joined
the list) and the docs list is generated from the set.

### The pre-commit gate works below the git top (#24)
The 0.4.4 churn fix left the gate reading `git diff --cached` from the git top,
so under a nested root staged paths matched no scope and a function at twice the
ceiling committed with a warning. Every git spawn now runs with
`diff.relative=true` and cat-file requests use `:./path`, which also fixes the
nine siblings that joined top-relative paths against root-relative rows: verify's
changed files, `rescore --gate`, lane reuse (which could republish a stale
artifact's score), `mutate` (which found targets and mutated nothing), the
ratchet's rename follow, and the per-edit advisory's own diff. A staged file
above the crapkit root is outside the diff by design and no longer named.

### A worktree add killed by a peer is retried once (#25)
`mutate` adds its worker worktrees in parallel; git's add enumerates the
existing `.git/worktrees/*` entries and dies reading a `commondir` a peer is
still building (one in about a thousand adds at four workers on Windows, seen on
CI). `worktree_add` retries once after 50 ms when the message names
`worktrees/` and `commondir`, whatever the git dir is called.

### Timeouts that bound the wall clock
`init`'s pytest-cov probe and `mutate`'s per-mutant timeout both used
`capture_output` under `shell=True`; on Windows the kill hit cmd.exe and `run()`
then waited on pipes the grandchild still held, so a 15 s timeout returned after
29 s and a looping mutant was never cut. Both now run through one bounded
spawn (`procs.run_bounded`): the command starts in its own process group and a
deadline kills the whole tree (`taskkill /T` on Windows, `killpg` on POSIX) and
waits for it, so no orphan suite keeps running after `mutate` gives up on a
mutant or a lane's `timeout_seconds` expires; the lane log still streams as
before.

### init on Windows
A PATH holding only the `py` launcher got a lane naming `python3`, which the
first `coverage` could not run: `py` is now in the fallback chain. The Store
`python.exe` alias (exit 9009, "Python was not found") gets its own note
naming the interpreter cmd.exe cannot run, and `doctor` now FAILs a lane whose
first word will not start instead of calling the repo clean. The
`pip install "crapkit[py]"` line uses double quotes in the note and the docs:
single quotes do not survive cmd.exe.

### Churn and coupling below the git top
`coupling`, `brief` and `worklist --batches` decode git's `core.quotePath`
quoting before joining paths, so a non-ASCII path is no longer a fake row;
`coupling` drops pairs naming a path git no longer tracks; the churn caches
carry their format in the file name (`churn-cache-v2.json`, `churn-log-v2.z`),
so a 0.4.3 sharing the repo keeps its own caches instead of both rebuilding on
every run; a warm 0.4.4 cache is adopted once and its file removed rather than
orphaned. `.git` is found by walking up from the root (doctor's commit-graph
check included), so the HEAD fast path fires below the top; `config_value`
asks git for the repo's own setting and no longer reads the `diff.relative`
flag crapkit injects into every spawn.

### Ten defects an architecture review reproduced
A review of the 0.4.5 tree proposed 37 deepening refactors; two skeptics per
candidate refuted 36 of them (every seam they asked for already existed) and
reproduced these defects on the way, each now fixed behind its existing seam:
a dirty non-ASCII file was invisible to lane reuse (git quoted it, `ls-files`
did not; every git spawn now runs with `core.quotePath=false`); `worklist`
and `next-item` could describe different runs, and `ratchet seed`/`prune`
could sign marks off a run `verify` had refused (both now pick the run their
peer picks); `explain PATH LINE` resolves a start line as `brief` does; scope
ownership was decided three ways (first-declared in scoring, longest-prefix in
test-scoped, prefix-only for lane reuse) and the packet mixed two of them; one
predicate in `universe` now answers everyone with deepest-declared-path
winning, so a repo with NESTED scopes may see files move between scopes on
its next scan; a file-valued scope path (`paths = ["core/hot.py"]`) marks its
lane changed; `doctor` reads a lane command with the shell that runs it (a
quoted interpreter, a runner after `&&`, a path inside a quoted `-k`); the
pytest-cov probe asks the python that runs pytest, so `coverage run -m pytest`
is left alone; shell cognitive complexity nests (`fi`/`done`/`esac` close a
level; a 4-deep `if` reads 10 like every other language, not 4), which is
analysis version 8; `_scored_run` returns named fields; `inventory` no longer
dies when a tracked file is missing from the working tree; `verify` no longer
dies when a lane lost its test count. `tests/unit` now drives `verify` and
`coverage` in process (verifying 34% -> 100%, scoring 43% -> 99% statement
coverage from the unit suite alone), and `tests/e2e` shares one CLI runner in
`conftest.py` and runs in about 1m30 with `-n 8`. The review left one
structural item open: `discover.py` (384 lines, no importer since birth) is
either wired into the packet or removed in a later release.

### Seven field fixes from the CodingGraph pilot (#26 through #31, #37)
`verify --baseline ID` naming a run that exists but cannot serve now says which
run it is, why (a failed verify, a hook run, a partial run, an inventory run)
and which runs can serve, instead of the empty-store line (#27). `verify`'s
gate exempts a touched function whose fresh CRAP sits at or under its ratchet
mark, the rule `rescore --gate` already applied, so an edit inside signed debt
no longer passes the commit gates and then meets exit 6 (#29); exit 7 stays for
a regression the diff never touched. A lane that wrote no test counts this run
gets one line naming the gap instead of a KeyError (#30). The twin-key note is
printed by the parent after the pool returns, never from a worker whose stderr
never saw the UTF-8 reconfigure, so on Windows its em dash no longer lands as a
lone cp1252 byte (#31). `doctor` WARNs on a coveragepy or istanbul lane that
declares no `results_artifact`, naming the two checks that cannot run for it,
and `init` writes `--junitxml` plus `results_artifact` on the lanes it detects
(#26). `doctor --plugin-root` takes the plugin root or any directory above it
and, with no path, reads Claude Code's plugin directory itself (#28). The packet
spells its commands as the console script (`crapkit rescore ... --gate`), the
form the docs promise and the one that resolves from a venv on Windows (#37).

### Performance, measured at consumer scale and refereed
A benchmark of every subsystem on a 31,459-file consumer (152k functions, 41,544
marks, 541 MB of lane artifacts, 72,653 commits) produced 76 improvement
candidates; skeptics re-implemented and re-measured each one, killed most, and
these six survived and shipped, each with its A/B on that corpus:
`coupling`, `worklist --batches` and `brief` stop re-pairing the churn log on
every warm run (a ranked-pairs cache beside the churn caches, keyed on HEAD plus
a digest of the tracked set; off-default thresholds bypass it): warm `coupling`
1.05 s -> 0.11 s, batches -62%, single `brief` -25%. `brief --batch N` shingles
the snapshot once instead of once per packet: batch 5 in 11.8 s -> 5.2 s, output
byte-identical (an on-disk shingle cache was refuted outright: shingles are
built on Python's per-process randomized hash). `doctor` probes each distinct
lane runner once, not once per lane: 7.5 s -> 1.4 s on 14 lanes over 2 runners.
`trend` and `report` read per-run rollups (new `run_rollup` table, filled once
per run, pruned with its run) instead of rescanning 4.3 M rows: `trend`
4.58 s -> 0.04 s warm, `report` -76%. `verify` reads each istanbul artifact once
for coverage, dead lines and its digest together, and skips the artifact walk on
an empty diff: 25.5 s -> 18.9 s (peak +55 MB, all digests byte-identical).
`mutate` keeps its worker worktrees under `.crapkit/mutate-pool` and re-prepares
them per run (30.6 s -> 0.46 s of setup on the big tree; `--drop-pool` reclaims
the disk; single-worker runs are untouched). Also refereed and REJECTED, so
nobody rebuilds them: skipping verify when HEAD and dirty names are unchanged
(the key cannot see a second edit to an already-dirty file), serving MCP tool
calls from a kept process (stale `source` breaks the packet contract), parallel
git date slices for the churn walk, and a faster JSON decoder.

### Docs
A section on running crapkit with its root below the repo top; the vitest guard
page lists every option whose value it licenses, pinned by a test; the
`crapkit-recover` skill routes the pytest half of "no coverage provider" to the
pytest docs.

## 0.4.4 — 2026-08-29

Three field fixes from @nicolaschapados (PR #23) against a real pytest/uv project,
plus the Windows half of the first one. No new capability.

### The lane guard reads a command line like the shell does
Lane commands run under `shell=True`, but both lane lints tokenized them with a
whitespace split. `python -m pytest -m "not live and not perf" --cov ...` was
refused with "positional argument 'live' narrows a full-suite coverage run" —
an argument the shell never hands pytest — and on the istanbul side a QUOTED
positional filter slipped past the guard, because the trailing quote defeated
the suffix check. Both lints now read the command the way the shell that runs
it will: sh on POSIX, and on Windows cmd.exe, where `'` is an ordinary
character and `\` a path separator. So `tests\unit` still reads as the path it
is, and a single-quoted value is refused on Windows with a hint to write it in
double quotes: cmd.exe would hand pytest five words and the lane would write no
artifact. A command the shell would refuse (an unbalanced quote) falls back to
the whitespace read instead of failing config load. The vitest guard learned the
flags whose value can end in a source suffix (`--coverage.exclude`,
`--coverage.include`, `--setupFiles`, `--globalSetup`, `-t`, ...), so a quoted
glob after one is a value, not a filter. Refusals name the token as written.

### A crapkit root below the repo top no longer reads as all-dormant
Scored rows are `git ls-files` paths, relative to the crapkit root; the churn
log came from `git log --name-only`, whose paths are relative to the repo top.
With the root one directory down (a monorepo member, or a project nested
inside a linked worktree's checkout) every churn lookup missed, and `worklist`
filed the entire corpus under dormant ("0 active, 215 dormant" on a repo with
90 commits that week). The churn log now runs with `--relative`, so its paths
join against the rows everywhere — worklist, next-item, brief and coupling
alike — and both churn caches carry a format marker that retires maps laid
down with top-relative paths.

### The first-run pytest-cov trap is named at init, not after the suite
The generated py lane runs `pytest --cov`, and the `--cov` flags come from
pytest-cov — a package of the repo's own interpreter, which a dependency on
crapkit could never guarantee (a pipx or uv-tool install shares nothing with
the suite's venv). `init` now probes the python its lane will run and prints
the install command when `pytest_cov` is not importable, a new `crapkit[py]`
extra pulls the plugin alongside crapkit for same-venv installs, and
`coverage`'s exit-5 hint stays as the last resort. The probe runs through the
same shell as the lane, so a bare `python` resolves to the interpreter the lane
will get, and only a lane init actually wrote is probed: a TypeScript repo whose
`pyproject.toml` holds ruff config gets no note about a suite it has no lane for.

## 0.4.3 — 2026-08-29

Fixes from a second consumer repo's field reports (issues #1, #14–#19, #21, #22).
No new capability; one key format grows, backward compatibly.

### A run nobody finished is not a measurement (#21, #16)
A lane's junit is now read as a trust check. pytest-xdist does not reschedule a
crashed worker's queue; on a 15,300-test lane one dead worker left 4,626 tests
unexecuted while coverage.py still wrote its JSON, and crapkit recorded a full
baseline. A junit carrying `worker 'gwN' crashed` or a session-level error now
fails the lane at exit 5 like a missing artifact, and `coverage` warns when a
lane's test count drops more than 10% below the last trusted run. `ratchet seed`
and `prune` now share `verify`'s trust rule: a failed verify never supplies the
scores they read, and the output line says which run was skipped.

### A measurement that bounced is not an improvement (#15)
`verify` tightens marks on a clean pass, which turned nondeterministic coverage
into a mark oscillator (20.0 → 72.0 → 20.0 on an unchanged tree). It now holds any
mark whose CRAP moved by more than `tighten_max_jump` (default 2.0) against the same
commit's previous scored run, printing one line per held mark; `--no-tighten` is
the blunt escape.

### Same-named functions each get their own key (#17)
Several functions with one name in one file (dataclass `__post_init__`s, C `#ifdef`
forks) shared a single ratchet/gate key, so only the last was marked or gated. The
key now carries a file-order ordinal: the first twin keeps the bare name, then
`name#2`, `name#3`. Existing marks stay valid as twin #1; no rewrite needed.

### The lane guard reads a command line like pytest does (#19, #22)
`-n 8`, `-o timeout=300`, `-p no:randomly` no longer fail as "narrowing
positionals"; the guard knows which options take a value, treats `key=value` as
never a path, and the refusal message names the attached-value rewrite.

### `duplication` skips a closure and the factory around it (#1)
Nesting pairs scored 1.0 by construction and drowned the report (43 of 43 pairs on
the reporting repo). They are dropped; kept pairs carry a `contained` flag.

### Plugin and docs (#14, #18)
`plugin/.mcp.json` spawns the `crapkit` console script, the same rule the hooks
use, so a `uv tool` install no longer gets a dead MCP server. The install docs gain
an "Upgrading on Windows" note: a live MCP server holds the launcher exe, `uv tool
upgrade` fails on the copy, and the rename-aside remedy.

## 0.4.2 — 2026-08-29

Fixes from a fresh-user verification pass: five simulated strangers followed the
published docs verbatim, and these are the places the tool or the docs lied.

### cc-only repos can follow the 60-second start
A repo whose languages all score on complexity alone (Go, Rust, shell, PowerShell,
Swift, the C family, Java, Zig, Objective-C) dead-ended at `crapkit coverage`
("no [[lane]] to run"). `init` now writes `coverage_optional = true` on every scope that
cannot have a coverage lane, `coverage` writes a real scored run for such repos, and
`worklist`, `next-item`, `rescore --gate`, `ratchet seed`, and `verify` all accept it.
A mixed repo (Python plus Rust, say) scores its coverage lane and its cc-only scopes in
the same run; nothing lands in `skipped_no_lane` for a scope that never needed a lane.

### Bare names for Rust and Go
`brief` and `next-item`'s `handle` derived the bare identifier by splitting the long
name on `(`, which Rust and Go long names do not carry. The bare name now comes from
the leading identifier, so `brief rust/lib.rs route` works. `explain` and `brief` share
one match rule: exact name first, prefix only when nothing matches exactly.

### Cognitive complexity counts a Rust `match`
The corrected Rust reader fixed ccn but cognitive still read 0 for a `match`; it now
counts like a switch (one plus nesting), so a match and its if/else-if twin agree.

### CI
Every test job failed, on both operating systems, because the suite's fixture lane
assumed pytest-xdist and the CI install did not ship it; the badge told every visitor
the project was broken. The dev extra now carries every plugin the fixture lanes need,
CI installs that extra, and a contract test pins both. CI also stops swallowing
crapkit's own gate: a commit over the ceiling now fails the build.

### Docs
Real `doctor` and `next-item` transcripts (the old samples predated 0.4.x); the gate
table says what `git commit` actually returns (the hook exits 6, git reports 1); the
vitest provider install is pinned to your vitest major; the published handbook's two
links out no longer 404; the packet field count and the PowerShell switch-arm rule now
match the code.

## 0.4.1 — 2026-08-29

A documentation and packaging release. No scoring or gate behavior changed.

- Every document rewritten for the 0.4.0 feature set in plain language: the README leads
  with the 60-second start and the plugin, the handbook gains a "two gates" section
  (the per-edit advisory versus the commit gate) and a walk-through for each of the six
  ways people run the tool, `docs/ratchet.md` leads with the changed gate semantics, and
  the skills name the new agent moments.
- The handbook is published at https://jeanfrancoisgagne.github.io/crapkit/handbook.html.
- PyPI metadata: project links, true classifiers, keywords; `pip install crapkit` is now
  the documented install everywhere.
- Contributor files: issue forms (bug, feature, language request), a pull-request
  template, `CODE_OF_CONDUCT.md`, and `SECURITY.md` with the tool's threat surface.
- Corrected the supported-language count: fourteen (TypeScript and TSX count separately).

## 0.4.0 — 2026-08-28

### Eight new languages
`rust`, `shell`, `cpp` (the whole C family: `.c .cc .cpp .cxx .h .hpp`), `objectivec`,
`vue`, `java`, `zig`, and `powershell` join the supported set — every one admitted only
after a hand-counted probe battery against lizard 1.24.0, and three of them on
crapkit-corrected readers:

- **Rust** ships a corrected reader: upstream lizard scores a 7-arm `match` as ccn 2
  (filed as lizard #494); crapkit counts each non-wildcard arm like a C `case`, so the
  same match scores 7. The module retires itself the day upstream fixes it.
- **shell** and **powershell** are new readers (lizard has neither): function-level
  ccn and cognitive, heredoc/here-string/quote/comment hazards each pinned by test,
  validated against real fleet scripts. PowerShell files in cp1252 decode via a
  narrow fallback instead of erroring.
- **C family**: one `cpp` label (lizard has a single reader for C and C++). Two
  defects are mitigated in crapkit: `#ifdef` fork arms that produce duplicate
  `(path, long_name)` records now warn at analyze time, and cognitive complexity no
  longer counts rvalue-reference `&&` in C++ parameter lists.

`ANALYSIS_VERSION` is 6; stores re-analyze on the next run. Coverage stays wherever a
lane exists; the new languages score cc-only until then (`coverage_optional = true`).

### The Claude Code plugin
The repo now carries an installable plugin: three skills, the MCP server, and a
per-edit advisory hook, installed once per user —

```
claude plugin marketplace add JeanFrancoisGagne/crapkit
claude plugin install crapkit@crapkit
```

Repos without a `crapkit.toml` cost a silent sub-50 ms no-op per edit; repos with one
get the full ladder with zero files added to the repo.

### `crapkit claude-hook`
A native subcommand speaking Claude Code's hook protocol (versioned: `--protocol 1`).
Advisory by design — PostToolUse cannot block, so the wording says so — with a strict
silence ladder: no config, unscoped file, mid-rebase, malformed input, or any internal
error exits 0 with no output. It never opens the store and never writes a file. A
breach prints the advisory to stderr and exits 2, which reaches the model as feedback.
Unknown `claude-*` subcommands exit 0 silently, so a plugin newer than the CLI
degrades to silence instead of an argparse usage dump.

### The commit gate and the advisory now agree
`hook-precommit` exempts functions that carry a ratchet mark (existence), matching the
advisory's exemption: signed debt no longer refuses a commit when merely touched.
`verify` still fails any mark that rises. One stderr line reports how many marked
functions were exempted.

### Faster rescore
`rescore` reads only the rescored files' rows instead of the whole scored run
(799.5 ms → 0.8 ms on a 100k-function store). `crapkit watch` inherits the win.

### doctor --plugin-root
Compares an installed plugin's version and hook protocol against the CLI and reports
drift in one line.

## 0.3.0 — 2026-08-28

### Correct cognitive complexity for Swift and Kotlin
Every Swift and Kotlin function scored cognitive 0: lizard's `SwiftReplaceLabel.preprocess`
materializes the token stream, draining any extension registered ahead of it. Those two
readers now get their own extension chain with the cognitive extension after lizard's
preprocessing; every other language keeps the existing chain. A 6-branch probe now scores
cognitive 10 in Python, TypeScript, Swift, and Kotlin alike. `ANALYSIS_VERSION` is 4, so
stores re-analyze on the next run. The upstream defects that block Kotlin and Rust
admission are filed as lizard #493 (Kotlin expression bodies missing from the function
list) and #494 (Rust match arms not counted).

### Go, cc-only
`go` joins the supported languages: complexity and the worklist, no coverage parser
(the coverprofile format carries no function records, and mapping blocks to spans
scored an untested function as fully covered in review — so it stays out).
`**/*_test.go` joins the default excludes. Scopes that cannot have a coverage lane
declare `coverage_optional = true`.

### `crapkit report`
One self-contained HTML page (`.crapkit/report.html`): the top-50 worklist, per-scope
grades, and the trend series — rendered from the same payloads the JSON commands print,
so the page cannot rank a different function first than the command just did. A per-lane
staleness banner names exactly which lane's artifact no longer describes the tree.

### verify emits uncovered changed lines as SARIF
New rule `crapkit/diff-uncovered`: one warning-level finding per changed line no lane
ran, the full list rather than the stderr 20-line preview. `uncovered` artifact reading
now refuses unknown parsers with the same error lanes use, instead of silently reading
them as coverage.py output.

### Small fixes
`.cjs` counts as a source suffix in lane-command checks; the caller-discovery pattern
matches Go `func` and Kotlin/Swift `fun`/`func` definitions; Swift range operators
`..<`/`...` are protected from mutation (two previously uncompilable mutants); the
README names the actual supported language set.

## 0.2.0 — 2026-08-24

### The start-editing packet

`brief --json` is now step one of the burn-down loop, not step two. An agent reads
the payload instead of opening the file, grepping for callers and running `git log`.
Every field is additive and `schema` stays `1`; every existing text output and JSON
field is byte-identical.

- `brief --json` gains `source` (the function's own text), `params` (its parameter
  names), `file_functions` and `file_totals` (the rest of the file, and its rollup),
  `gate_rule` (`ceiling`, `binds`, `ratchet_mark`, `mark_age_days`,
  `diff_uncovered_max`: what the edit will be judged by), `commands` (`gate`,
  `scoped_tests`, `verify`, `refresh`, already written for this file and scope),
  `lane`, `stale`, `versions`, `attempts`, `regrowth` (whether an earlier
  decomposition of this function did not hold) and `notes`.
- `coupling[]` gains `is_test`; `duplication_twins[]` gains `contained`.
- `brief NAME` takes the function's start line as a third name form. It settles a bare
  name two functions share, and it is the only handle on a function printed
  `(anonymous)`.
- `brief --batch N --json` returns `{schema, run_id, commit, stale, packets[]}`: the
  top N of the queue as N packets from one read of the store, the churn log and the
  ratchet file, for an orchestrator dealing work to a fleet.
- `next-item` gains `stale`, the field `worklist` already carried.
- `explain --json` emits what the plain output prints, and `--history` commits now
  carry their message `body`.
- `crapkit.toml` gains `[crapkit] notes` and per-scope `notes`, free-text house rules
  that ride into every packet. `doctor` warns about a scope a lane measures with no
  `[crapkit.scoped_tests]` template behind it, which leaves `commands.scoped_tests`
  null and the loop's step 4 with nothing to run.

### Performance

31 measured, adversarially verified improvements. No
scoring change (ccn identical on every function by differential test), no
schema change on any JSON output, stdout byte-identical on every read command.

- Churn: the raw log is cached deflated (`.crapkit/churn-log.z`) and refreshed
  from `cached..HEAD` instead of rewalked; `brief` and `worklist --batches`
  drop 60-80% of their wall time, `coupling` and the per-file map read through
  the same log.
- Startup: one command family imported per invocation; ~35-40 ms off every
  command, which also multiplies through every MCP `tools/call`.
- Store: identity-led index layout, integer verdict codes, deflated lane
  records; about 30% smaller on disk with faster reads. Crash-safe migration
  on first open (the file grows until the next `runs prune` or a manual
  `VACUUM` reclaims the rewritten pages).
- Analysis: one lizard pass instead of two (-40% cold), cognitive complexity
  now deterministic (state keyed on the function, not a reused `id()`),
  streamed cache writes, opt-in `CRAPKIT_ANALYSIS_MEMORY_MB` pool bound.
- Memory: coverage artifacts parse in O(chunk) not O(file); `duplication`
  releases source texts after shingling; `watch` polls with scandir.
- Hook: serial below 16 staged files, staged blobs analyzed in memory,
  pygments kept out of the process, HEAD read from the ref files.
- `lane_order` re-keys stamps so a renamed artifact path no longer orphans its
  recorded duration (silently defeating longest-first lane scheduling).
- `doctor` warns when the repo's commit-graph lacks changed-path Bloom filters.
- SARIF output is compact now (decoded-equal, deterministic, ~30% smaller
  files, 5x faster to write).

## 0.1.0

First public release.

- Per-function CRAP scoring (`ccn^2 x (1-cov)^3 + ccn`, ccn = min of standard
  and modified cyclomatic complexity) for TypeScript, TSX, JavaScript, and
  Python via lizard, with Sonar-spec cognitive complexity as a reporting column.
- Coverage lanes (istanbul and coverage.py parsers) with timeouts, retries,
  flake re-test, artifact provenance, and parallel execution.
- Churn-weighted worklist, `next-item`, session claims, disjoint-file batch
  planning, and a one-call `brief` payload for coding agents.
- Hard complexity gate on touched functions (pre-commit hook and `verify`),
  committed ratchet with metric-version stamp, rename following, debt policy,
  and an audited override trail.
- `verify` with merge-base diff scoping, portable TSV baselines, dirty-tree
  attribution, and receipt fields; SARIF and GitHub annotations output.
- `doctor` (config, lanes, unclaimed files, unmeasured directories, committed
  hooks that are not executable in the index, `--json`, `--tune`), `init` with
  test-runner detection and a commented `[crapkit.scoped_tests]` stub,
  diff-scoped mutation testing, duplication and change-coupling analysis,
  `watch`, and a read-only MCP server.
- Lane artifacts live under `.crapkit/cov/`: `init` scaffolds them there using
  each runner's own flag (`--cov-report=json:`, `--coverage.reportsDirectory`,
  `--coverageDirectory`), and `doctor` warns about a lane that writes at the
  repo root instead.
- A failed `verify` holds the baseline: runs taken after it are skipped by the
  default baseline selection until some `verify` passes, so a `coverage` run on
  the refused tree can no longer retire the finding. `verify` names the run it
  refused and both escapes, and `runs list` marks the run it compares against.
- `worklist` and `next-item` are documented as two views of one run. The
  worklist row carries an `ok` or `no-lane` marker and its JSON entries carry
  `flag` and `remedy`, so a wiring gap and a finished repo are visible without
  a second call.
- A function's scope, path and long name are stored once, in an `identities`
  table, instead of on every row of every run. An existing store migrates on
  the first open — one transaction, the old table swapped in last, so an
  interrupt changes nothing — and `runs prune` hands the freed pages back. On a
  1.1M-row store: 246 MB down to 131 MB, and 32 MB down to 14 MB per run
  written. Every read returns what it returned before, in the same order.
