# crapkit

[![ci](https://github.com/JeanFrancoisGagne/crapkit/actions/workflows/ci.yml/badge.svg)](https://github.com/JeanFrancoisGagne/crapkit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/crapkit)](https://pypi.org/project/crapkit/)
[![Python](https://img.shields.io/pypi/pyversions/crapkit)](https://pypi.org/project/crapkit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

crapkit scores every function in your repo on complexity times uncovered risk, ranks the
worst ones by how often the file changes, and blocks commits that add more. It reads
Python, TypeScript, TSX, JavaScript, Swift, Go, Rust, shell, PowerShell, C and C++,
Objective-C, Vue, Java and Zig through [lizard](https://github.com/terryyin/lizard), and
joins per-function branch coverage from the istanbul or coverage.py artifact your own test
command already writes. Every read command speaks sorted-keys JSON on a pinned schema,
because half the callers are coding agents.

```
CRAP = ccn^2 * (1 - cov)^3 + ccn
```

`ccn` is the smaller of standard and modified cyclomatic complexity, both read off one
lizard pass. `cov` is branch coverage inside the function's span; with no branches it
falls back to statement coverage, and with no statements to invoked-or-not, so a
half-executed straight-line function never reads as fully covered.

**Above the ceiling, coverage cannot save you. Decompose.** At the default target of 6, a
function at ccn 7 with 100% coverage still scores 7 and still fails the gate. The only
move that clears it is splitting the function.

crapkit scores **git-tracked files only**. Source you have not `git add`ed is invisible to
it.

---

## The 60-second start

```
pip install crapkit
cd your-repo
crapkit init        # writes crapkit.toml: scopes, a coverage lane, .gitignore lines
crapkit coverage    # runs the lane, joins coverage, stores a scored run
crapkit worklist    # the ranked risk map
crapkit ratchet seed && git add crapkit.toml crapkit-ratchet.tsv .gitignore
```

`coverage` scores, `worklist` ranks:

```
$ crapkit coverage
run 1 @ fae4db93108: 2 functions scored — 2 measured / 0 untested / 0 no-lane / 0 cc-only, 1 over target 6, CRAP load 41.0, grade F

$ crapkit worklist
worklist @ fae4db93108 (run 1, floor ccn>=5, churn 12mo) — 1 active, 0 dormant
  risk      0.0  ccn  14 ( 14 std)    1c/1a w   0.00  calc/grade.py:7  classify( score , attempts , late , bonus )
```

`ratchet seed` signs today's debt at today's score. From then on marks only ever fall, so
the repo can get better and never worse while you burn it down.

**One thing stops most first runs: the coverage plugin.** `init` writes a lane that shells
out to your own test runner, and the runner needs its coverage package installed:
`pytest-cov` for pytest, `@vitest/coverage-v8` (pinned to your vitest major) for vitest.
Without it the lane produces no artifact and `coverage` exits 5 quoting the runner's own
error. For pytest, `init` probes the python its lane will run and prints the install
command when `pytest_cov` is missing; `pip install "crapkit[py]"` pulls the plugin
alongside crapkit when the two share a venv. On a Windows PATH holding only the `py`
launcher it writes `py`, not a `python3` the lane could never run, and when cmd.exe cannot
start the interpreter at all (exit 9009, the Store alias) it names that instead of guessing
at pytest-cov. The two quickstarts below walk a real repo end to end.

**On Windows a lane command is read by cmd.exe**, the shell that will run it, not by sh.
Double quotes are the portable quoting. A single-quoted value is refused at config load
with exit 3, because cmd.exe would hand pytest five words and the lane would write no
artifact:

```
# the lane in crapkit.toml
command = "python -m pytest -m 'not live and not perf' --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json"

$ crapkit doctor
crapkit: lane 'py': positional argument 'live' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately (cmd.exe does not treat ' as a quote: write the value in double quotes)
```

Write it `-m "not live and not perf"`. Carets, `&&` and `|` segments, redirections and
empty quoted arguments all read the way the shell reads them, so a chained lane
(`cd tests && python -m pytest --cov ...`) is checked one segment at a time. `doctor` reads
a lane the same way, and FAILs one whose runner will not start.

## Install

```
pip install crapkit
```

That is the release on [PyPI](https://pypi.org/project/crapkit/). For the unreleased tip
of `main`, or from a local clone (run at the clone root):

```
pip install git+https://github.com/JeanFrancoisGagne/crapkit.git
pip install .
```

Every route pulls one dependency, `lizard>=1.24.0`, a normal PyPI wheel, so an offline
mirror installs fine. Requires Python 3.11 or newer. The `pip install -e ".[dev]"` under
[Development](#development) is a different thing: it adds the test extra, for people
changing crapkit.

```
$ crapkit --version
crapkit 0.4.5
```

`python -m crapkit` works identically to the console script and is what to use from a
source checkout. Every subcommand accepts `--repo PATH` (default: the current directory),
so you never have to `cd` into the repo you are scoring; [Subcommands](#subcommands) shows
where the flag goes.

## Upgrading from 0.4.4

**Run `crapkit ratchet seed` first.** Shell cognitive complexity now nests, which is
analysis version 8, and marks measured under version 7 are not comparable. Until you
re-seed, `verify` refuses at exit 3:

```
$ crapkit verify
crapkit: ratchet marks were recorded under [crapkit-analysis=7 lizard=1.24.0] but this run measures [crapkit-analysis=8 lizard=1.24.0] — CRAP scores are not comparable across metric versions; re-baseline with `crapkit ratchet seed`
```

Only shell and PowerShell cognitive numbers move. `ccn` does not, so a re-seed re-stamps
the file and leaves the marks where they were.

Five more things change under you. Three of them need nothing from you:

- **New cache files.** `.crapkit/coupling-cache-v1.json` joins `churn-cache-v2.json` and
  `churn-log-v2.z`. A warm 0.4.4 churn cache is adopted once and its file removed, and
  `.crapkit/` is already gitignored, so nothing new reaches your index.
- **`trend` and `report` write.** Both read a per-run rollup table, filled once per run and
  pruned with its run, instead of rescanning every scored row. A read-only `.crapkit/`
  costs the speedup, never the command.
- **Nested scopes may move files.** One predicate decides scope ownership now, and the
  deepest declared path wins, so a repo whose `[[scope]]` paths nest inside each other can
  see files change scope, rollup and ceiling on the next scan. Scopes that do not nest see
  no change.

The other two put something in front of you:

- **`mutate` keeps a worktree pool.** With `mutation_workers > 1` the worker worktrees now
  live under `.crapkit/mutate-pool/` between runs and are re-prepared each run, which is
  the setup cost gone (30.6 s to build four on a 31,459-file repo, 0.46 s to re-prepare
  them). The pool is not size-bounded and nothing sweeps it: `crapkit mutate --drop-pool`
  removes it and exits. Single-worker runs are untouched.
- **`doctor` WARNs on a lane with no `results_artifact`.** Every `coveragepy` or `istanbul`
  lane written before 0.4.5 gets one, with the two lines that fix it. Coverage is
  unaffected. What the lane cannot feed without a results file is the crashed-worker check
  and the no-new-failures check (exit 8).

### The exe lock on Windows

`uv tool upgrade crapkit`, and `pip install -U` into a tool venv, fail with `os error 32`
("The process cannot access the file because it is being used by another process") while a
crapkit MCP server is live: an agent session spawns `crapkit.exe mcp`, which holds the
launcher, and Windows will not overwrite a running executable. The venv upgrades before
that copy fails, so `crapkit --version` already reports the new version and only the
launcher is stale. Quit the agent session and rerun the upgrade, or rename the locked exe
aside (Windows allows renaming a running one) and copy the new one in. Two lines in
cmd.exe, where both `%` variables expand:

```bat
move %USERPROFILE%\.local\bin\crapkit.exe %USERPROFILE%\.local\bin\crapkit.exe.old
copy %APPDATA%\uv\tools\crapkit\Scripts\crapkit.exe %USERPROFILE%\.local\bin\crapkit.exe
```

Git Bash has no `move` and passes `%APPDATA%` through as literal text, so that block
fails there on its first line. Its form is `mv` and `cp` over `"$USERPROFILE"` and
`"$APPDATA"`, which Git Bash sets to the same two directories.

## The Claude Code plugin

```
claude plugin marketplace add JeanFrancoisGagne/crapkit
claude plugin install crapkit@crapkit
```

Two commands, installed once per user, and every repo on the machine gets it. The plugin
ships three skills (`crapkit`, `crapkit-recover`, `crapkit-onboard`), the read-only MCP
server, and one advisory PostToolUse hook that names any function an edit pushed over its
ceiling. It adds no files to your repo, and it needs the crapkit CLI on PATH.

A repo with no `crapkit.toml` costs a silent sub-50 ms no-op per edit. Other agent
runtimes have no marketplace: copy `plugin/skills/*` into their skills directory instead.

## Languages

14 languages, two coverage parsers. Coverage joins where a parser exists; everything else
scores on complexity alone.

| Language | Files | Coverage |
|---|---|---|
| Python | `.py` | coverage.py |
| TypeScript | `.ts` | istanbul |
| TSX | `.tsx` | istanbul |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | istanbul |
| Vue | `.vue` | istanbul, when your vitest run reports on `.vue` files |
| Swift | `.swift` | none: cc-only |
| Go | `.go` | none: cc-only |
| Rust | `.rs` | none: cc-only |
| shell | `.sh` `.bash` | none: cc-only |
| PowerShell | `.ps1` `.psm1` | none: cc-only |
| C and C++ | `.c` `.cc` `.cpp` `.cxx` `.h` `.hpp` | none: cc-only |
| Objective-C | `.m` `.mm` | none: cc-only |
| Java | `.java` | none: cc-only |
| Zig | `.zig` | none: cc-only |

A cc-only scope declares `coverage_optional = true`, scores `crap = ccn`, and needs no
lane. Nothing about it is provisional: the ceiling still binds and the gate still refuses
a function over it. Add a coverage lane the day a parser exists and the same scope starts
joining coverage.

`crapkit init` writes that key itself, on every scope whose languages all lack a parser,
and leaves it off any scope a lane could still measure. So the 60-second start above runs
unchanged on a Go, Rust or shell repo: `crapkit coverage` scores it with no lane at all,
and that run is the baseline `worklist`, `next-item`, `ratchet seed` and `verify` read.

Three readers are crapkit's own. lizard ships none for shell or PowerShell, so crapkit
counts their functions itself. Its Rust reader scores a 7-arm `match` as ccn 2 (filed as
lizard #494), so crapkit counts each non-wildcard arm like a C `case` and retires the
override the day upstream fixes it. The cognitive column charges that same block once,
the way Sonar charges a `switch`.

## The gate

Four surfaces ask the same question, ccn against the scope's ceiling, with four
different powers:

| Surface | Fires | Power |
|---|---|---|
| `crapkit claude-hook` | after an agent's edit lands | **advisory.** Names the breach on stderr. Blocks nothing, because PostToolUse runs after the write |
| `crapkit rescore FILE --gate` | when you ask, after the first coverage run | **preview.** The commit gate's verdict on demand, sub-second, before you stage. With no run behind it, exit 1 and `no snapshot` |
| `crapkit hook-precommit` | `git commit` | **blocks.** The hook exits 6; git reports 1. Staged blobs only, so it costs the size of the commit and needs no coverage |
| `crapkit verify` | before you push, and in CI | **the verdict.** Gate, ratchet, new test failures, diff coverage, against the trusted baseline |

Both hooks exempt a function the committed ratchet already carries a mark for, so touching
signed debt never refuses a commit. `verify` is what fails a mark that rises. Since 0.4.5
its gate exempts a touched function whose fresh CRAP sits **at or under** its mark, the
rule `rescore --gate` already applied; push it past the mark and the gate fires again. The
pre-commit hook still exempts on the mark's existence alone, on purpose: a staged blob has
no coverage, so there is no fresh CRAP to compare against. It reports each exemption count
on stderr (`staged function(s) carry a ratchet mark and were not gated`), and says the same
about a staged file no `[[scope]]` claims, so a new top-level directory cannot go ungated
in silence.

**The crapkit root does not have to be the git top.** Since 0.4.5 every git spawn runs with
`diff.relative=true` and `core.quotePath=false`, so a `crapkit.toml` in `packages/api`
gates that package's own staged files and names them `app/m.py`, not
`packages/api/app/m.py`, and a dirty non-ASCII path is a real row rather than an invisible
one. Before that a nested root matched staged paths against no scope, and a function at
twice the ceiling committed with a warning.

Git runs hooks outside your shell's activated venv. Bare `python` must resolve to an
interpreter that has crapkit installed, or spell it out
(`exec /path/to/venv/Scripts/python -m crapkit hook-precommit`).

### Route 1: `.git/hooks/pre-commit` (local, not committed)

```sh
cat > .git/hooks/pre-commit <<'EOF'
#!/bin/sh
exec python -m crapkit hook-precommit
EOF
chmod +x .git/hooks/pre-commit
```

### Route 2: a committed hooks directory

The whole route, from a repo that has no `githooks/` yet:

```sh
mkdir -p githooks
cat > githooks/pre-commit <<'EOF'
#!/bin/sh
exec python -m crapkit hook-precommit
EOF
chmod +x githooks/pre-commit
printf 'githooks/pre-commit text eol=lf\n' >> .gitattributes
git add .gitattributes githooks/pre-commit
git update-index --chmod=+x githooks/pre-commit
git commit -m "add crapkit gate hook"
git config core.hooksPath githooks
```

**The `--chmod` goes between the `add` and the `commit`.** It writes the executable bit to
the index, so a commit that already happened does not carry it: run it after and `git
ls-tree HEAD` still says `100644`, which is a hook Unix checkouts silently skip. The
`.gitattributes` line is the harder half of the same failure: under Windows' default
`core.autocrlf` the hook checks out CRLF and `#!/bin/sh\r` dies on Linux and macOS with a
bad-interpreter error. `crapkit doctor` warns when a file under `core.hooksPath` is not
`100755` in the index and prints the `update-index` line for it.

Git will not read a hooks path out of a committed file, so that `git config` line belongs
in your CONTRIBUTING setup steps. Every clone arms the gate with it.

### Route 3: the pre-commit framework

crapkit ships a `.pre-commit-hooks.yaml` declaring `id: crapkit-gate`. In your
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/JeanFrancoisGagne/crapkit
    # crapkit's release step rewrites this line to the tag it just cut
    rev: v0.4.5
    hooks:
      - id: crapkit-gate
```

That file arms nothing on its own. The framework writes `.git/hooks/pre-commit` when you
tell it to, and until then `git commit` runs no gate and says nothing:

```sh
pip install pre-commit
pre-commit install
```

`pre-commit install` is the line every clone needs, the way Route 2 needs its
`git config core.hooksPath` line.

`rev` is a git ref pre-commit resolves against that remote. Pin a release tag, not a
branch: `pre-commit autoupdate` only moves between tags, and a moving `main` would change
your gate under you.

### Route 4: CI

A CI job runs on a fresh clone, which has no `.crapkit/` store, so bare `crapkit verify`
exits 1. Running `coverage` first would make the PR's own tree the baseline, a gate that
can never fail. The portable baseline is the mechanism:

```
# on the default branch, after a passing verify: commit this file
crapkit verify --emit-baseline crapkit-baseline.tsv

# in the PR job, against the committed baseline
crapkit verify --baseline-tsv crapkit-baseline.tsv --github
```

`--github` emits `::error file=...` annotations that land on the PR diff; `--sarif PATH`
writes SARIF 2.1.0 for code-scanning upload. Refresh the committed baseline whenever the
default branch's verify passes.

Two things the job has to do before those lines run. **Install crapkit**, `pip install
crapkit`, and pin the version the way Route 3 pins `rev`: an unpinned install moves your
gate on whatever day a release lands. **Fetch the whole history.** `actions/checkout`
clones one commit by default, `verify` reads the diff against the baseline's commit out of
git, and a shallow clone does not have that commit:

```
$ crapkit verify --baseline-tsv crapkit-baseline.tsv
crapkit: baseline commit a74260f321f is not an ancestor of HEAD (rebase or amend rewrote history) — run `crapkit coverage` for a fresh baseline
```

That is exit 4 on a `git clone --depth 1` of a repo whose baseline verifies at full depth.
Set `fetch-depth: 0` on the checkout step, which is what crapkit's own
[.github/workflows/ci.yml](.github/workflows/ci.yml) does.

The whole PR job, on GitHub Actions:

```yaml
on: pull_request
jobs:
  crapkit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # verify needs the baseline's commit
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install crapkit
      - run: pip install -e ".[dev]"   # your own test dependencies
      - run: crapkit verify --baseline-tsv crapkit-baseline.tsv --github
```

The second install is the one people leave out. `verify` reruns your lanes, so the job
needs whatever your test command needs: the coverage plugin, `npm ci`, a database, all of
it. Without them the lane writes no artifact and `verify` exits 5 quoting the runner's own
error, which is a broken job and not a verdict.

### What a refusal looks like

```
$ git commit -m "add route"
crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:
  ccn   7  app/m.py:9  route( a , b , c , d )
decompose before committing (coverage cannot save a function above the target).
```

That commit exited **1**, not 6. Git collapses any failed hook to 1, so 6 is a code you
only ever see by running the hook yourself: `crapkit hook-precommit` exits 6 on a
violation and 0 otherwise. The stderr block above is the same either way.

`CRAPKIT_OVERRIDE_REASON` is not a bypass. Setting it routes the commit through the full
three-record audit: an alert line through `alert_command`, a ratchet entry staged into the
commit, and a row in the override log. All three land or nothing does, and an unset
`alert_command` refuses the override outright. See
[docs/ratchet.md](docs/ratchet.md#overrides-and-the-audit-trail).

## Subcommands

Every subcommand takes `--repo PATH` (default `.`), and the flag goes **after** the
subcommand. `claude-hook` is the one exception: it has no `--repo`, because it takes its
root from the file named in the hook payload it reads.

```
$ crapkit worklist --repo /path/to/repo --scope util --top 1
worklist @ a7c5c85ac37 (run 1, floor ccn>=5, churn 12mo) — 1 active, 0 dormant
  risk      5.4  ccn   5 (  5 std)    5c/1a w   1.08  util/stats.py:1  bucket( value , low , high )
```

Before it, argparse reads the path as the subcommand name and exits 2 without ever
mentioning `--repo`:

```
$ crapkit --repo /path/to/repo worklist --top 1
crapkit: error: argument command: invalid choice: '/path/to/repo' (choose from 'inventory', 'coverage', ...)
```

`--json` prints one sorted-keys JSON object on stdout, always carrying a `schema` field.

| Command | What it does |
|---|---|
| `init` | Sniffs tracked source into per-directory scopes, writes a self-validated starter `crapkit.toml` whose lanes report into `.crapkit/cov/`, and appends `.crapkit/` plus each runner's own droppings to `.gitignore`. Writes a live `[[lane]]` when it can detect the test runner, otherwise a commented template. Refuses to clobber an existing config. |
| `doctor [--show-files] [--json] [--tune] [--plugin-root [PATH]]` | Checks the config still describes the repo: unknown keys (with the accepted spellings), zero-file scopes, tracked source no scope claims, scopes no lane covers, lane cwds and commands that no longer resolve, lizard importable, oversized files. It reads each lane command with the shell that will run it, so a quoted interpreter path is one word and a runner after `&&` is checked too, and it FAILs a lane whose runner does not resolve on PATH or that the shell cannot start, naming the word to change; each distinct runner is probed once, not once per lane. It WARNs on a lane writing its artifact at the repo root, a `coveragepy` or `istanbul` lane with no `results_artifact` (the crashed-worker and no-new-failures checks are off for it, whichever runner the lane spells), a committed hook under `core.hooksPath` that is not executable in the index, a directory whose functions are all `untested` while its tests exist, and a scope a lane measures with no `[crapkit.scoped_tests]` template behind it, which is the loop's step 4 with nothing to run. `--tune` prints suggested parallelism knobs and writes nothing. `--plugin-root PATH` reads no repo at all: it checks an installed [plugin](plugin/) against this CLI on both version and hook `--protocol`, one line per disagreement and silence when they agree; PATH is the plugin root or any directory above it, `~/.claude` included (only manifests named `crapkit` count, and the newest install wins), and with no PATH it looks in Claude Code's plugin cache. A root it found rather than one you typed is named first, as `crapkit doctor: checking PATH`. See [docs/agent-json.md](docs/agent-json.md#doctor---json). |
| `inventory [--db PATH] [--export PATH] [--json]` | One lizard pass over every in-scope file into a SQLite snapshot run, cached by content hash. `--db` is the only way to point crapkit at a store outside `.crapkit/`, and only this command accepts it. |
| `coverage [--lane NAME] [--reuse-artifacts] [--reuse-unchanged] [--export PATH] [--sarif PATH] [--github] [--json]` | Runs the lanes, joins branch coverage onto a fresh inventory, writes a scored run. A failed lane is recorded, not fatal: its scopes fall back to `no-lane` and the run is typed `partial`, so it can never serve as a baseline. See [docs/lanes.md](docs/lanes.md). |
| `verify [--baseline ID \| --base REF \| --baseline-tsv PATH] [--emit-baseline PATH] [--override REASON] [--reuse-artifacts] [--reuse-unchanged] [--no-tighten] [--sarif PATH] [--github] [--json]` | The full verdict against the trusted baseline: gate on touched functions, ratchet, no new test failures, optional diff-coverage ceiling. The three baseline selectors are mutually exclusive; `--baseline ID` also bypasses the taint rule ([The trusted baseline](#the-trusted-baseline)), and `--baseline-tsv` reads a commit-stamped file so a fresh clone verifies with no store. `--no-tighten` passes the verdict without rewriting the ratchet. Findings a dirty tree produced are tagged `dirty` and counted apart. It reads each istanbul artifact once for coverage, dead lines and its digest, and skips the artifact walk on an empty diff; skipping the whole run on an unchanged tree was measured and rejected, because a key made of HEAD plus the dirty names cannot see a second edit to a file that was already dirty. |
| `worklist [--top N] [--scope NAME] [--batches N] [--json]` | The risk map: every admitted function ranked by `ccn * churn weight`, floored by `worklist_floor`, with hot simple code and anything over its ceiling admitted past that floor. It ranks finished rows and `no-lane` rows too, marked `ok` and `no-lane`, so it never empties; `next-item` carries the stop condition. `--scope NAME` (repeatable) is exact, not a substring. `--batches N` **adds** a `batches[]` view cutting the active list into at most N file-disjoint batches with co-changing files kept together, off the same cached pairs `coupling` reads; the normal keys stay. |
| `next-item [--top N] [--exclude FRAG] [--scope NAME] [--claim]` | The actionable queue as JSON, with churn, budget estimates and uncovered lines. Same run and same admission floor as `worklist`, a different view of it: `no-lane` rows are skipped and counted in `skipped_no_lane`, and what is left is ranked by `crap` descending rather than by risk, so the item it hands out is often not the worklist's first row. `--exclude FRAG` (repeatable) skips items whose path or function name contains FRAG; `--scope NAME` (repeatable) is exact, not a substring. `--claim` holds what it hands out so a second session skips it. `stale` is true when the ranked run's commit is not HEAD, the same field `worklist` carries. Every item carries a `handle`: the bare identifier, or `(anonymous)#N` for a function with no name, which is the name form that survives the edit the item asks for. |
| `claims [list \| release PATH NAME \| release --all] [--json]` | The open claims, and the way to hand one back without waiting for a verify. `release` takes the bare identifier, the whole long name, or the `handle` the claim was taken under, which is the only one that picks out a single `(anonymous)` claim. |
| `brief FILE NAME [--batch N] [--json]` | The start-editing packet for one function: its own `source` text, every function in the file, the scored row and the scope ceiling, the ratchet mark and what the gate will bind on, uncovered lines, duplication twins, file churn, coupling partners, the config's notes, and the literal commands for the rest of the loop. Plus `handle`, `remedy` and the same `est_splits` / `est_uncovered_paths` the queue prints, and a `commands.refresh` that writes a run (`refresh_writes_run`) rather than re-reading the stale one. `NAME` takes the bare identifier, the long name `next-item` printed, the function's start line, `(anonymous)#N` for a function printed `(anonymous)` counting the file's anonymous functions from the top, or `NAME#2` for the second of several functions a file gives one name to. `--batch N` drops the positionals and emits `packets[]` instead: the top N of the queue, built from one read of the store and one duplication pass over the snapshot for the whole batch (batch of 5: 11.8 s to 5.2 s, output byte-identical to five separate calls). |
| `explain FILE NAME [--history] [--tests] [--json]` | A function's score across runs plus its mark. `NAME` resolves exact first: a function whose bare identifier or long name is exactly `NAME` wins, and only when nothing matches exactly does it fall back to a prefix match, so `route` explains `route` rather than every `route_*` beside it. It also takes the function's start line, the form `brief` takes, which is how you open one printed `(anonymous)`. `--history` adds the commits that touched it (`git log -L`), each carrying its message `body`, `--tests` the tests that covered it, which needs coverage.py contexts turned on ([recipe](docs/lanes.md#test-attribution-for-explain---tests)). `--json` emits the same content as one `schema` 1 object. |
| `rescore FILE ... [--gate] [--json]` | Fresh complexity for named files over the latest run's stale coverage, joined by name. Advisory: it writes no run. `--gate` applies the pre-commit hook's policy to the same selection the hook uses (functions the tree changed since HEAD), minus functions whose CRAP sits at or under their ratchet mark, and exits 6. A marked function past its mark is gated; the pre-commit hook exempts on the mark's existence instead, because a staged blob has no coverage to score. |
| `ratchet seed \| prune \| merge \| move \| report [--enforce] [--json]` | The mark lifecycle: seed new debt, prune gone code (a mark whose file git renamed follows it), merge as a git driver, move re-paths marks, report reads burn-down from the file's own git history. See [docs/ratchet.md](docs/ratchet.md). |
| `runs [list \| prune [--keep N]] [--json]` | Run history, and retention. `list` marks the run `verify` compares against today `baseline`, and prints `verdict=-` for a run that produces no verdict rather than one that failed. See [The trusted baseline](#the-trusted-baseline). `--keep` (default 5) is a floor on the newest trusted runs, not a cap: the digest pair, every passing verify baseline, every run an override names, and the newest non-hook run are kept too. `prune` VACUUMs afterwards. |
| `overrides [--json]` | The override audit trail: who granted what, when, and why. |
| `trend [--json]` | Totals per trusted run: functions, over-target count, CRAP load, average, per-scope rollup. It reads a per-run rollup table rather than rescanning every scored row, and fills that table for any run missing one, so it writes to the store (best effort: a read-only `.crapkit/` costs the speed, not the command). |
| `digest [--alert]` | The delta between the two newest runs with identical lane sets. Silent when nothing changed. `--alert` pipes the body to `alert_command` on stdin. Plain lines, never JSON. |
| `report [--out PATH]` | One self-contained HTML page written to `.crapkit/report.html` (or `--out PATH`, repo-relative), with the path printed on stdout. It renders what `worklist --json` and `trend --json` already answer at their defaults: the ranked worklist capped at `worklist_top`, the per-scope grades off the newest run, the trend series, and a banner naming every stale lane. It measures nothing and opens no network connection. Per-function CRAP and coverage are absent because no repo-wide payload carries them; each row prints the `crapkit explain` call that does. It reads the same per-run rollups `trend` does, and writes them on the same terms. |
| `duplication [--min-lines N] [--similarity F] [--top N] [--json]` | Near-duplicate functions by normalized line shingles with containment scoring. Defaults: `--min-lines 8`, `--similarity 0.8`, `--top 50`. `--top` truncates the list. A function and a function nested inside it never pair: their spans nest, they score 1.0 by construction, and nobody can deduplicate a factory from its own closure. |
| `coupling [--min-support N] [--min-confidence F] [--top N] [--json]` | File pairs that keep landing in the same commits. Defaults: `--min-support 5` shared commits, `--min-confidence 0.5` max-direction ratio, `--top 50`. Bulk commits never couple pairs, and a young repo returns nothing at the default support. The ranked pairs are cached in `.crapkit/coupling-cache-v1.json`, keyed on HEAD, the churn window, today's UTC date, the path format and a digest of the tracked set, and shared with `brief` and `worklist --batches` (warm: 1.05 s to 0.11 s on a 72k-commit repo). The date is part of that key, so the first run after midnight UTC rebuilds the pairs on an unchanged HEAD. `--top` reads the cache, because it truncates that same order; `--min-support` or `--min-confidence` off their defaults ask a wider question than the file answers, so they bypass it and recompute. |
| `mutate [--files F ...] [--max-mutants N] [--drop-pool] [--json]` | Diff-scoped mutation testing: flips comparisons, boundary shifts, boolean connectives and boolean literals on changed lines, runs `mutation_command` per mutant, lists survivors. `--files` replaces diff scope with the whole file. `--max-mutants` (default 100) caps the run and the cap warning goes to stderr only, so `mutants` in `--json` is the capped count. Shell and PowerShell files are refused by name on stderr rather than mutated: `<` and `>` are redirections there, not comparisons. With `mutation_workers > 1` the worker worktrees are kept at `.crapkit/mutate-pool/` and re-prepared per run (30.6 s to build four on a 31,459-file repo, 0.46 s to re-prepare them); `--drop-pool` removes them and exits. |
| `test-scoped FILE ...` | Runs each owning scope's `[crapkit.scoped_tests]` template on the files (quoted, longest-prefix scope wins). A template with no `{files}` runs as written, which is how a scope whose tests live outside its own paths runs its whole suite. Exit code only; a nonzero runner exits 1. |
| `hook-precommit` | The cc-only gate on staged blobs. No coverage, no snapshot, no repo-wide cache. Exit 6 on a violation. |
| `claude-hook [--protocol N]` | Reads one Claude Code PostToolUse payload from stdin and judges the file it edited: ccn against the scope ceiling, on functions the edit changed, minus functions a ratchet mark already covers. Advisory only. The edit has landed, nothing is blocked, and `hook-precommit` stays the enforcement point. Exit 2 with three lines on stderr is the only thing it ever says: no `crapkit.toml` above the edited file, an unscoped file, mid-rebase or mid-merge, a `--protocol` other than 1, source that parses to no functions, or any internal failure all exit 0 in silence. Takes no `--repo`, because the root is the first `crapkit.toml` above the edited file and the upward walk stops at a `.git` entry, so a worktree never borrows its parent's config. It opens no snapshot and writes nothing. |
| `watch [--interval SECONDS] [--cycles N]` | Rescores tracked files as they change (mtime polling, default 2s, subprocess-isolated so a half-saved syntax error never kills the watcher). `--cycles N` polls exactly N times and exits 0; without it the loop runs until ctrl-c. |
| `mcp` | A dependency-free stdio MCP server (newline JSON-RPC 2.0) exposing nine read-only tools. Every tool shells to the CLI's own `--json` surface, so the MCP view cannot drift from what the CLI reports. Answering from a kept in-process store was benchmarked and rejected: a packet's `source` would go stale behind the edit it describes. See [docs/agent-json.md](docs/agent-json.md#mcp-server). |

## Reading the output

### Flags: why a coverage number is missing

| Flag | Meaning | Scored |
|---|---|---|
| `measured` | A lane artifact spoke about this function. | Real `cov`. |
| `untested` | A lane covers the scope, but its artifact is silent on this function, which normally means no test imports the file. | `cov = 0`. A testing gap, and `uncovered_lines` comes back `null` because no artifact can name lines it never saw. |
| `no-lane` | No lane's `scopes` list names this function's scope. | `cov = 0`. A tooling gap, not a testing gap. `next-item` never hands one out and counts them in `skipped_no_lane`; `worklist` ranks them and marks the row `no-lane`, because a wiring gap is a risk you have to see. |
| `cc-only` | The scope sets `coverage_optional = true`, so no coverage number can exist. | `crap = ccn`, and `remedy` can only be `ok` or `decompose`. `uncovered_lines` comes back `null` with a note naming that setting. |

The coverage summary counts all four as `measured` / `untested` / `no_lane` / `cc_only`.

### Remedy: what to do about it

| Remedy | Condition | Action |
|---|---|---|
| `decompose` | `ccn > ceiling` | Split it. No amount of coverage clears this. |
| `add-tests` | `ccn <= ceiling` and `crap > ceiling` | Cover the branches. |
| `ok` | `crap <= ceiling` | Nothing. |

### Grade and CRAP load

The grade is the share of functions over their ceiling: `A+` at exactly zero, `A` under
2%, `B` under 5%, `C` under 10%, `D` under 20%, `F` at 20% or more. `crap_load` beside it
is the plain sum of every function's CRAP score, so it moves when a function gets better
even if the letter does not.

### Risk: what ranks the worklist

`risk = ccn * churn weight`. The weight is a time-weighted sum over the file's commits in
the churn window: each commit contributes a logistic weight rising to 0.5 for the newest
commit in the log and falling to near zero for the oldest, so five edits last month
outrank fifty from two years ago. The window anchors on the newest commit, never on the
wall clock, so a fixed tree ranks identically forever.

Age is not the input, position in the log is. A log whose commits all share one timestamp
reads 0.0 everywhere, which is why a one-commit repo shows `risk 0.0` on every row and
falls back to ccn order. Commits minutes apart already rank. This repo was eight commits
old, all made the same day:

```
$ crapkit worklist --scope util
worklist @ a7c5c85ac37 (run 1, floor ccn>=5, churn 12mo) — 3 active, 0 dormant
  risk      5.4  ccn   5 (  5 std)    5c/1a w   1.08  util/stats.py:1  bucket( value , low , high )
  risk      4.5  ccn   9 (  9 std)    1c/1a w   0.50  util/curve.py:1  curve( scores , mode , floor , ceiling , skip_none )
  risk      4.3  ccn   4 (  4 std)    5c/1a w   1.08  util/stats.py:13  spread( values , cap )  ok
```

`bucket` at ccn 5 outranks `curve` at ccn 9 because five commits touched it and one
touched `curve`. That is the whole point of weighting by churn. `spread` carries the `ok`
marker: already at or under its ceiling, listed anyway, and `next-item` would not hand it
out.

The list splits in two: **active** (files with commits in the window) and **dormant**
(zero churn, kept out of the queue but counted). Two rules reach under the
`worklist_floor`. A file whose churn weight sits in the top 10% is promoted down to ccn 3,
which is why `spread` appears above at ccn 4. And a function over its ceiling is admitted
whatever its ccn, so the floor can never hold back debt.

### The trusted baseline

Every `verify` measures the working tree against one earlier run, the **trusted
baseline**. `crapkit runs list` marks which one that is today.

**Which runs qualify.** A `coverage` run, or a `verify` that passed. A failed `verify`
never qualifies, and neither does a `partial` run (a lane failed, so some scope fell back
to `no-lane`) nor a `hook` override record, which carries no scored rows at all. In `runs
list`, `verdict=-` marks a run that produces no verdict rather than one that failed: only
`verify` renders a verdict. Four readers ask this one question and get this one answer: the
baseline pick here, `ratchet seed`, `prune`, and the tighten damping that compares a mark
against the same commit's previous run. A mark can no longer be signed off a run `verify`
refused.

**What advances it.** Any qualifying run. `coverage` writes one wherever HEAD is, so a
dashboard cron advances the baseline exactly as CI does. A passing `verify` advances it
and tightens the ratchet on the way.

**The taint rule.** A failed `verify` recorded findings against a tree. Until some
`verify` passes, runs made after that failure do not become the baseline: choosing one
would move the comparison point past the findings, the flagged function would stop
counting as touched, and nothing would look at it again. `verify` says which run it
refused and falls back to the newest run in front of the failure.

```
$ crapkit runs list
run   1 @ 88012a148f6 2026-08-23T09:27:46Z coverage  verdict=-      lanes=py  baseline
run   2 @ 803bdde8556 2026-08-23T09:27:53Z verify    verdict=FAILED lanes=py
run   3 @ 803bdde8556 2026-08-23T09:28:02Z coverage  verdict=-      lanes=py

$ crapkit verify
warning: run 3 is not the baseline: verify run 2 FAILED with 1 finding(s) and no passing verify has cleared it since — measuring against run 1 @ 88012a148f6 instead, so those findings stay visible. Fix them, or pass `--baseline 3` to accept the newer run deliberately.
verify FAILED @ d89068de7f3 vs baseline 88012a148f6 (2 changed files)
  GATE  crap     72.0  ccn   8 cov 0%  calc/legacy.py:7  legacy_router( a , b , c , d , e )  -> decompose
  findings: 1 committed / 0 dirty (uncommitted edits and untracked files)
```

Run 3 is a `coverage` run somebody took on the tree run 2 refused, and it scores the same
ccn-8 function. Without the rule it would have become the baseline, `legacy_router` would
have stopped being a touched function, and that gate line would never print again.

**The escape, twice.** Fix the findings and let a `verify` pass, which clears the taint
for good. Or accept the newer run on purpose with `verify --baseline 3`: an explicit id
bypasses the rule, and the run history records which run the verdict used. Nothing here
touches a repo that has never run `verify`: with no failure to protect, `coverage` alone
always advances the baseline.

**When the id you pass cannot serve.** A `--baseline ID` naming a real run that is not a
candidate says which run it is, why, and which ones can:

```
$ crapkit verify --baseline 3
crapkit: run 3 is an inventory run (no coverage was measured) and cannot serve as a baseline; trusted runs: 1, 2; pass `--baseline 2` for the newest
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. For `verify` and `hook-precommit`: the gate passed. |
| 1 | **Overloaded.** Three unrelated things, listed below the table. |
| 2 | Usage error from argparse: unknown flag, missing positional. Raised before crapkit's own error handling. |
| 3 | Config error: `crapkit.toml` missing or unparseable, an unknown language or parser, a lane command the shell that runs it reads as a narrowed suite, a ratchet metric-stamp mismatch ([Upgrading from 0.4.4](#upgrading-from-044)), a `test-scoped` file under no scope or under a scope with no template. |
| 4 | Git error: not a repository, a baseline commit rewritten out of the history. |
| 5 | Tool error: lizard not importable, a lane produced no artifact, a lane timed out past its retries, an override alert command failed. A `timeout_seconds` kills the whole process tree, so no orphan suite keeps running behind the failure. |
| 6 | Gate violation. A function the diff touched is over its ceiling and past any ratchet mark it carries: an edit that leaves a marked function at or under its mark is the debt the repo signed for and is exempt. Also `rescore --gate`, which applies the same rule, and `hook-precommit`, which exempts on the mark's existence instead. |
| 7 | Ratchet regression the diff never touched. A marked function scores worse than its recorded high-water mark; a touched one past its mark reports 6. |
| 8 | New test failures against the baseline run. Failures the baseline already had do not count. |
| 9 | Diff-coverage ceiling breached: `diff_uncovered_max` is set and more changed lines than that never ran. |

### Exit 1 means one of three things

CI cannot tell a crash from a clean policy verdict on the code alone. Which one you got
depends on the command:

| Command | What exit 1 means |
|---|---|
| `doctor` | A **`FAIL` finding**. This is a verdict, not a crash. A `WARN` (an unmeasured directory, or a lane writing its artifact at the repo root) and a `note` (a file over `max_file_bytes`, or no lanes declared) both exit 0. |
| `ratchet report --enforce` | The **debt policy was breached**. Also a verdict. |
| anything else | An unexpected error: "no snapshot yet, run `crapkit coverage` first", a `brief` name that matches no function, a `test-scoped` runner that exited non-zero. |

`verify` reports the **first** of 6, 7, 8, 9 that fires, in that order. A gate violation
and a ratchet regression together report 6. A run that takes any of them fails, so it
neither advances the baseline nor tightens the ratchet, exit 9 included.

## Quickstart: Python

A repo with `calc/grade.py`, `tests/test_grade.py`, and a `pyproject.toml`. Commit first;
crapkit reads `git ls-files`. Install the coverage plugin first, because the lane `init`
writes runs `pytest --cov` and those flags come from `pytest-cov`:

```
pip install pytest-cov
```

(`pip install "crapkit[py]"` pulls both at once when crapkit shares the suite's venv.)

If your suite drives its own CLI through `subprocess.run`, add `[tool.coverage.run]
patch = ["subprocess"]` to `pyproject.toml` and keep `coverage>=7.10.6`: pytest-cov 7.0.0
dropped subprocess measurement, so without that key every entry point scores 0% and nothing
warns. [docs/lanes.md](docs/lanes.md) has the whole rule.

### 1. Scaffold the config

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): calc
detected 1 lane(s) from this repo's own files: py — next: run `crapkit coverage`
added to .gitignore: .crapkit/, .coverage, __pycache__/
```

`init` sniffs tracked source into one scope per top-level source directory, and detects a
coverage lane from what the repo already has: a pytest marker file (`pyproject.toml`,
`pytest.ini`, `setup.cfg`) writes a live `[[lane]]`, and so does a `test` script or
`vitest`/`jest` in `package.json`. Whatever it detects, it also leaves commented templates
for the runners it did not find. Every lane it writes reports into `.crapkit/cov/`, which
is why the `.gitignore` list is so short: see
[Where artifacts live](docs/lanes.md#where-artifacts-live).

```toml
[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[exclude]
globs = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**", "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py", "**/*_test.go", "*.config.ts", "*.config.js", "*.config.mts", "**/*.config.ts", "**/*.config.js", "**/*.config.mts"]

[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit-py.xml"
parser = "coveragepy"
scopes = ["calc"]

# Declare one [[lane]] per coverage command, then run `crapkit coverage`.
# [[lane]]
# name = "js"
# command = "npx vitest run --coverage --coverage.reportsDirectory=.crapkit/cov/js --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml"
# artifact = ".crapkit/cov/js/coverage-final.json"
# results_artifact = ".crapkit/cov/js/junit.xml"
# parser = "istanbul"
# scopes = ["<your-scope>"]

# `crapkit test-scoped FILES` runs one command per scope, with {files}
# replaced by that scope's files, each quoted.
[crapkit.scoped_tests]
calc = "python -m pytest {files} -q -p no:cacheprovider"
```

The last block is the one an agent loop needs. `crapkit test-scoped` exits 3 for a file
whose scope declares no template, and [AGENTS.md](AGENTS.md#4-run-the-owning-scopes-tests)
makes it step 4 of the burn-down loop. Every key is in
[docs/configuration.md](docs/configuration.md).

### 2. Check the config against the repo

```
$ crapkit doctor
ok   config keys all recognized
ok   scope 'calc': 1 files
ok   every tracked source file belongs to a scope
ok   1 lane(s) declared
ok   lizard 1.24.0
doctor: no problems found
```

`doctor` prints one line per check and exits 1 only on a `FAIL`. `WARN` and `note` report
and exit 0.

### 3. Score the repo, and read the queue

```
$ crapkit coverage
run 1 @ fae4db93108: 2 functions scored — 2 measured / 0 untested / 0 no-lane / 0 cc-only, 1 over target 6, CRAP load 41.0, grade F

$ crapkit worklist
worklist @ fae4db93108 (run 1, floor ccn>=5, churn 12mo) — 1 active, 0 dormant
  risk      0.0  ccn  14 ( 14 std)    1c/1a w   0.00  calc/grade.py:7  classify( score , attempts , late , bonus )
```

Columns: `risk`, `ccn` with the standard-only ccn in parentheses,
`<commits>c/<authors>a` in the churn window with `w<weight>`, `path:line`, the function's
long name, then a marker on rows the burn-down queue will not hand out (`ok`, `no-lane`).

**`worklist` is the risk map, not a to-do list.** It ranks finished rows too, so it does
not empty when the burn-down does. `next-item` is the other view of that run: it drops the
`no-lane` rows, ranks by `crap`, and its `empty: true` is the stop condition.

### 4. Take the top item

```
$ crapkit next-item
{"commit": "fae4db93108b4841a00959f9117430679e7250ca", "empty": false, "item": {"authors": 1, "ccn": 14, "ccn_std": 14, "cognitive": 13, "commits": 1, "cov": 0.5, "crap": 38.5, "end": 28, "est_splits": 3, "est_uncovered_paths": 7, "flag": "measured", "function": "classify( score , attempts , late , bonus )", "handle": "classify", "nesting": 8, "nloc": 22, "path": "calc/grade.py", "remedy": "decompose", "scope": "calc", "start": 7, "target": 6, "uncovered_lines": [9, 11, 15, 17, 19, 24, 25, 26, 27, 28]}, "run_id": 1, "schema": 1, "skipped_no_lane": 0, "stale": false}
```

`remedy: "decompose"`, `est_splits: 3` (this needs roughly three pieces to fit under 6),
and `uncovered_lines` naming the ten lines no test walks. `handle` is the name form to
pass back, and `stale: false` says the run still describes HEAD. Every field is in
[docs/agent-json.md](docs/agent-json.md).

### 5. Seed the ratchet

Arm the debt gate before fixing anything. `ratchet seed` records every over-target
function at its current score, and from then on nothing may get worse.

```
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 — 1 mark(s) vs run 1 (fae4db93108)

$ git add crapkit.toml crapkit-ratchet.tsv .gitignore && git commit -m "adopt crapkit"
```

### 6. Fix it and verify

Extract until every piece sits at or under the ceiling. Here `classify` became
`_validate`, `_adjusted`, `_band` and a `classify` that only sequences them, with the
table of cases pushed into parametrized tests. Commit the fix, then:

```
$ crapkit verify
verify OK @ 8d10c13303d vs baseline fae4db93108 (5 changed files)

$ crapkit coverage
run 3 @ 8d10c13303d: 5 functions scored — 5 measured / 0 untested / 0 no-lane / 0 cc-only, 0 over target 6, CRAP load 19.0, grade A+
```

CRAP load 41.0 to 19.0, grade F to A+. `verify` reruns the lanes and checks three things
against the trusted baseline: every function the diff touched sits at or under its
ceiling, no marked function got worse, and no test that passed in the baseline fails now.
Exit 0 advances the baseline and tightens `crapkit-ratchet.tsv` in place, so the repaid
mark leaves the file: follow up with `git commit -am "ratchet: classify repaid"`. The full
mark lifecycle is in [docs/ratchet.md](docs/ratchet.md).

`crapkit next-item` now comes back `empty: true` with a `reasons` object saying which
ending you got. That is most of the stop condition, not all of it:
[AGENTS.md](AGENTS.md#the-termination-rule) states the whole rule and reads the rest of
`reasons`.

## Quickstart: TypeScript

A vitest repo with `src/grade.ts` and `test/grade.test.ts`.

### 1. Scaffold the config

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): src
detected 1 lane(s) from this repo's own files: js — next: run `crapkit coverage`
added to .gitignore: .crapkit/
```

The lane `init` wrote is
`npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml`.
It reads vitest's `json` reporter from `.crapkit/cov/js/coverage-final.json`; the
`reportsDirectory` flag is what keeps that report out of your root. The junit half is the
lane's `results_artifact`, which the crashed-worker and no-new-failures checks read; both
reporters are named because `--reporter=junit` alone would replace the console output you
watch the suite through. Anything that produces
an istanbul `coverage-final.json` works; see [docs/lanes.md](docs/lanes.md) for the
[jest](docs/lanes.md#jest) and [pytest](docs/lanes.md#pytest) recipes, a package
[one directory down](docs/lanes.md#running-from-a-subdirectory), and a
[crapkit root below the repo top](docs/lanes.md#a-crapkit-root-below-the-repo-top).

### 2. Install a coverage provider

**This is the step that stops most TypeScript users.** vitest ships no coverage provider
by default. Without one, `init` and `doctor` are both happy and `coverage` dies with
exit 5:

```
$ crapkit coverage
crapkit: lane 'js' FAILED: lane 'js' produced no artifact at .crapkit/cov/js/coverage-final.json (command exit 1); last output: $ npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml

 MISSING DEPENDENCY  Cannot find dependency '@vitest/coverage-v8'

(exit 1)
crapkit: every lane failed: ...
```

That failure **writes no run**. Every lane failed, so `coverage` exits before it opens a
store: there is no `.crapkit/crap.sqlite` yet and the run ids below still start at 1.

Install the provider, and pin the major yourself. Unpinned, npm resolves the newest
provider against your older vitest and refuses the tree:

```
npm i -D "@vitest/coverage-v8@<your vitest major>"
```

| Question | Answer |
|---|---|
| Which provider? | Either works. `@vitest/coverage-v8` is vitest's default and needs no config. `@vitest/coverage-istanbul` also works and needs `coverage.provider = "istanbul"` in your vitest config. |
| Which crapkit parser? | Both feed `parser = "istanbul"`. The provider name and the parser name are unrelated: v8 output is remapped to the istanbul JSON schema before it is written. |
| Which version? | The provider's major has to match vitest's. On vitest 2 that is `npm i -D "@vitest/coverage-v8@2"`, on vitest 3 `npm i -D "@vitest/coverage-v8@3"`. Drop the pin and npm answers `ERESOLVE unable to resolve dependency tree`, naming the peer it could not satisfy. |

The artifact crapkit wants is `coverage-final.json`, written by vitest's `json` coverage
reporter, which is on by default. If your vitest config sets `coverage.reporter`
explicitly, keep `"json"` in the list.

One more vitest default worth flipping now: it writes **no coverage report at all when the
run fails**, so a single red test becomes a missing artifact and a lane failure. Set
`coverage.reportOnFailure = true`. The full config block is in
[docs/lanes.md](docs/lanes.md#reportonfailure).

### 3. Score the repo

```
$ crapkit coverage
run 1 @ 8bfbe613fcd: 2 functions scored — 2 measured / 0 untested / 0 no-lane / 0 cc-only, 1 over target 6, CRAP load 56.68, grade F

$ crapkit worklist
worklist @ 8bfbe613fcd (run 1, floor ccn>=5, churn 12mo) — 1 active, 0 dormant
  risk      0.0  ccn  15 ( 15 std)    1c/1a w   0.00  src/grade.ts:8  classify ( row Row )
```

`classify` is ccn 15 against a ceiling of 6: one function holding the late-and-retry
penalty, the letter bands, the demotion rule and the null case.

### 4. Seed the ratchet and commit

`ratchet seed` records every over-target function at the score it has today, so nothing
can get worse while you burn this one down.

```
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 — 1 mark(s) vs run 1 (8bfbe613fcd)

$ git add crapkit.toml crapkit-ratchet.tsv .gitignore && git commit -m "adopt crapkit"
```

### 5. Fix it

Above the ceiling, coverage cannot help, so `classify` gets split rather than tested.
`penalty`, `band` and `demote` come out as their own exported functions, and `classify`
keeps the null case and the bonus:

```ts
export function classify(row: Row): string {
  if (row.score === null) {
    return "N/A";
  }
  let score = row.score - penalty(row.attempts, row.late);
  if (row.bonus && score < 90) {
    score += 3;
  }
  return demote(band(score), row);
}
```

`rescore --gate` judges that edit on complexity alone, before the slow step:

```
$ crapkit rescore src/grade.ts --gate
rescore vs run 1 @ 8bfbe613fcd (coverage STALE, complexity fresh)
   ccn   cov     crap  remedy     function
     5    0%     30.0  add-tests  src/grade.ts:22  band ( score )
     5    0%     30.0  add-tests  src/grade.ts:38  demote ( letter , row Row )
     4    0%     20.0  add-tests  src/grade.ts:8  penalty ( attempts , late )
     4   45%      6.7  add-tests  src/grade.ts:48  classify ( row Row )
     4   75%      4.2  ok         src/grade.ts:59  average ( scores Array )
```

Exit 0: every piece is at or under 6. The `crap` column is loud because its coverage half
is still run 1's, from before three of those functions existed, and `add-tests` is the
literal instruction for step 6.

### 6. Cover the new pieces

`rescore --gate` passed on complexity, not on coverage. `penalty`, `band` and `demote` are
three functions no test has ever called, so each gets a table test:

```ts
describe("band", () => {
  it.each([[95, "A"], [85, "B"], [75, "C"], [65, "D"], [10, "F"]])(
    "scores %i as %s", (score, expected) => expect(band(score)).toBe(expected));
});
```

Run the suite once before the slow step:

```
$ npx vitest run
 Test Files  1 passed (1)
      Tests  21 passed (21)
```

Skip this step and step 7 fails rather than passes. Run on a copy of this repo with step 6
left out, `verify` reruns the lanes against the real tree and three functions the old
suite never called come back over the ceiling:

```
$ crapkit verify
verify FAILED @ 0296156ff21 vs baseline 0e646697946 (1 changed files)
  GATE  crap     17.8  ccn   5 cov 20%  src/grade.ts:38  demote ( letter , row Row )  -> add-tests
  GATE  crap     12.4  ccn   5 cov 33%  src/grade.ts:22  band ( score )  -> add-tests
  GATE  crap     10.8  ccn   4 cov 25%  src/grade.ts:8  penalty ( attempts , late )  -> add-tests
```

### 7. Verify

```
$ crapkit verify
verify OK @ 2af3433d979 vs baseline 8bfbe613fcd (3 changed files)

$ crapkit coverage
run 3 @ 2af3433d979: 5 functions scored — 5 measured / 0 untested / 0 no-lane / 0 cc-only, 0 over target 6, CRAP load 22.0, grade A+
```

CRAP load 56.68 to 22.0, grade F to A+, and the mark seeded in step 4 is gone: `verify`
dropped it once `classify` scored under the ceiling, rewriting the tracked
`crapkit-ratchet.tsv` in place. Commit it with your change. Marks only ever fall.

A verify may also print `warning: N changed line(s) have no coverage` above its verdict;
that block is advisory unless `diff_uncovered_max` is set
([docs/configuration.md](docs/configuration.md)).

## Documentation

| Page | Covers |
|---|---|
| [The handbook](https://jeanfrancoisgagne.github.io/crapkit/handbook.html) | **Start here for anything deeper.** The illustrated handbook: what crapkit is, how every piece works, and where each command earns its keep. Also at [docs/handbook.html](docs/handbook.html), self-contained, so it opens straight from a clone. |
| [docs/adoption.md](docs/adoption.md) | The judgment layer over the quickstarts: scope granularity, exclude vs lane, scoped_tests wiring, the first-verify taint hazard. |
| [docs/configuration.md](docs/configuration.md) | Every `crapkit.toml` key: type, default, and what it does. |
| [docs/lanes.md](docs/lanes.md) | The lane model, vitest and jest and pytest recipes, artifact reuse, flake retest, containers. |
| [docs/ratchet.md](docs/ratchet.md) | Seeding, pruning, the git merge driver, metric stamps, debt policy, overrides. |
| [docs/agent-json.md](docs/agent-json.md) | The machine surface: `schema`, every payload field, real captured examples. |
| [AGENTS.md](AGENTS.md) | The burn-down loop an agent runs, and the rules for changing crapkit itself. |
| [plugin/](plugin/) | The Claude Code plugin: three skills, the read-side MCP server, and the advisory PostToolUse hook. |

[crapkit.schema.json](crapkit.schema.json) is the authority on the config file shape.

## Development

```
pip install -e ".[dev]"
pip install pytest-xdist
git config core.hooksPath git-hooks
python -m pytest -q
```

`pytest-xdist` is not optional: `tests/fixtures/mini_repo` declares a lane that shells out
to `pytest ... -n 2`, and without it that subprocess dies on an unrecognized `-n`. The
`git config` line arms the complexity gate on your own commits. Same steps, with what each
one buys, in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
