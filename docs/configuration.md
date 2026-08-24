# crapkit.toml

One file at the repo root. `crapkit init` writes a working starter; this page is the whole
key list. [crapkit.schema.json](../crapkit.schema.json) is the machine authority, and
`crapkit doctor` rejects any key not on it:

```
$ crapkit doctor
FAIL unknown key crapkit.churn_windo_months — crapkit ignores it (typo?); [crapkit] accepts these keys: alert_command, analysis_workers, churn_window_months, debt_max_age_months, diff_uncovered_max, max_parallel_lanes, mutation_command, mutation_timeout_seconds, mutation_workers, notes, ratchet_file, repayment_min_per_30d, scoped_tests, target, worklist_floor, worklist_top
doctor: 1 problem(s)
```

The loader stays lenient so a config survives version skew: an unknown key is ignored at
load time and only `doctor` fails on it. Every rejection the loader *does* make (a bad
language, an unknown parser, a lane naming an undeclared scope, a negative
`timeout_seconds`) exits 3.

Four tables: `[crapkit]`, `[[scope]]`, `[[lane]]`, `[exclude]`.

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
| `mutation_command` | string | `""` | The suite run once per mutant. A nonzero exit means the mutant was killed. `mutate` refuses to run without it. |
| `mutation_timeout_seconds` | int >= 1 | `300` | Per-mutant timeout. Expiry counts as killed. At the default cap of 100 mutants this bounds one `mutate` run at over 8 hours, so lower it for a slow suite. |
| `mutation_workers` | int >= 1 | `1` | Mutants run at once. `1` applies them to the live working tree one at a time. Above 1, crapkit adds that many detached git worktrees, deals mutants round-robin, and removes every worktree on the way out, raise included. |
| `diff_uncovered_max` | int >= 0 | absent | Ceiling on changed lines that never ran. **Absent means warn only**: `verify` still prints `warning: N changed line(s) have no coverage` on stderr and lists the first 20, but exits 0. Set it and a breach exits 9. |
| `debt_max_age_months` | int >= 0 | absent | `ratchet report --enforce` flags open marks older than this (counted at 30 days per month). |
| `repayment_min_per_30d` | int >= 0 | absent | `ratchet report --enforce` flags a burn-down that repaid fewer marks than this in the last 30 days while debt is open. |
| `max_parallel_lanes` | int >= 1 | `1` | Lanes running at once. `1` is strictly serial. See [lanes.md](lanes.md#running-lanes-in-parallel). |
| `analysis_workers` | int >= 0 | `0` | The lizard process pool size. `0` means one worker per core. Set it when the analysis pass runs beside something else. |

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
| `paths` | array of string, min 1 | yes | | Repo-relative path prefixes the scope claims. A bare path also matches that exact file. |
| `languages` | array, min 1 | yes | | One or more of `typescript`, `tsx`, `javascript`, `python`, `swift`. A file joins the scope only when both its path prefix and its extension match. |
| `target` | int >= 1 | no | the repo `target` | This scope's ceiling. One repo, different ceilings: strict on new code, tolerant on a legacy tree. |
| `coverage_optional` | bool | no | `false` | Code no test can reach. See below. |
| `notes` | array of string | no | `[]` | House rules for this scope alone. They follow the repo-wide `[crapkit] notes` into every packet `brief` builds for a function this scope owns. |

Extensions per language:

| Language | Extensions |
|---|---|
| `typescript` | `.ts` |
| `tsx` | `.tsx` |
| `javascript` | `.js`, `.jsx`, `.mjs`, `.cjs` |
| `python` | `.py` |
| `swift` | `.swift` |

Scopes are tried in declaration order; the first whose path prefix **and** extension both
match wins. A prefix-only match does not stop the search, so two scopes sharing a prefix but
declaring different languages do not black-hole each other's files.

### `coverage_optional = true`

For production-only scripts, generated shims, entry points: code no test can reach. It
changes four things:

- Functions in the scope are flagged `cc-only` and scored `crap = ccn` instead of running
  through the formula. Feeding them `cov = 0` would say `add-tests` about code no test can
  reach.
- `remedy` can then only be `ok` or `decompose`.
- The scope needs no lane. Without this key, a lane-less scope is a `doctor` FAIL.
- The scope is skipped by doctor's unmeasured-directory check.

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
| `command` | string | yes | | Run through the shell, cwd at the repo root unless `cwd` says otherwise. Its exit code is recorded, not enforced: a suite with known failures still writes a valid artifact. |
| `artifact` | string | yes | | Repo-relative path to the coverage file the command writes. Its absence after the command (and its retries) is the failure. Two lanes may not share an artifact path. Point it under `.crapkit/cov/`, which `init` already gitignores; `doctor` warns about a lane writing at the repo root. See [Where artifacts live](lanes.md#where-artifacts-live). |
| `parser` | `istanbul` \| `coveragepy` | yes | | How to read the artifact. |
| `scopes` | array of string | yes | | Which scopes this lane's coverage speaks for. A scope in no lane's list can only score `no-lane`. |
| `cwd` | string | no | repo root | Repo-relative working directory for the command. `doctor` fails when it does not exist. |
| `path_prefix` | string | no | `""` | Prefix joined onto coverage.py's relative paths, for a suite run from a subdirectory. |
| `env` | table of string | no | `{}` | Extra environment for the command, merged over the inherited environment. Use it to cap a runner that sizes its own worker pool from free memory. |
| `full_suite` | bool | no | `true` | `false` permits a positional argument in a pytest coverage command. At `true`, a positional is a config error: subset coverage under a suite with cross-file pollution is run-order dependent. Set it false deliberately for a genuinely scoped suite. |
| `container_ok` | bool | no | `false` | Lets a `coveragepy` lane run inside a container. Without it such a lane refuses with exit 5 whenever `/.dockerenv` exists or `CRAPKIT_INSIDE_CONTAINER=1`. |
| `results_artifact` | string | no | `""` | A JUnit XML report, under `.crapkit/cov/` for the same reason as `artifact`. It feeds the no-new-failures check (exit 8) and the suite-shrink warning. Declared but missing is exit 5, so the check can never pass vacuously. |
| `timeout_seconds` | int >= 0 | no | `0` | crapkit kills the command past this. `0` means no crapkit-owned timeout. |
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
globs = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**", "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py", "*.config.ts", "*.config.js", "*.config.mts", "**/*.config.ts", "**/*.config.js", "**/*.config.mts"]
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
command = "npm run test -- --coverage --coverage.reportsDirectory=../.crapkit/cov/js"
artifact = ".crapkit/cov/js/coverage-final.json"
parser = "istanbul"
scopes = ["web"]
cwd = "web"
env = { NODE_OPTIONS = "--max-old-space-size=4096" }

[exclude]
globs = ["**/node_modules/**", "**/dist/**", "**/*.generated.ts"]
max_file_bytes = 400000
```
