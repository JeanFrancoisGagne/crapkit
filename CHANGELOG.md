# Changelog

## 0.4.8 — 2026-09-01

### A composite action that comments the worklist and the verdict on a pull request
`action.yml` at the repository root makes crapkit four lines in a consumer's workflow:

```yaml
      - uses: JeanFrancoisGagne/crapkit@v0.4.8
        with:
          gate: "false"
```

The action sets up python, installs crapkit, and runs `crapkit coverage --json`,
`crapkit verify --json --reuse-artifacts` and `crapkit worklist --json` in the consumer's
checkout. The three payloads become one comment: what the run measured, the verdict line
with verify's own exit code, and the ranked worklist rows for the files the pull request
changed. A hidden `<!-- crapkit-action -->` line lets the next push find that comment
through the API and edit it, so a fifteen-push branch carries one comment and not fifteen.
A push event has no pull request to carry one, and the same text goes to the job log
instead.

The install reads `$GITHUB_ACTION_PATH`, the action's own checkout, rather than
`pip install crapkit`: the crapkit that scores a tree is the one in the ref the consumer
pinned in `uses:`, so `@v0.4.8` cannot drift to whatever released last.

`gate` decides the exit code. `false`, the default, exits 0 whatever verify found and
leaves the comment as the whole output, which is how a team adopts the action before it
has decided which findings should stop a merge. `true` exits with verify's code, so a
finding fails the check. `top` caps the rendered rows at 5 by default and
`python-version` picks the interpreter. Posting the comment needs `pull-requests: write`
and nothing else.

