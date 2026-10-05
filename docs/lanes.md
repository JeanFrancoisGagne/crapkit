# Coverage lanes

Use the [pytest](#pytest), [Vitest](#getting-an-artifact-out-of-vitest) or
[Jest](#jest) recipe for setup. For an existing lane, jump to
[artifact reuse](#reusing-artifacts), [timeouts](#timeouts-and-retries),
[incomplete JUnit](#a-junit-that-says-the-run-did-not-finish) or
[paths from another tree](#an-artifact-that-measured-a-different-tree).

A lane is how crapkit gets coverage: it runs a command you already have, reads the artifact
that command writes, and maps the result onto the scopes it claims.

```toml
[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml --continue-on-collection-errors"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit-py.xml"
parser = "coveragepy"
scopes = ["calc"]
```

Five required keys, `name` plus the four below, and one rule each, plus `results_artifact`,
which is optional and on every lane on this page.

| Part | Rule |
|---|---|
| `name` | The lane's id. It names the lane's log, `.crapkit/lane-<name>.log`, and what `coverage --lane` takes. A name Windows cannot use as a file name, or two names that differ only in case, is refused at load with exit 3. |
| `command` | Runs through the shell at the repo root (or `cwd`). Its **exit code is recorded, not enforced**. A brownfield suite with 98 known failures still writes a valid artifact, and demanding green here would make such a repo unmeasurable. |
| `artifact` | The coverage file the command writes, repo-relative. Its absence after the command and all its retries is the failure. Two lanes may not declare the same artifact path: reused paths cross-attribute coverage under `--reuse-artifacts`. It names one file. An empty value or one that names the root (`.`, `cov/..`) is refused at load with exit 3, and so is a `results_artifact` that names the root. `doctor` FAILs an `artifact` or `results_artifact` that names a directory, such as vitest's `coverage` report directory: point it at the report file inside. |
| `parser` | `istanbul` or `coveragepy`. Nothing else exists. |
| `scopes` | Which scopes this lane's numbers speak for. A scope no lane names can only score `no-lane`, and `doctor` fails on it. |
| `results_artifact` | The JUnit report the same command writes. Two checks read it and neither runs without it: the [crashed-worker trust check](#a-junit-that-says-the-run-did-not-finish), which refuses a run the runner did not finish, and no-new-failures, which is `verify`'s exit 8. |

**Every lane on this page declares one**, because coverage alone cannot tell a finished
suite from a suite that lost a worker: both write a coverage artifact. The junit report is
the only thing that says which one happened, so a lane without one measures fine and
verifies blind. `doctor` says so, per lane, with the flag to add:

```
$ crapkit doctor
resources: up to 8 analysis worker(s) per pool, 8 shared slot(s); lane log limit 16777216 bytes per file
ok   config keys all recognized
ok   scope 'calc': 1 file
ok   every tracked source file belongs to a scope
ok   1 lane(s) declared
WARN lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it; add --junitxml=.crapkit/cov/junit-py.xml to the command and results_artifact = ".crapkit/cov/junit-py.xml" to the lane
ok   lane 'py': python -> /home/you/ledger/.venv/bin/python (pytest 8.3.3, pytest-cov 7.1.0, coverage 7.13.1)
ok   lane 'py': runs pytest (named in its command)
ok   lizard 1.24.0
doctor: no problems found, 1 warning above
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

On Windows the line is read twice, as it is when it runs. cmd.exe reads it first, for
its commands, blocks, carets and redirections, and hands each program the rest. The
program then splits its line into arguments with the C runtime's rules, which python
and node share. The two passes disagree about carets, backslashes and cmd.exe's
delimiters (`;`, `,`, `=` and a non-breaking space as well as the blanks), and the guard
reads both.

| What you write | How it reads |
|---|---|
| `-m "not live and not perf"` | One argument. Double quotes are the portable spelling: both shells drop them and hand the runner one token. |
| `-m 'not live and not perf'` | Five arguments on Windows. cmd.exe has no single-quote rule, so pytest gets `'not`, `live`, `and`, `not`, `perf'` and the lane is refused with a hint. |
| `-k ^"not slow^"` | One argument. Outside a quoted run cmd.exe drops the caret and hands on the character behind it, so the runner gets `-k "not slow"`. Inside a quoted run the caret stays: `-k "a^b"` reaches the runner with its caret. |
| `-k ^"x & pytest pylib/unit^"` | Two commands on Windows. The caret hands the quote to the runner but opens no quoted run for cmd.exe, so the `&` starts a second pytest, and that one runs only `pylib/unit`. The lane is refused. |
| `-k "a\" tests \"b"` | One argument, `a" tests "b`. Inside double quotes `\"` writes a quote. On Windows it is the runner that reads it so; cmd.exe sees the quote close the run, so an `&` between the two `\"` still ends the command. |
| `--cov-report=json:"cov/py 1.json"` | One argument. A quote opens a quoted run wherever it sits, mid-token included. |
| `-k "" tests` | Three arguments. An empty pair of quotes writes an empty argument, so `tests` stays the positional it is. Dropping it would slide `tests` onto `-k` and the narrowing lane would load clean. |
| `pytest --cov && coverage json` | Two commands. `&&`, `\|\|`, `&` and `\|` each start a new one, blank beside them or not (`py.json&& coverage json` is two commands too), and every segment that runs the runner is checked on its own. |
| `pytest --cov > lane.log 2>&1` | The redirections are the shell's; the runner never sees them, or their targets, quoted or not (`>"lane log.txt"`). A redirection touching a word leaves that word: `tests>lane.log` hands pytest `tests`. A quoted `">"` is an argument and stays. |
| `a2>x`, `>lane.log;2>&1` | On sh a number names the stream only when it is the whole word, so `a2>x` hands on `a2`. cmd.exe takes the digit when a delimiter, a quote, `&`, `\|` or a parenthesis stands in front of it, caret or not: `a2>x` hands on `a2`, and `a^\|2>x` hands on `a\|`. cmd.exe also drops the delimiters between two redirections, so `>lane.log;2>&1` hands pytest nothing, while `>lane.log ;b` hands it `;b`. |
| `(pytest --cov) > lane.log` | pytest gets `--cov` on both shells. sh runs the parentheses as a subshell. On cmd.exe a `(` where a command starts opens a block, an unquoted `)` inside it ends the command, and a block still open at the end of the line runs nothing on that line. Anywhere else, as in `-k (a)`, both are text. |
| `pytest --cov; echo done` | On sh the `;` ends the command, and so does `;echo done`. To cmd.exe it is an ordinary character, so `echo` and `done` land in pytest's argv and the lane is refused. It does end the program's name: `python;-m pytest` starts python with `;-m` and `pytest`. |
| `bash -c 'pytest --cov tests'` | The payload is sh text on both OSes. Under cmd.exe, bash.exe splits the line it is handed with sh's quote rules, so `'` quotes there and `tests` is one positional. The guard judges each command inside the payload, and `doctor` WARNs when sh cannot split it (a quote that never closes), since then no check looked inside. |
| a line break | On sh a line break ends the command the way `;` does, and a backslash at the end of a line joins it to the next. cmd.exe runs the first line only. |
| `--cov # the whole suite` | On sh a `#` that starts a word comments out the rest of the line. To cmd.exe it is text. |
| a non-breaking space in a value | Not a word break. Both shells break words on space and tab only, so a value pasted out of rendered docs stays one token. cmd.exe does count it among its delimiters, so after `>` it ends the file name. |

A quote that never closes: sh refuses the line, and crapkit reads that quote as an
ordinary character, because a rough lint beats a crash at config load. cmd.exe runs the
line, the quoted run takes the rest of it, and crapkit reads it the same way.

One cmd.exe rule crapkit leaves out: a block on either side of a `|` runs in a second
cmd.exe, which reads the block's text again, so its carets work twice. In
`(pytest -k a ^& echo b) | more` the second read starts `echo`. crapkit reads the block
once, so there it counts more arguments than pytest gets and may refuse a lane that
runs. Take the carets out of the block, or take the block out of the pipe.

### The refusals, as they print

Single quotes on Windows, the most common way to trip the guard:

```
# command = "python -m pytest -m 'not live and not perf' --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json"
$ crapkit doctor
crapkit: lane 'py': positional argument 'live' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately (cmd.exe does not treat ' as a quote: write the value in double quotes); a suite whose testpaths cannot be collected in one process needs one lane per testpath, each with full_suite = false and its own artifact
EXIT=3
```

The same command in double quotes loads, and so does the caret spelling
(`-k ^"not slow^"`), the mid-token quote
(`--cov-report=json:".crapkit/cov/py report.json"`), the redirected form
(`... --cov-report=json:.crapkit/cov/py.json > lane.log 2>&1`) and the same form with
the operators touching their words (`...py.json>"lane.log" 2>&1&& python -m coverage
json`). All five come back `doctor: no problems found`, exit 0.

A second run after `&&` narrows as much as the first, so the segment it sits in is checked
too:

```
# command = "python -m pytest --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json && python -m pytest calc/hot.py"
$ crapkit doctor
crapkit: lane 'py': positional argument 'calc/hot.py' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately; a suite whose testpaths cannot be collected in one process needs one lane per testpath, each with full_suite = false and its own artifact
EXIT=3
```

The refusal names a word from the segment it read, never from the next command.

On Windows a `;` starts nothing, so what follows it is pytest's:

```
# command = "python -m pytest --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json; echo done"
$ crapkit doctor
crapkit: lane 'py': positional argument 'echo' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately; a suite whose testpaths cannot be collected in one process needs one lane per testpath, each with full_suite = false and its own artifact
EXIT=3
```

Write that lane as two `&&` segments and both shells agree about it.

### What doctor does with the same reading

`doctor` checks the runner of every segment, not just the first word of the line. A quoted
interpreter path (`"C:/Program Files/Python/python.exe" -m pytest ...`) is one word, not
two; a dead runner after `&&` is still a dead runner; and a path inside a quoted
`-k "tests/gone.py or x"` is a marker expression, not a file the repo owes.

The pytest-cov probe asks the python inside a `bash -c` or `sh -c` payload, the one that
runs pytest there, and the coverage data-file check reads a `--data-file` there too. A
payload sh cannot split gets its own WARN, for example:

```
WARN lane 'py': sh cannot split the script in `bash -c "python -m pytest 'tests"`, since a quote or an escape in it never closes, so crapkit reads nothing inside it: the full-suite guard, the pytest-cov probe and the coverage data-file check all pass it unjudged; fix its quoting
```

`doctor` also starts each distinct first word once, with `--version`, and FAILs the lane when the
shell cannot run it. A stock Windows PATH carries a `python.exe` stub with no Store app
behind it: it resolves, it exits 9009, and before 0.4.5 `doctor` called that repo clean
while `coverage` exited 5 on the same command. Reproduced here with a `python3` on PATH
that exits 9009:

```
$ crapkit doctor
resources: up to 8 analysis worker(s) per pool, 8 shared slot(s); lane log limit 16777216 bytes per file
ok   config keys all recognized
ok   scope 'calc': 1 file
ok   every tracked source file belongs to a scope
FAIL lane 'py': cmd.exe cannot run 'python3' (exit 9009) - the lane cannot start, so its scopes can only ever score no-lane
ok   lane 'py': runs pytest (named in its command)
ok   lizard 1.24.0
doctor: 1 problem(s)
```

The probe runs from the lane's `cwd` with its `env` merged in, the way the lane itself
starts, and is memoized on the word and that directory and environment, so a repo declaring
14 lanes over 2 runners from one directory starts two processes, not fourteen.

## How crapkit reads a lane's runner

No key in crapkit.toml names a lane's runner. crapkit reads it from what the lane runs, and
`doctor` prints the answer, one line per lane:

```
ok   lane 'py': runs pytest (named in its command)
ok   lane 'js': runs vitest (named in package.json script "test")
ok   lane 'js': runs vitest (package.json devDependencies; the command names no runner)
note lane 'js': runner unknown (npm run cov names none crapkit knows); runner-specific hints and refusals are off for it
```

`doctor --json` carries the same answer as each lane's `toolchain`
([agent-json](agent-json.md#doctor---json)). The runners are pytest, vitest, jest, bun,
deno, `cargo llvm-cov`, `go test` and c8. Three places are read, in this order:

1. **The command.** Each segment is read the way the shell that runs it reads it (sh, or
   cmd.exe on Windows), a `bash -c` script included. A runner counts as a bare word
   (`pytest`), a path's last part (`.venv/bin/pytest`), or either with `.cmd`, `.exe`,
   `.js`, `.cjs` or `.mjs` (`.venv\Scripts\pytest.exe`, `node_modules\.bin\vitest.cmd`).
   On Windows letter case does not matter. These wrappers are read through to the command
   they run: `npx`, `bunx`, `pnpm exec` and `pnpm dlx` (with `--dir D`, `-C D` or
   `--filter F` in front), `yarn exec` and `yarn dlx`, `uv run`, `poetry run`,
   `pipenv run`, `pdm run`, `hatch run`, `python -m` (past the interpreter's own options,
   such as `-X utf8` or `-W error`), `coverage run -m` (past `--source src` and the like),
   `env` and `cross-env` with their variable assignments. No other wrapper is read
   through: a runner that `timeout`, `nice`, `xvfb-run`, `pipx run` or `dotenv run` starts
   counts as not named, so set `timeout_seconds` in place of `timeout`. A script file
   that node, bun or deno runs names a runner when the runner's name is one of the
   hyphen- or underscore-separated parts of its stem: `node scripts/run-vitest.mjs` runs vitest. A dot does not separate,
   so `node scripts/run.vitest.mjs` names nothing.
2. **The package.json script it runs.** `npm test`, `npm run X`, `pnpm test`, `pnpm run X`,
   `pnpm X` when X is a script, `yarn test`, `yarn X`, `yarn run X` and `bun run X` are
   followed into that script, which is read by the same rules. A script that runs another
   script is followed three scripts deep; a fourth, or a script that leads back to one
   already read, names nothing. The package.json is the one in the lane's `cwd`, else the
   nearest one above it in the repo, else the root one.
3. **devDependencies.** When neither names a runner, a package.json whose devDependencies
   name exactly one runner (vitest, or jest) gives that runner. Nothing in what runs
   spells it, so it feeds this line alone: no runner-specific check keys on it.

`make`, `just`, `tox` and `nox` run recipes crapkit does not read, so the command stops
there and only the script and devDependencies steps can answer. A lane that names two
runners, in two segments or in its command and its script, gets no runner, and its line
says which two.

"runner unknown" is not a failure, and doctor's exit code does not change. It means the
hints and refusals that key on one runner skip that lane. Naming the runner in the command
turns them back on:
`npx vitest run --coverage` in place of `npm run cov`. A package.json crapkit cannot read
(not UTF-8, UTF-16, or not one JSON object) is one WARN naming the file, and each lane under
it is read from its command alone.

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
WARN lane 'js' writes coverage/coverage-final.json at the repo root - point it under .crapkit/ (for example .crapkit/cov/js/) to keep the tree clean
```

That warning is never a `FAIL`. A lane writing at the root measures exactly what it always
did, so breaking an existing gate over tree hygiene would cost more than the litter. A lane
that writes inside a scope's own tree (`web/coverage/` beside the `web/src` it measures) is
that package's business and is not warned about.

`init` reads those two fields the way npm reads them. A `scripts` value that is not an object,
null included, is no scripts, and so is a script whose command is not a string. A
`devDependencies` list names the strings in it; null or any other value that is not an object
names no package. A root `package.json` that does not parse, parses to something other than an
object, or is not UTF-8 stops `init` at exit 3 before it writes any file, naming the file and
where the parse stopped, as in `init wrote no file: package.json is not valid JSON (Expecting
value at line 1 column 1); fix that line`. A nested one is skipped with one warning line naming
it, and `init` writes what it writes when that file is not there.

### What the istanbul parser reads

Your runner writes `coverage-final.json` and you never open it. Read this section only if
you are building one by hand, converting another format into it, or staring at a lane that
scores nothing.

Both coverage readers take the artifact as UTF-8 JSON and read past a UTF-8 byte-order
mark, which a copy saved with PowerShell's `Out-File -Encoding utf8` carries. An artifact
in UTF-16, what PowerShell 5.1's `>` and `Out-File` write, or one holding a byte that is
not UTF-8, fails its lane naming the bytes and saying to save it as UTF-8. An artifact
with no JSON in it fails saying it is empty, so rerun the lane.

Both coverage readers reject invalid numeric counts before scoring. Counts must
be nonnegative integers, including integral JSON numbers such as `1.0`; strings,
booleans, fractions and non-finite values are refused. A coverage.py summary also
cannot report more covered lines or branches than its declared total. A field the
format writes as an object or a list that holds something else, such as an istanbul `s`
of `null`, is refused naming the file, the field and the type it holds. A bad
artifact fails its lane with the input named in the error.

A count that is missing is refused the same way, never read as a zero. In istanbul every
`fnMap`, `statementMap` and `branchMap` id needs its counter in `f`, `s` and `b`: a
dropped counter read as a function never called, a statement that never ran or a branch
pair that did not exist, and the score moved with nothing said. A `b` array also needs one
hit count per location its `branchMap` entry lists, since crapkit counts a branch's paths
by its hit counts: an if/else cut to `[1]` scored 1 of 1, and `[]` fell back to the
statements. An entry with no `locations` list is counted by its hit counts. The refusal
names the file and the first id:

```
crapkit: lane 'ui' FAILED: unparseable istanbul artifact coverage/ui.json: src/hot.ts: statement '3' has no hit count in `s`, so crapkit cannot tell whether it ran (1 such in this file); regenerate the artifact with the coverage tool, or merge shards with one that keeps every counter
```

```
crapkit: lane 'ui' FAILED: unparseable istanbul artifact coverage/ui.json: src/hot.ts: branch '0' has 1 hit count(s) in `b` for its 2 location(s), so crapkit cannot tell which of its paths ran (1 such in this file); regenerate the artifact with the coverage tool, or merge shards with one that keeps every counter
```

A coverage.py function needs its `summary`, and each kind of count in it as a pair:
`num_statements` with `covered_lines`, `num_branches` with `covered_branches`. A summary
with neither kind, one count without its partner, or no statement counts beside 0 of 0
branches is refused: each read as 0 of 0 where that count decides, and a function that ran
scored cov 0. A kind with neither count and nothing to decide is one the report did not
measure: branches in a report run without `--cov-branch`, or statements beside branches,
which decide on their own.

crapkit scores functions, so `fnMap` is the part that decides everything. Per file in the
artifact:

| Key | What crapkit does with it |
|---|---|
| `fnMap` | The function list. Every entry needs `decl.start.line`, and `loc.end.line` to close the span. A function's span runs from `decl.start` to `loc.end`. Its body starts at `loc.start`, which falls back to `decl.start`. A missing `name` reads as `(anonymous)`. |
| `f` | Call counts per `fnMap` id. |
| `branchMap` and `b` | Branch coverage. Each branch counts against the innermost function whose span holds its `loc.start`, or the `line` beside it when `loc` is missing. A branch with neither refuses the artifact. A default parameter's arm sits between the name and the body, so it is the function's; a ternary that opens ahead of an arrow on the same line counts for the code around the arrow. This is the function's coverage whenever it has one branch. |
| `statementMap` and `s` | The fallback for a function with no branch in its span. Each statement counts against the innermost function whose body holds its `start`. For `const f = (x) => x * 2` istanbul writes one statement for the declaration, which starts ahead of the arrow's body and runs when the declaration does, and one for the body. The first counts for the code around the arrow, so an arrow no test calls reads 0. Statements are also the only source of the uncovered lines `verify` measures a diff against. |

A function with neither a branch nor a statement in its span scores on `f` alone: 1.0 when
it was called, 0.0 when it was not.

`/* istanbul ignore next */` and `/* v8 ignore next */` drop the function after them from
`fnMap`, with its branches and statements. A function the source holds and `fnMap` leaves
out, in a file whose `fnMap` lists others beside a `statementMap`, reads `excluded`:
`crap = ccn`, remedy `ok` or `decompose`, because no test can move its number. Through
0.8.0 it read `untested` with `add-tests` advice no test could follow. The statements are
the proof that an instrumenter wrote the entry: a hand-built entry with `fnMap` alone
cannot say what it left out, so a function missing from it stays `untested`. A nested
function a hint drops sits inside its encloser's span and reads the encloser's number, not
`excluded`: jest's `v8` provider and c8 also leave out a nested callback V8 never compiled,
so a missing nested function cannot be told from an uncalled one.

Line numbers are placed on crapkit's own lines, which end at LF, CRLF and a lone CR.
Producers number by their own rule: `@vitest/coverage-v8` ends a JavaScript line at LF
only, and Babel (jest, nyc, `@vitest/coverage-istanbul`) and TypeScript source maps also
end one at U+2028 and U+2029. Only a file that holds a lone CR, U+2028 or U+2029 can read
differently, and for one crapkit reads the source and takes the rule under which each
named function's name sits on the line its `decl.start` gives, with `column` placing a
position inside a line V8 counted whole. A file with no named function keeps its numbers
as written.

The outer object is keyed by path. crapkit strips the crapkit root off an absolute key and
takes any other key as it stands, so root-relative keys work too. `path_prefix` is
coverage.py's key and does nothing here.

An artifact that names no functions is not an error. It parses, every scored function falls
to `untested`, and nothing says so. Same repo, same two TypeScript files, one lane whose
command writes the artifact by hand:

```
$ crapkit coverage        # artifact holds path, statementMap and s
run 1 @ 6f736a5b12a: 2 functions scored: 2 untested, 2 over ceiling 6, CRAP load 32.0, grade F
-> next: crapkit worklist
$ crapkit coverage        # same file plus fnMap, f, branchMap and b
run 2 @ 6f736a5b12a: 2 functions scored: 2 measured, 0 over ceiling 6, CRAP load 7.39, grade A+
-> next: crapkit worklist
```

Both exit 0. That is the [wrong `path_prefix`](#running-from-a-subdirectory) failure from the
other side: the lane ran, the artifact parsed, and the score is wrong. `0 measured` on a lane
that ran is the number to read.

Three shapes do fail loudly. An `fnMap` entry with no `decl.start.line` exits 5 naming the
file and the entry, as istanbul 0.x wrote no `decl`. Before 0.8.1 the line held only
`'decl'`:

```
crapkit: lane 'js' FAILED: unparseable istanbul artifact /repo/.crapkit/cov/js/coverage-final.json: src/a.ts: fnMap['0'] has no decl.start.line (every istanbul reporter writes one; regenerate the artifact with the runner's reporter)
```

So does an entry with no `loc.end.line`. Read as the declaration line, as it was before
0.8.1, the span shrank to one line, the body's branches attached to nothing, and a function
that was called scored as covered:

```
crapkit: lane 'js' FAILED: unparseable istanbul artifact /repo/.crapkit/cov/js/coverage-final.json: src/app.ts: fnMap['0'] has no loc.end.line (every istanbul reporter writes one; regenerate the artifact with the runner's reporter)
```

So does a `branchMap` entry with neither `loc.start.line` nor the `line` beside it. Before
0.8.1 it was left out, and the function it sat in scored without those arms:

```
crapkit: lane 'js' FAILED: unparseable istanbul artifact /repo/.crapkit/cov/js/coverage-final.json: src/app.ts: branchMap['1'] has no loc.start.line and no line (every istanbul reporter writes one; regenerate the artifact with the runner's reporter)
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
churn-cache-v3.json
churn-commits-v1.json
churn-log-v3.json
churn-log-v3.z
coupling-cache-v2.json
cov
crap.sqlite
lane-py.log
measurement.lock
owner.log

.crapkit/cov:
junit-py.xml
py.json
```

| Path | What it holds | Key |
|---|---|---|
| `crap.sqlite` | The store: run history, every scored function, the override audit trail, and the per-run rollups `trend` and `report` read. Durable, not a cache. The ratchet marks are not here; they live in the committed `crapkit-ratchet.tsv`. | |
| `cov/` | Where `init` points every lane's `artifact` and `results_artifact`. | |
| `lane-<name>.log` | One lane's streamed output, an `--- attempt N ---` header per retry. Current and `.log.1` files each have a 16 MiB default bound; see [log policies](resources.md#logs-and-retained-evidence). | |
| `artifacts.json` | Per artifact: the commit it was built at, the lane that built it, how long that took, the reuse `proof` with the named parts it hashes (`proof_parts`) or, when it did not hold, why (`unproved`), the git blob id of each file under the lane's scopes as the run left it (`blobs`), the untracked files the run itself wrote (`byproducts`), and, for an artifact the lane's last attempt failed to write, the sha256 of the file it left (`refused_sha256`, which `crap.sqlite` keeps a copy of). Written through a temporary file that replaces it in one step. Drives `--reuse-unchanged`, the dark-line notes, `doctor --tune` and the [reuse refusal](#the-artifact-a-failed-attempt-left-behind-is-refused). | |
| `aside/` | A lane's declared artifact and results file while its attempt runs: they move here before the command starts, so a file at the declared path afterwards is one the attempt wrote. Each goes back when the attempt wrote nothing in its place, and the directory is removed when the lane ends. A kill or a CI timeout runs no cleanup, so the next crapkit command that measures or reuses the lane puts each file back first and says so: `crapkit: lane 'unit': coverage/coverage-final.json is back at its path; an attempt that did not finish (a kill or a timeout) had set it aside under .crapkit/aside/`. Where a file was written at the path since, it stays, and the line names the copy's path so you can move it back. | |
| `cache.json` | Analysis records per file, so an unchanged file is not re-analyzed. | The file's content hash, under a fingerprint of the lizard pin and the analysis version. |
| `measurement.lock` | The lock a lane run holds on this checkout's lane logs and artifact stamps while its commands run, so two crapkit processes never measure one checkout at once. It stays behind between runs and holds nothing. | |
| `<ratchet_file name>.lock` | The lock each marks-file write holds: seed, prune, move, merge, verify's tighten and overrides. It stays behind, holds nothing between writes and is safe to delete. It lives in a `.crapkit/` beside the marks file, so `ratchet_file = "gates/r.tsv"` puts it at `gates/.crapkit/r.tsv.lock`. | |
| `owner.log` | What the measurement owner wrote to stderr: nothing on a run that ends normally, and a dated line and a traceback when it [stops early](#when-the-measurement-owner-stops). Every owner on this checkout appends to it. | |
| `stat-stamps.json` | What the last run saw for each file (mtime, size, hash), so unchanged files are not re-hashed. A file enters it once it has held still for two seconds, so a run right after the files were written, like the listing above, leaves no `stat-stamps.json` yet. A same-length rewrite put back under its old mtime (`cp -p`, `touch -r`) keeps the old hash until the file's next real write; `crapkit watch` misses that rewrite the same way. | |
| `churn-cache-v3.json` | Per-file churn for the window: commits, authors, weight. | HEAD sha, window months, today's UTC date, path format, history depth. The date never moves the window. |
| `churn-commits-v1.json` | The window's commits: each one's author, author date and commit date, and each path's commits. Read only when the churn map misses; a HEAD that grew from it walks only the new commits. Not kept in a shallow clone. | HEAD sha, window months, path format and the window cutoff its commits were cut at, plus the body's size and CRC. |
| `churn-log-v3.z` | The window's `git log --name-only` output, deflated, with its key in `churn-log-v3.json` beside it. | Same five fields. The key also records the window cutoff the log was cut at; a refresh below it walks the window again. |
| `coupling-cache-v2.json` | Ranked co-change pairs at the default thresholds, ordered and uncut. | The churn map's key plus a digest of the tracked set. |
| `mutate-pool/` | Kept worktrees for every mutation worker, including one. See [mutation worktrees](configuration.md#mutation-worktrees). | |
| `mutate-tmp/` | Recognized concurrent mutation runs, removed after completion or recovered under an exclusive lease. | |
| `mutate-pool.lock` | The lease on `mutate-pool/`, held while a mutation run uses the kept worktrees. | |
| `test-runs/` | Marked default test evidence from crapkit's own development runner, `tools/testing/run.py`, which prunes it by age and count (`--retention-days`, `--retention-count`) before each default run. `clean` leaves it alone. Explicit output and active leases are preserved. | |
| `report.html` | Where `crapkit report` writes by default. | |

Since 0.4.5 the rollup is filled once per run and pruned with its run, which is why `trend`
answers in 0.04 s warm on a corpus where it used to rescan 4.3 M rows. It means `trend` and
`report` write to `crap.sqlite` on a cold rollup, best effort: they read as before on a
checkout they cannot write to, just without the speedup.

The window ends at HEAD's commit date: its cutoff is `churn_window_months` calendar months
earlier, in UTC, so one HEAD names one window on any day and any machine. The tracked set is
in the coupling key because ranking drops any pair naming a file `git ls-files` no longer
lists, and the index moves without HEAD: `git rm --cached src/util.py` leaves the sha alone
and still has to retire every pair naming that file. The history depth is in all three keys
because deepening a shallow clone adds commits under an unmoved HEAD: it is git's shallow
boundary (the `shallow` file in the git directory), so after `git fetch --unshallow` or
`--deepen` the next `worklist`, `brief` or `coupling` reads the whole history it now holds.
Before 0.8.1 they served the shallow counts until the UTC date changed.

Two thresholds bypass the coupling cache. What is stored is the ranking at
`--min-support 5` and `--min-confidence 0.5`, so `--top` reads it and either threshold off
its default recomputes: serving a wider question from a narrower file would drop the pairs
the wider thresholds exist to surface.

The version marker is in the file name on purpose. 0.4.3 and 0.4.5 sharing one working tree
each read the other's cache as cold and rewrote it, so every run of both rebuilt the map.
Different formats, different files, both warm. This version never reads the files 0.4.5 to
0.8.0 wrote and leaves them to an older crapkit on the tree; 0.4.4's are deleted.

---

## Getting an artifact out of vitest

vitest ships **no coverage provider**. Without one, `crapkit init` writes a lane, `doctor`
reports no problems, and `coverage` exits 5:

```
crapkit: lane 'js' FAILED: lane 'js' produced no artifact at .crapkit/cov/js/coverage-final.json (command exit 1); lane log: /repo/.crapkit/lane-js.log; last output: $ npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js
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
scores (`4 measured, 0 over ceiling 6, CRAP load 12.0, grade A+`). Pick on your
project's grounds, not on crapkit's.

The provider version must match your vitest **major**, or npm refuses the install with
`ERESOLVE unable to resolve dependency tree`, naming the vitest peer the provider wants.
Read your vitest major with `npm ls vitest`, then install the provider at that major. On
vitest 5, the major `npm i -D vitest` installs today:

```
npm i -D "@vitest/coverage-v8@5"
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

`crapkit init` excludes `**/*.config.ts` (and `.js`/`.mts`) from scoring by default, so
this file never trips doctor's unclaimed-file check. If you wrote your `[exclude]` list by
hand, add `"**/*.config.ts"`: a leading `**/` matches zero or more directories, so that one
glob reaches the repo root and every nested copy.

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

`crapkit init` writes `--coverage.reportOnFailure` on the vitest lane it scaffolds, so a
repo that starts from `init` gets the report on a red run without touching its vitest
config. The key above is the same switch spelled in the file that already holds your other
coverage settings; either one is enough, and init writes the flag because it must not edit
your vitest config to write a lane. jest gets no such flag: it reports on a red run already,
and exits on a flag it does not know.

### The lane

```toml
[[lane]]
name = "js"
command = "npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js --coverage.reportOnFailure --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml"
artifact = ".crapkit/cov/js/coverage-final.json"
results_artifact = ".crapkit/cov/js/junit.xml"
parser = "istanbul"
scopes = ["web"]
```

vitest ships the junit reporter, so those three flags need no package. Name `default`
alongside it: `--reporter=junit` on its own replaces the console output you watch the run
through. That is the lane `crapkit init` writes for a repo whose `devDependencies` name
vitest and whose package.json has a `test` script. When the test script has another name
that starts with `test`, the command runs that script. With no such script it writes
`npx vitest run --coverage ...` with the same flags.

Never put a file filter in a `--coverage` command. vitest silently narrows the coverage
include set to the filtered files, so everything else reads as uncovered. crapkit refuses
the config rather than letting that happen at runtime. It checks each segment of the
command that names vitest, read by step 1 of
[How crapkit reads a lane's runner](#how-crapkit-reads-a-lanes-runner), whatever the
lane's `parser`: `npx vitest run`, `pnpm exec vitest` and, by the script-stem rule,
`node scripts/run-vitest.mjs run`. Config load reads crapkit.toml and nothing else, so a
vitest that a package.json script or a recipe runs is not checked:
`npm run test -- --coverage src/a.test.ts` and `make cov` load. Write
`npx vitest run --coverage` to keep the check. The scan starts after the word that names
vitest, past a `run` right behind it:

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
run 1 @ 70ac5e065df: 1 functions scored: 1 measured, 1 over ceiling 6, CRAP load 8.12, grade F
-> next: crapkit worklist
```

The junit half is a separate package: `npm i -D jest-junit` first, or jest exits on a
reporter it cannot resolve. It reads no path off the command line either: package.json,
the jest config or those two variables are the whole list, so without the `env` block it
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

That note names the interpreter word it probed and the path that word resolves to here,
and its install command is bound to the same word:

```
note: lane 'py' names `python`, which resolves here to /home/dev/venvbare/bin/python and cannot import pytest_cov - run `python -m pip install pytest-cov` in the environment the suite runs in (pip install "crapkit[py]" when that is crapkit's own environment), then `crapkit coverage`
```

A machine has more than one python, and the note used to say only "this python". Where the
one it names is not the one the suite should run in, installing the package is the wrong
move: repoint the lane instead.

A lane that names its python by path, such as the `.venv\Scripts\python.exe` init writes
for a repo's venv on Windows, gets an install command that names the file the path
resolves to, `C:/work/app/.venv/Scripts/python.exe -m pip install pytest-cov`. The word as
the lane spells it runs only from the lane's directory, and Git Bash reads its
backslashes as escapes. Forward slashes run in cmd.exe, PowerShell and Git Bash, and a
directory name that needs quoting is quoted on its own, as in a next step
([ADR 0003](adr/0003-a-pasted-command-never-opens-with-a-quote.md)).

A lane that reaches `crapkit coverage` with the plugin still missing gets the same fact from
the refusal: the package has to land in the environment the SUITE runs in, not in the shell's
active venv. Before 0.4.12 it read `pip install pytest-cov` and named no environment at all,
so a reader whose lane ran its own venv installed the package where it changed nothing. The
refusal binds the install to an interpreter under the same condition the probe uses: the
step that runs pytest starts with a python. So `python -m pytest --cov` and
`cd web && python -m pytest --cov` earn `python -m pip install pytest-cov`, while
`uv run pytest --cov` and `coverage run -m pytest --cov=pylib` name the environment and stop
there. Neither `uv`
nor `coverage` has a `-m pip install`, and a reader who runs one gets a second, unrelated
failure.

The probe asks the interpreter that runs pytest, found in the segment that holds the
`pytest` token. `coverage run -m pytest --cov=pylib && coverage json` names no interpreter
in front of pytest, so nothing is asked and no note is printed. If cmd.exe cannot start that
interpreter at all, the note names the word to change instead of talking about pytest-cov,
and on a Windows PATH holding only the `py` launcher `init` writes `py` rather than a
`python3` the first `coverage` could not run.

```toml
[[lane]]
name = "py"
command = "python -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit.xml --continue-on-collection-errors"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit.xml"
parser = "coveragepy"
scopes = ["api"]
```

`--cov-branch` is what makes the coverage term measure the same structure the complexity
term counts. Without it, coverage.py writes a report with no branch data, and since 0.4.12
that scores from statements with the downgrade said out loud rather than failing the lane:

```
$ crapkit coverage
crapkit: lane 'py': coverage.py report carries no branch data, so the coverage term is statement-based for this artifact - add --cov-branch to the lane command to measure branches
```

The report measures branches when its `meta.branch_coverage` says so, or when any of its
functions carries branch counts, so a report with no `meta` is judged by what it holds.
In a report that measures branches, coverage.py writes `num_branches: 0` for a function
with none, so a function with no branch counts at all was rewritten by something else.
Read from its statements, its coverage moved with nothing said; the report is refused:

```
crapkit: lane 'py' FAILED: coverage.py report measures branches, but 1 function(s) carry no branch counts (api/views.py: render), so crapkit cannot tell how many of their branches ran; regenerate the report with `coverage json`
```

Every function in the model already falls back to statement coverage when it holds no
branches, so refusing the report blocked arithmetic crapkit performs on every run, and
`pytest --cov --cov-report=json` is the shape most existing CI artifacts have, which is
what `--reuse-artifacts` is for. Add the flag anyway: statement coverage overstates a
branchy function, and the CRAP number is cubed in `(1 - cov)`.

One report is still refused, because there is nothing to divide by and every function in it
would come out fully covered:

```
$ crapkit coverage
crapkit: lane 'py' FAILED: coverage.py report lacks branch data - run the lane with branch coverage on
crapkit: every lane failed (1 of 1); the errors are above
```

Exit 5.

### A file the report carries no regions for

coverage.py writes the per-file `functions` key once per code-region kind that file's own
reporter declares, so a file measured by a plugin reporter declaring none (django or jinja
template coverage) loses the key while every `.py` file in the same report keeps it. That
one entry used to fail the lane and throw away every other file in the report, including
the ones that were fine. Those files are now skipped and named, and the rest is scored:

```
crapkit: lane 'py': coverage.py report has no function regions for 1 of 40 file(s) (tpl/page.html) - those files are skipped and the rest of the report is scored
```

A report where NO file carries regions is still exit 5, which is the "coverage is too old"
case the message was written for: `coverage.py report has no function regions for any of
its 40 file(s) - needs coverage>=7.13.1`. That verdict is read before the branch-data one, so
a report missing both is told its coverage is too old rather than sent to add `--cov-branch`,
which a coverage that old would not fix.

### A report from coverage 7.6 to 7.13.0

coverage.py writes each function region's `def` line as `start_line` from 7.13.1, and a
function's span starts there. An older report carries none, and no line inside the region
is the `def` line: the body starts below it, and a nested function's `def` statement sits in
its encloser's region. Read from the body, a nested function that never ran joined its
encloser's region and scored as half covered. A report with a region whose `start_line` is
missing or null is refused at exit 5, naming the artifact, the file and the first such
function:

```
crapkit: lane 'py' FAILED: unparseable coverage.py report /repo/.crapkit/cov/py.json: pkg/mod.py: outer: no start_line; coverage.py writes it on every function from 7.13.1, so install coverage>=7.13.1 and rerun the lane
```

Install coverage.py 7.13.1 or newer where the lane runs (`pip install "crapkit[py]"` pulls
it when crapkit shares the suite's venv), then rerun `crapkit coverage`. A repo that measured
on an older coverage can see a nested function's mark exceeded once; the
[upgrade guide](upgrading.md#081-on-coverage-76-to-7130) says what to do.

Every region in the report needs its `summary` object; coverage.py writes one on each. A
region without one exits 5, where it used to score the function as never run:

```
crapkit: lane 'py' FAILED: unparseable coverage.py report /repo/.crapkit/cov/py.json: pkg/mod.py: guarded: no summary object, so crapkit cannot tell how much of it ran; regenerate the report with `coverage json`
```

### A function coverage.py excludes

`# pragma: no cover` on a def line, or an `exclude_lines` or `exclude_also` pattern that
takes every statement in a function, leaves coverage.py a region with no statements and
its lines under `excluded_lines`. From coverage.py 7.10.1 the default patterns also exclude
a stub whose body is `...`, such as a `Protocol` method. That function reads `excluded`:
`crap = ccn`, remedy `ok` or `decompose`.
A pattern that takes some statements and leaves others, such as a `raise
NotImplementedError` line, excludes those lines and the function is measured on the rest.
Through 0.8.0 an excluded function read cov 0, `crap = ccn^2 + ccn`, with `add-tests`
advice no test could follow.

### `--continue-on-collection-errors`

This is pytest's `reportOnFailure`, and it is the flag people leave out. pytest raises
`Interrupted` at the **end of collection** when any test module fails to import, so
pytest-cov's session finish never runs and **no coverage report is written at all**, even
though every other test file collected fine and would have run. One renamed module, one
missing optional extra or one stale editable install takes the whole lane down and drops
every scope it measures to no-lane. Same repo, same command, only that flag toggled:

```
$ python -m pytest --cov --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml   # one bad import
Interrupted: 1 error during collection
$ ls .crapkit/cov/
junit-py.xml

$ python -m pytest --cov --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml --continue-on-collection-errors
$ ls .crapkit/cov/
junit-py.xml  py.json
```

The junit lands either way, which is what makes the failure read as half a run rather than
as a flag. `crapkit init` writes the flag on the pytest lane it scaffolds and on the
commented template beside it, so a repo that starts from `init` scores the files that did
collect. Nothing is hidden: the uncollected file's tests are still errors in the junit, and
`verify`'s no-new-failures check reads them there.

### The interpreter a lane binds to

`python -m pytest` is not one command. `python` resolves through the shell's `PATH`, so
the lane runs under whichever virtualenv the shell happened to have active, which is
almost always right, and silently wrong in the one case that matters.

Two git worktrees of one repo. Checkout B's venv is active, and it holds an editable
install pointing at B's `src`. Run `crapkit coverage` in checkout A and the lane's pytest
imports **B's** sources. When the two checkouts' APIs have diverged, collection dies and
you get exit 5. When they have not (the ordinary case for two worktrees of one branch),
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
| none, and a venv in the tree | `{python:.venv} -m pytest …`, which runs `.venv/bin/python` on Linux and macOS and `.venv\Scripts\python.exe` on Windows |
| none at all | `{python} -m pytest …`, which runs `python3` on Linux and macOS and `python` on Windows; where that name does not resolve, the first of `python`, `python3` and `py` that does |

The first match in that order wins, so a repo mid-migration between two managers gets the
same config every time.

With no lockfile, `init` looks for the environment the repo carries: `.venv`, `venv`, and
one `.venv` inside each scope it just sniffed. A directory counts only when it holds
`pyvenv.cfg` and its interpreter imports `pytest`, so an empty environment, or a `venv/`
package of somebody's sources, leaves the bare name alone. The path is repo-relative
because an absolute one does not survive the repo reaching anyone else.

crapkit.toml is committed, so a Windows author's lane runs on a Linux collaborator's
checkout and the other way round, each with a venv of its own. No one spelling of the
launcher runs on both. cmd.exe reads an unquoted `/` as the end of the command name, so
`.venv/bin/python` answers `'.venv' is not recognized`, and sh reads
`.venv\Scripts\python.exe` as `.venvScriptspython.exe`. A bare `python` fails on an
Ubuntu without python-is-python3. So `init` writes a
[launcher token](configuration.md#the-launcher-token), and the loader replaces it with
the launcher of the OS reading the file before anything reads the command. The one gap
left is a machine where only another name resolves: `init` writes `py` on a Windows PATH
that carries only the launcher, and a committed `py -m pytest` fails every Unix
collaborator's doctor. Install a Python that puts `python` on that PATH, then write
`{python}` in place of `py`.

Every python line `init` writes carries the same prefix, and there are two in any one
file: the `[crapkit.scoped_tests]` entry, plus either the live `[[lane]]` command or,
in a repo with no pytest marker file, the commented `[[lane]]` template that stands in
for it. Step 3 measuring one environment while step 4 tests another is the same bug one
command later,
and a template that reads `python -m pytest` on a `uv.lock` repo is that same bug one
uncomment later.

`init` does not probe a managed lane for `pytest-cov`. `uv run` and its siblings create or
sync the project environment before running anything, and `init` has no business
provisioning one to ask a question about it. If the plugin is missing, the lane says so on
its first run, with the log path. `doctor` holds to the same rule and says so: where a
python-headed lane gets `ok   lane 'py': python -> <path> (pytest X, pytest-cov Y, coverage Z)`,
a managed one gets a `note` that its interpreter and pytest-cov were not probed, so a lane
doctor did not ask never reads as one it found healthy. A probed lane whose coverage.py is
older than 7.13.1 FAILs, because coverage.py writes each function's start line, which
crapkit scores from, only since 7.13.1 and `crapkit coverage` refuses that lane's report
with exit 5:

```
FAIL lane 'py' runs coverage 7.4.4 (/home/you/ledger/.venv/bin/python), which writes no function start lines, so `crapkit coverage` refuses its report with exit 5 (needs coverage >= 7.13.1); install 7.13.1 or later there with `/home/you/ledger/.venv/bin/python -m pip install "coverage>=7.13.1"` and raise any pin that holds it lower
```

It does check that the manager itself is installed here, because the lockfile is the
repo's property and the PATH is the machine's. A `uv.lock` a teammate committed on a
machine that installed the dependencies with pip gets the `uv run` lane, which is the
right command for the repo and cannot start on this checkout, so `init` says so rather
than pointing at a `crapkit coverage` that exits 5:

```
note: lane 'py' runs through `uv`, which this machine's PATH does not carry - install uv, or point the lane's command in crapkit.toml at an interpreter that resolves here, then `crapkit coverage`
```

Writing the prefix by hand is the fix for a repo that adopted crapkit earlier, or one that
pins its environment some other way: `command` is a shell string and takes anything. A
config `init` wrote before 0.8.1 names one OS's venv launcher: swap `.venv/bin/python`,
or `.venv\\Scripts\\python.exe` as the TOML string spells it, for `{python:.venv}` so
checkouts on the other OS run it too.

### A suite that spawns subprocesses

Two things have to be true, and neither one fails loudly.

**Ask coverage to patch subprocess.** pytest-cov measured subprocesses itself until 7.0.0,
which dropped the feature and pointed at coverage's own patch system (coverage 7.10 and
later):

```toml
[tool.coverage.run]
patch = ["subprocess"]
```

Without it, on pytest-cov 7 and later, a suite that drives its CLI through
`subprocess.run` measures the parent only: every entry point reads 0% and every function
behind one is scored as untested. Nothing warns. crapkit's own e2e suite is exactly that
shape, and the key is in crapkit's pyproject.toml for exactly that reason. Note that
coverage 7.9 and earlier answer the key with a warning and ignore it. Pin
`coverage>=7.13.1` beside `pytest-cov` wherever the lane's environment is declared: it
takes the key, and it writes the `start_line` crapkit
[needs](#a-report-from-coverage-76-to-7130).

**Prefer `--cov=<module>` over `--cov=<path>`.** The source has to resolve from the
**child's** cwd, and a suite that runs its CLI in a tmp directory is not in the repo any
more. A path-based source measures nothing there while still reporting a confident 0%.
crapkit's own lane uses the module form because of it.

### The full-suite rule

A lane refuses a positional argument in each segment of its command that names pytest,
read by step 1 of [How crapkit reads a lane's runner](#how-crapkit-reads-a-lanes-runner),
whatever its `parser`: `pytest`, `python -m pytest`, `uv run pytest`, `.venv/bin/pytest`
and, by the script-stem rule, a script that node, bun or deno runs whose stem names pytest
(`node scripts/run-pytest.mjs`). The command alone is read: a pytest that `make cov` or a
package.json script runs is not checked, so write `python -m pytest` in the command to keep
the check:

```
crapkit: lane 'api': positional argument 'api' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately; a suite whose testpaths cannot be collected in one process needs one lane per testpath, each with full_suite = false and its own artifact
```

Subset coverage under a suite with cross-file pollution is run-order dependent, so the
number moves for reasons that have nothing to do with your code. Two situations get past
the refusal, and they take different edits.

**The suite really is scoped and isolated.** Opt out explicitly with `full_suite = false`
on the lane.

**The testpaths cannot be collected together.** A repo whose `pytest.ini` names four
testpaths and whose whole-suite run dies during collection has no full-suite command to
write at all. The reported case is a `conftest.py` two testpaths both import, which under
`--import-mode=importlib` registers the same plugin module twice and ends the run with
`ValueError: Plugin already registered under a different name` before one test runs.
Setting `full_suite = false` on a single narrowed lane clears the refusal and measures one
testpath; the other three go dark, their functions score `no-lane`, and nothing says so.

Declare one lane per collectable testpath instead, each with `full_suite = false`, its
own artifact and its own coverage.py data file:

```toml
[[lane]]
name = "py-conform"
command = "python -m pytest conform --cov --cov-branch --cov-report=json:.crapkit/cov/py-conform.json --junitxml=.crapkit/cov/junit-py-conform.xml --continue-on-collection-errors"
artifact = ".crapkit/cov/py-conform.json"
results_artifact = ".crapkit/cov/junit-py-conform.xml"
parser = "coveragepy"
env = { COVERAGE_FILE = ".coverage.py-conform" }
scopes = ["impl"]
full_suite = false

[[lane]]
name = "py-impl"
command = "python -m pytest impl --cov --cov-branch --cov-report=json:.crapkit/cov/py-impl.json --junitxml=.crapkit/cov/junit-py-impl.xml --continue-on-collection-errors"
artifact = ".crapkit/cov/py-impl.json"
results_artifact = ".crapkit/cov/junit-py-impl.xml"
parser = "coveragepy"
env = { COVERAGE_FILE = ".coverage.py-impl" }
scopes = ["impl"]
full_suite = false
```

Both lanes start in the repo root, where coverage.py writes `.coverage` unless
`COVERAGE_FILE` names another file. When a lane starts, pytest-cov deletes its data file and
every file named after it plus a dot, and when it ends it combines those files, so a lane
left on `.coverage` also takes in the other lane's `.coverage.py-impl`. Serial lanes take
turns on these files. Under [`max_parallel_lanes`](#running-lanes-in-parallel), two lanes on
one data file can lose one to `sqlite3.OperationalError: table coverage_schema already
exists`, and a lane on `.coverage` beside one on `.coverage.py-impl` can lose one to
`PermissionError: [WinError 32]` on Windows. Keep the `env` line in both.

Several lanes may name the same scope: the parts table at the top of this page forbids
two lanes sharing an `artifact` path, and nothing else. Both lanes above name `impl`, the
only scope this repo sniffed: `conform` holds tests, not scored source, and `init` copies
the scopes it found onto every stub it writes. Every testpath stays measured,
and each lane fails on its own. `crapkit init` writes this block for you, commented out,
when the repo's pytest config names more than one testpath.

A flag's value is not a positional. `-n 8`, `-o timeout=300`, `-p no:randomly` and
`--deselect tests/test_x.py::test_slow` all pass: the guard knows the pytest options that
read the next token, and treats a `key=value` token as a value everywhere. Quoting is the
shell's, and [How a lane command is read](#how-a-lane-command-is-read) has the whole rule:
`-m "not live and not perf"` is one marker expression, not four positionals. In
`crapkit.toml`, a single-quoted TOML string keeps the double quotes unescaped:
`command = 'python -m pytest -m "not live and not perf" --cov=pylib ...'`. After a flag it
does not know, a bare word is that flag's value too; only a path or a node id
(`pylib/unit`, `tests/test_x.py::test_slow`) outranks the guess and is refused:

```
$ crapkit doctor       # command = "python -m pytest -q pylib/unit"
crapkit: lane 'py': positional argument 'pylib/unit' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately; a suite whose testpaths cannot be collected in one process needs one lane per testpath, each with full_suite = false and its own artifact
```

When the refused token really was a value, the attached form is the one-edit fix: dropping
it breaks the command, because the flag then eats whatever comes next.

Positionals that together name every configured `testpaths` entry are not narrowing either.
`python -m pytest tests --cov=app` beside a `pyproject.toml` whose `[tool.pytest.ini_options]`
says `testpaths = ["tests"]` collects exactly what a bare `pytest` collects there, so it
loads. The entries are read from the file pytest would pick where the lane runs (`cwd` when
the lane sets one), in pytest's order: `pytest.ini` and `.pytest.ini` decide when present,
even empty; `pyproject.toml`, `tox.ini` and `setup.cfg` decide when they hold a pytest
section. `tests/`, `./tests` and `tests` are one entry. One entry of several is still
narrowing: `pytest tests` under `testpaths = ["tests", "integration"]` runs half of what a
bare `pytest` runs and is refused, and so is `tests/unit` under `testpaths = ["tests"]`. A
lane without a positional reads none of those files.

### Test attribution for `explain --tests`

`crapkit explain FILE NAME --tests` lists the tests that covered a function, from coverage.py
contexts. Without them it says so rather than guessing:

```
tests: no context data - run the py lane with dynamic_context = test_function and a --show-contexts JSON report
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

Getting `path_prefix` wrong is quiet and expensive. The same tree, same suite, only the key
removed:

```
with    path_prefix: 1 functions scored: 1 measured, ..., CRAP load 10.75
without path_prefix: 1 functions scored: 1 untested, ..., CRAP load 20.0
```

The lane ran and passed both times. Without the prefix, no artifact path matched any scoped
file, so every function fell to `untested` and scored as if nothing tested it. The run
still exits 0. The one sign is a stderr line that opens
`crapkit: lane 'py' measured 1 file(s), none of them under the paths its scopes declare`.
A lane with no prefix is told the runner may report paths it needs `path_prefix` to
rebase. A lane whose prefix names the wrong directory is told which prefix crapkit read,
and which prefix would key a file the runner named that is on disk and in the lane's
scopes:
`or path_prefix 'web', which crapkit.toml sets for this lane, does not rebase the
runner's paths onto those scopes; path_prefix = 'api' would key the runner's src/calc.py
as api/src/calc.py, a file those scopes claim`. When the runner's paths need no prefix,
the line says to drop `path_prefix` from the lane. When no declared path holds a file the
runner named, it says to set `path_prefix` to the directory the runner's paths are
relative to.

Any spelling of the right directory works, because `path_prefix` is read the way a scope
path is: `api\`, `./api/`, `.\api\` and `/api/` all read `api/`, and on a disk that ignores
case `API/` takes the case the directory lists. Before 0.8.1 each of those glued its own
text onto every key, so a Windows-written `api\` scored the whole scope untested on every
OS, with the same stderr line as the only sign.

---

## A crapkit root below the repo top

The crapkit root is wherever `crapkit.toml` sits, and it does not have to be the git top. A
package one directory down inside a bigger repo is a supported shape, and so is a root
configuration whose scopes claim the packages below it; neither needs a mode or a config
key. Stand in the package, or point `--repo` at it.

```
$ crapkit coverage --repo packages/api
run 1 @ 387e938f537: 1 functions scored: 1 measured, 1 over ceiling 6, CRAP load 13.12, grade F
-> next: crapkit worklist
$ crapkit worklist --repo packages/api
worklist @ 387e938f537 (run 1, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk     10.5  ccn   7  crap    13.1  cov  50%    6c/1a  calc/grade.py:1  classify( score , attempts , late , bonus )
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
```

`--repo` names the crapkit root, never the git top, and every subcommand you invoke by hand
takes it; `claude-hook` is the exception and finds the root by walking up from the edited
file instead. Without `--repo`, every command finds the root the way the hook does
([ADR 0002](adr/0002-configuration-is-found-upward-nearest-wins.md)): it walks up from the
working directory to the nearest `crapkit.toml`. `cd packages/api && crapkit worklist` reads
`packages/api/crapkit.toml`, and in a monorepo whose root configuration claims `web/`,
`cd web && crapkit worklist` reads the root's. When the root found is not the working
directory, one stderr line says which file is in use, `crapkit: using crapkit.toml at
/repo`, and a relative path argument is read from where you stand: `crapkit brief
src/grade.ts classify` typed in `web/` names `web/src/grade.ts`, and a path that climbs out
of the root is refused. Nearest wins, so a nested configuration shadows an ancestor's, and
a `.git` entry (file or directory) without a configuration stops the walk: a linked
worktree or a nested repository never borrows a parent's configuration or its store. A
given `--repo` names an exact root, walks nowhere, and reads a relative path argument
against that root. `init` writes where you stand and
refuses, exit 3, under a directory an ancestor's scope path already claims:
`crapkit.toml at /repo already claims web (scope 'web'); edit that configuration instead`.

Both halves of a row are root-relative. The scored path comes from `git ls-files`, which
answers relative to its cwd. The churn count comes from `git log --relative`, which answers
the same way. The `6c/1a` above is 6 commits and 1 author against `calc/grade.py`, joined
out of a history whose own paths read `packages/api/calc/grade.py`.

Before 0.4.4 the churn log ran without `--relative`, so every lookup missed and `worklist`
filed the whole corpus under dormant: `0 active, 215 dormant` on a repo with 90 commits that
week. If you see that, check the version before you check your config. The churn caches
moved to new file names, so a map laid down with top-relative paths is ignored rather than
reused, and a 0.4.3 sharing the repo keeps its own.

### The gate reads paths from the crapkit root

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
in that warning. The hook git runs needs no `cd`: git starts it at the top, and with no
`crapkit.toml` there the gate runs in `packages/api` itself and prints the same row from the
top, `packages/api/calc/grade.py:17`.

A command other than the hook that runs at the git top, such as Route 4's `crapkit verify`
in a CI step, finds no `crapkit.toml` there and exits 3, and the refusal says which flag to
add: `no crapkit.toml at /repo - nothing to analyze; crapkit.toml sits below it in packages/api:
pass --repo packages/api`.

---

## Containers

A lane whose command names pytest refuses to run inside a container and exits 5:

```
crapkit: lane 'py' FAILED: lane 'py' runs the python suite, which is host-only (container runs OOM); set container_ok = true only if this environment truly differs
```

Two triggers, either one is enough: the file `/.dockerenv` exists, or
`CRAPKIT_INSIDE_CONTAINER=1` is set in the environment. Docker writes `/.dockerenv` into
every container it starts, so these all count as containers: a devcontainer, a GitHub
Codespace, a CI job that runs in a `container:` image or on a Docker executor, and an agent
that works in a cloud container, such as a Codex cloud task. `crapkit doctor` names each
lane the guard will refuse with a WARN, before the first `crapkit coverage`
does. Podman writes `/run/.containerenv` instead, which the guard does not read; set
`CRAPKIT_INSIDE_CONTAINER=1` there if the container caps memory. The guard exists because a python
suite under coverage is memory-hungry and a container memory cap turns that into an OOM kill
that looks like a flaky lane. It fires on the path that launches the suite and nowhere else,
so `--reuse-artifacts` reads a host-built report inside a container without hitting it: that
run parses a file already on disk and starts nothing. If your container is sized for it, say
so per lane:

```toml
container_ok = true
```

The guard reads the command alone, by the rules in
[How crapkit reads a lane's runner](#how-crapkit-reads-a-lanes-runner) step 1, whatever
the lane's `parser`: a lane whose command names no pytest is never refused. `make cov`
runs, and so does a vitest lane; write `python -m pytest` in the command to keep the guard.

`crapkit doctor` reads the same two triggers, so a devcontainer, a Codespace, a Codex cloud
task or a CI job in a container hears about the guard before the first `coverage` run
refuses. It prints one WARN per lane whose command names pytest and that has no
`container_ok`, naming the trigger it found:

```
WARN lane 'py' runs a coverage.py suite and this is a container (/.dockerenv exists): `crapkit coverage` refuses it with exit 5; if the container is sized for the suite, set container_ok = true on the lane (docs/lanes.md#containers)
doctor: no problems found, 1 warning above
```

doctor exits 0 here, because the config is right and the machine is the question. The closing
line counts the warnings, so a reader who reads only the last line still learns there is one.

---

## Reusing artifacts

Rerunning a suite crapkit already read is the slowest thing it does. Two flags skip it.

Every artifact crapkit reads is stamped in `.crapkit/artifacts.json` with the commit it was
built at, the lane that built it, and how long the lane took. A lane that ran and left its
artifact unwritten stamps the sha256 of the file it left instead, under `refused_sha256`,
which is what [refuses that file on reuse](#the-artifact-a-failed-attempt-left-behind-is-refused).

| Flag | Behavior |
|---|---|
| `--reuse-artifacts` | Skip every lane command, parse whatever is on disk, except the artifact a lane's last attempt failed to write: that one is refused (exit 5) while it holds the same bytes, so a touch, a copy that drops times or a same-bytes rewrite keeps it refused and a file with new bytes is read. Warns per lane when files under that lane's scopes changed since the stamp, or when git could not tell. A declared junit it cannot read is a warning under `coverage` and [exit 5 under `verify`](#under---reuse-artifacts-it-is-a-warning). |
| `--reuse-unchanged` | Reuse a lane only when its stamp proves nothing it reads changed; otherwise run it again. A lane without `inputs` needs the same clean HEAD, unchanged lane settings, `crapkit.toml` bytes, inherited environment, crapkit version and coverage/JUnit bytes. A lane with `inputs` needs its artifact's commit in this clone, no change under those paths between that commit's tree and the working tree, its own lane table and `env` unchanged, the same crapkit version, and the same coverage/JUnit bytes. A failed attempt's leftover always reruns, whatever its modification time says. Each lane prints one line saying which it did: a rerun names the first condition that failed, and a reuse names what its proof leaves out. |

Without `inputs`, automatic reuse covers the whole tracked tree, including tests and
shared helpers: any tracked or untracked change, or a new commit, reruns the lane.
A change means new bytes, as `git status` reads them: a `touch`, a file saved with the
same bytes or a fresh copy of the checkout is no change, whatever `diff.autoRefreshIndex`
the repo sets, and crapkit's reads leave `.git/index` as they found it.
With [`inputs`](configuration.md#lane) it covers exactly those paths, literal paths
from the root with no globs, so a docs commit or an untracked draft elsewhere reruns
nothing, and a file the command reads that the list leaves out is never checked. The
inputs proof compares trees, not history: a message-only amend, a rebase onto a commit
that touched nothing under the inputs, or a switch to a sibling branch with the same
inputs reuses the lane. A clone that does not hold the stamp's commit, such as a
shallow CI checkout with `.crapkit/` restored, reruns it and says so.

git's own diff skips three kinds of edit, and reuse does not: a file flagged
`--skip-worktree` or `--assume-unchanged` whose bytes differ from the index, and an
edit inside a submodule whose `.gitmodules` entry says `ignore = dirty`. A same-size
edit whose old modification time was put back (`cp -p`, `tar -x`, `rsync -t`) passes
git's stat check, and reuse trusts that check: it is a named limit until the cost of
hashing every file on every run is measured. The limit holds on Windows, where the change
time is the creation time, and under `core.trustctime=false`. On Linux and macOS git's
default stat check also compares the change time, which no copy puts back, so git sees
the edit once the change time moves a second past the one it recorded. Run `crapkit
coverage` after such a copy.
An entry that matches no tracked file, and no untracked file outside `.gitignore`,
hides every change behind it, so `doctor` fails on it and names the entry.
Measurements made while their proof did not hold (a dirty tree, or dirty inputs)
and older stamps without this proof cannot be reused automatically.

The declared `artifact` and `results_artifact` of every lane in `crapkit.toml` are not
changes to the tree or to a lane's inputs, whether git ignores them or not: every run
rewrites them, and each stamp proves its own by their digests. Neither is an untracked
file a lane's own run wrote, such as `.coverage` at the root from pytest-cov or
`__pycache__` under the scopes: the stamp lists it under `byproducts`, and no lane's
proof counts it while it stays untracked. `crapkit init` ignores only `.crapkit/`, so
without this the first run's own output voided its proof and the lane never reused.
Any other file that git does not ignore is a change, and the rerun line names it.

The environment half of the proof leaves out what a shell or terminal keeps for its
own bookkeeping: `OLDPWD`, `PWD`, `SHLVL`, `_`, terminal session ids such as
`WT_SESSION`, `TERM_SESSION_ID` and `SSH_CONNECTION`, the agent and IPC sockets a
login, tmux or an editor terminal opens, such as `SSH_AUTH_SOCK`, `SSH_AGENT_PID`,
`TMUX` and `VSCODE_GIT_IPC_HANDLE`, and PowerShell's own `PSModulePath`, so a `cd`, a
new terminal or a switch between PowerShell and Git Bash between two runs reruns
nothing. Names are compared in upper case, the way Windows spells them. Every other
inherited variable counts, `PATHEXT` included: it decides what cmd.exe starts for a
lane's first word. The stamp keeps a
16-hex digest of each value under `proof_parts`, never the value, so a rerun can
name the variable that changed.

Each lane says what `--reuse-unchanged` decided, before any lane starts:

```
$ crapkit coverage --reuse-unchanged
crapkit: lane 'py': measurement inputs unchanged; reusing without rerun (artifact built at 525a3276065); its proof leaves out gitignored files and anything outside the repository
crapkit: lane 'web': rerunning: the working tree has 1 uncommitted change(s): web/src/app.ts
```

A rerun names the first condition that failed: `no artifact at PATH`, a last attempt
that wrote none, `its stamp holds no proof` and why (the uncommitted changes it was
measured with, a git read that failed while it was measured, or a crapkit that recorded
none), uncommitted changes, `HEAD is X and its artifact was built at Y`,
`crapkit.toml changed`, `its lane table changed`, `the crapkit version changed`,
`N environment variable(s) changed: NAME`, changes under a lane's `inputs` since its
commit, `nothing proves its inputs unchanged` and what git said when a read of them
failed, a stamp commit this clone does not hold, or a declared file that no longer
matches its stamp: `PATH: missing`, `PATH: unreadable (why)` or
`PATH: bytes differ from its stamp`. `crapkit.toml` is compared with CRLF read as LF, so a
checkout under `core.autocrlf=true` is the file it was. `coverage --json` carries the
same sentence per lane as `rerun_reason`, `""` for a lane it reused.

Some inputs are outside the proof, and the line that reuses a lane names them. For a
lane without `inputs`: gitignored files other than `crapkit.toml`, and anything outside
the repository (installed dependencies, tools, services). For a lane with `inputs`:
gitignored files, files outside its inputs and inherited environment variables, since
a variable its command reads belongs in its `env`. An edit there reuses the old
artifact by design; run fresh coverage when those inputs change. `--reuse-artifacts`
remains an explicit request to read saved artifacts and keeps its warning about stale
source coverage.

Commands running as the same user on the same host cannot own the same
measurement outputs. Ownership covers
execution, coverage/JUnit parsing and artifact stamps, including absolute artifact
paths shared by different checkouts. A conflicting command refuses before it
runs. Independent output paths can run in parallel. Coordination files live under
`~/.cache/crapkit/measurements/<host-id>`, outside report directories that runners
may delete and recreate. The key uses the full resolved artifact path. `TEMP`,
`TMP` and `CRAPKIT_RESOURCE_DIR` do not select another measurement domain. A process
started without `USERPROFILE` (Windows) or `HOME` (POSIX) still finds the same `~`
through the operating system; [resources.md](resources.md#analysis-workers) says how.
If the CLI dies, its helper stops registered test processes before releasing
ownership. Small stable lease files remain as coordination state; do not delete
them as idle evidence.

Before 0.7.1, adjacent artifact locks could also coordinate different users or
hosts when their shared filesystem supported those locks. The new per-user/host
domain does not provide that coordination. Such writers need external
serialization or distinct artifacts. Finish old-version measurements before
starting new-version measurements because their lock locations differ.

**Passing both makes `--reuse-artifacts` win.** It is checked first, so nothing reruns
whatever changed, and the `measurement inputs unchanged; reusing without rerun` line
never prints. Live, on a tree with an edited source file:

```
$ crapkit coverage --reuse-artifacts --reuse-unchanged
crapkit: lane 'py' reuses .crapkit/cov/py.json; 2 file(s) in its scopes changed since it measured them (calc/grade.py, calc/hot.py), so its coverage may be stale; rerun the lane (`crapkit coverage --lane py`) to measure the tree as it is
run 9 @ 525a3276065: 5 functions scored: 4 measured / 1 untested, ...

$ crapkit coverage --reuse-unchanged
run 10 @ 525a3276065: 5 functions scored: 5 measured, ...
```

The new function reads `untested` in the first run and `measured` in the second, because
only the second actually ran the suite.

A stale artifact also silences the dark-line fields of the files that moved. `next-item`
and `brief` then emit `uncovered_lines: null` for such a file, with a note naming it and
the lane to rerun, rather than an empty list a caller would read as "nothing left to
cover". Every other file keeps its lines.

"Moved" is about content, not history. Each run's stamp in `.crapkit/artifacts.json`
holds the git blob id of every file under the lane's scopes as the run left them
(`blobs`): the id `git add` would store, through the repo's filters. A submodule is
recorded by the commit its checkout holds. The note and the warning above compare those
ids with the files on disk, so a `touch`, a mode bit, a CRLF checkout under
`core.autocrlf=true`, an expanded `$Id$`, a message-only amend, a rebase, a detached HEAD
and a shallow CI clone with `.crapkit/` restored are not moves, whatever
`diff.autoRefreshIndex` says. An edit is, and so is a new or deleted file under the
scopes, a CRLF rewrite under `core.autocrlf=false`, an uncommitted `.gitattributes` that
renormalizes a file, an edit inside a submodule, and an edit reverted after the lane
measured it. A file the lane itself writes under its scopes while it runs, such as
`src/__pycache__`, is recorded as the run left it and is not a move.

git's index is the fast path: a tracked file `git status` calls unchanged holds the
id the index records, and only the rest is hashed. So a same-size edit whose old
modification time was put back keeps the index's id and is not seen: the same named
limit as reuse, on the same OSes and settings, until hashing every file is measured.

A stamp written by crapkit 0.8.0 or older holds no `blobs`. It is judged the old way
until the next `crapkit coverage` replaces it: git's diff since the stamp's commit,
uncommitted edits included, which needs that commit behind HEAD. While that says stale,
every file's dark lines are null. When git cannot answer, or the clone does not hold
the stamp's commit, the note and the warning say so and give git's error rather than
claim a file changed.

### The artifact a failed attempt left behind is refused

`coverage` refuses a lane that ran and did not rewrite its artifact ([the artifact has to
be the one this run wrote](#the-artifact-has-to-be-the-one-this-run-wrote)). Until 0.5.0
that refusal ended with the run: the file stayed on disk, the next `coverage
--reuse-artifacts` parsed it, and `verify --reuse-artifacts` passed over it and wrote
itself in as the trusted baseline. The failed attempt now records the sha256 of the file it
left in the stamp, and reuse refuses the file while it holds those bytes:

```
$ crapkit coverage --reuse-artifacts
crapkit: lane 'py' FAILED: lane 'py' wrote no artifact on its last attempt - the .crapkit/cov/py.json on disk predates it and is the previous run's, which --reuse-artifacts will not score; lane log: /repo/.crapkit/lane-py.log; last output: ...
crapkit: every lane failed (1 of 1); the errors are above
EXIT=5
```

Same exit 5 as the run that failed, same log path. With other lanes measured the run is
`partial` and `verify` refuses to conclude, exactly as for any failed lane. New bytes
clear it: a real run that writes the artifact, or a file combined by hand. A coverage
JSON combined from a killed run's shards holds other bytes than the refused one, so the
[shard recipe](#a-killed-run-leaves-its-coverage-shards-behind) still ends in a
`--reuse-artifacts` run that scores. A touch, a copy of the checkout that drops times, a
backup restore or a sync client that rewrites the same bytes does not: the refusal was
keyed on the modification time until 0.8.1, and each of those handed the dead lane's
numbers back as a trusted run. A file that is gone is the missing-artifact refusal, as
before.

The refusal is written to `.crapkit/artifacts.json` through a temporary file that
replaces it in one step, and `crap.sqlite` keeps a copy, so deleting
`.crapkit/artifacts.json` does not lift it. A stamp file that cannot be read (cut short,
a top level that is not an object, or an entry for the lane's artifact that is not an
object) may have held a refusal the store does not, so reuse refuses that lane too. Before
0.8.1 each of those read as no stamp at all, and reuse scored a dead lane's leftover as a
trusted run:

```
$ crapkit coverage --reuse-artifacts
crapkit: lane 'py' FAILED: lane 'py': .crapkit/artifacts.json cannot be read (it does not parse as JSON), so crapkit cannot tell whether the .crapkit/cov/py.json on disk is the file a failed attempt left; rerun the lane (`crapkit coverage --lane py`), or delete .crapkit/artifacts.json to reuse the file as it stands
```

A refusal crapkit 0.8.0 recorded holds a modification time (`refused_mtime_ns`) and no
digest, and is judged by that time until the lane's next run records a digest.

The refusal is keyed on the attempt, not on the failure. A lane refused before it ran, such
as the python lane under the [container guard](#containers), records nothing, and
`--reuse-artifacts` stays the way through that guard. A lane that failed after rewriting
its artifact (a junit that says the run did not finish, an artifact from another tree)
records nothing either: that file is this run's, and reuse judges it on its own terms.

`--reuse-unchanged` reads the same refusal, so a lane whose last attempt wrote nothing
reruns even when every other input still matches, and a touch of the leftover does not
change that.

`doctor` WARNs about the same file. crapkit writes it through a temporary file that
replaces the old one in one step, so a crash mid-write no longer leaves it cut short. A
missing file is not an unreadable one: a repo that only ever reuses artifacts another
command wrote never stamps any. That is also why deleting `.crapkit/artifacts.json` drops
every refusal it held, and why a store a crapkit older than 0.5.0 left behind holds none:
reuse then scores whatever file is on disk. Delete it only when you mean to trust every
artifact there.

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

`command` runs under a shell, so stopping the shell alone can leave the suite running.
crapkit registers the command before it runs any code: Windows starts it suspended and
resumes it once its Job holds it, and POSIX holds a launcher at a start gate that execs
the command after registration. A separate owner keeps resource locks until command
cleanup finishes, even if the caller dies.

One window stays open on Windows. A caller killed after the command starts and before
crapkit writes the command's add request to its owner leaves the command suspended for
good, holding every file it inherited, the lane log among them. That hand-over can wait
behind another command's stop on the same owner. Creating the process inside its Job,
with `PROC_THREAD_ATTRIBUTE_JOB_LIST` through a raw `CreateProcess`, is the known way to
close it.

Windows uses a Job that retains descendants after their parent exits. POSIX uses
the command's process group and keeps its leader unreaped until cleanup completes.
Normal command exit, timeout and caller death stop the owned descendants before
resources are released. A command with no timeout still has no deadline.

POSIX commands must retain their inherited process group. A daemon that explicitly
escapes with `setsid` is outside that ownership. Linux confirms group termination
through `/proc`; other POSIX hosts use the system `ps` command. Windows waits until
the Job has no active processes.

The lane above spawns a grandchild that appends a line to `ticks.txt` twice a second for
30 s. Two attempts at a 2 s deadline, and the file stops growing the moment the deadline
lands:

```
$ crapkit coverage
crapkit: lane 'slow' FAILED: lane 'slow' timed out after 2s (attempt 2); log: ...\.crapkit\lane-slow.log
crapkit: every lane failed (1 of 1); the errors are above
EXIT=5
$ wc -l < ticks.txt
10
$ sleep 5; wc -l < ticks.txt
10
```

The same bounded spawn backs `mutation_timeout_seconds` and `init`'s pytest-cov probe, so a
looping mutant is cut instead of outliving the run that gave up on it. The lane log still
streams while the command runs.

### A suite that stops making progress

`timeout_seconds` has to be longer than your slowest honest run, so it cannot cut a suite
that hangs at minute three without also cutting the slow ones. And its default is `0`, no
deadline at all, which is where a hang costs you the whole run: crapkit waits, the suite
sits at 0% CPU, and nothing watches the log.

`no_progress_seconds` is the other half. crapkit polls while the lane runs and kills the
tree when the log has not grown for that many seconds:

```toml
no_progress_seconds = 300
timeout_seconds = 1800
```

```
crapkit: lane 'py' FAILED: lane 'py' wrote no output for 300s (attempt 1), so crapkit killed it; log: /repo/.crapkit/lane-py.log
```

`.crapkit/lane-py.log` ends `[crapkit] no output for 300s; killed`, which is a different
line from the timeout's `[crapkit] timed out after 1800s; killed`: one command ran too
long, the other stopped doing anything. `retries` covers both the same way.

Set it above the longest quiet stretch your runner has. A suite that prints a line per test
stalls in seconds; one that runs a silent build step first needs that step's duration.
Leave it `0` (the default) and the total deadline is the only one.

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
verify OK @ f0070ff1fad vs baseline f0070ff1fad (0 changed files) (1 new failure passed on rerun, first tests.test_net::test_timeout)
```

Three placeholders, filled from the JUnit ids (`classname::name`):

| Placeholder | Fills with | Use for |
|---|---|---|
| `{tests}` | the sorted ids, each quoted, verbatim | runners whose ids are runnable node ids |
| `{files}` | the unique classnames, quoted | **vitest**, whose JUnit classname is the test file path |
| `{names}` | a regex alternation of the test names, `re.escape`d | **pytest**, whose classname is a dotted module and not a path: use it with `-k` |

Rules that keep this from hiding real failures:

- Only lanes that declare `retest_command` retest. Lanes without one keep every failure.
  A test that several lanes failed drops out only when each of those lanes reran it and
  it passed.
- A test only drops out of `new_failures` when the rerun's own results artifact says it
  passed. The lane's report moves aside while the retest runs, so a report at the path
  afterwards is the retest's, even one written inside the old report's time tick. No
  artifact (the lane's report goes back), a crash, or a timeout during the retest keeps
  everything failed.
- The retest never touches the gate or the ratchet. It only shrinks the new-failure set.
- A test that passed its rerun is stored under the lane's `retried_passes`, and the lane's
  `failures` keeps the first attempt. A later verify that measures against this run never
  forgives that test.
- A verify run stored by crapkit 0.7.x has no `retried_passes`: a test that passed its
  rerun sits in its `failures` beside the real ones. A later verify reads that run's
  failures from the newest trusted run behind it that a baseline can forgive from, and says
  so (`baseline run 4 was written by crapkit 0.7.6, which kept a failure that passed its
  flake retry in its failure list`). Reading that list as it stood forgave a real failure of
  the test that once passed its rerun.

---

## Running lanes in parallel

Wall time, and nothing else. The scores come out identical, as long as no two lanes
write the same file.

```toml
[crapkit]
max_parallel_lanes = 2
```

Lanes are subprocesses, so this is a thread pool: it moves wall time only. Above 1, crapkit
starts the lane that took longest last time first (its duration rides along in the artifact
stamp), prints `lane 'x' started` and `finished` to stderr, and folds results back in
**declaration** order regardless of who finished. The run scores byte-identically to a
serial one. A lane with no recorded run, because its artifact was reused or `.crapkit/` was
cleaned, is placed by the time its `results_artifact` JUnit report claims, the figure
`doctor --tune` costs it by. A lane with neither starts after the measured ones.

Every reuse decision is taken up front on one thread, before any lane starts, because a lane
command writes to the working tree and deciding lane by lane would let one lane's output
change the next lane's answer.

Three things to check before raising it:

- Raise it only when the suites are independent. Two lanes sharing a port or a temp
  directory manufacture failures that `verify` reads as a gate breach.
- Runners that size their own worker pool from free memory (vitest does) need a per-lane
  `env` cap, or N lanes each claim the whole box.
- Two `coveragepy` lanes that start in one directory can share coverage.py data there. Two
  lanes left on `.coverage` write one file. A lane on `.coverage` also deletes and combines
  every `.coverage.*` beside it, which takes in a lane on `.coverage.py-impl` and the pieces
  that lane writes while it runs. Run at once, one of them intermittently dies, with
  `sqlite3.OperationalError: table coverage_schema already exists` or, on Windows,
  `PermissionError: [WinError 32]`, and the run comes back partial, exit 5. Give every lane
  its own `COVERAGE_FILE`, none named after another's plus a dot:
  `env = { COVERAGE_FILE = ".coverage.py-conform" }` in one and
  `env = { COVERAGE_FILE = ".coverage.py-impl" }` in the other. `doctor` WARNs about such
  lanes when `max_parallel_lanes` is above 1.

`crapkit doctor --tune` suggests a value from your cpu count and the recorded lane durations,
and estimates the makespan. It writes nothing. While one `coveragepy` lane deletes and
combines another's data files, as happens to the two testpath lanes above once either drops
its `env` line, it holds the suggestion at 1 and names them:

```
max_parallel_lanes = 1
# held at 1: lanes 'py-conform', 'py-impl' write coverage.py data files that one of them deletes and combines, and two of them at once can fail one lane; give each lane its own COVERAGE_FILE, for example env = { COVERAGE_FILE = ".coverage.py-conform" } in lane 'py-conform' and env = { COVERAGE_FILE = ".coverage.py-impl" } in lane 'py-impl', then rerun doctor --tune
```

`[crapkit] analysis_workers` (default `0`, automatic sizing within the CPU limit; see
[configuration.md](configuration.md#crapkit)) caps the lizard pool separately. Set it when the analysis pass runs beside parallel lanes so the two are not both
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

These reports are refused:

| In the junit | What it means |
|---|---|
| an `<error>` naming `worker 'gwN' crashed while running '<nodeid>'` | pytest-xdist lost a worker mid-run |
| an `<error>` outside every `<testcase>` | the runner errored the session itself |
| an `<error message="collection failure">` | pytest failed collection, including xdist runs that exit 1 |
| a `tests` count on `<testsuite>` or `<testsuites>` that differs from its descendant testcase count | the report's declared total does not match what it contains |

Declared counts must be nonnegative decimal integers. Both nested suites and
aggregate wrappers are checked. A report with no declared counts is accepted
when it otherwise contains completed cases. The same admission runs for flake
retests: an incomplete retry cannot forgive a previously failed test.

pytest-xdist 3.8 does not reschedule a crashed worker's queue. On a 15,300-test lane one dead
worker left 4,626 tests unexecuted; coverage.py still wrote its JSON at session end, so the
lane read as a complete suite and a quarter of the scope scored `cov 0`. The untested count,
the CRAP load and the ratchet were all wrong, and the run was written as a coverage baseline.

Now the lane fails, its scopes fall back to `no-lane`, and the run is typed `partial`, which
no baseline reader will take. `verify` refuses to conclude at all.

A clean junit changes nothing, and an ordinary errored test is still just a failed test.

### Under `--reuse-artifacts` it is a warning

The refusal is about a run crapkit watched. `--reuse-artifacts` is you saying run nothing
and read what is on disk, and what is on disk can be a salvage: a coverage JSON combined
by hand out of a killed run's `.coverage.*` shards, with that run's empty or missing junit
still sitting beside it. So there the same admission refusals are one line on stderr, and the
lane scores off the coverage JSON. (The one refusal reuse does keep is the [artifact a
failed attempt left behind](#the-artifact-a-failed-attempt-left-behind-is-refused), and a
salvage is newer than that file by construction.)

```
$ crapkit coverage --reuse-artifacts
crapkit: lane 'py' reused .crapkit/cov/junit-py.xml and cannot check it: junit report contains zero testcases - the suite crashed before collecting, not a pass; the crashed-worker and no-new-failures checks cannot run for this lane
run 11 @ 525a3276065: 5 functions scored: 5 measured, ...
```

The lane records no test counts, which is the same no-counts path a lane with no
`results_artifact` takes. The alternative was deleting `results_artifact` from the config,
which gives up both checks on every future run to get past one.

`verify --reuse-artifacts` does not pass over that warning. Its verdict is the
no-new-failures check, and a lane whose declared junit it could not read checked no test.
So it exits 5 before it stores the run, as a real run over the same file does:

```
$ crapkit verify --reuse-artifacts
crapkit: lane 'py' reused .crapkit/cov/junit-py.xml and cannot check it: results_artifact .crapkit/cov/junit-py.xml is missing; the crashed-worker and no-new-failures checks cannot run for this lane
crapkit: lane 'py' declares results_artifact .crapkit/cov/junit-py.xml, which this verify reused and could not read, so no test in it was checked for a new failure; run verify without --reuse-artifacts so the lane writes it again
EXIT=5
```

That verify used to pass: exit 0, `"ok": true` under `--json`, and the run stored as the
next trusted baseline. The Action never reached it, since its `coverage` step refuses the
same junit first. A lane that declares no `results_artifact` had no report to read, so
verify still passes it and lists it under `lanes_without_results`.

### The test count is the second check

A runner killed from outside (an OOM, a signal) writes no crash into its report at all, and
then the count is the only signature left. `coverage` compares each lane's junit total
against the last trusted run's and warns past a **10%** drop:

```
$ crapkit coverage
crapkit: lane 'py' ran 12 tests, 8 fewer than the last trusted run's 20 - check the runner's log for a worker that died without reporting it
run 2 @ df858be0149: 1 functions scored: 1 measured, 0 over ceiling 6, CRAP load 2.0, grade A+
-> next: crapkit worklist
```

A warning, never a failure: deleting a test file is a legitimate way to get there. `verify`
reports **any** shrink against its own baseline, which is the strict half of the same check.

Both counts are optional and neither absence is an error. A baseline recorded before the
lane declared a `results_artifact` carries no count and compares nothing. A lane that
declares no junit this run gets one line naming the gap. Here the baseline had a junit and
the lane has since stopped declaring one:

```
$ crapkit verify
warning: lane 'py' wrote no test counts this run (no results_artifact was parsed), so the baseline's 1 tests cannot be compared
verify OK @ 437a254ba09 vs baseline 437a254ba09 (2 changed files)
```

Reading that absent count as zero is what used to turn such a run into a KeyError, after
the lane had already run.

`coverage` reads both absences the same way. A lane with no count this run compares
nothing and prints no drop: a lane with no `results_artifact` has nothing to count, and
under `--reuse-artifacts` the reuse warning above already names the missing junit. A
trusted run that counted nothing for a lane is passed over, so a drop is measured from the
newest count a trusted run recorded, even when an older run holds it. The line then names
that run and why:

```
crapkit: lane 'py' ran 12 tests, 8 fewer than run 1's 20 (the last trusted run, run 2, recorded no test count for it) - check the runner's log for a worker that died without reporting it
```

`verify` reaches past its baseline the same way, for the count and for the failure list.
When the baseline recorded neither for a lane, it compares with the newest trusted run at
or behind the baseline's commit that did, and says which:

```
$ crapkit verify
warning: lane 'py': baseline run 2 recorded no test results, so its failures are compared with run 1's
warning: lane 'py' runs 8 fewer tests than run 1 (baseline run 2 recorded no test count for it)
verify OK @ 0e8073a7421 vs baseline 0e8073a7421 (0 changed files) (1 unchanged failure forgiven, first t::c0)
```

Before, that run compared nothing: a suite that fell from 20 tests to 2 passed without a
word, and a test failing at the baseline's own commit came back as a `NEW FAILURE`, exit 8.
When no run recorded a failure list for the lane, its failures still count as new, since a
gate fails closed, and the line says they may predate the change:

```
warning: lane 'py': no trusted run at or behind the baseline recorded which of its tests failed, so its 1 new failure may predate this change; a baseline measured with results_artifact declared tells them apart
```

That is the pull request that adds `results_artifact` to a lane whose suite already fails a
test. `verify --json` lists such lanes under `lanes_without_baseline_results`, and every
lane that declares no `results_artifact` under `lanes_without_results`. A lane with no
`results_artifact` whose command exited nonzero gets its own line, since its exit code is
recorded and not enforced and nothing else says a test failed:

```
warning: lane 'py' exited 1 and declares no results_artifact, so verify cannot see which of its tests failed; declare results_artifact (the lane's junit report) to check them
```

---

## What a failed lane does to scoring

A lane failure is recorded, not fatal. The run still happens:

```
$ crapkit coverage
crapkit: lane 'scripts' FAILED: lane 'scripts' produced no artifact at .crapkit/cov/scripts.json (command exit 1); lane log: /repo/.crapkit/lane-scripts.log
partial run (lane py; lane scripts failed; scripts unmeasured; not a baseline)
run 3 @ 393b8dad2a1: 2 functions scored: 1 measured / 1 no-lane, 1 over ceiling 6, CRAP load 56.83, grade F
  lane 'scripts' FAILED: lane 'scripts' produced no artifact at .crapkit/cov/scripts.json (command exit 1); lane log: /repo/.crapkit/lane-scripts.log
-> rerun changed lanes: crapkit coverage --reuse-unchanged
```

Exit 5. The summary opens by saying the run is partial, counts `over` and the grade over
the measured scopes only (the failed lane's function is `scripts`' debt under
`by_scope`, not this run's grade), and ends with the lanes to rerun. On a tree with
uncommitted changes that last line adds ``(the working tree has uncommitted changes, so
every lane that lists no `inputs` reruns)``, and when git cannot say whether the tree is
clean it adds that instead, with git's error. Four consequences:

1. **The failed lane's scopes fall back to `no-lane`, not `untested`.** The distinction is
   the point: `untested` means a working lane had nothing to say about this function,
   `no-lane` means no measurement was possible. A lane outage must not read as a testing gap.
2. **The run is typed `partial`.** `crapkit runs` shows it, and partial runs are never
   baseline candidates, so a pre-existing failure in a missing lane cannot read as NEW
   forever. `coverage --lane NAME` produces a partial run for the same reason.
3. **`verify` refuses to conclude at all**: `verify cannot conclude with failed lanes:
   scripts`, exit 5. A verdict with a blind lane is not a verdict.
4. **Every lane failing raises** `every lane failed (N of N); the errors are above`, exit 5,
   with no run written. That last line is a count and a pointer: each lane already printed
   its own refusal on the lines above it.

`coverage --json` carries the reasons under `lane_failures`, keyed by lane name, alongside
the successful lanes' provenance under `lanes`.

### The artifact has to be the one this run wrote

A lane passes when its command finishes AND writes every file the lane declares that was
already on disk. crapkit moves those files aside under `.crapkit/aside/` before each
attempt, so a file at the declared path afterwards is one the attempt wrote, whatever its
modification time or bytes. When the attempt writes nothing there, the file goes back and
the lane is refused. A command that reads its previous report finds no file at that path
while it runs. A file that was never there is not part of that check: it is the
missing-artifact refusal crapkit already had, and a `results_artifact` that never
appeared gets its own sentence from the provenance reader.
Existence used to be the whole test, so a lane failed loud exactly once (on the first run,
against an empty `.crapkit/`) and scored the previous run's file on every run after that. A
vitest lane without `reportOnFailure` and a pytest run that dies in collection both land
there, and what came out was a confident grade off a measurement nothing took, stamped with
the current commit so `--reuse-unchanged` went on trusting it.

A leftover artifact says so in its own words, because "produced no artifact at
.crapkit/cov/py.json" about a path that holds a report reads as crapkit failing to see it.
The lane below declares its junit file too, as every lane `init` writes does, so the line
names both files the run left:

```
crapkit: lane 'py' FAILED: lane 'py' wrote no artifact this run - the .crapkit/cov/py.json and .crapkit/cov/junit-py.xml on disk predate it and are the previous run's (command exit 2); lane log: /repo/.crapkit/lane-py.log; last output: ...
```

When the artifact is not on disk at all and the leftover is some other declared file, the
artifact path leads and the leftover follows it: `produced no artifact at
.crapkit/cov/py.json, and the .crapkit/cov/junit.xml on disk is the previous run's`.

A runner that rewrites a byte-identical report wrote it, so an unchanged rerun stays
green. A command that only touches the old report writes nothing: it finds no file there
to touch, and the lane is refused. Until 0.8.1 the check was the modification time, so
such a command passed and the previous run's coverage was scored and stamped with the new
commit. `results_artifact` is held to the same rule, so a killed suite's junit cannot feed
the test-count and no-new-failures checks last run's numbers. `--reuse-artifacts` reads
the same refusal back: the failed attempt stamps the sha256 of the file it left, and
[reuse refuses that file](#the-artifact-a-failed-attempt-left-behind-is-refused) while it
holds those bytes.
Until 0.5.0 reuse was untouched by this rule, and a dead lane's old artifact was one
`--reuse-artifacts` away from being scored.

### The failure message names its own log

Every lane refusal carries `lane log: <path>` before the tail it quotes. The tail is 500
characters cut on line boundaries. The current log and optional `.1` backup retain the
newest output within `log_max_bytes` per file. When the end of the log is a
summary block (pytest closes on `ERROR path` lines that say which files broke and never
why), the message pulls the last few lines that DO name a cause up in front of it, with an
ellipsis marking the output skipped between them:

```
crapkit: lane 'py' FAILED: lane 'py' produced no artifact at .crapkit/cov/py.json (command exit 2); lane log: /repo/.crapkit/lane-py.log; last output: E   ImportError: cannot import name 'Widget' from 'faro.core' (/other/checkout/src/faro/core.py)
...
ERROR tests/test_widgets.py
============================== 10 errors in 0.62s ==============================
(exit 2)
```

A retried lane appends every attempt to that one log, and the hoisted cause is read from
the last attempt alone: the text after the final `--- attempt N ---` banner line. A lane
that died of an `ImportError` and then failed its retry for a different reason used to
report the ImportError over the retry's own output, with nothing marking which attempt
each half came from. The banner counts only as a whole line, so log output quoting those
words mid-line starts no attempt, and attempt 1 writes no banner at all, which makes a
bannerless log one attempt.

The refusal quotes the log with its escape codes removed, and the log file keeps them. A
lane runs with crapkit's environment, so a job that sets `FORCE_COLOR` (any value, `0`
included) or `PY_COLORS=1` gets a coloured pytest log, and on Python 3.14 `PYTHON_COLORS=1`
colours pytest's usage error as well. The cause lines are found and pulled up front
whatever colour pytest wrote, the pytest-cov hint still fires, and the refusal on stderr,
`lane_failures` under `--json`, the Action's pull-request comment and its base-run reason
carry no escape byte. A junit report's error text gets the same treatment: pytest writes
ESC there as the text `#x1B`, and the collection refusal an xdist lane draws drops those
sequences too. Tail the file, or open it in a CI viewer that renders colour, to see the
original.

### A Python child writes its log in UTF-8

crapkit reads `.crapkit/lane-<name>.log` as UTF-8 and quotes its tail in the refusal. A
Python child below 3.15 writes a pipe in the ANSI code page on Windows (cp1252) and in the
locale's encoding on POSIX, and an inherited `PYTHONIOENCODING` overrides both. So crapkit
sets `PYTHONIOENCODING=utf-8` for every lane child, its flake retest and every `mutate`
suite run, unless the lane's `env` sets `PYTHONIOENCODING` itself: then the lane's value
stands. Before 0.8.1 a refusal quoted `No module named 'caf�'` where the child printed
`café`, a test that printed an emoji under `pytest -s` raised `UnicodeEncodeError`
under crapkit alone while the lane still exited 0 and the CRAP load moved, and `mutate`
refused the same suite as failing on the unmutated tree.

The variable sets stdio only. A test that opens a file with no `encoding` still gets the
locale's, and a child that is not Python ignores it.

### When the measurement owner stops

A lane run holds its locks through a helper process, the measurement owner, which also
stops each command's process tree. When that helper stops early the command exits 5 with
one of three lines, `before confirming ownership`, `during command registration` or
`before publication`, and each names the file the helper's stderr went to:

```
crapkit: measurement owner stopped before confirming ownership; its error is at the end of /repo/.crapkit/owner.log
```

The file is `owner.log` in the `.crapkit/` of the checkout whose locks the owner holds,
which is where `coverage`, `verify` and `mutate` write. An owner that holds no lock there,
such as the one behind `test-scoped` or the MCP server, writes `owner.log` beside the
measurement locks in `~/.cache/crapkit/`. Owners append, and each error starts with a
dated line and the owner's process id, so the last entry is this run's. `it wrote nothing
to` in place of `its error is at the end of` means no Python error ended the owner: a
signal did, such as the one the OOM killer sends. When the file cannot be opened, the
line ends at the reason. Before 0.8.1 the owner's stderr went nowhere, so one process on a
Linux host whose name was not UTF-8 stopped every lane run with this line and no cause.

### A killed run leaves its coverage shards behind

`coverage run --parallel-mode`, which pytest-xdist turns on for you, writes one
`.coverage.<host>.<pid>.<random>` per process and combines them only when the run ends. A
run that was killed leaves every measurement it took on disk and no JSON, one directory
above the artifact path the refusal names. So the refusal counts the shards and says where
they are:

```
crapkit: lane 'py' FAILED: lane 'py' produced no artifact at .crapkit/cov/coverage.json (command exit 1); lane log: /repo/.crapkit/lane-py.log; last output: ...; 8 coverage shards (.coverage.box.pid5.aaaa, ...) sit in /repo, which is what a killed parallel run leaves behind: `coverage combine` followed by `coverage json -o .crapkit/cov/coverage.json` there, then a re-run with --reuse-artifacts, scores what that suite did measure
```

The `-o` target is written relative to the shard directory, because that is where the
message tells you to stand: on a lane with a `cwd` it reads `../.crapkit/cov/coverage.json`
rather than the repo-relative `artifact` key, which would have put the JSON one directory
below the path the next run opens. Only a `coveragepy` lane gets the recipe. `coverage
combine` is coverage.py's command, so a jest or vitest lane rooted beside a python one is
never told to run it over the python lane's leftovers. The two commands are named one
after the other rather than chained with `&&`, which Windows PowerShell 5.1 cannot parse.

crapkit does not run the combine for you. Shards from an interrupted suite merge into a
report that looks exactly like a whole run, and taking that for a full measurement is what
the crashed-worker check above refuses. Whether a half-run is worth scoring is your call,
and `--reuse-artifacts` is where you make it.

Shards left under a directory whose name is not UTF-8, or named for a host whose name is
not, come from a run that was not killed: coverage.py stores every measured path as UTF-8
text, so its own combine failed with a `UnicodeEncodeError`, and a `coverage combine` by
hand fails the same way. The refusal names the directory or the host name, with each byte
that is not UTF-8 as `\xNN`, instead of the recipe:

```
crapkit: lane 'py' FAILED: lane 'py' produced no artifact at .crapkit/cov/coverage.json (command exit 1); lane log: /home/ren\xe9/repo/.crapkit/lane-py.log; last output: ...; coverage.py cannot combine the shards it left in /home/ren\xe9/repo: the directory /home/ren\xe9/repo holds bytes that are not UTF-8, and coverage.py stores every path as UTF-8. Rename it to UTF-8 and run the lane again
```

---

## An artifact that measured a different tree

A lane whose artifact names paths that reach **none** of the scopes it claims gets one of
three verdicts. The measured paths decide which, resolved against this checkout's own
root:

| What the artifact reports | Verdict | The knob |
|---|---|---|
| paths outside the root, or climbing out of it (`../...`) | the lane FAILS, exit 5, "describes a different tree" | the environment the lane binds to on a coveragepy lane, the artifact itself on an istanbul one |
| absolute paths that resolve under the root | the lane FAILS, exit 5, this tree spelled absolutely | the runner's own switch: `relative_files` on coveragepy, the reporter's `cwd`/`root` on istanbul |
| in-tree relative paths that miss every scope | a warning on stderr, exit 0, and the run scores on | `path_prefix` when the runner reports from a subdirectory, and nothing at all on a greenfield repo |

The rest of this section takes the rows in that order. Paths outside the root fail the
lane:

```
$ crapkit coverage
crapkit: lane 'py' FAILED: lane 'py' measured 3 file(s), none of them under the paths its scopes declare (src), and 3 of them outside this checkout entirely - .crapkit/cov/py.json describes a different tree, so joining it would score every function in those scopes untested; it reports paths like /other/checkout/src/faro/core.py, /other/checkout/src/faro/util.py, /other/checkout/src/faro/widgets.py. Point the lane at this checkout's own environment (a bare `python -m pytest` binds to whichever venv the shell has active - run it through the project's manager, `uv run python -m pytest ...`), or rerun the lane here rather than reusing a report copied from another checkout; path_prefix only prepends, so it cannot rebase these paths
```

Coverage joins on path and nothing else, so such an artifact contributes exactly nothing
and every function in those scopes reads `untested`. That is a grade assembled out of a
tooling mistake, and it is worse than the exit 5 a missing artifact already earns, because
it looks like an answer. The refusal is an ordinary lane failure: the scopes fall back to
`no-lane`, and the four consequences above apply unchanged.

Both parsers rebase a file inside the repo to a repo-relative path, so a measured path
that stayed absolute, drive-lettered (`C:/...`) or climbing (`../...`) was not written
relative to this checkout. Whether it names a file somewhere else is a second question,
and crapkit answers it by resolving the path against the root.

A stale artifact copied in, or the wrong environment, is what puts another tree's paths
there: [The interpreter a lane binds to](#the-interpreter-a-lane-binds-to) is the section
that fixes it. An istanbul lane gets the artifact named instead of the environment,
because its reader rebases every path under this checkout's root, so a path that stayed
outside it was written somewhere else.

### The same tree, spelled absolutely

An absolute path that resolves **under** this root is this checkout, measured by a runner
that was told to report absolute paths. The join is root-relative, so it matches nothing
either and the lane fails the same way, but the environment is right, and `path_prefix`
only ever prepends, so neither sentence above is the fix. This refusal names the runner's
own switch instead:

```
$ crapkit coverage
crapkit: lane 'py' FAILED: lane 'py' measured 2 file(s), none of them under the paths its scopes declare (src), and 2 of them written as absolute paths that DO sit under this checkout - .crapkit/cov/py.json measured this tree and spelled it absolutely, and the join is on root-relative paths, so it still matches nothing and every function in those scopes would score untested; it reports paths like /repo/src/faro/core.py, /repo/src/faro/util.py. Make the runner write relative paths: `relative_files = true` under `[tool.coverage.run]` in pyproject.toml, or `[run] relative_files = true` in .coveragerc, then rerun the lane
```

An istanbul lane does not reach this refusal. Its reporter writes every path absolute, and
its reader rebases each one that resolves under this checkout, whatever spelling it
arrives in: a lower-case drive letter from a shell that stood in `c:\...`, another letter
case, a junction or symlink to the checkout, or the `\\?\` prefix. Before 0.8.1 the reader
stripped the root as literal text, so each of those failed the lane with advice to point
the reporter at the checkout it had already measured.

For a coveragepy lane crapkit does not rebase these itself. The join contract stays
root-relative, and a runner spelling every path absolutely is one setting to fix once, not
a shape to re-derive on every run.

Both sides of that comparison are resolved the same way, symlinks followed and the case
folded where the filesystem folds it, so a checkout reached through a symlink or spelled
with a different drive-letter case is still recognized as itself.

Two shapes stay in the first verdict whatever else the artifact holds. A `../` climb is
relative to the runner's working directory, which no artifact records, so there is nothing
to resolve it against. And a mixed artifact, some paths under the root and some outside
it, is another tree: a path from somewhere else can only have come from somewhere else,
and the count in that message names the outside ones alone.

### In-tree paths that miss every scope

Relative paths that reach none of the declared scopes warn instead, and the run
scores on:

```
crapkit: lane 'py' measured 1 file(s), none of them under the paths its scopes declare (src), so every function in those scopes will score untested; it measured tests/test_core.py - either nothing in them is exercised yet, or the runner reports paths this lane needs path_prefix to rebase
```

That is the greenfield shape as well: a suite importing none of the scoped source yet,
which should score `untested`. Refusing it would exit 5 on exactly the repos adopting
crapkit, so it stays a warning.

Zero overlap is the whole test. A partial overlap has honest readings, a lane measuring
part of a scope or generated files outside it, and any threshold over zero would need
tuning per repo; zero has no reading under which the join was going to work.

The two tests run on different spellings of the path, which is what keeps
[`path_prefix`](#running-from-a-subdirectory) out of the other two verdicts. The reach test
runs after the prefix is applied, so a prefix that already fixes the join is never
mentioned at all, and it asks only the keys the runner wrote relative to this checkout.
The escape test runs on the path the runner wrote, with the prefix taken back off. Gluing
`backend/` onto `/other/checkout/a.py` gives `backend//other/checkout/a.py`, a path under
a `backend` scope, and a root scope (`.`) claims any key. Before 0.8.1 the reach test asked
such keys, so a lane with `path_prefix`, or scoped to the root, scored every function
untested with exit 0 on another tree's report; it now gets the refusal above.
`path_prefix` is a coveragepy key, and the istanbul reader never reads it.

The reach test is `universe.owning_scope` over the lane's own scopes, the same predicate
that assigns files to scopes, so a scope declaring individual files rather than
directories is reached exactly and never reads as unmeasured.
