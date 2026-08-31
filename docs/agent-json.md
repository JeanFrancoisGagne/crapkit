# The machine surface

Every read-side command has a JSON form. This page is the field contract.

## The `schema` field

Every `--json` payload carries `schema`, currently `1`.

```json
{"...": "...", "schema": 1}
```

| Change | Bumps `schema`? |
|---|---|
| A field is added | No |
| A field is removed | Yes |
| A field changes type | Yes |
| A field's meaning changes | Yes |

Parse defensively for additions and pin on `schema` for the rest. A payload whose `schema`
is higher than the one you were written against may have dropped or retyped something you
read.

Two more house rules that hold across every payload:

- **Keys are sorted** and there are no timestamps in scored rows, so the same tree and the
  same artifacts produce byte-identical JSON.
- **stdout carries exactly one JSON object.** Warnings, progress, lane chatter and gate
  findings all go to stderr, so `crapkit ... --json 2>/dev/null` is always parseable.

`next-item` is the exception to the flag: it has no `--json` because it only ever emits JSON.

---

## `next-item`

The top of the burn-down queue: the rows a lane measures, whose remedy is not `ok`,
ranked by **`crap` descending**. That ordering is the difference from
[`worklist`](#worklist), which ranks the same run by risk and lists rows this command
never offers, so the two do not lead with the same function.

```
$ crapkit next-item
```

```json
{
  "commit": "8c14f3daa8e88230c5b702d8f452ee2616d4de30",
  "empty": false,
  "item": {
    "authors": 1,
    "ccn": 14,
    "ccn_std": 14,
    "cognitive": 14,
    "commits": 2,
    "cov": 0.45,
    "crap": 46.60950000000001,
    "end": 27,
    "est_splits": 3,
    "est_uncovered_paths": 8,
    "flag": "measured",
    "function": "classify( score , attempts , late , bonus )",
    "handle": "classify",
    "nesting": 6,
    "nloc": 24,
    "path": "calc/grade.py",
    "remedy": "decompose",
    "scope": "calc",
    "start": 4,
    "target": 6,
    "uncovered_lines": [8, 10, 12, 14, 17, 18, 19, 20, 22, 24, 26]
  },
  "run_id": 1,
  "schema": 1,
  "skipped_no_lane": 0,
  "stale": false
}
```

### Envelope

| Key | Type | Always | Meaning |
|---|---|---|---|
| `run_id` | int | yes | The scored run these numbers come from. |
| `commit` | string | yes | That run's commit, full sha. |
| `empty` | bool | yes | Whether there is work to hand out. |
| `skipped_no_lane` | int | yes | Rows above the floor that no lane covers. They are excluded from ranking because their `cov = 0` is a tooling gap, not a testing gap. |
| `stale` | bool | yes | `true` when the ranked run's commit is not HEAD, so `cov`, `crap` and `uncovered_lines` describe an older tree. Same field, same rule as [`worklist`](#worklist). |
| `item` | object | when `empty` is false and `--top` is absent or 1 | The one item. |
| `items` | array | when `empty` is false and `--top` > 1 | Up to N items, same object shape. |
| `skipped_claimed` | int | only when a claim actually hid something | How many rows another session is holding. Absent, never `0`, so a store nobody claims in emits the same JSON it always did. |
| `reasons` | object | when `empty` is true | Why the queue is empty. |

### `item` fields

| Field | Type | Meaning |
|---|---|---|
| `scope` | string | The scope that owns the file. |
| `path` | string | Repo-relative source path, forward slashes. |
| `function` | string | The lizard long name, including the spaced parameter list. `brief` and `explain` also accept the bare identifier. |
| `handle` | string | The short name form: the bare identifier, or `(anonymous)#N` for a function lizard could not name. Unlike `start` it names a position rather than a line, so it survives the edit this item asks for. `brief`, `explain` and `claims release` all take it. |
| `start`, `end` | int | 1-based inclusive line span. |
| `ccn` | int | `min(ccn_std, ccn_mod)`. This is what the gate and the ratchet judge. |
| `ccn_std` | int | Standard cyclomatic complexity. |
| `cognitive` | int | Sonar-spec cognitive complexity, measured in every language crapkit scans. Reporting only, never gated. |
| `nloc` | int | Non-comment lines of code. |
| `nesting` | int | Maximum nesting depth. |
| `cov` | float | Branch coverage in the span, 0.0 to 1.0. |
| `flag` | string | `measured`, `untested`, `no-lane` or `cc-only`. See the [README](../README.md#flags-why-a-coverage-number-is-missing). |
| `crap` | float | The score. |
| `remedy` | string | `decompose`, `add-tests` or `ok`. |
| `target` | int | This scope's effective ceiling. |
| `commits`, `authors` | int | Churn for the file in the window. |
| `est_splits` | int | `0` when `ccn <= target`, else `ceil(ccn / target)`. Roughly how many functions this needs to become. |
| `est_uncovered_paths` | int | `round((1 - cov) * ccn)`. Decision paths no test walks. |
| `uncovered_lines` | array or **null** | See below. |
| `uncovered_lines_note` | string | Present **only** when `uncovered_lines` is null. |

### `uncovered_lines`: null is not `[]`

| Value | Means |
|---|---|
| `[8, 11, 14]` | The artifacts answered. These lines never ran. |
| `[]` | The artifacts answered. Nothing in this span is dark. |
| `null` | No artifact could answer. Read `uncovered_lines_note`. |

The distinction is load-bearing. An empty list is what a fully covered function returns, so
returning `[]` for a file no artifact measured would tell you there is nothing left to test.

**The note is prose and may be reworded; `flag` is the contract.** Branch on `flag`, and
print the note for a human. These three are what it reads like today, captured from real
runs:

```json
{
  "flag": "measured",
  "uncovered_lines": null,
  "uncovered_lines_note": "lane 'py': files in its scopes changed since .crapkit/cov/py.json was written (uncommitted edits count), so its line numbers are stale — commit or revert them, then rerun `crapkit coverage`"
}
```

```json
{
  "flag": "untested",
  "uncovered_lines": null,
  "uncovered_lines_note": "no lane artifact measured app/m.py (flag untested: no test imports it, so coverage records nothing for it; write the first test that imports app/m.py)"
}
```

```json
{
  "flag": "cc-only",
  "uncovered_lines": null,
  "uncovered_lines_note": "scope 'tools' sets coverage_optional = true, so no artifact can name uncovered lines for tools/helper.py"
}
```

A repo with no `[[lane]]` at all answers `no [[lane]] declared, so no artifact can say
which lines are dark`, and an artifact that will not parse answers `unreadable lane
artifact: ...`. The key is opt-in, so a repo whose artifacts answer never emits it at all.

The move differs per flag. On `measured` a lane did speak about the file and its artifact
has since gone stale: commit or revert the edits, then rerun `crapkit coverage`. Nothing
rereads the artifact until a run does, so committing alone leaves the lines null. On
`untested` no test imports the file, so no artifact was ever going to mention it: the whole
span is dark and the first test is the move, not another `coverage` run. On `cc-only` the
scope set `coverage_optional`, so no artifact can ever name lines for it and nothing to do
will change that. A `no-lane` row is a wiring gap; `next-item` never hands one out.

### `reasons`, and the stop condition

An empty queue must say what was filtered, or the silence reads as done.

```json
{
  "empty": true,
  "reasons": {
    "all_remaining_at_or_under_target": 4,
    "below_floor": 1,
    "churn_window_months": 12,
    "excluded_by_flag": 0,
    "no_churn_in_window": 0,
    "no_lane": 0,
    "no_lane_over_target": 0
  }
}
```

| Key | Meaning |
|---|---|
| `below_floor` | Rows under `worklist_floor`, counted in SQL rather than fetched. Every one is at or under its ceiling: an over-target row is queued whatever its ccn. |
| `no_lane` | Rows above the floor whose scope no lane covers. |
| `no_lane_over_target` | The subset of those that are over their ceiling. Debt the queue is not allowed to rank, because a `no-lane` row's `cov = 0` is a tooling gap. |
| `no_churn_in_window` | Rows in files with no commits in the churn window. |
| `excluded_by_flag` | Rows an `--exclude` fragment matched. |
| `churn_window_months` | The window that produced those counts, echoed back. |
| `all_remaining_at_or_under_target` | **Present only when candidates remained but every one has `remedy: "ok"`.** |

**The stop condition is `empty == true` AND `skipped_claimed` 0-or-absent AND
`reasons.no_lane_over_target` 0-or-absent.** `empty` alone says the queue has nothing to
hand out, which two things cause without the work being done: a claim hides a row from
every session, and a scope no lane measures can hold debt that never ranks. The
`worklist_floor` is not one of them. It withheld no over-target row on its way here,
whatever that row's ccn. Do not loop on "is there an item" either, because a function at
ccn 6 with 100% coverage clears the `ccn >= 5` floor forever and would be handed back
every time. The rule is stated once, with the moves, in
[AGENTS.md](../AGENTS.md#the-termination-rule).

### Claims

`--claim` records a claim on each item it hands out. Filtering is unconditional: a claimed
row is hidden from every session, including the one that took it, because a claim only one
session honours is worthless.

```
$ crapkit next-item --claim     # returns the item, and holds it
$ crapkit next-item             # {"empty": false, "skipped_claimed": 1, "item": {...next one...}}
```

`--claim` on a finished queue holds nothing, so an exploratory call cannot hide tomorrow's
top item.

A claim is released three ways: `verify` releases it once the function sits at its ceiling
or its commit leaves the history, `runs prune` drops claims older than the oldest kept run,
and `crapkit claims release` closes one by hand.

---

## `claims`

Who is holding what. A fleet reads this to see why the queue handed back nothing.

```
$ crapkit claims --json
```

```json
{
  "claims": [
    {
      "commit": "9a1d11895c5ff5b791b497a13294494fdab949ce",
      "created_at": "2026-08-23T01:39:07Z",
      "handle": "classify",
      "id": 1,
      "long_name": "classify( score , attempts , late , bonus )",
      "path": "calc/grade.py"
    }
  ],
  "open": 1,
  "schema": 1
}
```

`commit` is HEAD when the claim was taken, not the snapshot's commit: it describes the tree
the session started editing, which is what makes the ancestor test at verify meaningful.

`handle` is the name `next-item` handed the claim out under, stored rather than
recomputed. On an anonymous function it is the only string that releases the right claim:
every anonymous function in a file carries the same `(anonymous)` long name, and the
handle stays valid after the session's own edit moves the lines. `null` on a claim taken
before handles existed, or by a caller that had none.

Release takes any name form:

```
$ crapkit claims release calc/grade.py classify --json
{"released": 1, "schema": 1}

$ crapkit claims release app/parse_csv.py "(anonymous)#2" --json
{"released": 1, "schema": 1}

$ crapkit claims release --all --json
{"released": 3, "schema": 1}
```

A release naming a claim that is not open is exit 1, and the message lists what is:

```
crapkit: no open claim on 'classify' in calc/grade.py — open: calc/grade.py audit( rows , strict , cap , floor , verbose )
```

---

## `brief`

The start-editing packet. One call returns everything a burn-down session would
otherwise open the file, grep the callers and read `git log` to find out, which is why
[AGENTS.md](../AGENTS.md#1-the-packet) makes it step one of the loop rather than step
two.

```
$ crapkit brief app/parse_csv.py parse_row --json
```

```json
{
  "attempts": 1,
  "churn": {"authors": 1, "commits": 7, "weight": 0.8609},
  "commands": {
    "gate": "crapkit rescore app/parse_csv.py --gate",
    "refresh": "crapkit coverage --reuse-unchanged",
    "refresh_writes_run": true,
    "scoped_tests": "python -m pytest \"app/parse_csv.py\" -q -p no:cacheprovider",
    "verify": "crapkit verify"
  },
  "commit": "1b7b76bb6c16824a7bcee2d9e4c7f71a69eb4c3d",
  "coupling": [{"confidence": 1.0, "is_test": false, "path": "app/parse_tsv.py", "support": 7}],
  "duplication_twins": [
    {
      "contained": false,
      "end": 16,
      "long_name": "parse_line( text , strict , sep , header )",
      "nloc": 13,
      "path": "app/parse_tsv.py",
      "similarity": 0.9,
      "start": 4
    }
  ],
  "file_functions": [
    {"ccn": 9, "cov": 0.6, "crap": 14.184000000000001, "end": 16,
     "long_name": "parse_row( text , strict , sep , header )", "remedy": "decompose", "start": 4},
    {"ccn": 2, "cov": 1.0, "crap": 2.0, "end": 24,
     "long_name": "_split( text , sep )", "remedy": "ok", "start": 18}
  ],
  "est_splits": 2,
  "est_uncovered_paths": 4,
  "file_totals": {"crap_load": 16.18, "functions": 2, "over_target": 1},
  "function": "parse_row( text , strict , sep , header )",
  "handle": "parse_row",
  "gate_rule": {
    "binds": "ratchet_mark",
    "ceiling": 6,
    "diff_uncovered_max": 0,
    "mark_age_days": 12,
    "ratchet_mark": 14.184
  },
  "lane": {
    "artifact": ".crapkit/cov/py.json",
    "command": "python -m pytest -q --cov=app --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/py-junit.xml",
    "cwd": "", "env": {}, "name": "py", "parser": "coveragepy", "timeout_seconds": 0
  },
  "notes": ["app/ is the public seam: no new dependencies below it"],
  "params": ["text", "strict", "sep", "header"],
  "path": "app/parse_csv.py",
  "ratchet_mark": 14.184,
  "remedy": "decompose",
  "regrowth": {
    "history": [{"ccn": 5, "crap": 8.0, "run_id": 1}, {"ccn": 9, "crap": 14.184, "run_id": 2}],
    "regrown": true
  },
  "run_id": 2,
  "schema": 1,
  "scored": {
    "ccn": 9, "ccn_mod": 9, "ccn_std": 9, "cognitive": 8, "cov": 0.6,
    "crap": 14.184000000000001, "end": 16, "flag": "measured",
    "long_name": "parse_row( text , strict , sep , header )", "nesting": 6,
    "nloc": 13, "params": 4, "path": "app/parse_csv.py", "remedy": "decompose",
    "scope": "app", "start": 4
  },
  "source": "def parse_row(text, strict, sep, header):\n    ...\n",
  "stale": false,
  "target": 6,
  "uncovered_lines": [9, 11, 13, 15],
  "versions": {"crapkit": "0.1.0", "lizard": "1.24.0"}
}
```

### What the session reads

| Key | Type | Nullable | Meaning |
|---|---|---|---|
| `run_id`, `commit` | int, string | no | The scored run and its commit. |
| `path`, `function` | string | no | The resolved function. `function` is always the long name, whichever form you asked with. |
| `handle` | string | no | The short name form for this row: the bare identifier, or `(anonymous)#N`. Same value and same rules as [`next-item`'s](#item-fields), so a packet and a queue item name one function one way. |
| `remedy` | string | no | `decompose`, `add-tests` or `ok`. Promoted out of `scored` because it is the branch the session takes; `scored.remedy` carries the same value. |
| `est_splits`, `est_uncovered_paths` | int | no | The budget, from the same code `next-item` publishes it with. Formulas under [`item` fields](#item-fields). |
| `source` | string | no | The function's own text, `start` to `end` inclusive, newlines intact. The packet is editable without a second read of the file. |
| `params` | array of string | no | Its parameter names, in declaration order, so a new test can call it without opening the file. `scored.params` is the count of these. |
| `scored` | object | no | The whole scored row: the 16 fields above, including `params` and `ccn_mod`, which `next-item` does not carry. |
| `target` | int | no | The scope's effective ceiling. |
| `stale` | bool | no | `true` when `commit` is not HEAD, so every number here describes an older tree. Run `commands.refresh` first. |
| `file_functions` | array | no | Every scored function in the same file: `long_name`, `start`, `end`, `ccn`, `cov`, `crap`, `remedy`. What an extracted helper lands beside, and what names are already taken. |
| `file_totals` | object | no | That file rolled up: `functions`, `over_target`, `crap_load`. |
| `gate_rule` | object | no | What the gate will judge this edit by. Below. |
| `commands` | object | no | The rest of the loop, filled in for this file and this scope. Below. |
| `lane` | object | **yes** | The lane whose artifact produced `cov` and `uncovered_lines`, verbatim from the config: `name`, `command`, `artifact`, `parser`, `cwd`, `env`, `timeout_seconds`. A session rerunning the lane by hand needs the cwd and the env as declared; reconstructing them from the command string is how the reruns drift. `null` when no lane covers the scope, which is a `no-lane` row. |
| `versions` | object | no | `{crapkit, lizard}`: the metric identity behind every number in the packet. |
| `notes` | array of string | no | The `notes` lines the config carries, repo-wide first then this scope's. Empty when the config declares none. See [configuration.md](configuration.md#crapkit). |
| `attempts` | int | no | How many claims have already been opened on this function. `0` is a first attempt; above `0`, someone took it and stopped. |
| `regrowth` | object | no | `{regrown, history}`. Below. |
| `ratchet_mark` | float | **yes** | `null` when the function carries no mark, and also when the repo has no ratchet file at all. Read under the function's own ratchet key, so twins sharing a long name report their own marks and not each other's. |
| `churn` | object | **yes** | `{commits, authors, weight}`, or `null` when the file has no commits in the window. |
| `coupling` | array | no | Up to 5 partners, each `{path, support, confidence, is_test}`. Empty when nothing clears support 5 and confidence 0.5. |
| `duplication_twins` | array | no | Near-duplicate functions, each with `similarity` and `contained` plus its location. Empty is normal. |
| `uncovered_lines` | array | **yes** | Same null-vs-empty contract as `next-item`. |
| `uncovered_lines_note` | string | conditional | Present only when `uncovered_lines` is null. |

### `gate_rule`: what the edit is judged by

Three limits an edit can fail and the two facts that qualify them, in one object, so a
session need not read the config to learn which number it is aiming at.

| Key | Type | Meaning |
|---|---|---|
| `ceiling` | int | The scope's effective target. `rescore --gate` compares `ccn` against this and nothing else. Same value as `target`. |
| `binds` | string | The gate's scope rule as one sentence, the same string in every packet: `changed functions only; a ratchet mark pardons standing debt at or under it`. Print it, do not branch on it. |
| `ratchet_mark` | float or null | The mark this function already carries. At or under it, an edit inside the function passes `rescore --gate` and `verify`'s gate alike. Push it past the mark and both refuse, `rescore --gate` at exit 6 and `verify` at exit 6 as well since #29. Exit 7 is what is left for a mark that rose in a function the diff never touched. |
| `mark_age_days` | int or null | How old that mark is, measured from the newest commit that touched the ratchet file, never from the wall clock. |
| `diff_uncovered_max` | int or null | The configured ceiling on changed lines with no coverage. `null` means warn only: `verify` prints the count and exits 0. |

### `commands`: steps 3 to 5, already written

| Key | Type | Meaning |
|---|---|---|
| `gate` | string | `rescore --gate` for this file. Step 3 of the loop. |
| `scoped_tests` | string or null | This scope's own `[crapkit.scoped_tests]` template, with `{files}` already replaced by the packet's file, double-quoted. Not a `crapkit test-scoped` call: the packet hands you the runner the scope declared. `null` when the scope declares no template, and then there is no step 4; `doctor` warns about the gap. |
| `scoped_tests_note` | string | Present **only** when `scoped_tests` is `null`, naming the scope that declares no template. |
| `verify` | string | The `verify` call. Step 5, the only authoritative one. |
| `refresh` | string | The `coverage --reuse-unchanged` call that makes this packet current: it reruns the lanes whose scope files have moved and parses the rest off the artifacts they already have. Run it first when `stale` is `true`. |
| `refresh_writes_run` | bool | Always `true`. `refresh` is the only one of the four that writes: it appends a scored run to `.crapkit/crap.sqlite`. A read-only session runs the other three and stops here. |

Each one is a whole command line. Run them as given: a retyped `scoped_tests` loses whatever
the scope's template carries, and a retyped `refresh` loses `--reuse-unchanged` and reruns
every lane.

Both shapes, captured off one repo. With a template, and with the block removed:

```json
{"gate": "crapkit rescore calc/grade.py --gate",
 "refresh": "crapkit coverage --reuse-unchanged", "refresh_writes_run": true,
 "scoped_tests": "python -m pytest \"calc/grade.py\" -q -p no:cacheprovider",
 "verify": "crapkit verify"}

{"gate": "crapkit rescore calc/grade.py --gate",
 "refresh": "crapkit coverage --reuse-unchanged", "refresh_writes_run": true,
 "scoped_tests": null,
 "scoped_tests_note": "no [crapkit.scoped_tests] template for scope 'calc'",
 "verify": "crapkit verify"}
```

**The three crapkit calls are spelled as the console script**, `crapkit rescore ... --gate`
and not `python -m crapkit rescore ... --gate` (#37). That is the form the docs promise, and
the one that resolves from a venv on Windows, where bare `python` reaches the WindowsApps
stub or the base interpreter the venv wraps. `scoped_tests` is the scope's own string and
crapkit does not respell it.

`refresh` is what `stale: true` asks for, and the only thing that answers it. `stale`
compares the run's commit against HEAD, so nothing clears it but a run landing on the
current commit. Another `brief` re-reads the same snapshot and reports the same staleness.

### `regrowth`: did this get fixed before?

| Key | Type | Meaning |
|---|---|---|
| `regrown` | bool | `true` when this function's `crap` fell across runs and then rose again. An earlier decomposition did not hold, and repeating it will not either. |
| `history` | array | One entry per trusted run that scored the function, oldest first: `{run_id, ccn, crap}`. Empty on a function only one run has seen. |

### `coupling[]` and `duplication_twins[]`

| Key | Type | Meaning |
|---|---|---|
| `is_test` | bool | On a coupling partner: the path is a test file. Test paths are excluded from the corpus unconditionally, so a coupled test never appears in `file_functions` or the worklist, and it is still the file your edit breaks. |
| `contained` | bool | On a twin: every shingle of the smaller function appears in the larger. Containment, not mere similarity, so one of the two can call the other instead of being rewritten. |

### Name resolution

`NAME` takes five forms, all resolving to the same row:

| Form | Example |
|---|---|
| the long name | `"parse_row( text , strict , sep , header )"` |
| the bare identifier | `parse_row` |
| the function's start line | `4` |
| the ordinal handle | `"(anonymous)#2"` |
| the twin selector | `"__post_init__#2"` |

The bare identifier is the leading token of the long name, before the parameter list.
Only some of lizard's readers spell that list with parentheses: Rust prints
`route cmd : & Cmd` and Go prints `Classify n int`, so the identifier there ends at the
first space and the bare names are `route` and `Classify`.

Matching is exact first. A NAME that IS a long name or a bare identifier resolves to
that function alone, so `route` never also answers with `route_chain` and `route_num`.
A NAME that names no function falls back to a substring search over the file's long
names, which is what turns a half-remembered name into a list of candidates. `brief`
and `explain` run the identical rule, so one string cannot name one function in a
packet and three in a trajectory.

The start line is the disambiguator: it settles a bare name two functions share. `explain`
resolves it the same way as of 0.4.5, off the newest run that scored the path, so a line
number read out of a packet opens the same function in either command:

```
$ crapkit explain calc/grade.py 1
calc/grade.py  classify( score , attempts , late , bonus )
  run   1 @ fd47cb9c767 coverage  ccn  13  cov   39%  crap     51.6  measured
  mark: no ratchet file
  uncovered lines: 6, 8, 11, 12, 13, 14, 15, 16, 18, 20
```

The two commands word a miss differently. `brief` lists the lines that do open a function;
`explain` reports the NAME it could not resolve, because a line that names nothing is one
of several ways its lookup comes back empty:

```
$ crapkit brief calc/grade.py 12
crapkit: no function starts at line 12 in calc/grade.py in the latest scored run — it starts functions at: 1, 24

$ crapkit explain calc/grade.py 12
crapkit: no function matching '12' in calc/grade.py appears in any run
```

The ordinal handle names a function with no name of its own, which every payload prints
as `(anonymous)`. `N` counts the file's anonymous functions from the top, so
`(anonymous)#2` is the second one wherever it has drifted to. That is why `handle` carries
it and not the start line. An ordinal past the end is exit 1 listing the handles the file
does hold:

```
crapkit: no (anonymous)#5 in app/parse_csv.py in the latest scored run — it holds: (anonymous)#1, (anonymous)#2
```

`explain` resolves the handle the same way, against the newest run that scored the path.
Note that the store keys a function's identity on its long name, so one file's anonymous
functions share one history there: the handle picks the position, and `explain`'s history
covers every anonymous function in the file.

Twins sharing one long name are one candidate, not an ambiguity: the worst-scoring twin
wins, the same rule the queue ranks on. Anything genuinely ambiguous or absent is exit 1
with the candidates listed:

```
crapkit: no function named 'nope' in calc/grade.py in the latest scored run — it holds: _adjusted, _band, classify, extra, summarize
```

The twin selector picks one of them instead. `NAME#2` is the second function of that name
in file order, `NAME#3` the third — the same ordinals the ratchet keys their marks on, so
`ratchet_mark` in the packet belongs to the function the packet opened. An ordinal past
the last twin is exit 1:

```
crapkit: no __post_init__#5 in calc/iso_cost.py in the latest scored run — it holds 2 function(s) named '__post_init__'
```

Only a whole-number tail selects: a long name that merely contains a `#`, such as an
Objective-C or C++ operator name, resolves as itself.

### `--batch N`

One call, N packets, one read of the store.

```
$ crapkit brief --batch 3 --json
```

```json
{
  "commit": "1b7b76bb6c16824a7bcee2d9e4c7f71a69eb4c3d",
  "packets": [{"function": "parse_row( text , strict , sep , header )", "...": "..."}],
  "run_id": 2,
  "schema": 1,
  "stale": false
}
```

| Key | Meaning |
|---|---|
| `packets` | Up to N packets, in `next-item` order (`crap` descending). Each one is the object above, minus the keys the envelope hoists. |
| `run_id`, `commit`, `stale` | Hoisted, because every packet in one call comes from one run. |
| `schema` | `1`, as everywhere. |

`--batch` takes no `FILE` or `NAME`: the queue picks the functions. It exists so an
orchestrator pays the store, churn-log and ratchet-file reads once for a whole fleet
instead of once per session, and so every session starts at step 1 with nothing left to
look up. Since 0.4.5 it also shingles the repo once per batch rather than once per packet,
which is what `duplication_twins` costs: a batch of 5 on the 31,459-file corpus the 0.4.5
work was measured against fell from 11.8 s to 5.2 s, output byte-identical. The shingles are
built on Python's per-process randomized hash, so there is no on-disk cache behind that
number and one call is the whole saving. Hand one packet to one session, and see
[Multi-agent sessions](../AGENTS.md#multi-agent-sessions) for the file-disjoint split
that keeps their diffs mergeable.

---

## `worklist`

The risk map: every admitted function ranked by complexity times churn. It lists rows the
queue will never hand out, so it never empties and carries no stop condition.

```
$ crapkit worklist --json
```

```json
{
  "active": [
    {
      "authors": 1, "ccn": 7, "ccn_std": 7, "commits": 2, "end": 15,
      "flag": "measured", "function": "render( rows , wide , totals , header )",
      "nloc": 12, "path": "calc/report.py", "remedy": "decompose", "risk": 3.5,
      "scope": "calc", "start": 4, "weight": 0.5
    },
    {
      "authors": 1, "ccn": 14, "ccn_std": 14, "commits": 2, "end": 27,
      "flag": "measured", "function": "classify( score , attempts , late , bonus )",
      "nloc": 24, "path": "calc/grade.py", "remedy": "decompose", "risk": 0.0252,
      "scope": "calc", "start": 4, "weight": 0.0018
    }
  ],
  "churn_window_months": 12,
  "commit": "8c14f3daa8e88230c5b702d8f452ee2616d4de30",
  "dormant_count": 0,
  "dormant_top": [],
  "floor": 5,
  "run_id": 1,
  "schema": 1,
  "stale": false
}
```

| Key | Type | Meaning |
|---|---|---|
| `run_id`, `commit` | int, string | The run ranked, and its commit. |
| `stale` | bool | `true` when the run's commit is not HEAD. In plain output this also prints a stderr warning; in JSON it is only this field. |
| `floor` | int | The effective `worklist_floor`, echoed so a caller need not read the config. |
| `churn_window_months` | int | Same. |
| `active` | array | The queue: files with churn in the window, ranked, capped at `--top` or `worklist_top`. |
| `dormant_count` | int | How many ranked entries have zero churn in the window. |
| `dormant_top` | array | The first 10 dormant entries, same shape. Sleeping hazards, recorded without clogging the queue. |
| `batches` | array | Only with `--batches N`. |

Each entry carries `scope`, `path`, `function`, `start`, `end`, `ccn`, `ccn_std`, `nloc`,
`commits`, `authors`, `weight`, `risk`, plus `flag` and `remedy` from the run that scored
it. Those last two are `null` on an inventory-only run, which scored no verdict. There is no
`cov` and no `crap`: `worklist` ranks on complexity times churn. Use `next-item` or `brief`
when you need the scored numbers.

`floor` orders the list and withholds no debt. A function the ranked run scored over its
ceiling is listed whatever its ccn. An inventory-only run has no such verdict to read, and
there the floor is the whole rule.

**`worklist` and `next-item` are two views of one state, and they disagree on purpose.**
Both read the newest **trusted** run, which since 0.4.5 is one rule with one answer: a
`coverage` run, or a `verify` run whose verdict passed. A `partial` run and a failed verify
are refused, because a partial measured a fraction of the suite with its CRAP inflated to
match, and a failed verify's numbers can come off a red tree. `worklist` used to admit both
and rank off them while `next-item` picked its item off an older run, so the two commands
answered one question differently. An inventory-only run never splits them either: `worklist`
falls back to it only when no trusted run exists at all, ranking complexity alone.
`worklist` is the risk map: every admitted function ranked by `risk`, including rows at or
under their ceiling and rows no lane measures, so it never empties and holds no stop
condition. [`next-item`](#next-item) is the actionable queue: it drops the `no-lane` rows,
counts them in `skipped_no_lane`, ranks by `crap` descending, and reports `empty` once
nothing it ranks has work left. Read `flag` and `remedy` on an entry to tell which of its
rows the queue will hand you: `no-lane` never, `ok` never, anything else next. The two
payloads on this page come from one run of one repo: `worklist` leads with `render` at
risk 3.5, `next-item` hands out `classify` at crap 46.6. Neither is wrong.

`risk = ccn * weight`, rounded to four decimals. On a repo whose commits share a timestamp
every weight rounds to `0.0` and every risk with it; ranking then falls back to ccn.

### `--batches N`

`--batches` **adds** a `batches` key. Every other key stays, so a caller that reads `active`
or `stale` off a batched call still gets them.

```json
{
  "active": ["... unchanged ..."],
  "batches": [
    {
      "entries": [{"function": "render( rows , wide , totals , header )", "path": "calc/report.py", "...": "..."}],
      "files": ["calc/report.py"]
    },
    {
      "entries": [{"function": "classify( score , attempts , late , bonus )", "path": "calc/grade.py", "...": "..."}],
      "files": ["calc/grade.py"]
    }
  ],
  "...": "..."
}
```

At most N batches, sharing no file, with co-changing files kept in the same batch. One batch
per agent session: two sessions working different batches cannot collide in the same file.

The session that holds a batch briefs its own rows. Every entry carries `path` and
`function`, the two arguments `brief` takes. [`--batch N`](#--batch-n) is not the per-batch
form of that call: its N counts packets off the `crap`-ranked queue, and this split ranks by
`risk`, so its packets can pile into one of these batches and miss the rest.

---

## `verify`

The verdict, plus the receipt that says what produced it.

```
$ crapkit verify --json
```

```json
{
  "baseline_commit": "8c780bb18da329dfe039b55d14faa5a6dc9fcb50",
  "baseline_run": 8,
  "changed_files": 1,
  "commit": "8c780bb18da329dfe039b55d14faa5a6dc9fcb50",
  "committed_findings": 1,
  "diff_uncovered": [],
  "diff_uncovered_count": 0,
  "dirty_failures": [],
  "dirty_findings": 0,
  "gate_violations": [],
  "new_failures": [],
  "ok": false,
  "overridden": [],
  "ratchet_regressions": [
    {
      "dirty": false,
      "fresh_crap": 20.0,
      "long_name": "pick( a , b , c )",
      "path": "app/m.py",
      "recorded": 10.75
    }
  ],
  "ratchet_sha256": "3d05caa586f1d6e63cfce21b70ac06dc31243f82c9ac3071398f67f463cafe2f",
  "run_id": 9,
  "schema": 1,
  "tool_versions": {"crapkit": "0.1.0", "lizard": "1.24.0"}
}
```

### Verdict

| Key | Type | Meaning |
|---|---|---|
| `ok` | bool | The whole verdict. `false` if any finding list is non-empty or the diff-coverage ceiling was breached. |
| `run_id` | int | The run this verify wrote. |
| `baseline_run`, `baseline_commit` | int, string | What it was measured against. |
| `commit` | string | The commit the verified tree is at. Equal to `baseline_commit` when you are verifying uncommitted work. |
| `changed_files` | int | Files in the diff being judged. |

### Findings

| Key | Shape | Fires exit |
|---|---|---|
| `gate_violations` | `{path, long_name, start, ccn, cov, crap, remedy, dirty, key_name}` | 6 |
| `ratchet_regressions` | `{path, long_name, recorded, fresh_crap, dirty}` | 7 |
| `new_failures` | array of `classname::name` test ids | 8 |
| `diff_uncovered_count` | int, and `diff_uncovered[]` of `{path, line}` | 9, only when `diff_uncovered_max` is set |
| `overridden` | gate-violation objects an `--override` exempted | none; the run passes |

`key_name` on a gate violation is the ratchet key: the `long_name` when one function in
the file holds that name, and `long_name#2` for the second function holding it. It is the
string to look up in `crapkit-ratchet.tsv`, and `long_name` alone is not, whenever a file
gives one name to several functions. `ratchet_regressions` carries the key in `long_name`
already, because the entry it reports comes from the marks file.

**`diff_uncovered` truncates at 50 entries; `diff_uncovered_count` does not.** Above 50 the
two disagree on purpose. Trust the count.

`verify` reports the first of 6, 7, 8, 9 that fires, in that order.

Since 0.4.5 the gate exempts a touched function whose fresh CRAP sits at or under its ratchet
mark, the rule `rescore --gate` already applied (#29). So a `gate_violations` entry on a
marked function means the edit pushed it past the mark, and one payload can carry that entry
and a `ratchet_regressions` entry for the same function. Exit 6 is the verdict there. Exit 7
is for a mark that rose in a function the diff never touched. Both rules are stated once in
[ratchet.md](ratchet.md#the-commit-gate-skips-marked-functions).

A `--baseline ID` naming a run that exists but cannot serve now says which run it is, why,
and which runs can (#27), instead of the empty-store line:

```
crapkit: run 5 is a failed verify and cannot serve as a baseline; trusted runs: 1, 2, 4, 6, 7; pass `--baseline 7` for the newest
crapkit: run 8 is an inventory run (no coverage was measured) and cannot serve as a baseline; trusted runs: 1, 2, 4, 6, 7, 9; pass `--baseline 9` for the newest
crapkit: no run 99 in the store (`crapkit runs` lists them); trusted runs: 1, 2, 4, 6, 7
```

The reason is the run's own kind: a failed verify, a verify with no verdict, a hook run, a
partial run (a lane subset, or a lane that failed), an inventory run.

A lane that wrote no test counts this run gets one line naming the gap rather than a
KeyError (#30), and `inventory` no longer dies when a tracked file is missing from the
working tree.

### Dirty attribution

A verdict measures the working tree, so a concurrent session's uncommitted edits land in it.

| Key | Meaning |
|---|---|
| `dirty` (on each finding) | The finding's file has uncommitted tracked edits. |
| `committed_findings` | Findings across all three kinds whose file is clean. |
| `dirty_findings` | Findings whose file is not. |
| `dirty_failures` | The subset of `new_failures` whose test id names a file with uncommitted edits. Both the repo-path form and pytest's dotted-module form are matched. |

CI should treat any non-zero finding count as a failure. A local pre-push check can
reasonably look at `committed_findings` alone.

### Receipt

| Key | Meaning |
|---|---|
| `tool_versions` | `{"crapkit": ..., "lizard": ...}`. The metric identity behind the numbers. |
| `ratchet_sha256` | Digest of the ratchet file as read. **`null` when the repo has no ratchet file.** Pin it to prove which marks a verdict was measured against. |

---

## `coverage`

The run summary: corpus size, the four flags counted, the grade, and provenance for every
lane that spoke.

```
$ crapkit coverage --json
```

```json
{
  "by_scope": {"calc": {"crap_load": 124.07, "functions": 3, "grade": "F", "over_target": 2}},
  "cache_hits": 2,
  "cc_only": 0,
  "commit": "9a1d11895c5ff5b791b497a13294494fdab949ce",
  "crap_load": 124.07,
  "db": "/repo/.crapkit/crap.sqlite",
  "files": 2,
  "functions": 3,
  "grade": "F",
  "lane_failures": {},
  "lanes": {
    "py": {
      "artifact_sha256": "313ce0f1dcaa3d914622a28b3f9694df876bef194d27fc753176bef36c698cf1",
      "exit_code": 0,
      "parser": "coveragepy",
      "scopes": ["calc"]
    }
  },
  "measured": 2,
  "no_lane": 0,
  "over_target": 2,
  "run_id": 2,
  "schema": 1,
  "skipped_max_bytes": 0,
  "untested": 1
}
```

| Key | Meaning |
|---|---|
| `run_id`, `commit`, `db` | The run written, its commit, and the absolute store path. |
| `files`, `functions` | Corpus size. |
| `cache_hits` | Files served from the content-hash analysis cache. |
| `skipped_max_bytes` | Files dropped by `[exclude] max_file_bytes`. |
| `measured`, `untested`, `no_lane`, `cc_only` | The four flags, counted. They sum to `functions`. |
| `over_target` | Functions whose `crap` exceeds their scope ceiling. |
| `crap_load` | Sum of every function's CRAP, rounded to 2dp. |
| `grade` | The letter for over-target density. `A+` only at exactly zero. |
| `by_scope` | Per scope: `{functions, over_target, crap_load, grade}`. |
| `lanes` | Provenance per lane that succeeded: `artifact_sha256`, the command's `exit_code` (`null` when the artifact was reused), `parser`, `scopes`, plus `failures`, `tests_total` and `tests_skipped` when the lane declares a `results_artifact`. |
| `lane_failures` | Lane name to failure text, for lanes that did not produce an artifact. Non-empty means the run is typed `partial` and cannot be a baseline. |

`inventory --json` is the same run summary minus everything coverage adds: `run_id`,
`commit`, `files`, `functions`, `cache_hits`, `skipped_max_bytes`, `db`.

---

## `doctor --json`

The only health payload crapkit exposes. It works on a repo that has never run anything.

```
$ crapkit doctor --json
```

```json
{
  "analysis_version": 8,
  "lanes": [
    {
      "artifact": ".crapkit/cov/py.json",
      "artifact_present": true,
      "commit": "9a1d11895c5ff5b791b497a13294494fdab949ce",
      "name": "py",
      "seconds": 1.1
    }
  ],
  "newest_run": {"id": 2, "kind": "coverage", "verdict_ok": null},
  "problems": [],
  "schema": 1,
  "store": {"path": ".crapkit/crap.sqlite", "present": true, "size_bytes": 40960},
  "versions": {"crapkit": "0.1.0", "lizard": "1.24.0", "python": "3.11.2"},
  "warnings": []
}
```

| Key | Meaning |
|---|---|
| `problems` | The FAIL findings, as text. **Non-empty is exit 1.** |
| `warnings` | The WARN findings: unmeasured directories, scopes a lane measures with no `scoped_tests` template, lanes writing their artifacts at the repo root instead of under `.crapkit/`, and lanes with no `results_artifact`. Exit stays 0. |
| `versions` | crapkit, lizard, python. `lizard` is `null` when it is not importable, which is also a FAIL. |
| `analysis_version` | The analysis semantics version, `8` since 0.4.5 (`7` in 0.4.4). It plus `lizard` are the ratchet's metric stamp, so a bump refuses every existing mark until the repo re-seeds. See [ratchet.md](ratchet.md#upgrading-to-045-analysis-version-8). |
| `store` | `.crapkit/crap.sqlite`: whether it exists and how big it is. `present: false` and `size_bytes: 0` on a fresh repo. |
| `newest_run` | `{id, kind, verdict_ok}`, or `null` when nothing has run. `verdict_ok` is `null` for non-verify runs. |
| `lanes` | Per declared lane: `name`, `artifact`, whether the artifact is on disk now, and the `commit` and `seconds` from its stamp. `commit` and `seconds` are `null` for a lane that has never run here. |

`note`-level findings (a file over `max_file_bytes`, no lanes declared) appear in the plain
output only. They are neither problems nor warnings.

The `results_artifact` warning is new in 0.4.5 (#26), and it names the two checks the lane
loses rather than the key alone. A repo whose one lane declares neither the artifact nor a
`scoped_tests` template answers:

```json
["lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it; add --junitxml=.crapkit/cov/junit-py.xml to the command and results_artifact = \".crapkit/cov/junit-py.xml\" to the lane",
 "scope 'calc' has a lane but no [crapkit.scoped_tests] template — `crapkit test-scoped` exits 3 on its files, so whoever edits them is handed no command to run their tests; add calc = \"<test command>\" under [crapkit.scoped_tests]"]
```

`crapkit init` writes `--junitxml` and `results_artifact` on the lanes it detects, so this
one fires on a config written by hand or by an older crapkit.

A lane whose first word will not start is a FAIL, not a warning: `doctor` reads the command
with the shell that will run it (sh on POSIX, cmd.exe on Windows), so a quoted interpreter
path is one word and a runner after `&&` is checked too. Each distinct runner is probed
once per `doctor` call rather than once per lane.

`doctor --tune` is a different command shape: it prints TOML lines, not JSON, and it
respects neither `--json` nor `--show-files`.

### `doctor --plugin-root PATH`

The plugin and the CLI ship as two artifacts with one version number between them, and
neither notices when they drift. This is the check, and it reads no repo at all.

It compares the plugin's `.claude-plugin/plugin.json` version against the running crapkit,
and every `--protocol` in its `hooks/hooks.json` against the protocol `claude-hook` answers.
One line per disagreement, silence when they agree, exit 1 when it printed anything:

```
$ crapkit doctor --plugin-root crapkit
crapkit doctor: the plugin at crapkit is version 0.3.0, this crapkit is 0.4.0. Reinstall whichever is behind: `claude plugin install crapkit@crapkit`, or `pip install -U crapkit`.
crapkit doctor: the plugin at crapkit asks for hook protocol 2; this crapkit answers 1, so `claude-hook` exits 0 silent on every edit.
```

A root doctor found rather than one you typed gets a `crapkit doctor: checking <that root>`
line first, naming the install the verdict is about: the search reaches three levels under
the directory you named, so a source checkout can win over an install and the two look the
same from the outside. A `PATH` that is itself a plugin root prints no such line, and
neither does a check that found nothing to say.

```
$ crapkit doctor --plugin-root plugin        # PATH is the plugin root: silent, exit 0

$ crapkit doctor --plugin-root .             # PATH is a directory above it
crapkit doctor: checking plugin
```

With no `PATH` at all it reads Claude Code's own plugin directory (`CLAUDE_CONFIG_DIR`, else
`~/.claude`), and when nothing is installed there it names the directory it looked in and
exits 1:

```
$ crapkit doctor --plugin-root
crapkit doctor: no installed crapkit plugin under ...\.claude\plugins (install with `claude plugin install crapkit@crapkit`, or pass --plugin-root PATH)
```

(The absolute path is elided; the line prints it in full.)

A plugin with no manifest gets one line saying so and no protocol check: there is no version
to compare, and the protocol line underneath would bury the fact that explains both. A plugin
shipping no `hooks/hooks.json` registers no advisory hook, and the output says that instead.
It prints no JSON and ignores `--json`.

`PATH` may be the plugin root itself or any directory above it: `~/.claude`, `~/.claude/plugins`,
the cache root `~/.claude/plugins/cache`, or a marketplace or plugin directory inside it. Claude
Code keeps an install at `cache/<marketplace>/<plugin>/<version>/` and leaves the old version
beside the new one after an update, so among the manifests named `crapkit` under `PATH` the
newest install is the one checked; the other plugins sharing that cache are never read. With no `PATH` at
all, doctor looks in Claude Code's plugin directory (`CLAUDE_CONFIG_DIR`, else `~/.claude`),
through `installed_plugins.json` and the cache, and names that directory when nothing is
installed there.

---

## `ratchet report --json`

How much debt is open, how much was repaid, and whether the configured policy is breached.

```json
{
  "anchor_ts": 1787230800,
  "dropped_last_30d": 0,
  "dropped_last_90d": 0,
  "dropped_total": 0,
  "oldest": [
    {"age_days": 0, "long_name": "classify( score , attempts , late , bonus )", "path": "calc/grade.py"},
    {"age_days": 0, "long_name": "render( rows , wide , totals , header )", "path": "calc/report.py"}
  ],
  "open": 2,
  "policy_violations": null,
  "schema": 1,
  "uncommitted": 0
}
```

| Key | Meaning |
|---|---|
| `open` | Marks open **on disk**, working tree included. A seed you have not committed counts. |
| `uncommitted` | Marks the working tree and the newest committed version disagree on: added, repaid or tightened but not committed. |
| `dropped_total`, `dropped_last_30d`, `dropped_last_90d` | Repayments, from committed history only. |
| `oldest` | Up to 20 open marks, oldest first, each with `age_days`. |
| `anchor_ts` | Unix seconds of the **newest commit** that touched the ratchet file. Every age and window is measured back from here, never from the wall clock, which is what makes the report deterministic on a fixed history. |
| `policy_violations` | `null` when no policy was evaluated, `[]` when it ran clean, otherwise the findings. See [ratchet.md](ratchet.md#the-debt-policy). |

---

## Other payloads

| Command | Shape |
|---|---|
| `runs --json` | `{"runs": [{id, kind, verdict_ok, findings, baseline, commit, lanes[], created_at}]}`. `kind` is `inventory`, `coverage`, `partial`, `verify`, `hook` or `legacy`. Only `coverage`, `legacy` and passing `verify` runs are baseline candidates. `verdict_ok` is `null` on a run that renders no verdict, which is every kind but `verify`. `findings` is how many a verify recorded. `baseline` is true on the one run `verify` compares against today, which is not always the newest candidate: see [the trusted baseline](../README.md#the-trusted-baseline). |
| `runs prune --json` | `{"pruned_runs": 6, "kept_runs": 4, "freed_bytes": 0}`. |
| `trend --json` | `{"runs": [{run_id, commit, created_at, functions, over_target, crap_load, avg, by_scope}], "target": 6}`, trusted runs only. Reads and fills the `run_rollup` cache; see below. |
| `overrides --json` | `{"overrides": [{run_id, commit, created_at, path, function, crap, reason}]}`. |
| `rescore --json` | `{"baseline_run", "baseline_commit", "functions": [{scope, path, function, start, end, ccn, cov, flag, crap, remedy, stale_coverage}], "note"}`. Every row carries `stale_coverage: true`: the complexity is the working tree's, the coverage is the baseline run's. |
| `duplication --json` | `{"run_id", "pairs": [{similarity, contained, functions: [{path, long_name, start, end, nloc}, ...]}]}`. Containment scoring: shared shingles over the smaller function. A pair whose two spans nest in one file is dropped, not ranked: a factory and the closure defined inside it score 1.0 by construction and cannot be deduplicated. `contained` is therefore `false` on every pair here, and it is emitted so pairs and `duplication_twins` read as one shape. |
| `coupling --json` | `{"window_months", "pairs": [{files: [a, b], support, confidence}]}`. `support` is shared commits, `confidence` is the max-direction ratio. It reads raw `git log`, so any path in the history can appear, not only scoped source. Ranked pairs are cached; see below. |
| `mutate --json` | `{"mutants", "killed", "survived", "survivors": [{path, line, op, original, mutated}]}`. `mutants` is the count **after** `--max-mutants`; the truncation warning goes to stderr only. With `mutation_workers > 1` the worker worktrees are kept under `.crapkit/mutate-pool/`; `crapkit mutate --drop-pool` removes them and exits. |
| `claims --json` | Above. |
| `digest` | **Never JSON.** Plain lines, and silent when nothing changed. |
| `report` | No payload of its own. It writes one self-contained HTML page to `.crapkit/report.html` (or `--out PATH`, repo-relative) and prints that path on stdout, rendering the `worklist` and `trend` payloads above at their defaults. Read those two instead of parsing the page. |
| `explain` | Plain lines by default. `--json` emits the same content as one sorted-keys object with `schema` 1: the score per run, the ratchet mark, and under `--history` the commits that touched the function, each carrying its message `body` alongside its sha. `NAME` takes a start line as of 0.4.5, the same form `brief` takes. |

### Two read commands that write

`trend` and `report` are still read commands to their caller, and since 0.4.5 they write to
the store. Both used to re-derive per-run totals from every scored row of every run, twice,
on every invocation: 4.3 M rows on the corpus the 0.4.5 work was measured against, 4.58 s per
`trend`. A run is immutable once written, so its totals are now summed once into a
`run_rollup` table and read back from there: `trend` 4.58 s to 0.04 s warm, `report` down
76%. A prune takes a run's rollup rows with it.

Two consequences for a caller.

- **A read-only checkout is fine, a read-only store is not.** The fill is best effort: if
  another crapkit process holds the write lock, or the file cannot be written, the command
  drops the cache and still prints the right answer. It never fails on the write.
- **The cache is keyed on the ceiling the totals were decided against**, repo target plus
  per-scope targets. Change a ceiling in `crapkit.toml` and the next `trend` refills under a
  new key rather than reporting the old numbers.

### The coupling cache

`coupling`, `brief` and `worklist --batches` rank the same co-change pairs out of the same
window, and each one used to re-cut the churn log and re-count every combination on every
run. Since 0.4.5 the ranked pairs live in `.crapkit/coupling-cache-v1.json`, beside
`churn-cache-v2.json` and `churn-log-v2.z`. Warm `coupling` on the measured corpus went from
1.05 s to 0.11 s, `worklist --batches` down 62%, a single `brief` down 25%.

What is stored is the ranking at the **default** thresholds, in full order, uncut. `--top`
truncates that order, so it reads the cache. `--min-support` or `--min-confidence` off the
defaults ask a wider question than the file answers and recompute, because serving them a
filtered subset would drop the pairs those thresholds exist to surface.

The key is HEAD, the window, and a digest of the tracked set. The tracked set is in the key
because ranking drops any pair naming a file `git ls-files` no longer lists, and the index
moves without HEAD: `git rm --cached src/util.py` leaves the sha alone and must still retire
every pair naming that file. Unreadable or unkeyable content reads as cold, never as a crash.

---

## `claude-hook`

The one command on this page Claude Code runs for you, after every Edit or Write of a source
file. It names functions that edit pushed over their ceiling, while the session can still act
on it.

```
crapkit claude-hook --protocol 1
```

**In:** one Claude Code PostToolUse event, as JSON on stdin. **Out:** nothing on stdout,
ever. Protocol 1 reserves stdout for a future JSON channel, and Claude Code parses stdout
JSON on exit 0. There is no `--repo`: the root is the first `crapkit.toml` above the edited
file. The plugin registers it async with a 20-second timeout, so no edit waits on it.

| Exit | Means | Output |
|---|---|---|
| `0` | nothing to say | stdout and stderr both empty |
| `2` | a changed function is over its ceiling | three or more lines on stderr, which reach the model |

Captured from a real run, on a file whose `route` reached ccn 7 under a ceiling of 6:

```
crapkit advisory: 1 function(s) over ceiling 6 in app/m.py (the edit landed; nothing was blocked)
  ccn 7  app/m.py:1  route( a , b , c , d )
the commit gate enforces this; decompose there or mark the debt
```

**It is advisory, and the wording says so.** PostToolUse runs after the write, so the edit is
already on disk and nothing can block it. `hook-precommit` stays the only enforcement point.
The head line states that outright, because the reader is a model holding a nonzero exit
code.

It judges the functions the edit touched, not the whole file. Judging the file would fire on
every edit in a repo with seeded debt and say nothing new. An untracked file is the one
exception: `git diff` can see none of it, so every function in it counts.

### The silence ladder

Five rungs, each exiting 0 with both streams empty. Any uncaught exception does the same.

| Rung | Silent when |
|---|---|
| event | stdin is not one JSON object, or not a `PostToolUse` carrying `tool_input.file_path` |
| repo | no `crapkit.toml` above the edited file; the walk up stops at any `.git` entry, so a worktree never borrows its parent's config |
| git state | mid-rebase, mid-merge or mid-cherry-pick |
| protocol | `--protocol` is anything but `1` |
| verdict | no scope claims the file, the source parses to no functions, no changed function is over the ceiling, or every one that is carries a ratchet mark |

Silence is the design. PostToolUse renders every nonzero exit but 2 invisible, and 47.5% of
the edits this was measured against land in repos with no `crapkit.toml`. A hook that fired
there would be either useless or unbearable.

The mark exemption is existence, not the numeric rule `verify` applies: the store is never
opened, so the hook holds no CRAP to compare. Same rule as the commit gate, described in
[ratchet.md](ratchet.md#the-commit-gate-skips-marked-functions).

An unknown `claude-*` subcommand exits 0 silently too, so a plugin newer than the installed
CLI degrades to silence instead of an argparse usage dump on every edit.

---

## MCP server

```
crapkit mcp
```

A dependency-free stdio MCP server: JSON-RPC 2.0, one message per line, protocol version
`2024-11-05`. Read-only. Every tool shells to the CLI's own surface, so the MCP view cannot
drift from what the CLI reports, and nothing here writes a baseline, a ratchet, or a mutant.

With no `--repo`, the server serves the directory the client started it in, which is what a
globally registered server sees in each project. In a directory with no `crapkit.toml` the
server still starts and answers `initialize` and `tools/list`. Each `tools/call` there comes
back as a tool result, not a JSON-RPC error, and that result carries `isError: true` with
text naming the missing config and `crapkit init`:

```json
{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "no crapkit.toml in .../noconfig — nothing measured here. Run `crapkit init` in the repo you want scored, or pass this tool a `repo` argument (or start the server with --repo) pointing at one."}], "isError": true}}
```

Both halves are deliberate. The result keeps the client's session alive, so a global
registration never turns into a dead server in unmeasured repos. `isError` stays true so
nothing reads an unmeasured directory as a repo with nothing to report.

Client wiring:

```json
{
  "mcpServers": {
    "crapkit": {
      "command": "crapkit",
      "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

Every tool also accepts a `repo` argument that overrides the server's default, so one server
can serve several checkouts.

| Tool | Arguments | Returns |
|---|---|---|
| `worklist` | `top` | JSON text |
| `runs` | | JSON text |
| `brief` | `path`, `name` | JSON text |
| `coupling` | `min_support`, `min_confidence` | JSON text |
| `duplication` | `similarity` | JSON text |
| `ratchet_report` | | JSON text |
| `explain` | `path`, `name` | **plain text**, not JSON |
| `doctor` | | **plain text**, not JSON |
| `next_item` | `top` (int), `exclude` (array of strings: one fragment per element, each becoming its own `--exclude`) | JSON text |

Results arrive as MCP text content. For the seven JSON tools the text is the payload; parse
it. For `explain` and `doctor` it is the human output, which has no schema. `isError` is
true whenever the underlying CLI call exited non-zero, and then the text is whatever the CLI
wrote to stderr. It is true in one case where no CLI call runs at all: the missing-config
result above.