What the verdict covers is worth reading once. The baseline is the coverage run the same
job wrote a step earlier, so on a clean checkout verify judges an empty diff and reports
the tree's own health rather than the pull request's delta. README's
[The GitHub Action](README.md#the-github-action) says so in the same words and names the
portable baseline that makes it judge the diff instead.

crapkit's own dogfood job runs the action on crapkit with `uses: ./`, on every push and
every pull request. `action.yml` is read by the runner and never imported, so that job is
the only thing that executes its steps; `tests/unit/test_action_contract.py` covers what a
unit test can, which is that the file parses, that every step names its shell, that every
`crapkit` call in it exists on the parser with the flags it passes, and that the marker
the builder writes is the one the action greps for.
### The README and the handbook open with a generated demo
`docs/demo.gif` and `docs/demo.svg` show a 90-second terminal session: `init` sniffing a
small Python repo, `coverage` scoring it, `worklist --top 5` ranking it, a shell heredoc
appending a function at ccn 7 while the per-edit advisory reports it and exits 2, and the
commit gate refusing the staged file with exit 6. The README embeds the GIF under its
badges and the handbook shows it on its first screen.

Nothing in the frames is written by hand. `python tools/demo/generate.py` builds a git
repo from the fixture under `tools/demo/fixture/`, replays its commit plan so the
worklist has real churn to rank, runs those five commands against this checkout's crapkit
and renders what they printed. Every captured line goes through a redaction pass that
strips the temp repo's path, wall-clock stamps and durations, and the generator refuses
to write an image if a machine path survived it. Two runs on an unchanged tree write
byte-identical files, which `tests/unit/test_demo_generator.py` holds them to, so
regenerating the demo for a release is a no-op unless the output actually moved.

The handbook's lanes section also links a new note on pytest-cov 7 and subprocess
coverage, beside the lane rules it belongs to.
### A note on the Pages site: what pytest-cov 7 stopped measuring
`docs/notes/pytest-cov-7-subprocess-coverage.html` writes up the trap that made crapkit
floor `coverage>=7.10.6` and set `[tool.coverage.run] patch = ["subprocess"]` in the first
place, for readers who will never install crapkit. pytest-cov 7.0.0 (2025-09-09) dropped
its own subprocess measurement, so any suite that drives a CLI through `subprocess.run`
loses the coverage of every entry point on upgrade, with nothing printed and the tests
still green.

The numbers on the page are not remembered, they are produced.
`tools/notes/pytest_cov7_repro.py` builds one virtualenv per pytest-cov pin, installs
crapkit editable into each, and runs `tests/e2e/test_init_doctor_e2e.py` four times: two
pins times the patch key present and absent. It toggles the key through
`COVERAGE_RCFILE`, so the tree under measurement is never edited, and writes the executed
and total statement counts for `src/crapkit/cli/admin.py` to
`tools/notes/pytest_cov7_repro.json`. Committed run: 324/521 statements under pytest-cov
6.3.0 with or without the key, 324/521 under 7.1.0 with it, and 0/521 under 7.1.0 without
it. All four runs exited 0.

`tests/unit/test_notes_contract.py` joins the two. Every measurement row on the page has
to match the JSON on the pin, the coverage version, the state of the key and the count, so
a number edited by hand fails the suite.

## 0.4.7 — 2026-08-31

One contributed capability and three fixes. The capability is the per-edit advisory,
which now hears writes that arrive through a shell: PR #45, from @nicolaschapados. The
three fixes are #42, #43 and #44, filed off the review of PR #41, the incident report of
his that became 0.4.6. They are a lane refusal that named the wrong cause, a cause line
hoisted out of a superseded retry attempt, and a commented `init` template that handed
back the environment bug the live lane no longer has. Nothing here is required of a
consumer on upgrade; [Upgrading from 0.4.6](#upgrading-from-046) at the end of this
section has the one thing you may want to choose.

### The per-edit advisory now hears Bash writes
`crapkit claude-hook` judged the one file named in `tool_input.file_path`, which only
Edit, Write and MultiEdit events carry. A `Bash` PostToolUse event carries
`tool_input.command` instead, so an agent writing source through a shell heredoc or
`python - <<'PY'`, which is how some harness modes make every write, got no complexity
advice at all. Found running crapkit 0.4.4 over a real milestone, in the same nested-root
repo that surfaced the 0.4.5 `diff.relative` fixes.

A Bash event now falls back to the working tree: the `*.py` files git reports as dirty or
untracked, whose mtime sits inside a 12-second freshness window, capped at 25 files. Each
one takes the same per-file ladder an Edit takes, so scope, sequencing, changed ranges,
ratchet marks and the untracked rule all mean what they already meant, a nested crapkit
root judges the same root-relative paths the commit gate will, and exit 2 still means one
thing. The freshness window is what keeps a later `ls` from re-advising a file that was
already dirty. A clean tree, a stale file and a cwd outside any git repo are all silence.

Python only, and on purpose: every other language stays the commit gate's business,
because only Python is cheap enough to analyze on every shell call. The shipped plugin
still registers `Edit|Write` alone, so the fallback fires only for a consumer who adds a
`Bash` matcher; the upgrade note below has the snippet and the cost.

The `--protocol` check also moved to the top of the ladder. The outcomes are the same, but
a payload from a future protocol is now answered before the root walk rather than after
it.

### A lane reporting this tree in absolute paths is no longer "another tree"
`_escapes_repo` called a measured path outside the checkout whenever it was absolute,
drive-lettered or climbing out, and never compared it against the repo root. A runner that
reports this checkout's own files by absolute path, which is coverage.py whenever
`relative_files` is off, was refused with the another-tree message and advice about venvs
and `path_prefix`: none of it the cause, and `path_prefix` only ever prepends.

The root now reaches the check and the refusal splits in two. Paths outside the root keep
the old message verbatim. Absolute paths that resolve under it get their own exit 5,
naming the cause (the runner spelled paths absolutely, crapkit joins on root-relative
ones) and the runner's own switch: `relative_files = true` under `[tool.coverage.run]`, or
`[run] relative_files = true` in `.coveragerc`, for a coveragepy lane, the reporter's
`cwd`/`root` option for an istanbul one. Both sides of the comparison resolve the same
way, symlinks followed and the case folded where the filesystem folds it.

Nothing is rebased: the join contract stays root-relative and only the diagnosis moved. A
`../` climb keeps the another-tree refusal, having no recorded working directory to
resolve against, and so does a mixed artifact, where one path from somewhere else decides
for all of them and the count names the outside paths alone. In-tree relative paths that
simply miss every scope still warn and score on, which is the greenfield shape 0.4.6
described. Three readings of zero overlap, three verdicts.

### A retried lane quotes the attempt that failed it
The cause hoisted in front of a lane refusal is now read from the final attempt only.
Every attempt appends to one `.crapkit/lane-<name>.log`, and the scan that looks for a
reason ran over the whole file, so a lane that timed out on an `ImportError` and then
failed attempt 2 for a different reason reported the ImportError, standing above attempt
2's own output with nothing marking the boundary between them. The final attempt starts
after the last `--- attempt N ---` banner line; the banner has to be the whole line, so
output that quotes those words mid-text is still output. Attempt 1 writes no banner, so a
log without one is a single attempt and reads exactly as before. The tail itself still
reads the end of the whole log, and the message names no attempt number: the log path it
already quotes is where that lives.

### The commented lane template names the python the lockfile pins
0.4.6 taught `init` to write `uv run python -m pytest …` off a lockfile, but only where it
detected a live pytest lane. A repo with a lockfile and no pytest marker file
(`pyproject.toml`, `pytest.ini`, `setup.cfg`) gets the coveragepy lane as a commented
template instead, and that template still read a bare `python`. Uncommenting it handed the
reader back the environment bug the prefix exists to prevent.

The template now carries a `{python}` placeholder, filled the same way the
`[crapkit.scoped_tests]` entries already fill theirs, so every python line `init` writes
names one launcher, whether that is the live lane, the scoped-tests entry, or the
commented template that stands in for a lane the repo did not get. `python_launcher` takes
the launcher as its fallback for a repo with no lane to read it back off. The js templates
are unchanged; they carry no placeholder. A repo with no lockfile writes `python` (or
`python3`, or `py`) exactly as before.

`_warn_missing_pytest_cov` now documents the rule it applies rather than the one it used
to. A manager-headed lane names no python in the position the probe reads, so it is never
probed and can never earn the pytest-cov note; it still earns the two notes ahead of the
probe, for a manager that does not resolve on PATH and for a first word the shell cannot
start.

### Upgrading from 0.4.6
- **Nothing is required.** No config key, no ratchet reseed, no stamp change. Every
  0.4.6 config and every committed ratchet reads the same here.
- **One thing you may want to add: a `Bash` matcher for the advisory.** The shipped
  plugin registers `Edit|Write`, so out of the box the new fallback never fires. To get
  it, add a second PostToolUse entry to your own settings, same command, matcher `Bash`:

  ```json
  {
    "hooks": {
      "PostToolUse": [
        {
          "matcher": "Bash",
          "hooks": [
            { "type": "command", "command": "crapkit claude-hook --protocol 1", "timeout": 20 }
          ]
        }
      ]
    }
  }
  ```

  The cost is one `git rev-parse --show-toplevel` and one `git status --porcelain -z
  -uall` per shell call inside a git repo, whether or not crapkit measures it: about
  30 ms together on crapkit's own checkout, and it grows with the size of the tree git
  has to walk. The fallback judges `*.py` files only, so a repo whose source is TypeScript
  or Go pays those two spawns and gets nothing back.

## 0.4.6 — 2026-08-31

Three findings from @nicolaschapados, out of one incident on a real pytest/uv project
checked out twice through git worktrees. The incident was not a crapkit bug: the shell
held checkout B's venv while crapkit ran in checkout A, and B's editable install pointed
pytest at B's sources. What crapkit owns is that it made the cause hard to find, and was
one step away from reporting a confident wrong answer instead.

### Reading a failed lane
Every no-artifact refusal now carries `full log: <path>` before the tail it quotes, the
`--reuse-artifacts` one included: it raised its own bare sentence, and the log from the
run that built the artifact was usually still on disk.
`_raise_no_artifact` had the path and never printed it, so a reporter saw 500 bytes of
tail and nothing naming `.crapkit/lane-py.log`; finding the log took a second agent while
ten collection tracebacks sat inside it.

The tail is cut on line boundaries rather than on a byte count, so it can no longer open
mid-line on a fragment that reads like the start of a message (the report that prompted
this opened on `last output:  short test summary info ====`). A line longer than the
budget on its own keeps its end behind an ellipsis, which at least says so.

When the end of the log names no cause, the last few lines that do name one are hoisted
in front of it with an `...` marking the output skipped between them. pytest closes a
collection failure on a block of `ERROR path` lines saying which files broke and never
why, so a plain tail spends its whole budget on filenames while the reason scrolls off
above it.

A hoisted line too long to show whole keeps its END behind an ellipsis. The path that
names the other checkout sits at the end of an `E   ImportError: cannot import name ...`
line, and the cut used to be taken from the right, so on a deep path the one detail worth
hoisting was the one dropped, with nothing saying so.

### An artifact that measured a different tree
Nothing checked that a lane's artifact was about this checkout. Coverage joins on path
and nothing else, so an artifact whose paths reach none of the scopes its lane claims
contributes exactly nothing and every function in those scopes reads `untested`: a
confident `N untested … grade F` assembled out of a tooling mistake, which is worse than
the exit 5 a missing artifact already earns, because it looks like an answer. In the
incident it failed loudly only because the two checkouts' APIs had diverged. Had they
matched, as two worktrees of one branch normally do, the suite would have passed and the
grade would have been fiction.

Zero overlap has two readings, and the measured paths tell them apart:

| Measured paths | Reading | Verdict |
|---|---|---|
| absolute, drive-lettered or climbing out of the tree | both parsers rebase an in-tree file to a repo-relative path, so a path that stayed absolute names a file somewhere else | the lane FAILS, exit 5; its scopes fall back to `no-lane`, not `untested` |
| in-tree, just not under the scope (`tests/test_core.py`), or nothing at all | the greenfield shape: a suite importing none of the scoped source yet | a WARNing on stderr, and the run scores on |

The refusal quotes a few of the paths the artifact does name and says what to do about
them, which is not the same sentence for both readers: a coveragepy lane is pointed at
the environment it binds to and at `path_prefix`, an istanbul lane at the artifact
itself, because the istanbul reader rebases every path under this checkout's root and
never reads `path_prefix` at all. The reach half runs after `path_prefix` is applied, so a prefix that fixes the
join is never refused, and it asks `universe.owning_scope`, the same predicate that
assigns files to scopes, so a scope declaring individual files rather than directories is
reached exactly. The escape half asks the path the runner WROTE, with the prefix taken
back off: the prefix is glued onto every key including the absolute ones, and judged on
the key instead, `backend/` + `/other/checkout/a.py` reads as relative, so no lane that
declares a prefix could ever be refused.

Zero overlap is the whole test. A partial overlap has honest readings, a lane measuring
part of a scope or generated files outside it, and any threshold over zero would need
tuning per repo.

### The environment a scaffolded lane binds to
The scaffolded lane was a bare `python -m pytest`, which resolves through the shell's
PATH to whichever venv happens to be active. That was the root cause of the whole report.
A lockfile is the repo naming the manager that owns its environment, and only that
manager's `run` binds a command to it:

| Lockfile at the root | Lane command `init` writes |
|---|---|
| `uv.lock` | `uv run python -m pytest --cov …` |
| `poetry.lock` | `poetry run python -m pytest --cov …` |
| `pdm.lock` | `pdm run python -m pytest --cov …` |
| `Pipfile.lock` | `pipenv run python -m pytest --cov …` |
| none | `python -m pytest --cov …`, unchanged |

`init` now also checks that the manager resolves on THIS machine's PATH, and names it
when it does not: the lockfile is the repo's property, so a `uv.lock` a teammate
committed gets the `uv run` lane on a checkout whose owner installed the dependencies
with pip. Nothing caught that — the start check skips a first word that does not resolve
at all, and the pytest-cov probe declines to provision an environment — so `init` exited
0 pointing at a `crapkit coverage` that exited 5 on `'uv' is not recognized`.

First match wins in that order, so a repo mid-migration between two managers gets the
same config every time. The prefix only prefixes: which python name follows it is still
the first of `python`, `python3`, `py` that resolves, so a Windows PATH carrying only the
launcher gets `uv run py`. The `--junitxml` flag and `results_artifact` 0.4.5 added ride
on the managed lane unchanged.

`[crapkit.scoped_tests]` takes the same prefix, read back off the lane rather than passed
in beside it: step 3 measuring one environment while step 4 tests another is the same bug
one command later.

A managed lane is not probed for pytest-cov. `uv run` and its siblings create or sync the
project environment before running anything, and `init` has no business provisioning one
to ask a question about it. Doctor still asks whether the lane can start, which for a
managed lane is `uv --version`.

### The py lane measures the CLI again on pytest-cov 7

pytest-cov 7.0.0 dropped subprocess measurement. crapkit's `dev` extra asked for
`pytest-cov>=5`, so a fresh `pip install -e ".[dev]"` resolves 7.x, and every CLI entry
point the e2e suite drives through `subprocess.run` went to 0% with nothing said. On
`tests/e2e/test_init_doctor_e2e.py`, `src/crapkit/cli/admin.py` scored 0/498 statements
under pytest-cov 7.1.0 against 315/498 under 6.3.0.

pyproject.toml now sets coverage's own replacement, `[tool.coverage.run] patch =
["subprocess"]`, and floors `coverage>=7.10.6` in the `dev` and `py` extras.
Coverage 7.9 and earlier warn about the unknown key and ignore it, which is the same
silence one warning louder. The same file scores 317/498 under pytest-cov 7.1.0 and 6.3.0 alike.

If your own repo runs a pytest lane over a suite that spawns subprocesses, you want both
lines too; [docs/lanes.md](docs/lanes.md) has the section.

### Fixed

- The three unit tests that probe crapkit's import cost in a child now strip
  `COVERAGE_PROCESS_*` as well as `COV_CORE_*`. `test_pygments_deferral` stripped neither,
  and two of its tests failed under any `pytest --cov` on pytest-cov 6.3.0.

## 0.4.5 — 2026-08-30

A fix release with no new capability: an audit of 0.4.4 through six lenses, with every
finding reproduced twice; a benchmark of every subsystem at consumer scale; and the
field reports from the CodingGraph pilot. The audit filed issues #24 and #25; the pilot
filed #26 through #31 and #37 and sent the pull requests that closed them, #32 through
#40 (PR #23, from @nicolaschapados, was 0.4.4, not this release). After upgrading, run
`crapkit ratchet seed` once, then read [Upgrading from 0.4.4](#upgrading-from-044) at
the end of this section for the five other things that change.

### Windows and lane commands
0.4.4 taught the lane guard cmd.exe's quoting, with two gaps: a quote that opens
mid-token (`--cov-report=json:"a b\py.json"`) was two tokens here and one argument to
cmd.exe, so a good lane was refused; a caret escape (`-k ^"not slow^"`) stayed in the
token and split the value. The cmd.exe reading is now a character walk that toggles on
every quote and honours `^` outside quoted runs, checked against real `cmd.exe` argv on
thirty command shapes. A chained command (`cd tests && python -m pytest --cov ...`,
`... --cov && echo done`) is read one argv per `&&`, `||`, `&` or `|` segment and every
segment that runs the runner is checked, so a second narrowing run after the operator is
still refused and a refusal never names a word from the next command. An empty quoted
argument (`-k ""`) stays an empty argument instead of shifting the next path onto the
flag; a quoted or caret-escaped operator is a word, not a separator; words break on
space, tab and line endings only, as cmd.exe and sh do, so a pasted non-breaking space
no longer splits a value; redirections (`> nul`, `2>&1`) are the shell's and never a
positional; on sh a trailing `;` ends the command. The vitest guard licenses 25
value-taking options (`--workspace`, `--diff`, `--snapshotEnvironment`,
`--coverage.extension` and `--typecheck.tsconfig` joined the list) and the docs list is
generated from the set.

`init`'s pytest-cov probe and `mutate`'s per-mutant timeout both used `capture_output`
under `shell=True`. On Windows the kill hit cmd.exe and `run()` then waited on pipes the
grandchild still held, so a 15 s timeout returned after 29 s and a looping mutant was
never cut. Both now run through one bounded spawn (`procs.run_bounded`): the command
starts in its own process group, and a deadline kills the whole tree (`taskkill /T` on
Windows, `killpg` on POSIX) and waits for it. No orphan suite keeps running after
`mutate` gives up on a mutant or a lane's `timeout_seconds` expires, and the lane log
still streams as before.

A PATH holding only the `py` launcher got a lane naming `python3`, which the first
`coverage` could not run; `py` is now in the fallback chain. The Store `python.exe`
alias (exit 9009, "Python was not found") gets its own note naming the interpreter
cmd.exe cannot run, kept separate from the note for a python that runs pytest with no
pytest-cov installed. The `pip install "crapkit[py]"` line uses double quotes in the
note and in the docs: single quotes do not survive cmd.exe.

`mutate` adds its worker worktrees in parallel, and git's add enumerates the existing
`.git/worktrees/*` entries and dies reading a `commondir` a peer is still building: one
add in about a thousand at four workers on Windows, seen on CI (#25). `worktree_add`
retries once after 50 ms when the message names `worktrees/` and `commondir`, whatever
the git dir is called.

### Roots, paths and scopes
The 0.4.4 churn fix left the pre-commit gate reading `git diff --cached` from the git
top, so under a crapkit root below that top staged paths matched no scope and a function
at twice the ceiling committed with a warning (#24). Every git spawn now runs with
`diff.relative=true` and cat-file requests use `:./path`, which also fixes the nine
siblings that joined top-relative paths against root-relative rows: verify's changed
files, `rescore --gate`, lane reuse (which could republish a stale artifact's score),
`mutate` (which found targets and mutated nothing), the ratchet's rename follow, and the
per-edit advisory's own diff. A staged file above the crapkit root is outside the diff
by design and no longer named.

Every git spawn also runs with `core.quotePath=false`, so a dirty non-ASCII file is no
longer invisible to lane reuse (git quoted it, `ls-files` did not). `coupling`, `brief`
and `worklist --batches` decode git's quoting before joining paths, so a non-ASCII path
is no longer a fake row, and `coupling` drops pairs naming a path git no longer tracks.
`.git` is found by walking up from the root, doctor's commit-graph check included, so
the HEAD fast path fires below the top; `config_value` asks git for the repo's own
setting and no longer reads back the `diff.relative` flag crapkit injects into every
spawn.

The churn caches carry their format in the file name (`churn-cache-v2.json`,
`churn-log-v2.z`), so a 0.4.3 sharing the repo keeps its own caches instead of both
rebuilding on every run. A warm 0.4.4 cache is adopted once and its file removed rather
than orphaned.

Scope ownership was decided three ways (first-declared in scoring, longest-prefix in
test-scoped, prefix-only for lane reuse) and the packet mixed two of them. One predicate
in `universe` now answers everyone, with the deepest declared scope path winning, so
scoring, test-scoped routing, lane reuse and the packet agree; a repo with NESTED scopes
may see files move between scopes on its next scan. A file-valued scope path
(`paths = ["core/hot.py"]`) marks its lane changed.

### Runs, gates and verify
`worklist` and `next-item` could describe different runs, and `ratchet seed` and `prune`
could sign marks off a run `verify` had refused. All four now pick the run their peer
picks, the newest trusted run, and a failed verify sitting above every trusted run is
refused with a line naming it.

`verify --baseline ID` naming a run that exists but cannot serve now says which run it
is, why (a failed verify, a hook run, a partial run, an inventory run) and which runs
can serve, instead of the empty-store line (#27, PR #36).

`verify`'s gate exempts a touched function whose fresh CRAP sits at or under its ratchet
mark, the rule `rescore --gate` already applied, so an edit inside signed debt no longer
passes the commit gates and then meets exit 6 (#29, PR #35); exit 7 stays for a
regression the diff never touched. The pre-commit hook still exempts on the mark's
existence alone, on purpose: a staged blob has no coverage to score.

A lane that wrote no test counts this run gets one line naming the gap instead of a
KeyError (#30, PR #32), and `inventory` no longer dies when a tracked file is missing
from the working tree.

`explain PATH LINE` resolves a start line the way `brief` does, and `_scored_run`
returns named fields.

The twin-key note is printed by the parent after the pool returns, never from a worker
whose stderr never saw the UTF-8 reconfigure, so on Windows its em dash no longer lands
as a lone cp1252 byte (#31, PR #33).

### Analysis
Shell cognitive complexity nests: `fi`, `done` and `esac` close a level, so a 4-deep
`if` reads 10 like every other language, not 4. That is analysis version 8. Cognitive
complexity is reported and never gated, ccn does not move, and no other language moves,
but the ratchet still refuses to weigh fresh scores against marks another metric
produced, so see [Upgrading from 0.4.4](#upgrading-from-044).

### Performance
A benchmark of every subsystem on a 31,459-file consumer (152k functions, 41,544 marks,
541 MB of lane artifacts, 72,653 commits) produced 76 improvement candidates. Skeptics
re-implemented and re-measured each one and killed most; these six survived and shipped,
each with its A/B on that corpus.

`coupling`, `worklist --batches` and `brief` stop re-pairing the churn log on every warm
run. A ranked-pairs cache sits at `.crapkit/coupling-cache-v1.json` beside the churn
caches, keyed on HEAD plus the window plus a digest of the tracked set; `--min-support`
or `--min-confidence` off the defaults bypasses it, and `--top` reads it. Warm
`coupling` 1.05 s -> 0.11 s, batches -62%, a single `brief` -25%.

`brief --batch N` shingles the snapshot once per batch instead of once per packet: batch
5 in 11.8 s -> 5.2 s, output byte-identical. An on-disk shingle cache was refuted
outright, because shingles are built on Python's per-process randomized hash.

`doctor` probes each distinct lane runner once, not once per lane: 7.5 s -> 1.4 s on 14
lanes over 2 runners.

`trend` and `report` read per-run rollups instead of rescanning 4.3 M rows. The new
`run_rollup` table is filled once per run and pruned with its run: `trend`
4.58 s -> 0.04 s warm, `report` -76%. Both commands write now, best effort; see
[Upgrading from 0.4.4](#upgrading-from-044).

`verify` reads each istanbul artifact once for coverage, dead lines and its digest
together, and skips the artifact walk on an empty diff: 25.5 s -> 18.9 s, peak memory
+55 MB, all digests byte-identical.

`mutate` keeps its worker worktrees under `.crapkit/mutate-pool/` and re-prepares them
per run: 30.6 s -> 0.46 s of setup on the big tree. `crapkit mutate --drop-pool`
reclaims the disk, and single-worker runs are untouched.

Four candidates were refereed and rejected, named here so nobody rebuilds them: skipping
verify when HEAD and the dirty names are unchanged (the key cannot see a second edit to
an already-dirty file), serving MCP tool calls from a kept process (a stale `source`
breaks the packet contract), parallel git date slices for the churn walk, and a faster
JSON decoder.

### Doctor, init and the plugin
`doctor` WARNs on a coveragepy or istanbul lane that declares no `results_artifact`,
names the two checks that cannot run for it (the crashed-worker check and the
no-new-failures check, exit 8) and prints the line that fixes it; `init` writes
`--junitxml` plus `results_artifact` on the pytest and JS lanes it detects (#26,
PR #38).

`doctor` reads a lane command with the shell that runs it, so a quoted interpreter path
is one word, a runner after `&&` is checked, and a path inside a quoted `-k` is a value.
A lane whose first word will not start is now a FAIL instead of a clean report. The
pytest-cov probe asks the python that runs pytest, so `coverage run -m pytest` is left
alone.

`doctor --plugin-root` takes the plugin root or any directory above it, `~/.claude`
included, where the newest crapkit install under it wins; with no path it reads Claude
Code's plugin cache itself. It names the root it chose (#28, PR #39).

The packet spells its commands as the console script (`crapkit rescore ... --gate`), the
form the docs promise and the one that resolves from a venv on Windows (#37, PR #40).

### Tests and repo
An architecture review of the 0.4.5 tree proposed 37 deepening refactors. Two skeptics
per candidate refuted 36 of them, because every seam they asked for already existed, and
reproduced ten defects on the way; each of those is fixed above, behind the seam that
was already there.

`tests/unit` now drives `verify` and `coverage` in process (`cli/verifying.py` 34% ->
100%, `cli/scoring.py` 43% -> 99% statement coverage from the unit suite alone), and
`tests/e2e` shares one CLI runner in `conftest.py` and runs in about 1m30 with `-n 8`.
The unit suite is 2,283 tests.

The review left one structural item open: `discover.py`, 384 lines with no importer
since birth, is either wired into the packet or removed in a later release.

### Docs
A section on running crapkit with its root below the repo top; the vitest guard page
lists every option whose value it licenses, pinned by a test; the `crapkit-recover`
skill routes the pytest half of "no coverage provider" to the pytest docs.

### Upgrading from 0.4.4
- **Run `crapkit ratchet seed` once.** Shell cognitive complexity now nests, which is
  analysis version 8, and `verify` exits 3 on marks stamped under version 7:
  `ratchet marks were recorded under [crapkit-analysis=7 ...] but this run measures
  [crapkit-analysis=8 ...]`. Seeding re-baselines the marks against the latest run and
  restamps the file. Cognitive complexity is reported, never gated, and ccn does not
  move, so no CRAP score changes: the reseed is there to make the stamp match.
- **New cache files appear under `.crapkit/`:** `coupling-cache-v1.json`, plus
  `churn-cache-v2.json` and `churn-log-v2.z` in place of the 0.4.4 churn cache, which is
  read once and then deleted. All of it is derived data, and `init` already puts
  `.crapkit/` in `.gitignore`.
- **`trend` and `report` write now.** The first run of either sums every existing run
  into `run_rollup`, and changing `target` or a scope's target keys a new ceiling and
  makes it sum them again. The write is best effort: when another crapkit process holds
  the store's write lock, the command still prints its answer and pays the scan next
  time.
- **A repo with NESTED scopes may see files move between scopes** on its next scan,
  because the deepest declared scope path now wins for scoring, test-scoped routing,
  lane reuse and the packet alike. Per-scope rollups and ceilings shift for those files.
  A repo whose scopes do not nest sees no change.
- **`mutate` with `mutation_workers > 1` keeps a worktree pool.** Its workers now live
  under `.crapkit/mutate-pool/` between runs and are re-prepared each run. The pool is
  not size-bounded; `crapkit mutate --drop-pool` removes it and exits. Single-worker runs
  are untouched.
- **`doctor` WARNs on a 0.4.4 lane with no `results_artifact`** and prints the fix. For
  a pytest lane named `py` that reads: add `--junitxml=.crapkit/cov/junit-py.xml` to the
  command and `results_artifact = ".crapkit/cov/junit-py.xml"` to the lane. Coverage is
  unaffected; what the lane cannot feed is the crashed-worker check and the
  no-new-failures check (exit 8).

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
