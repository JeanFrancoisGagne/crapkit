# Coverage lanes

A lane is how crapkit gets coverage: it runs a command you already have, reads the artifact
that command writes, and maps the result onto the scopes it claims.

```toml
[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit-py.xml"
parser = "coveragepy"
scopes = ["calc"]
```

Four required parts and one rule each, plus `results_artifact`, which is optional and on
every lane on this page.

| Part | Rule |
|---|---|
| `command` | Runs through the shell at the repo root (or `cwd`). Its **exit code is recorded, not enforced**. A brownfield suite with 98 known failures still writes a valid artifact, and demanding green here would make such a repo unmeasurable. |
| `artifact` | The coverage file the command writes, repo-relative. Its absence after the command and all its retries is the failure. Two lanes may not declare the same artifact path: reused paths cross-attribute coverage under `--reuse-artifacts`. |
| `parser` | `istanbul` or `coveragepy`. Nothing else exists. |
| `scopes` | Which scopes this lane's numbers speak for. A scope no lane names can only score `no-lane`, and `doctor` fails on it. |
| `results_artifact` | The JUnit report the same command writes. Two checks read it and neither runs without it: the [crashed-worker trust check](#a-junit-that-says-the-run-did-not-finish), which refuses a run the runner did not finish, and no-new-failures, which is `verify`'s exit 8. |

**Every lane on this page declares one**, because coverage alone cannot tell a finished
suite from a suite that lost a worker: both write a coverage artifact. The junit report is
the only thing that says which one happened, so a lane without one measures fine and
verifies blind. `doctor` says so, per lane, with the flag to add:

```
$ crapkit doctor
ok   config keys all recognized
ok   scope 'calc': 1 files
ok   every tracked source file belongs to a scope
ok   1 lane(s) declared
WARN lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it; add --junitxml=.crapkit/cov/junit-py.xml to the command and results_artifact = ".crapkit/cov/junit-py.xml" to the lane
ok   lizard 1.24.0
doctor: no problems found
```

A WARN, never a FAIL: the lane still scores. `crapkit init` writes both halves on the lanes
it detects, so a repo scaffolded since 0.4.5 starts with the checks on.

Output streams to `.crapkit/lane-<name>.log` while the command runs. Tail that file to
supervise a long suite; crapkit prints nothing until the lane finishes.

Every key, including the optional ones, is tabled in
[configuration.md](configuration.md#lane).

---

## How a lane command is read

The command runs through a shell, so crapkit reads it with the shell that will run it: sh
on POSIX, cmd.exe on Windows. One reading feeds two readers. The lane guard uses it to
decide whether a token narrows the run, and `doctor` uses it to decide which word is the
runner and which words are files the repo owes.

| What you write | How it reads |
|---|---|
| `-m "not live and not perf"` | One argument. Double quotes are the portable spelling: both shells drop them and hand the runner one token. |
| `-m 'not live and not perf'` | Five arguments on Windows. cmd.exe has no single-quote rule, so pytest gets `'not`, `live`, `and`, `not`, `perf'` and the lane is refused with a hint. |
| `-k ^"not slow^"` | One argument. Outside a quoted run cmd.exe drops the caret and hands on the character behind it, so the runner gets `-k "not slow"`. Inside a quoted run the caret stays: `-k "a^b"` reaches the runner with its caret. |
| `--cov-report=json:"cov/py 1.json"` | One argument. A quote opens a quoted run wherever it sits, mid-token included. |
| `-k "" tests` | Three arguments. An empty pair of quotes writes an empty argument, so `tests` stays the positional it is. Dropping it would slide `tests` onto `-k` and the narrowing lane would load clean. |
| `pytest --cov && coverage json` | Two commands. `&&`, `\|\|`, `&` and `\|` each start a new one, and every segment that runs the runner is checked on its own. |
| `pytest --cov > lane.log 2>&1` | The redirections are the shell's; the runner never sees them. A quoted `">"` is an argument and stays. |
| `pytest --cov; echo done` | On sh the `;` ends the command. To cmd.exe it is an ordinary character, so `echo` and `done` land in pytest's argv and the lane is refused. |
| a non-breaking space in a value | Not a word break. Words break on space, tab and line endings, the way both shells break them, so a value pasted out of rendered docs stays one token. |

A command the shell itself would refuse (a quote that never closes) falls back to a
whitespace split. A rough lint beats a crash at config load.

### The refusals, as they print

Single quotes on Windows, the most common way to trip the guard:

```
# command = "python -m pytest -m 'not live and not perf' --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json"
$ crapkit doctor
crapkit: lane 'py': positional argument 'live' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately (cmd.exe does not treat ' as a quote: write the value in double quotes)
EXIT=3
```

The same command in double quotes loads, and so does the caret spelling
(`-k ^"not slow^"`), the mid-token quote
(`--cov-report=json:".crapkit/cov/py report.json"`) and the redirected form
(`... --cov-report=json:.crapkit/cov/py.json > lane.log 2>&1`). All four come back
`doctor: no problems found`, exit 0.

A second run after `&&` narrows as much as the first, so the segment it sits in is checked
too:

```
# command = "python -m pytest --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json && python -m pytest calc/hot.py"
$ crapkit doctor
crapkit: lane 'py': positional argument 'calc/hot.py' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately
EXIT=3
```

The refusal names a word from the segment it read, never from the next command.

On Windows a `;` starts nothing, so what follows it is pytest's:

```
# command = "python -m pytest --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json; echo done"
$ crapkit doctor
crapkit: lane 'py': positional argument 'echo' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately
EXIT=3
```

Write that lane as two `&&` segments and both shells agree about it.

### What doctor does with the same reading

`doctor` checks the runner of every segment, not just the first word of the line. A quoted
interpreter path (`"C:/Program Files/Python/python.exe" -m pytest ...`) is one word, not
two; a dead runner after `&&` is still a dead runner; and a path inside a quoted
`-k "tests/gone.py or x"` is a marker expression, not a file the repo owes.

It also starts each distinct first word once, with `--version`, and FAILs the lane when the
shell cannot run it. A stock Windows PATH carries a `python.exe` stub with no Store app
behind it: it resolves, it exits 9009, and before 0.4.5 `doctor` called that repo clean
while `coverage` exited 5 on the same command. Reproduced here with a `python3` on PATH
that exits 9009:

```
$ crapkit doctor
ok   config keys all recognized
ok   scope 'calc': 1 files
ok   every tracked source file belongs to a scope
FAIL lane 'py': cmd.exe cannot run 'python3' (exit 9009) — the lane cannot start, so its scopes can only ever score no-lane
ok   lizard 1.24.0
doctor: 1 problem(s)
```

The probe is memoized on the word, so a repo declaring 14 lanes over 2 runners starts two
processes, not fourteen.

---

## Which languages a lane can measure

Short answer: whichever ones your runner reports in one of two formats.

A parser reads an artifact format, not a language. `coveragepy` reads coverage.py's JSON
report. `istanbul` reads `coverage-final.json`, which vitest, jest and every other
istanbul-shaped runner write. If your suite writes one of those two files, declare a lane and
crapkit measures it. In practice that is Python plus the JavaScript and TypeScript family.

Anything else has no lane to declare today. Go's coverprofile, `cargo-llvm-cov`, Swift's
llvm profdata, JaCoCo XML: crapkit reads none of them, and no config key unlocks one.
Somebody writing the parser is the only thing that changes that.

**Those scopes run cc-only, which is a real answer, not a failure.** Complexity, the
worklist, the ratchet and the commit gate all work on a cc-only scope; `crap` is just `ccn`,
because the coverage half was never measured. The scope carries `coverage_optional = true`
so `doctor` stops asking for a lane that cannot exist, and `crapkit init` writes that key
for you. See [configuration.md](configuration.md#coverage_optional--true).

A repo where **every** scope is cc-only declares no `[[lane]]` at all, and both `coverage`
and `verify` run on it: the lane list is empty because there is nothing left to measure,
not because something is unwired. They still exit 3 for a scope that has neither a lane nor
the key, and the message names that scope.

---

## Where artifacts live

Point every `artifact` and `results_artifact` under `.crapkit/`. `crapkit init` ignores
that directory in the same breath it writes the config, so nothing a lane drops there ever
reaches the consumer's `git status`.

Lanes that let their runner default instead put a coverage directory and a junit file at
the repo root, one per lane. A 14-lane repo grew fifteen `coverage-*` directories and seven
junit files that way. Every lane worked; nothing in the tree said which lane owned which
file.

`init` scaffolds the convention, and each runner has its own flag for it:

| Runner | Flag it takes | Resulting `artifact` |
|---|---|---|
| pytest | `--cov-report=json:.crapkit/cov/py.json` | `.crapkit/cov/py.json` |
| vitest | `--coverage.reportsDirectory=.crapkit/cov/js` | `.crapkit/cov/js/coverage-final.json` |
| jest | `--coverageDirectory=.crapkit/cov/js` | `.crapkit/cov/js/coverage-final.json` |

The two JS flags are not interchangeable: jest exits on vitest's spelling and vitest exits on
jest's. `init` writes one only when `package.json` names exactly one of the two runners in
`devDependencies`. `npm test` can run anything, and a wrong flag turns a working lane into an
argument error. A repo naming neither or both keeps its runner's default directory, and
`doctor` says so:

```
WARN lane 'js' writes coverage/coverage-final.json at the repo root — point it under .crapkit/ (for example .crapkit/cov/js/) to keep the tree clean
```

That warning is never a `FAIL`. A lane writing at the root measures exactly what it always
did, so breaking an existing gate over tree hygiene would cost more than the litter. A lane
that writes inside a scope's own tree (`web/coverage/` beside the `web/src` it measures) is
that package's business and is not warned about.

### What the istanbul parser reads

Your runner writes `coverage-final.json` and you never open it. Read this section only if
you are building one by hand, converting another format into it, or staring at a lane that
scores nothing.

crapkit scores functions, so `fnMap` is the part that decides everything. Per file in the
artifact:

| Key | What crapkit does with it |
|---|---|
| `fnMap` | The function list. Every entry needs `decl.start.line`. `loc.end.line` closes the span and falls back to the start line. A missing `name` reads as `(anonymous)`. |
| `f` | Call counts per `fnMap` id. |
| `branchMap` and `b` | Branch coverage. Each branch counts against the innermost function whose span holds its `loc.start.line`. This is the function's coverage whenever it has one branch. |
| `statementMap` and `s` | The fallback for a function with no branch in its span, and the only source of the uncovered lines `verify` measures a diff against. |

A function with neither a branch nor a statement in its span scores on `f` alone: 1.0 when
it was called, 0.0 when it was not.

The outer object is keyed by path. crapkit strips the crapkit root off an absolute key and
takes any other key as it stands, so root-relative keys work too. `path_prefix` is
coverage.py's key and does nothing here.

An artifact that names no functions is not an error. It parses, every scored function falls
to `untested`, and nothing says so. Same repo, same two TypeScript files, one lane whose
command writes the artifact by hand:

```
$ crapkit coverage        # artifact holds path, statementMap and s
run 1 @ 6f736a5b12a: 2 functions scored — 0 measured / 2 untested / 0 no-lane / 0 cc-only, 2 over target 6, CRAP load 32.0, grade F
$ crapkit coverage        # same file plus fnMap, f, branchMap and b
run 2 @ 6f736a5b12a: 2 functions scored — 2 measured / 0 untested / 0 no-lane / 0 cc-only, 0 over target 6, CRAP load 7.39, grade A+
```

Both exit 0. That is the [wrong `path_prefix`](#running-from-a-subdirectory) failure from the
other side: the lane ran, the artifact parsed, and the score is wrong. `0 measured` on a lane
that ran is the number to read.

One shape does fail loudly. An `fnMap` entry with no `decl` exits 5:

```
crapkit: lane 'js' FAILED: unparseable istanbul artifact: 'decl'
```

### What else lives in .crapkit/

Everything crapkit writes goes in one gitignored directory beside `crapkit.toml`. One file
is durable state, a few are output you asked for, and the rest are caches. Delete a cache
and the next run rebuilds it, a little slower. A cache that is unreadable, torn or keyed for
another format reads as cold, never as a crash, so two crapkit versions can share a working
tree.

After one `coverage`, one `worklist` and one `coupling` on a one-file repo:

```
$ ls .crapkit .crapkit/cov
.crapkit:
artifacts.json
cache.json
churn-cache-v2.json
churn-log-v2.json
churn-log-v2.z
coupling-cache-v1.json
cov
crap.sqlite
lane-py.log
stat-stamps.json

.crapkit/cov:
junit-py.xml
py.json
```

| Path | What it holds | Key |
|---|---|---|
| `crap.sqlite` | The store: run history, every scored function, the override audit trail, and the per-run rollups `trend` and `report` read. Durable, not a cache. The ratchet marks are not here; they live in the committed `crapkit-ratchet.tsv`. | |
| `cov/` | Where `init` points every lane's `artifact` and `results_artifact`. | |
| `lane-<name>.log` | One lane's streamed output, an `--- attempt N ---` header per retry. | |
| `artifacts.json` | Per artifact: the commit it was built at, the lane that built it, how long that took. Drives `--reuse-unchanged` and `doctor --tune`. | |
| `cache.json` | Analysis records per file, so an unchanged file is not re-analyzed. | The file's content hash, under a fingerprint of the lizard pin and the analysis version. |
| `stat-stamps.json` | What the last run saw for each file (mtime, size, hash), so unchanged files are not re-hashed. | |
| `churn-cache-v2.json` | Per-file churn for the window: commits, authors, weight. | HEAD sha, window months, today's UTC date, path format. |
| `churn-log-v2.z` | The window's `git log --name-only` output, deflated, with its key in `churn-log-v2.json` beside it. | Same four fields. |
| `coupling-cache-v1.json` | Ranked co-change pairs at the default thresholds, ordered and uncut. | The churn map's key plus a digest of the tracked set. |
| `mutate-pool/` | The `w0..wN` worker worktrees `mutation_workers > 1` keeps. Removed by `crapkit mutate --drop-pool`. | |
| `report.html` | Where `crapkit report` writes by default. | |

Since 0.4.5 the rollup is filled once per run and pruned with its run, which is why `trend`
answers in 0.04 s warm on a corpus where it used to rescan 4.3 M rows. It means `trend` and
`report` write to `crap.sqlite` on a cold rollup, best effort: they read as before on a
checkout they cannot write to, just without the speedup.

The date is in the churn key because `--since=12 months ago` is measured against the wall
clock, so yesterday's map describes a window one day wider than today's. The tracked set is
in the coupling key because ranking drops any pair naming a file `git ls-files` no longer
lists, and the index moves without HEAD: `git rm --cached src/util.py` leaves the sha alone
and still has to retire every pair naming that file.

Two thresholds bypass the coupling cache. What is stored is the ranking at
`--min-support 5` and `--min-confidence 0.5`, so `--top` reads it and either threshold off
its default recomputes: serving a wider question from a narrower file would drop the pairs
the wider thresholds exist to surface.

The version marker is in the file name on purpose. 0.4.3 and 0.4.5 sharing one working tree
each read the other's cache as cold and rewrote it, so every run of both rebuilt the map.
Different formats, different files, both warm. A warm 0.4.4 cache is adopted once and its
file removed rather than left behind.

---

## Getting an artifact out of vitest

vitest ships **no coverage provider**. Without one, `crapkit init` writes a lane, `doctor`
reports no problems, and `coverage` exits 5:

```
crapkit: lane 'js' FAILED: lane 'js' produced no artifact at .crapkit/cov/js/coverage-final.json (command exit 1); full log: /repo/.crapkit/lane-js.log; last output: $ npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js
 MISSING DEPENDENCY  Cannot find dependency '@vitest/coverage-v8'
```

Install one:

```
npm i -D @vitest/coverage-v8
```

### v8 or istanbul

Both providers work and both feed crapkit's `parser = "istanbul"`. The provider name and the
parser name are unrelated: v8's raw counters are remapped to the istanbul JSON schema before
`coverage-final.json` is written, so what lands on disk is the same shape either way.

| Provider | Config needed |
|---|---|
| `@vitest/coverage-v8` | none, it is vitest's default |
| `@vitest/coverage-istanbul` | `coverage.provider = "istanbul"` in the vitest config |

Both were run against the same repo through the same crapkit lane and produced the same
scores (`4 measured / 0 untested, 0 over target 6, CRAP load 12.0, grade A+`). Pick on your
project's grounds, not on crapkit's.

The provider version must match your vitest **major**, or npm refuses the install
(`peer vitest@"4.x" from @vitest/coverage-v8@4.x`). On vitest 2:

```
npm i -D "@vitest/coverage-v8@2"
```

### The json reporter

crapkit reads `coverage-final.json`, written by vitest's `json` coverage reporter, which is
on by default. If your config sets `coverage.reporter` explicitly, keep `"json"`. Setting
`reportsDirectory` here is the alternative to the lane's `--coverage.reportsDirectory`
flag; either one keeps the report out of your root, and the lane's `artifact` has to name
whichever you picked:

```ts
// vitest.config.ts
export default {
  test: {
    coverage: {
      provider: "v8",
      reporter: ["text", "json"],
      reportsDirectory: ".crapkit/cov/js",
      reportOnFailure: true,
    },
  },
};
```

`crapkit init` excludes `*.config.ts` (and `.js`/`.mts`, at any depth) from scoring by
default, so this file never trips doctor's unclaimed-file check. If you wrote your
`[exclude]` list by hand, add `"*.config.ts"`. Globs are whole-path, so the bare form matches
the repo root and `**/*.config.ts` matches nested copies.

### `reportOnFailure`

That last key is the one people leave out. vitest writes **no coverage report at all when
the run fails**, so one red test becomes a missing artifact and a lane failure, which is a
different problem than the one you have. Same repo, same command, only that key toggled:

```
$ npx vitest run --coverage          # reportOnFailure unset, 1 test failing
Tests  1 failed | 12 passed (13)
$ ls .crapkit/cov/js/coverage-final.json
ls: cannot access '.crapkit/cov/js/coverage-final.json': No such file or directory

$ npx vitest run --coverage          # reportOnFailure: true
.crapkit/cov/js/coverage-final.json
```

### The lane

```toml
[[lane]]
name = "js"
command = "npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml"
artifact = ".crapkit/cov/js/coverage-final.json"
results_artifact = ".crapkit/cov/js/junit.xml"
parser = "istanbul"
scopes = ["web"]
```

vitest ships the junit reporter, so those three flags need no package. Name `default`
alongside it: `--reporter=junit` on its own replaces the console output you watch the run
through. That is the lane `crapkit init` writes for a repo whose `devDependencies` name
vitest.

Never put a file filter in a `--coverage` command. vitest silently narrows the coverage
include set to the filtered files, so everything else reads as uncovered. crapkit refuses
the config rather than letting that happen at runtime:

```
crapkit: lane 'js': file filter 'src/grade.ts' combined with --coverage silently narrows the coverage include set; drop the filter or use a dedicated config
```

Exit 3.

A path after one of the 25 vitest options the guard knows is that option's value, not a
filter: `--config vitest.ci.ts`, `--exclude src/legacy.cjs` and
`--reporter ./tools/my-reporter.ts` all pass, and so do `-c`, `-t`, `--coverage.exclude`,
`--coverage.extension`, `--coverage.include`, `--coverage.provider`,
`--coverage.reporter`, `--coverage.reportsDirectory`, `--diff`, `--dir`,
`--environment`, `--globalSetup`, `--outputFile`, `--pool`, `--project`, `--root`,
`--setupFiles`, `--shard`, `--snapshotEnvironment`, `--testNamePattern`,
`--typecheck.tsconfig` and `--workspace`. That
list is the whole licence, not a rule about values: after any other flag, a path ending in
a source suffix is read as a filter and refused. Attach it
(`--coverage.customProviderModule=./tools/prov.ts`) and it passes.

---

## jest

jest needs no extra package: it bundles istanbul and its default `coverageReporters` already
include `json`, which writes `coverage-final.json` into `--coverageDirectory`.

```toml
[[lane]]
name = "js"
command = "npx jest --coverage --coverageDirectory=.crapkit/cov/js --reporters=default --reporters=jest-junit"
artifact = ".crapkit/cov/js/coverage-final.json"
results_artifact = ".crapkit/cov/js/junit.xml"
parser = "istanbul"
env = { JEST_JUNIT_OUTPUT_DIR = ".crapkit/cov/js", JEST_JUNIT_OUTPUT_NAME = "junit.xml" }
scopes = ["web"]
```

That is exactly the lane `crapkit init` writes for a repo with `jest` and `jest-junit` in
`devDependencies` and no `test` script. Pin the reporter with `--coverageReporters=json` if
your jest config overrides the default list. Both forms score identically:

```
$ crapkit coverage
run 1 @ 70ac5e065df: 1 functions scored — 1 measured / 0 untested / 0 no-lane / 0 cc-only, 1 over target 6, CRAP load 8.12, grade F
```

The junit half is a separate package: `npm i -D jest-junit` first, or jest exits on a
reporter it cannot resolve. It reads no path off the command line either — package.json,
the jest config or those two variables are the whole list — so without the `env` block it
drops `junit.xml` at the repo root. Without `jest-junit` at all, drop the reporter flags,
the `results_artifact` and the `env`, and take the `doctor` WARN: the lane still measures
coverage, with the crashed-worker check and no-new-failures off.

---

## pytest

The `--cov` family of flags comes from the `pytest-cov` package, not pytest itself:
`pip install pytest-cov` before the lane's first run, or the lane fails with
`unrecognized arguments: --cov`. It has to live in the environment the SUITE runs in;
`pip install "crapkit[py]"` pulls it beside crapkit when the two share a venv, and
`crapkit init` probes the lane's python and prints this fix when the plugin is missing.
Write that install command in double quotes: single quotes do not survive cmd.exe.

The probe asks the interpreter that runs pytest, found in the segment that holds the
`pytest` token. `coverage run -m pytest --cov=pylib && coverage json` names no interpreter
in front of pytest, so nothing is asked and no note is printed. If cmd.exe cannot start that
interpreter at all, the note names the word to change instead of talking about pytest-cov,
and on a Windows PATH holding only the `py` launcher `init` writes `py` rather than a
`python3` the first `coverage` could not run.

```toml
[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit.xml"
parser = "coveragepy"
scopes = ["api"]
```

`--cov-branch` is not optional. Without it, coverage.py writes a report with no branch data
and the lane is refused rather than quietly downgraded to statement coverage:

```
$ crapkit coverage
crapkit: lane 'py' FAILED: coverage.py report lacks branch data — run the lane with branch coverage on
crapkit: every lane failed: ...
```

Exit 5.

### The interpreter a lane binds to

`python -m pytest` is not one command. `python` resolves through the shell's `PATH`, so
the lane runs under whichever virtualenv the shell happened to have active — which is
almost always right, and silently wrong in the one case that matters.

Two git worktrees of one repo. Checkout B's venv is active, and it holds an editable
install pointing at B's `src`. Run `crapkit coverage` in checkout A and the lane's pytest
imports **B's** sources. When the two checkouts' APIs have diverged, collection dies and
you get exit 5. When they have not — the ordinary case for two worktrees of one branch —
the suite passes, coverage.py measures B's files, the join against A's scoped files finds
nothing, and crapkit prints a confident `N untested … grade F` that is entirely an
artifact of the wrong venv.

A lockfile is the repo saying which environment is the right one, and the manager's `run`
is the only spelling that binds a command to it. `crapkit init` reads that off the tree
along with everything else:

| Lockfile at the root | Lane command |
|---|---|
| `uv.lock` | `uv run python -m pytest …` |
| `poetry.lock` | `poetry run python -m pytest …` |
| `pdm.lock` | `pdm run python -m pytest …` |
| `Pipfile.lock` | `pipenv run python -m pytest …` |
| none | `python -m pytest …` (or `python3`, whichever resolves) |

The first match in that order wins, so a repo mid-migration between two managers gets the
same config every time. The `[crapkit.scoped_tests]` entry `init` writes takes the same
prefix: step 3 measuring one environment while step 4 tests another is the same bug one
command later.

`init` does not probe a managed lane for `pytest-cov`. `uv run` and its siblings create or
sync the project environment before running anything, and `init` has no business
provisioning one to ask a question about it. If the plugin is missing, the lane says so on
its first run — with the log path.

It does check that the manager itself is installed here, because the lockfile is the
repo's property and the PATH is the machine's. A `uv.lock` a teammate committed on a
machine that installed the dependencies with pip gets the `uv run` lane, which is the
right command for the repo and cannot start on this checkout, so `init` says so rather
than pointing at a `crapkit coverage` that exits 5:

```
note: lane 'py' runs through `uv`, which this machine's PATH does not carry — install uv, or point the lane's command in crapkit.toml at an interpreter that resolves here, then `crapkit coverage`
```

Writing the prefix by hand is the fix for a repo that adopted crapkit earlier, or one that
pins its environment some other way: `command` is a shell string and takes anything.

Prefer `--cov=<module>` over `--cov=<path>` when your suite spawns subprocesses. pytest-cov
hands children a `COV_CORE_SOURCE` that must resolve from the **child's** cwd, so a
path-based source measures nothing there while still reporting a confident 0%. crapkit's own
lane uses the module form for exactly that reason.

### The full-suite rule

A lane whose `parser` is `coveragepy` refuses a positional argument in a pytest command:

```
crapkit: lane 'api': positional argument 'api' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately
```

Subset coverage under a suite with cross-file pollution is run-order dependent, so the
number moves for reasons that have nothing to do with your code. If your suite really is
scoped and isolated, opt out explicitly with `full_suite = false` on the lane.

A flag's value is not a positional. `-n 8`, `-o timeout=300`, `-p no:randomly` and
`--deselect tests/test_x.py::test_slow` all pass: the guard knows the pytest options that
read the next token, and treats a `key=value` token as a value everywhere. Quoting is the
shell's, and [How a lane command is read](#how-a-lane-command-is-read) has the whole rule:
`-m "not live and not perf"` is one marker expression, not four positionals. In
`crapkit.toml`, a single-quoted TOML string keeps the double quotes unescaped:
`command = 'python -m pytest -m "not live and not perf" --cov=pylib ...'`. After a flag it
does not know, a bare word is that flag's value too — only a path or a node id
(`pylib/unit`, `tests/test_x.py::test_slow`) outranks the guess and is refused:

```
$ crapkit doctor       # command = "python -m pytest -q pylib/unit"
crapkit: lane 'py': positional argument 'pylib/unit' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately
```

When the refused token really was a value, the attached form is the one-edit fix: dropping
it breaks the command, because the flag then eats whatever comes next.

### Test attribution for `explain --tests`

`crapkit explain FILE NAME --tests` lists the tests that covered a function, from coverage.py
contexts. Without them it says so rather than guessing:

```
tests: no context data — run the py lane with dynamic_context = test_function and a --show-contexts JSON report
```

Two pieces. Add `--cov-context=test` to the lane command, and turn contexts on in
`.coveragerc`:

```ini
[run]
dynamic_context = test_function

[json]
show_contexts = True
```

`[json] show_contexts` is the half people miss: without it coverage.py records the contexts
and then omits them from the JSON report.

```
$ crapkit explain app/parse_csv.py parse_row --tests
  uncovered lines: 9, 11, 13, 15
    covered by test_parse.test_basic
    covered by test_parse.test_blank
```

### Running from a subdirectory

coverage.py records paths relative to where it ran. Point the lane at the subdirectory and
tell crapkit what to prepend. Note that `artifact` stays **repo-relative** even though the
command writes it relative to `cwd`:

```toml
[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:../.crapkit/cov/py.json --junitxml=../.crapkit/cov/junit-py.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit-py.xml"
cwd = "api"
path_prefix = "api/"
parser = "coveragepy"
scopes = ["api"]
```

Getting `path_prefix` wrong is silent and expensive. The same tree, same suite, only the key
removed:

```
with    path_prefix: 1 functions scored — 1 measured / 0 untested / ..., CRAP load 10.75
without path_prefix: 1 functions scored — 0 measured / 1 untested / ..., CRAP load 20.0
```

The lane ran and passed both times. Without the prefix, no artifact path matched any scoped
file, so every function fell to `untested` and scored as if nothing tested it.

---

## A crapkit root below the repo top

The crapkit root is wherever `crapkit.toml` sits, and it does not have to be the git top. A
package one directory down inside a bigger repo is a supported shape. There is no monorepo
mode and no config key for it: point crapkit at the package.

```
$ crapkit coverage --repo packages/api
run 1 @ 387e938f537: 1 functions scored — 1 measured / 0 untested / 0 no-lane / 0 cc-only, 1 over target 6, CRAP load 13.12, grade F
$ crapkit worklist --repo packages/api
worklist @ 387e938f537 (run 1, floor ccn>=5, churn 12mo) — 1 active, 0 dormant
  risk     10.5  ccn   7 (  7 std)    6c/1a w   1.50  calc/grade.py:1  classify( score , attempts , late , bonus )
```

`--repo` names the crapkit root, never the git top, and every subcommand you invoke by hand
takes it; `claude-hook` is the exception and finds the root by walking up from the edited
file instead. Running the same two commands from inside `packages/api` with no flag does the
same thing.

Both halves of a row are root-relative. The scored path comes from `git ls-files`, which
answers relative to its cwd. The churn count comes from `git log --relative`, which answers
the same way. The `6c/1a` above is 6 commits and 1 author against `calc/grade.py`, joined
out of a history whose own paths read `packages/api/calc/grade.py`.

Before 0.4.4 the churn log ran without `--relative`, so every lookup missed and `worklist`
filed the whole corpus under dormant: `0 active, 215 dormant` on a repo with 90 commits that
week. If you see that, check the version before you check your config. The churn caches
moved to new file names, so a map laid down with top-relative paths is ignored rather than
reused, and a 0.4.3 sharing the repo keeps its own.

### The gate gates below the top since 0.4.5

0.4.4 fixed churn and left the pre-commit gate reading `git diff --cached`, which answers
from the git top whatever the root is. Those paths matched no scope under a nested root, so
the gate gated nothing and printed `staged file(s) belong to no scope and were not gated`
naming files that start with the package directory. A function at twice the ceiling
committed with a warning.

Since 0.4.5 every git spawn runs with `diff.relative=true` and `core.quotePath=false`, and
cat-file asks for `:./path`. Every reader that joined top-relative paths against
root-relative rows now agrees: the commit gate, `verify`'s changed files, `rescore --gate`,
lane reuse (which could republish a stale artifact's score), `mutate`'s targets, the
ratchet's rename follow, and the per-edit advisory's own diff. `core.quotePath=false` is
the other half: git quotes a non-ASCII path in its diff output and `ls-files` does not, so a
dirty file with an accent in its name was invisible to lane reuse.

Staged from the git top, gated from `packages/api`, one directory down:

```
$ git add -A                                   # run at the git top
$ crapkit hook-precommit                       # run in packages/api
crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:
  ccn   8  calc/grade.py:17  rank( a , b , c , d , e )
decompose before committing (coverage cannot save a function above the target).
```

Exit 6, and the path in the row is root-relative like every other crapkit row. A staged file
that sits **above** the crapkit root is outside the diff by design, and is no longer named
in that warning.

---

## Containers

A `coveragepy` lane refuses to run inside a container and exits 5:

```
crapkit: lane 'py' FAILED: lane 'py' runs the python suite, which is host-only (container runs OOM); set container_ok = true only if this environment truly differs
```

Two triggers, either one is enough: the file `/.dockerenv` exists, or
`CRAPKIT_INSIDE_CONTAINER=1` is set in the environment. The guard exists because a python
suite under coverage is memory-hungry and a container memory cap turns that into an OOM kill
that looks like a flaky lane. If your container is sized for it, say so per lane:

```toml
container_ok = true
```

`istanbul` lanes are never refused.

---

## Reusing artifacts

Rerunning a suite crapkit already read is the slowest thing it does. Two flags skip it.

Every artifact crapkit reads is stamped in `.crapkit/artifacts.json` with the commit it was
built at, the lane that built it, and how long the lane took.

| Flag | Behavior |
|---|---|
| `--reuse-artifacts` | Skip every lane command, parse whatever is on disk. Warns per lane when files under that lane's scopes changed since the stamp. |
| `--reuse-unchanged` | Rerun only the lanes whose scopes moved. A lane is reused when its stamp commit is an ancestor of HEAD **and** nothing under its scope paths changed since, working-tree edits included. |

**Passing both makes `--reuse-artifacts` win.** It is checked first, so nothing reruns
whatever changed, and the `artifact still matches its scopes; reusing without rerun` line
never prints. Live, on a tree with an edited source file:

```
$ crapkit coverage --reuse-artifacts --reuse-unchanged
crapkit: lane 'py' artifact was built at 525a3276065; 2 file(s) in its scopes changed since (their coverage is stale)
run 9 @ 525a3276065: 5 functions scored — 4 measured / 1 untested / 0 no-lane / 0 cc-only, ...

$ crapkit coverage --reuse-unchanged
run 10 @ 525a3276065: 5 functions scored — 5 measured / 0 untested / 0 no-lane / 0 cc-only, ...
```

The new function reads `untested` in the first run and `measured` in the second, because
only the second actually ran the suite.

A stale artifact also silences the dark-line fields. `next-item` and `brief` then emit
`uncovered_lines: null` with a note naming the lane to rerun, rather than an empty list a
caller would read as "nothing left to cover".

---

## Timeouts and retries

A hung suite would otherwise hang the run. Two keys bound it:

```toml
timeout_seconds = 1800
retries = 1
```

crapkit kills the command past `timeout_seconds` (`0`, the default, means no crapkit-owned
timeout). `retries` reruns after a timeout **or** a missing artifact; each attempt appends to
the same log. Exhausted retries raise exit 5:

```
crapkit: lane 'slow' FAILED: lane 'slow' timed out after 2s (attempt 2); log: .../.crapkit/lane-slow.log
```

`.crapkit/lane-slow.log`:

```
$ python -c "import subprocess,sys; subprocess.run([sys.executable, 'tick.py'])"

[crapkit] timed out after 2s; killed

--- attempt 2 ---
$ python -c "import subprocess,sys; subprocess.run([sys.executable, 'tick.py'])"

[crapkit] timed out after 2s; killed
```

### The kill takes the whole process tree

`command` runs under a shell, so the shell is the child and your runner is a grandchild.
Killing the shell alone leaves the suite running with nothing waiting on it. Since 0.4.5
the command starts in its own process group and the deadline kills the group: `taskkill /T`
on Windows, `killpg` on POSIX. crapkit waits for the tree to die before it moves on, so the
working directory the lane ran in is free.

The lane above spawns a grandchild that appends a line to `ticks.txt` twice a second for
30 s. Two attempts at a 2 s deadline, and the file stops growing the moment the deadline
lands:

```
$ crapkit coverage
crapkit: lane 'slow' FAILED: lane 'slow' timed out after 2s (attempt 2); log: ...\.crapkit\lane-slow.log
crapkit: every lane failed: lane 'slow' timed out after 2s (attempt 2); log: ...\.crapkit\lane-slow.log
EXIT=5
$ wc -l < ticks.txt
10
$ sleep 5; wc -l < ticks.txt
10
```

The same bounded spawn backs `mutation_timeout_seconds` and `init`'s pytest-cov probe, so a
looping mutant is cut instead of outliving the run that gave up on it. The lane log still
streams while the command runs.

---

## Flake retest

A test that fails once and passes on rerun should not be exit 8. Declare a rerun template
and crapkit reruns just the newly failed ids before it decides:

```toml
results_artifact = ".crapkit/cov/junit.xml"
retest_command = "python -m pytest --junitxml=.crapkit/cov/junit.xml -q -k \"{names}\""
```

```
$ crapkit verify
flake retry: 1 of 1 new failures passed on rerun
verify OK @ f0070ff1fad vs baseline f0070ff1fad (0 changed files)
```

Three placeholders, filled from the JUnit ids (`classname::name`):

| Placeholder | Fills with | Use for |
|---|---|---|
| `{tests}` | the sorted ids, each quoted, verbatim | runners whose ids are runnable node ids |
| `{files}` | the unique classnames, quoted | **vitest**, whose JUnit classname is the test file path |
| `{names}` | a regex alternation of the test names, `re.escape`d | **pytest**, whose classname is a dotted module and not a path: use it with `-k` |

Rules that keep this from hiding real failures:

- Only lanes that declare `retest_command` retest. Lanes without one keep every failure.
- A test only drops out of `new_failures` when the rerun's own results artifact says it
  passed. No artifact, a crash, or a timeout during the retest keeps everything failed.
- The retest never touches the gate or the ratchet. It only shrinks the new-failure set.

---

## Running lanes in parallel

Wall time, and nothing else. The scores come out identical.

```toml
[crapkit]
max_parallel_lanes = 2
```

Lanes are subprocesses, so this is a thread pool: it moves wall time only. Above 1, crapkit
starts the lane that took longest last time first (its duration rides along in the artifact
stamp), prints `lane 'x' started` and `finished` to stderr, and folds results back in
**declaration** order regardless of who finished. The run scores byte-identically to a
serial one.

Every reuse decision is taken up front on one thread, before any lane starts, because a lane
command writes to the working tree and deciding lane by lane would let one lane's output
change the next lane's answer.

Two things to check before raising it:

- Raise it only when the suites are independent. Two lanes sharing a port or a temp
  directory manufacture failures that `verify` reads as a gate breach.
- Runners that size their own worker pool from free memory (vitest does) need a per-lane
  `env` cap, or N lanes each claim the whole box.

`crapkit doctor --tune` suggests a value from your cpu count and the recorded lane durations,
and estimates the makespan. It writes nothing.

`[crapkit] analysis_workers` (default `0` = one process per core) caps the lizard pool
separately. Set it when the analysis pass runs beside parallel lanes so the two are not both
claiming every core.

---

## A junit that says the run did not finish

A `results_artifact` is read as a trust check, not only as a failure list. A report that
admits the runner stopped early fails the lane with exit 5, exactly the way a missing
coverage artifact does:

```
$ crapkit coverage
crapkit: lane 'py' FAILED: junit reports a run that did not finish, so its coverage measures a partial suite: worker 'gw1' crashed while running 'tests/integration/test_pipeline.py::test_bulk_extract'
EXIT=5
```

Two signatures are refused:

| In the junit | What it means |
|---|---|
| an `<error>` naming `worker 'gwN' crashed while running '<nodeid>'` | pytest-xdist lost a worker mid-run |
| an `<error>` outside every `<testcase>` | the runner errored the session itself |

pytest-xdist 3.8 does not reschedule a crashed worker's queue. On a 15,300-test lane one dead
worker left 4,626 tests unexecuted; coverage.py still wrote its JSON at session end, so the
lane read as a complete suite and a quarter of the scope scored `cov 0`. The untested count,
the CRAP load and the ratchet were all wrong, and the run was written as a coverage baseline.

Now the lane fails, its scopes fall back to `no-lane`, and the run is typed `partial`, which
no baseline reader will take. `verify` refuses to conclude at all.

A clean junit changes nothing, and an ordinary errored test is still just a failed test.

### The test count is the second check

A runner killed from outside — an OOM, a signal — writes no crash into its report at all, and
then the count is the only signature left. `coverage` compares each lane's junit total
against the last trusted run's and warns past a **10%** drop:

```
$ crapkit coverage
crapkit: lane 'py' ran 12 tests, 8 fewer than the last trusted run's 20 — check the runner's log for a worker that died without reporting it
run 2 @ df858be0149: 1 functions scored — 1 measured / 0 untested / 0 no-lane / 0 cc-only, 0 over target 6, CRAP load 2.0, grade A+
```

A warning, never a failure: deleting a test file is a legitimate way to get there. `verify`
reports **any** shrink against its own baseline, which is the strict half of the same check.

Both counts are optional and neither absence is an error. A baseline recorded before the
lane declared a `results_artifact` carries no count and compares nothing. A lane that wrote
no junit this run gets one line naming the gap. Here the baseline had a junit and the lane
that ran under `verify` had lost it:

```
$ crapkit verify
warning: lane 'py' wrote no test counts this run (no results_artifact was parsed), so the baseline's 1 tests cannot be compared
verify OK @ 437a254ba09 vs baseline 437a254ba09 (2 changed files)
```

Reading that absent count as zero is what used to turn such a run into a KeyError, after
the lane had already run.

---

## What a failed lane does to scoring

A lane failure is recorded, not fatal. The run still happens:

```
$ crapkit coverage
crapkit: lane 'scripts' FAILED: lane 'scripts' produced no artifact at .crapkit/cov/scripts.json (command exit 1); full log: /repo/.crapkit/lane-scripts.log
run 3 @ 393b8dad2a1: 2 functions scored — 1 measured / 0 untested / 1 no-lane / 0 cc-only, 2 over target 6, CRAP load 56.83, grade F
```

Exit 5. Four consequences:

1. **The failed lane's scopes fall back to `no-lane`, not `untested`.** The distinction is
   the point: `untested` means a working lane had nothing to say about this function,
   `no-lane` means no measurement was possible. A lane outage must not read as a testing gap.
2. **The run is typed `partial`.** `crapkit runs` shows it, and partial runs are never
   baseline candidates, so a pre-existing failure in a missing lane cannot read as NEW
   forever. `coverage --lane NAME` produces a partial run for the same reason.
3. **`verify` refuses to conclude at all**: `verify cannot conclude with failed lanes:
   scripts`, exit 5. A verdict with a blind lane is not a verdict.
4. **Every lane failing raises** `every lane failed: ...`, exit 5, with no run written.

`coverage --json` carries the reasons under `lane_failures`, keyed by lane name, alongside
the successful lanes' provenance under `lanes`.

### The failure message names its own log

Every lane refusal carries `full log: <path>` before the tail it quotes. The tail is 500
characters cut on line boundaries; the log is the whole run. When the end of the log is a
summary block — pytest closes on `ERROR path` lines that say which files broke and never
why — the message pulls the last few lines that DO name a cause up in front of it, with an
ellipsis marking the output skipped between them:

```
crapkit: lane 'py' FAILED: lane 'py' produced no artifact at .crapkit/cov/py.json (command exit 2); full log: /repo/.crapkit/lane-py.log; last output: E   ImportError: cannot import name 'Widget' from 'faro.core' (/other/checkout/src/faro/core.py)
...
ERROR tests/test_widgets.py
============================== 10 errors in 0.62s ==============================
(exit 2)
```

---

## An artifact that measured a different tree

A lane whose artifact names paths that reach **none** of the scopes it claims gets one of
two verdicts, and the measured paths decide which. Paths outside this checkout fail the
lane:

```
$ crapkit coverage
crapkit: lane 'py' FAILED: lane 'py' measured 3 file(s), none of them under the paths its scopes declare (src), and 3 of them outside this checkout entirely — .crapkit/cov/py.json describes a different tree, so joining it would score every function in those scopes untested; it reports paths like /other/checkout/src/faro/core.py, /other/checkout/src/faro/util.py, /other/checkout/src/faro/widgets.py. Point the lane at this checkout's own environment (a bare `python -m pytest` binds to whichever venv the shell has active — run it through the project's manager, `uv run python -m pytest ...`), or set path_prefix when the runner reports paths relative to a subdirectory
```

Coverage joins on path and nothing else, so such an artifact contributes exactly nothing
and every function in those scopes reads `untested`. That is a grade assembled out of a
tooling mistake, and it is worse than the exit 5 a missing artifact already earns, because
it looks like an answer. The refusal is an ordinary lane failure: the scopes fall back to
`no-lane`, and the four consequences above apply unchanged.

Both parsers rebase a file inside the repo to a repo-relative path, so a measured path
that stayed absolute, drive-lettered (`C:/...`) or climbing (`../...`) names a file
somewhere else. That is the only shape that fails.

In-tree paths that simply miss the scopes warn instead, and the run scores on:

```
crapkit: lane 'py' measured 1 file(s), none of them under the paths its scopes declare (src), so every function in those scopes will score untested; it measured tests/test_core.py — either nothing in them is exercised yet, or the runner reports paths this lane needs path_prefix to rebase
```

That is the greenfield shape as well: a suite importing none of the scoped source yet,
which should score `untested`. Refusing it would exit 5 on exactly the repos adopting
crapkit, so it stays a warning.

Zero overlap is the whole test. A partial overlap has honest readings, a lane measuring
part of a scope or generated files outside it, and any threshold over zero would need
tuning per repo; zero has no reading under which the join was going to work.

Two things reach it:

- **The runner reports paths relative to a subdirectory.** [`path_prefix`](#running-from-a-subdirectory)
  is the knob, and it is applied before this check, so a prefix that fixes the join is
  never refused here.
- **The run measured another tree.** A stale artifact copied in, or the wrong environment:
  see [The interpreter a lane binds to](#the-interpreter-a-lane-binds-to).

The reach test is `universe.owning_scope` over the lane's own scopes, the same predicate
that assigns files to scopes, so a scope declaring individual files rather than
directories is reached exactly and never reads as unmeasured.
