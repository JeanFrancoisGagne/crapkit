# Coverage lanes

A lane is how crapkit gets coverage: it runs a command you already have, reads the artifact
that command writes, and maps the result onto the scopes it claims.

```toml
[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["calc"]
```

Four required parts and one rule each:

| Part | Rule |
|---|---|
| `command` | Runs through the shell at the repo root (or `cwd`). Its **exit code is recorded, not enforced**. A brownfield suite with 98 known failures still writes a valid artifact, and demanding green here would make such a repo unmeasurable. |
| `artifact` | The coverage file the command writes, repo-relative. Its absence after the command and all its retries is the failure. Two lanes may not declare the same artifact path: reused paths cross-attribute coverage under `--reuse-artifacts`. |
| `parser` | `istanbul` or `coveragepy`. Nothing else exists. |
| `scopes` | Which scopes this lane's numbers speak for. A scope no lane names can only score `no-lane`, and `doctor` fails on it. |

Output streams to `.crapkit/lane-<name>.log` while the command runs. Tail that file to
supervise a long suite; crapkit prints nothing until the lane finishes.

Every key, including the optional ones, is tabled in
[configuration.md](configuration.md#lane).

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

---

## Getting an artifact out of vitest

vitest ships **no coverage provider**. Without one, `crapkit init` writes a lane, `doctor`
reports no problems, and `coverage` exits 5:

```
crapkit: lane 'js' FAILED: lane 'js' produced no artifact at .crapkit/cov/js/coverage-final.json (command exit 1); last output: $ npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js
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
command = "npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js"
artifact = ".crapkit/cov/js/coverage-final.json"
parser = "istanbul"
scopes = ["web"]
```

Never put a file filter in a `--coverage` command. vitest silently narrows the coverage
include set to the filtered files, so everything else reads as uncovered. crapkit refuses
the config rather than letting that happen at runtime:

```
crapkit: lane 'js': file filter 'src/grade.ts' combined with --coverage silently narrows the coverage include set; drop the filter or use a dedicated config
```

Exit 3.

A path after one of the vitest options the guard knows is that option's value, not a
filter: `--config vitest.ci.ts`, `--exclude src/legacy.cjs` and
`--reporter ./tools/my-reporter.ts` all pass, and so do `-c`, `-t`, `--coverage.exclude`,
`--coverage.include`, `--coverage.provider`, `--coverage.reporter`,
`--coverage.reportsDirectory`, `--dir`, `--environment`, `--globalSetup`, `--outputFile`,
`--pool`, `--project`, `--root`, `--setupFiles`, `--shard` and `--testNamePattern`. That
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
command = "npx jest --coverage --coverageDirectory=.crapkit/cov/js"
artifact = ".crapkit/cov/js/coverage-final.json"
parser = "istanbul"
scopes = ["web"]
```

That is exactly the lane `crapkit init` writes for a repo with `jest` in `devDependencies`
and no `test` script. Pin the reporter with `--coverageReporters=json` if your jest config
overrides the default list. Both forms score identically:

```
$ crapkit coverage
run 1 @ 70ac5e065df: 1 functions scored — 1 measured / 0 untested / 0 no-lane / 0 cc-only, 1 over target 6, CRAP load 8.12, grade F
```

Add `--reporters=default --reporters=jest-junit` and a `results_artifact` for the
no-new-failures check.

---

## pytest

The `--cov` family of flags comes from the `pytest-cov` package, not pytest itself:
`pip install pytest-cov` before the lane's first run, or the lane fails with
`unrecognized arguments: --cov`. It has to live in the environment the SUITE runs in;
`pip install "crapkit[py]"` pulls it beside crapkit when the two share a venv, and
`crapkit init` probes the lane's python and prints this fix when the plugin is missing.

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
read the next token, and treats a `key=value` token as a value everywhere. The guard reads
the command the way the shell running it will (sh on POSIX, cmd.exe on Windows), so a
quoted value is one token: `-m "not live and not perf"` is one marker expression, not four
positionals. Use double quotes: cmd.exe does not treat `'` as a quote, so a single-quoted
value reaches pytest one word per space there, and the guard says so on Windows. In
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
command = "python -m pytest --cov --cov-branch --cov-report=json:../.crapkit/cov/py.json"
artifact = ".crapkit/cov/py.json"
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
$ python -c "import time; time.sleep(30)"

[crapkit] timed out after 2s; killed

--- attempt 2 ---
$ python -c "import time; time.sleep(30)"

[crapkit] timed out after 2s; killed
```

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

---

## What a failed lane does to scoring

A lane failure is recorded, not fatal. The run still happens:

```
$ crapkit coverage
crapkit: lane 'scripts' FAILED: lane 'scripts' produced no artifact at .crapkit/cov/scripts.json (command exit 1)
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
