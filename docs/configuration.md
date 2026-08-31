# crapkit.toml

One file at the repo root. `crapkit init` writes a working starter; this page is the whole
key list.

Five dials carry almost every decision you will make. Everything else has a default you can
leave alone:

| Dial | Key | Decides |
|---|---|---|
| the ceiling | `[crapkit] target` | what counts as too complex, repo-wide or per scope |
| the floor | `[crapkit] worklist_floor` | how small a function has to be before the queue ignores it |
| what is scored at all | `[exclude] globs` | which files leave the corpus for good |
| what is measured | `[[lane]]` | which command produces coverage, and for which scopes |
| what an agent runs | `[crapkit.scoped_tests]` | the test command a packet hands back at step 4 of the loop |

Four tables hold them: `[crapkit]`, `[[scope]]`, `[[lane]]`, `[exclude]`.

[crapkit.schema.json](../crapkit.schema.json) is the machine authority, and
`crapkit doctor` rejects any key not on it:

```
$ crapkit doctor
FAIL unknown key crapkit.churn_windo_months — crapkit ignores it (typo?); [crapkit] accepts these keys: alert_command, analysis_workers, churn_window_months, debt_max_age_months, diff_uncovered_max, max_parallel_lanes, mutation_command, mutation_timeout_seconds, mutation_workers, notes, ratchet_file, repayment_min_per_30d, scoped_tests, target, tighten_max_jump, worklist_floor, worklist_top
doctor: 1 problem(s)
```

The loader stays lenient so a config survives version skew: it ignores an unknown key at load
time, and only `doctor` fails on it. Every rejection the loader *does* make (a bad language,
an unknown parser, a lane naming an undeclared scope, a negative `timeout_seconds`) exits 3.

---

## `[crapkit]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `target` | int >= 1 | `6` | The repo-wide CRAP ceiling. A `[[scope]]` may override it. Drives the gate, the `remedy` column, the ratchet drop threshold and every over-target count. |
| `churn_window_months` | int >= 1 | `12` | How far back `git log` is read for churn weighting, coupling and the worklist rank. |
| `worklist_floor` | int >= 1 | `5` | Minimum ccn for queue admission, in `worklist` and `next-item` alike. Printed in the worklist header as `floor ccn>=5`. It has no CLI flag. Two rules reach under it: files whose churn weight is in the top 10% are promoted down to ccn 3, and a function scoring over its ceiling is admitted whatever its ccn. |
| `worklist_top` | int >= 1 | `50` | Cap on the worklist active list. `worklist --top N` overrides it per call. |
| `ratchet_file` | string | `"crapkit-ratchet.tsv"` | The committed marks file, repo-relative. |
| `alert_command` | string | `""` | A shell command that receives a digest or override body on **stdin**. `digest --alert` uses it, and an override refuses to grant without it. Never interpolated into the shell string. |
| `scoped_tests` | table | none | Written as its own table, `[crapkit.scoped_tests]`, mapping scope name to a command template. `test-scoped` fills `{files}` with the quoted file list; a template with no `{files}` runs as written, which is how a scope runs its whole suite when its tests live outside its own `paths`. It is also step 4 of the burn-down loop and `brief`'s `commands.scoped_tests`, so **doctor warns** about a scope a lane measures with no template behind it: `null` there leaves a session with nothing to run between the gate and verify. |
| `notes` | array of string | `[]` | House rules for this repo, in the config rather than in a file an agent has to find. `brief` carries them into the packet as `notes`, repo-wide lines first, then the scope's own. crapkit never parses them. |
| `mutation_command` | string | `""` | The suite run once per mutant. A nonzero exit means the mutant was killed. `mutate` refuses to run without it. Shell and PowerShell files in the diff are skipped and named on stderr: `<` and `>` are redirections in both, so their mutants would be noise. |
| `mutation_timeout_seconds` | int >= 1 | `300` | Per-mutant timeout. Expiry counts as killed, and the kill takes the suite's whole process tree, so a looping mutant does not outlive the run that gave up on it. At the default cap of 100 mutants this bounds one `mutate` run at over 8 hours, so lower it for a slow suite. |
| `mutation_workers` | int >= 1 | `1` | Mutants run at once. `1` applies them to the live working tree one at a time and keeps no pool. Above 1, crapkit runs each worker in its own detached git worktree and deals mutants round-robin. Those worktrees are **kept**, at `.crapkit/mutate-pool/w0..wN`: building four of them costs 30.6 s on a 31,459-file repo and re-preparing the kept four costs 0.46 s, and every run re-prepares them (`git checkout --force <HEAD sha>`, then `git clean -xdff`) before a mutant is applied, so the last run's mutant goes back and a commit made since lands. What stays on disk is that many checkouts of your repo, and nothing bounds its size; `crapkit mutate --drop-pool` removes them and exits. A second `mutate` running in the same repo finds the pool locked and gets its own throwaway worktrees, removed on the way out as before. Results merge by the mutant's position in the list, so the same tree reports the same JSON at any worker count. |
| `diff_uncovered_max` | int >= 0 | absent | Ceiling on changed lines that never ran. **Absent means warn only**: `verify` still prints `warning: N changed line(s) have no coverage` on stderr and lists the first 20, but exits 0. Set it and a breach exits 9. |
| `debt_max_age_months` | int >= 0 | absent | `ratchet report --enforce` flags open marks older than this (counted at 30 days per month). |
| `repayment_min_per_30d` | int >= 0 | absent | `ratchet report --enforce` flags a burn-down that repaid fewer marks than this in the last 30 days while debt is open. |
| `max_parallel_lanes` | int >= 1 | `1` | Lanes running at once. `1` is strictly serial. See [lanes.md](lanes.md#running-lanes-in-parallel). |
| `analysis_workers` | int >= 0 | `0` | The lizard process pool size. `0` means one worker per core. Set it when the analysis pass runs beside something else. |
| `tighten_max_jump` | number >= 1 | `2.0` | How far a function's CRAP may move between two runs of the **same commit** and still tighten its mark. Past this factor, `verify` holds the mark and prints one `NO TIGHTEN` line on stderr naming the function and both values. One commit measured twice cannot have improved, so a jump that size is the measurement talking, not the code. See [ratchet.md](ratchet.md#damping-a-measurement-that-bounces). |

### The debt policy

`--enforce` is inert unless at least one of `debt_max_age_months` and
`repayment_min_per_30d` is set. With neither, `ratchet report --json` reports
`"policy_violations": null` and the flag can never fire. See
[ratchet.md](ratchet.md#the-debt-policy).

---

## `[[scope]]`

An array of tables. One scope per group of directories that share a ceiling and a set of
languages. Every tracked source file in a declared language must belong to a scope, or
`doctor` fails with `N tracked file(s) match a scope language but no scope path`.

| Key | Type | Required | Default | What it does |
|---|---|---|---|---|
| `name` | string | yes | | The scope's id. Lanes reference it, `--scope` filters on it (exact, not substring). |
| `paths` | array of string, min 1 | yes | | Repo-relative path prefixes the scope claims. A bare path also matches that exact file, so `paths = ["core/hot.py"]` claims one file and editing it marks that scope's lane changed. The **deepest** declared path wins when two claim the same file; see [Scope matching](#scope-matching). |
| `languages` | array, min 1 | yes | | One or more of `typescript`, `tsx`, `javascript`, `python`, `swift`, `go`, `rust`, `shell`, `powershell`, `cpp`, `objectivec`, `vue`, `java`, `zig`. A file joins the scope only when both its path prefix and its extension match. |
| `target` | int >= 1 | no | the repo `target` | This scope's ceiling. One repo, different ceilings: strict on new code, tolerant on a legacy tree. |
| `coverage_optional` | bool | no | `false` | Code no test can reach, or code no parser can measure. See below. |
| `notes` | array of string | no | `[]` | House rules for this scope alone. They follow the repo-wide `[crapkit] notes` into every packet `brief` builds for a function this scope owns. |

Fourteen languages, two coverage parsers. A lane's `parser` is `coveragepy` or `istanbul` and
nothing else, so a lane can measure a scope only when its runner writes coverage.py's JSON
report or istanbul's `coverage-final.json`. In practice that means Python and the
JavaScript/TypeScript family. A scope whose runner writes neither scores complexity alone and
declares `coverage_optional = true`. See
[lanes.md](lanes.md#which-languages-a-lane-can-measure).

Extensions per language:

| Language | Extensions |
|---|---|
| `typescript` | `.ts` |
| `tsx` | `.tsx` |
| `javascript` | `.js`, `.jsx`, `.mjs`, `.cjs` |
| `python` | `.py` |
| `swift` | `.swift` |
| `go` | `.go` |
| `rust` | `.rs` |
| `shell` | `.sh`, `.bash` |
| `powershell` | `.ps1`, `.psm1` |
| `cpp` | `.c`, `.cc`, `.cpp`, `.cxx`, `.h`, `.hpp` |
| `objectivec` | `.m`, `.mm` |
| `vue` | `.vue` |
| `java` | `.java` |
| `zig` | `.zig` |

### Per-language gotchas

Read the ones you are about to point a scope at. Skip the rest.

**`shell` and `powershell` report functions only.** Statements outside any function land in
lizard's `*global*` pseudo-function, exactly like Python module-level code. A script that is
one long top-level sequence reports nothing. That is the answer, not a parse failure.

**`powershell` counts one point per `switch` arm**, the way `case` is counted in C, and
`default` is free. Its keywords are matched case-sensitively as written, so `If (` in code
counts nothing.

**Pester test files need a glob of your own.** Pester names them `Foo.Tests.ps1`, beside the
source they test, and no default exclude claims that spelling. `**/*.test.*` does not match
`.Tests.ps1`, and crapkit will not invent a glob that deletes production files from repos
naming scripts that way. One line does it:

```toml
[exclude]
globs = ["**/*.Tests.ps1"]
```

**`cpp` is the whole C family, C included.** There is no separate `c` label. lizard resolves
all six suffixes to one reader, so two labels could never measure differently, and `.h` is
the header both dialects share. `.hh`, `.hxx` and `.ipp` are not claimed: no lizard reader
declares them, and lizard serves an undeclared suffix from a silent fallback rather than from
a mapping.

Two more things before you point a scope at C code:

- Both arms of an `#ifdef` fork are textually present, so a platform shim defines the same
  function twice in one file. Each arm takes its own ratchet key — the first as written,
  later ones suffixed `#2`, `#3` in file order — so both are marked and both are gated.
  `analyze` prints one stderr line naming any file this happens in. See
  [Twins](ratchet.md#twins-one-name-several-functions).
- A `&&` before a function's opening brace declares an rvalue reference, not a decision, and
  costs no cognitive complexity. The rule covers `objectivec` too, because `.mm` is
  Objective-C++ and carries C++ move semantics. A `&&` inside a default argument is the one
  case it reads wrong; lizard's cyclomatic column drops the whole parameter list anyway, so
  the two columns agree about it.

**`vue` scores the `<script>` block.** Template directives (`v-if`, `v-else-if`, `v-for`) are
HTML attributes to lizard and contribute nothing, so a component whose branching lives in its
template reports only what its methods do.

**`zig` reads one point high on `switch`.** It counts the `else` prong as one more case. The
error is inflation only: it can cost a refactor that was not needed, never hide one that was.

**`rust`, `shell` and `powershell` run on crapkit's own readers.** lizard has neither a shell
nor a PowerShell reader, and its Rust reader scores a 7-arm `match` as ccn 2 (filed upstream
as lizard #494), so crapkit counts each non-wildcard arm the way C counts a `case`. The Rust
module retires itself the day upstream fixes it.

The cognitive column charges a Rust `match` like a `switch`: +1 plus the nesting it sits
in, arms free. The two columns therefore say different things about one block on purpose.
The 7-arm match above is ccn 7 and cognitive 1: seven ways through it, one decision to
read. The rule is Rust's alone, because `match` is a soft keyword in Python and an
ordinary identifier anywhere else.

### Scope matching

**The deepest declared path wins.** Paths are tried longest first, and the first whose path
prefix **and** extension both match owns the file. Declaration order breaks a tie between
two paths of the same length and decides nothing else. A prefix-only match does not stop the
search, so two scopes sharing a prefix but declaring different languages do not black-hole
each other's files.

Depth is a property of the path, not the scope, so a scope declaring both `src` and
`packages/web/src` claims a file under each at its own depth.

Declaring `src` first and `src/hot` second, `src/hot` still gets its file:

```
$ crapkit doctor
ok   config keys all recognized
ok   scope 'src': 1 files
ok   scope 'hot': 1 files
ok   every tracked source file belongs to a scope
```

One predicate answers this for everyone since 0.4.5: the scored corpus, `test-scoped`
routing, lane reuse and the `brief` packet. They used to answer separately, so `brief` could
take a function's lane and test command from one scope and its ceiling from another.

**If your repo has nested scopes, its next scan may move files between them.** Whichever
scope declares the longer path now owns those files, so their per-scope rollups, ceilings
and lane change. A repo with no nested scopes sees no change at all. Run `crapkit doctor`
after upgrading and read the per-scope file counts.

### `coverage_optional = true`

Two reasons to set it. Either no test can reach the code (entry points, deploy scripts,
generated shims), or nothing produces a coverage artifact crapkit reads for it (Go, Rust,
Swift, C and the rest).

`crapkit init` writes the key for the second reason on its own: a scope whose languages
all lack a parser gets it, a scope holding one language a parser reads does not. The first
reason is yours to declare, because only you know a test cannot reach the code.

Four effects:

- crapkit flags every function in the scope `cc-only` and scores it `crap = ccn`, skipping
  the formula. Feeding them `cov = 0` would say `add-tests` about code no test can reach.
- `remedy` narrows to `ok` or `decompose`.
- The scope needs no lane. Without this key, a lane-less scope is a `doctor` FAIL.
- doctor's unmeasured-directory check skips the scope.

```
$ crapkit rescore scripts/deploy.py --json
{"functions": [{"ccn": 5, "cov": 0.0, "crap": 5.0, "flag": "cc-only", "remedy": "ok", ...}], ...}
```

---

## `[[lane]]`

An array of tables. One lane per coverage command. Full recipes in [lanes.md](lanes.md).

| Key | Type | Required | Default | What it does |
|---|---|---|---|---|
| `name` | string | yes | | The lane's id. Names its log at `.crapkit/lane-<name>.log` and `coverage --lane`. |
| `command` | string | yes | | Run through the shell, cwd at the repo root unless `cwd` says otherwise. Its exit code is recorded, not enforced: a suite with known failures still writes a valid artifact. crapkit reads it with the shell that will run it, sh on POSIX and cmd.exe on Windows, and so does `doctor`. That reading covers quoting, carets, `&&` segments, redirections and `;`: [How a lane command is read](lanes.md#how-a-lane-command-is-read). |
| `artifact` | string | yes | | Repo-relative path to the coverage file the command writes. Its absence after the command (and its retries) is the failure. Two lanes may not share an artifact path. Point it under `.crapkit/cov/`, which `init` already gitignores; `doctor` warns about a lane writing at the repo root. See [Where artifacts live](lanes.md#where-artifacts-live). |
| `parser` | `istanbul` \| `coveragepy` | yes | | How to read the artifact. |
| `scopes` | array of string | yes | | Which scopes this lane's coverage speaks for. A scope in no lane's list can only score `no-lane`. |
| `cwd` | string | no | repo root | Repo-relative working directory for the command. `doctor` fails when it does not exist. |
| `path_prefix` | string | no | `""` | Prefix joined onto coverage.py's relative paths, for a suite run from a subdirectory. |
| `env` | table of string | no | `{}` | Extra environment for the command, merged over the inherited environment. Use it to cap a runner that sizes its own worker pool from free memory, and to hand a junit reporter its output path when the reporter reads no path off the command line (`jest-junit` is one). Every lane gets it, so raising `max_parallel_lanes` without one lets N lanes each claim the whole box. |
| `full_suite` | bool | no | `true` | `false` permits a positional argument in a pytest coverage command. At `true`, a positional is a config error: subset coverage under a suite with cross-file pollution is run-order dependent. A flag's value is not a positional (`-n 8`, `-o timeout=300`, `-p no:randomly` all pass), and the command is read by the shell that will run it, one argv per `&&`, `\|\|`, `&` or `\|` segment, with every segment that runs pytest checked. On cmd.exe a `;` starts nothing, so `pytest --cov; echo done` hands pytest `echo` and is refused; write the second command after `&&`. Use double quotes for values, since cmd.exe does not treat `'` as a quote. Set it false deliberately for a genuinely scoped suite. |
| `container_ok` | bool | no | `false` | Lets a `coveragepy` lane run inside a container. Without it such a lane refuses with exit 5 whenever `/.dockerenv` exists or `CRAPKIT_INSIDE_CONTAINER=1`. |
| `results_artifact` | string | no | `""` | A JUnit XML report, under `.crapkit/cov/` for the same reason as `artifact`. Two checks read it and neither runs without it: no-new-failures (`verify` exit 8) and the crashed-worker trust check, plus the suite-shrink warning. `doctor` WARNs on a `coveragepy` or `istanbul` lane that declares none, naming both, and `crapkit init` writes it on the lanes it detects. Declared but missing is exit 5, so the check can never pass vacuously, and so is a report saying the run never finished ([a crashed xdist worker or a session error](lanes.md#a-junit-that-says-the-run-did-not-finish)). |
| `timeout_seconds` | int >= 0 | no | `0` | crapkit kills the command past this. The kill takes the whole process tree, not just the shell, and crapkit waits for it, so no orphan suite keeps running after the lane fails. `0` means no crapkit-owned timeout. |
| `retries` | int >= 0 | no | `0` | Reruns after a timeout or a missing artifact. Each attempt appends to the lane log under an `--- attempt N ---` header. |
| `retest_command` | string | no | `""` | Rerun template for newly failed tests, before exit 8 is decided. See [lanes.md](lanes.md#flake-retest). |

---

## `[exclude]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `globs` | array of string | `[]` | Paths matching any glob leave the corpus. Each glob must match the **whole** repo-relative path, case-insensitively. |
| `max_file_bytes` | int >= 0 | absent (no limit) | Files larger than this leave the corpus entirely, minified blobs included. `doctor` reports each one as a `note`, never a FAIL, and the count surfaces as `skipped_max_bytes` in `inventory --json` and `coverage --json`. |

Test directories are excluded **unconditionally**, before `globs` is consulted: any path
component matching `test`, `tests` or `__tests__`, case-insensitively. You do not need a
glob for them, and you cannot glob your way back in.

Globs are whole-path, so `**/dist/**` requires at least one directory *before* `dist`. It
matches `web/dist/bundle.js` and not a repo-root `dist/bundle.js`. That matters because
`init` applies the same rule, so a tracked build directory at the repo root becomes a scope:

```
$ crapkit init
wrote crapkit.toml with 2 scope(s): dist, src
```

Add the unanchored form beside it, then delete the scope `init` wrote (an emptied scope is a
`doctor` FAIL):

```toml
globs = ["**/dist/**", "dist/**"]
```

`crapkit init` writes this default set:

```toml
[exclude]
globs = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**", "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py", "**/*_test.go", "*.config.ts", "*.config.js", "*.config.mts", "**/*.config.ts", "**/*.config.js", "**/*.config.mts"]
```

The six `*.config.*` entries keep runner config files (`vitest.config.ts` and friends) out
of the unclaimed-file doctor check; globs are whole-path, so the bare form matches the repo
root and the `**/` form matches nested copies.

```
$ crapkit doctor
note app/core.py (181 bytes) skipped: over max_file_bytes
```

---

## Tuning the parallelism knobs

`doctor --tune` reads this machine's cpu count and whatever lane durations are already
recorded in `.crapkit/artifacts.json`, and prints paste-ready lines. It writes nothing and
runs nothing:

```
$ crapkit doctor --tune
# doctor --tune: suggestions for 24 cpu(s); nothing was written
[crapkit]
max_parallel_lanes = 1
analysis_workers = 23
mutation_workers = 6
# lane cost: 1.2s serial -> ~1.2s across 1 lane slot(s)
```

The cost line needs at least one recorded lane duration. With none it says so and the
suggestion comes from the cpu count alone. The suggestion never proposes more lane slots
than there are lanes.

---

## A worked config

```toml
[crapkit]
target = 6
churn_window_months = 12
notes = ["parse errors surface at the CLI boundary, never below it"]
diff_uncovered_max = 0
debt_max_age_months = 6
repayment_min_per_30d = 1
max_parallel_lanes = 2
mutation_command = "python -m pytest -q -x"

[crapkit.scoped_tests]
api = "python -m pytest tests/api -q {files}"
web = "npx vitest run {files}"

[[scope]]
name = "api"
paths = ["api"]
languages = ["python"]

[[scope]]
name = "web"
paths = ["web/src"]
languages = ["typescript", "tsx"]

[[scope]]
name = "legacy"
paths = ["legacy"]
languages = ["python"]
target = 12
notes = ["no new files here: extract into api/ instead"]

[[scope]]
name = "bin"
paths = ["bin"]
languages = ["python"]
coverage_optional = true

[[lane]]
name = "api"
command = "python -m pytest api --cov=api --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-api.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit-api.xml"
parser = "coveragepy"
scopes = ["api", "legacy"]
full_suite = false
timeout_seconds = 1800
retries = 1
retest_command = "python -m pytest --junitxml=.crapkit/cov/junit-api.xml -q -k \"{names}\""

[[lane]]
name = "web"
command = "npm run test -- --coverage --coverage.reportsDirectory=../.crapkit/cov/js --reporter=default --reporter=junit --outputFile=../.crapkit/cov/js/junit.xml"
artifact = ".crapkit/cov/js/coverage-final.json"
results_artifact = ".crapkit/cov/js/junit.xml"
parser = "istanbul"
scopes = ["web"]
cwd = "web"
env = { NODE_OPTIONS = "--max-old-space-size=4096" }

[exclude]
globs = ["**/node_modules/**", "**/dist/**", "**/*.generated.ts"]
max_file_bytes = 400000
```
