# The machine surface

This page defines payloads from commands with `--json`, the JSON-only `next-item`
command, and the read-side MCP tools. For the edit sequence, use
[the agent workflow](../AGENTS.md#1-the-packet). For an existing integration moving
to a new reader, use [Upgrading](upgrading.md).

Jump to [packets](#brief), [verification](#verify), [errors](#errors),
[MCP setup](#mcp-server) or [portable exports](portable-records.md).

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

- **Keys are sorted** and scored rows carry no timestamps. A new stored run can change
  envelope fields such as `run_id`; compare row content when comparing measurements.
- **stdout carries exactly one JSON object.** Warnings, progress, lane chatter and gate
  findings all go to stderr, so `crapkit ... --json 2>/dev/null` is always parseable. A
  command that dies prints one object too: see [Errors](#errors).

`next-item` has no `--json`: it prints its payload as JSON. On an error it prints nothing on
stdout and the reason on stderr, with no error object, so read its exit code before parsing.

TSV exports and portable baselines follow the separate
[portable record encoding](portable-records.md); JSON fields preserve their original strings.

Function rows carry `occurrence` alongside their real `start` and `end` lines. A new
measurement numbers functions sharing a start line from 1 in source creation order,
including functions with different names. `0` means an older row has no such position.
Use `(path, long_name or function, start, occurrence)` to distinguish rows within a
run. The raw function signature stays unchanged. This field appears in `next-item`,
`worklist`, `brief.scored`, `brief.file_functions` and `rescore.functions`; adding it
does not change JSON schema 1.

---

## `next-item`

The top of the burn-down queue: the rows a lane measures, whose remedy is not `ok`,
ranked by **`crap` descending**. Scores equal at 4 decimal places go to the file with more
commits in the churn window, then by path and start line. That ordering is the difference from
[`worklist`](#worklist), which ranks the same run by risk and lists rows this command
never offers, so the two do not lead with the same function.

```
$ crapkit next-item
```

```json
{
  "commands": {"refresh": "crapkit coverage --reuse-unchanged"},
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
    "nesting": 3,
    "nloc": 24,
    "occurrence": 1,
    "path": "calc/grade.py",
    "remedy": "decompose",
    "scope": "calc",
    "start": 4,
    "target": 6,
    "uncovered_lines": [8, 10, 12, 14, 17, 18, 19, 20, 22, 24, 26],
    "unmeasured": false
  },
  "run_id": 1,
  "schema": 1,
  "scored_changes": 0,
  "shallow": false,
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
| `stale` | bool | yes | `true` when the ranked run's commit is not HEAD. It judges the commit, not the files: see [`stale` and `scored_changes`](#stale-and-scored_changes-does-the-run-still-describe-the-files). Same field, same rule as [`worklist`](#worklist). |
| `scored_changes` | int or **null** | yes | How many files the ranked run scored hold other content now than the run recorded. `0` means the numbers describe the files on disk. `null` means crapkit cannot compare: the run recorded no content, or git failed reading the tree. |
| `commands` | object | yes | `{refresh}`: the call that answers `stale` and `scored_changes`, the same string a [`brief` packet's](#commands-steps-3-to-5-already-written) `commands.refresh` holds. |
| `shallow` | bool | yes | `true` when the checkout is a shallow clone, so `commits`, `authors` and the churn that breaks ties in the ranking count only the commits the clone holds: a depth-1 clone counts one commit per file. stderr carries one line naming the fix, `set fetch-depth: 0 on the checkout or run git fetch --unshallow`. `false` in a full clone. Same field, same rule as [`worklist`](#worklist). |
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
| `handle` | string | The short name form: the bare identifier; `NAME#N` in file order when one file gives that long name to several functions, starting at `NAME#1`; the full long name when functions with different signatures share an identifier; or `(anonymous)#N` for a function lizard could not name. A handle is not a ratchet key: the first twin's mark is keyed with no suffix and the second's `long_name#2`, and `ratchet_mark` already reports each twin's own mark. Unlike `start` it names a position rather than a line, so it survives the edit this item asks for. `brief`, `explain` and `claims release` all take it. |
| `start`, `end` | int | 1-based inclusive line span. |
| `occurrence` | int | Source creation order among functions sharing `start`, from 1; `0` on older rows with no recorded position. |
| `ccn` | int | `min(ccn_std, ccn_mod)`. This is what the gate and the ratchet judge. |
| `ccn_std` | int | Standard cyclomatic complexity. |
| `cognitive` | int | Sonar-spec cognitive complexity (v1.7), measured in every language crapkit scans. Each language charges its own control structures: Swift's `guard` costs what an `if` costs, a Python `match` statement what a `switch` costs, and a member such as `c.do(1)` nothing. Recursion is a call that reaches the function itself: a Python, JavaScript, Go or Rust method through its object or its type, a Swift function only with its own argument labels, never a bare name that a parameter, an import or an assignment in the body binds. In C++, Java and Swift a call that another function of the same name in the file also takes is not counted, because crapkit reads no types. `??` is free. Reporting only, never gated. |
| `nloc` | int | Non-comment lines of code, lizard's NLOC: comment-only lines and blank lines do not count. |
| `nesting` | int | Maximum nesting depth, read off crapkit's cognitive pass in every language: the deepest level that pass reaches. In a brace language each `if`, `else if`, `else`, loop, `switch` (and Rust `match`) and `catch` opens a level for its body, with or without braces (`if (a) return 0;` is one level), and a conditional operator `?:` puts its arms one level down, a second one inside an arm one further. A logical operator such as `&&`, a `case` label (Zig's `else =>` prong is one), `try`, `finally`, a bare block, `@synchronized`, `@autoreleasepool`, a literal in a structure's header (`range []int{1, 2} {`) and a `?` with no `:` (Rust's `?`, `??`, an optional type) open none; so does a closure, whose blocks count on its own row where lizard lists it as a function and at the level around it where lizard does not. In Python one level per `if`, `elif`, `else`, `for`, `while`, `except`, `match` statement and comprehension `for`, none for `with`, `try`, `finally`, `case`, a conditional expression or a nested `def` (a nested function's blocks count on its own row); a comprehension's level closes with its bracket, so `[p for p in a] + [q for q in b]` reads 1, and a line inside a bracket opens and closes nothing. In shell `if`, `case` and each loop open a level and `fi`, `done` and `esac` close it: seven ifs side by side read 1, four nested read 4, a `case` reads 1 whatever its arm count, and `&&` or `||` opens none. A flat function of seven `if`s reads 1, three nested loops read 3, a `switch` reads 1 whatever its case count, and an `if` inside a `with` inside an `if` reads 2. |
| `cov` | float | Branch coverage in the span, 0.0 to 1.0. With no branches it falls back to statement coverage, and with no statements to invoked-or-not (1.0 or 0.0). Python `and`/`or` add to `ccn`, but coverage.py records no branch arc for them, so a short-circuit no test takes leaves `cov` unchanged. An `untested`, `excluded`, `no-lane` or `cc-only` row reads 0.0. |
| `flag` | string | `measured`, `untested`, `excluded`, `no-lane` or `cc-only`. See the [README](../README.md#flags-why-a-coverage-number-is-missing). `untested` also marks a function the artifact cannot tell apart from another: one whose line span another function shares, or a Python def whose body starts on the line its signature ends. That row scores `cov` 0.0 whatever the tests do, and its `uncovered_lines` still lists what the artifact saw, `[]` when every line ran; its `remedy` is `split-lines` once `crap` is over `target`. |
| `crap` | float | The score, `ccn^2 * (1 - cov)^3 + ccn`, unrounded. A `cc-only` or `excluded` row scores `ccn`, since no test can move a number its lane was told not to measure. |
| `remedy` | string | `decompose`, `split-lines`, `add-tests` or `ok`. `split-lines` means another function shares the source lines, or a Python def's body starts on the line its signature ends under a coverage.py lane, which reads that body as the `def` statement that runs at import; either way no test lowers the score until the definitions, or the signature and its body, are on separate lines. Judged against `target`, the ceiling `crapkit.toml` holds now, not the one the run was scored under: an uncommitted ceiling edit moves the remedy, and what the queue offers, before the next run lands. |
| `target` | int | This scope's effective ceiling: the highest CRAP a function may carry, and so also the highest `ccn`, since CRAP never falls below `ccn`. A scope's own `target` overrides the `[crapkit]` one. |
| `commits`, `authors` | int | Churn for the file in the window. |
| `est_splits` | int | `0` when `ccn <= target`, else `ceil(ccn / target)`. Roughly how many functions this needs to become. |
| `est_uncovered_paths` | int | `(1 - cov) * ccn`, rounded half to even on the exact product: 2.5 reads 2 and 3.5 reads 4. Decision paths no test walks. |
| `unmeasured` | bool | `true` when no measurement stands behind `cov`: `flag` is `no-lane` or `cc-only`, so no artifact could speak about the span. `cov` then reads 0.0 and `est_uncovered_paths` reads `ccn`, stand-ins rather than counts; both keep those values in schema 1. `false` for `measured`, `untested` and `excluded`: an `untested` row was measured at 0.0 by a lane whose artifact never reached it, and an `excluded` row's artifact measured the file and was told to leave the function out. |
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
print the note for a human. These four are what it reads like today, captured from real
runs:

```json
{
  "flag": "measured",
  "uncovered_lines": null,
  "uncovered_lines_note": "lane 'py': calc/grade.py changed since .crapkit/cov/py.json measured it, so its line numbers there are stale - rerun `crapkit coverage` to measure it again"
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

```json
{
  "flag": "no-lane",
  "uncovered_lines": null,
  "uncovered_lines_note": "no lane covers scope 'lib', so no artifact can name uncovered lines for lib/util.py; add 'lib' to a [[lane]]'s scopes to measure it"
}
```

A repo with no `[[lane]]` at all answers `no [[lane]] declared, so no artifact can say
which lines are dark`, and an artifact that will not parse answers `unreadable lane
artifact: ...`. When git cannot say whether the file changed since the lane measured it,
the lines are withheld too and the note quotes git's error: `lane 'py': git cannot say
whether calc/grade.py changed since .crapkit/cov/py.json measured it (...), so its line
numbers there are withheld`, then the rerun once git answers. The key is opt-in, so a repo whose artifacts answer never emits it at all.

The move differs per flag. On `measured` a lane did speak about the file and the file's
bytes have changed since, so the lines the artifact holds point at code that moved. Rerun
`crapkit coverage`. Committing changes nothing: the lane's stamp holds a digest of every
file under its scopes as its run left them, and only a file whose bytes differ from that
digest loses its lines. A touch, a mode bit, a CRLF checkout, an amend, a rebase and a
shallow CI clone with `.crapkit/` restored leave the lines in place, and an edit reverted
after the lane measured it withholds them. Every other file keeps its lines. An artifact
whose stamp crapkit 0.8.0 or older wrote records no digests: until the next `crapkit
coverage` it is judged by git's diff since its commit, and while that says stale every
file's lines are null, with a note that names the files, a commit HEAD no longer descends
from, or the git error that left the question open. On
`untested` no test imports the file, so no artifact was ever going to mention it: the
whole span is dark and the first test is the move, not another `coverage` run. On
`cc-only` the scope set `coverage_optional`, so no artifact can ever name lines for it and
nothing to do will change that. A `no-lane` row is a wiring gap; `next-item` never hands
one out. An `excluded` row comes back `[]`, not null: the artifact measured the file and was
told to leave the function out, so `crap` is `ccn` and only `decompose` moves it.

### `stale` and `scored_changes`: does the run still describe the files?

Two fields answer two questions, and every payload that ranks a run carries both
(`next-item`, `brief`, `brief --batch`, `worklist --json` and the MCP tools that print
them).

`stale` keeps the meaning it has had since schema 1: the ranked run's commit is not HEAD.
It judges the commit and never the content. An amend that moves no byte, an empty commit
and a commit that touches only a README all set it, with every scored number still true.
An uncommitted rewrite of a scored function leaves it `false`, while the packet hands out
the pre-edit `ccn`, `crap` and span beside a `source` read from disk.

`scored_changes` is the content answer. Each scored run records the content of every file
it scored, and `scored_changes` counts the files whose content differs now, deleted files
included. A touch, a same-bytes rewrite and a commit that moves no scored byte leave it at
`0`; an edit to a scored file raises it, committed or not, and so does reverting an edit
the run measured. It compares content through the same record lane stamps use, so the
limits [docs/lanes.md](lanes.md) names for lane freshness apply here too. `null` means
crapkit cannot compare: the run recorded no content, or git failed while reading the tree.
crapkit 0.8.0 and older wrote no record, so their runs read `null` until the next
`crapkit coverage` writes one. A git failure is never counted as `0` or as a change; the
plain `worklist` quotes git's error. Treat `null` as "unknown" and run `commands.refresh`.

| Change after the run | `stale` | `scored_changes` |
|---|---|---|
| nothing, or a touch | `false` | `0` |
| uncommitted edit to a scored file | `false` | `1` or more |
| coverage measured on an uncommitted edit, then the edit reverted | `false` | `1` or more |
| `commit --amend -m`, `commit --allow-empty`, a commit outside every scope | `true` | `0` |
| the run recorded no content, or git failed reading the tree | either | `null` |

The plain `worklist` prints each case on stderr, and names up to three changed files:

```
warning: 2 file(s) changed since run 4 scored them: calc/grade.py, calc/report.py - rerun `crapkit coverage`
warning: cannot tell which files changed since run 4 scored them, because git failed: <git's error> - fix what git reports, then rerun `crapkit coverage`
```

`commands.refresh` answers both. Schema 2, planned for crapkit 0.9.0, redefines `stale`
as "a scored file's content differs from the run's", which is what `scored_changes > 0`
says today. Until then read `scored_changes` for the content question and `stale` for the
commit question.

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
`reasons.no_lane_over_target` 0-or-absent AND `scored_changes == 0`.** `empty` alone says
the queue has nothing to hand out, which three things cause without the work being done:
a claim hides a row from every session, a scope no lane measures can hold debt that never
ranks, and the ranked run can describe files your own edits have since changed. The last
is `scored_changes`: anything but `0`, `null` included, means run `commands.refresh` and
ask again. The
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

A claim keeps the name it was handed out under. One taken before analysis version 11 on
a Python def nested three or more deep saved `a.a.b.c( x )`, which version 11 names
`a.b.c( x )`. `next-item`, `brief --batch`, `verify` and `brief`'s `attempts` match it
against the run's own names, so it keeps holding that def
([upgrading](upgrading.md#analysis-version-11)).

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

Release takes any name form. A claim taken before analysis version 11 on a nested def
also answers to the name version 11 gives the def, when no claim answers to the name as
saved:

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
crapkit: no open claim on 'classify' in calc/grade.py - open: calc/grade.py audit( rows , strict , cap , floor , verbose )
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
  "attempts": [{"closed": "2026-09-23T02:33:04Z", "opened": "2026-09-23T02:33:00Z"}],
  "churn": {"authors": 1, "commits": 7, "weight": 0.1911},
  "commands": {
    "gate": "crapkit rescore app/parse_csv.py --gate",
    "refresh": "crapkit coverage --reuse-unchanged",
    "refresh_writes_run": true,
    "scoped_tests": "crapkit test-scoped app/parse_csv.py",
    "verify": "crapkit verify"
  },
  "commit": "9c7eed1a91d12a4b84b51ecefbbf9e1f5551d216",
  "coupling": [{"confidence": 1.0, "is_test": false, "path": "app/parse_tsv.py", "support": 7}],
  "duplication_twins": [
    {
      "contained": false,
      "end": 17,
      "long_name": "parse_line( text , strict , sep , header )",
      "nloc": 17,
      "path": "app/parse_tsv.py",
      "similarity": 0.8571,
      "start": 1
    }
  ],
  "est_splits": 2,
  "est_uncovered_paths": 4,
  "file_functions": [
    {"ccn": 11, "crap": 15.481481481481483, "end": 17,
     "function": "parse_row( text , strict , sep , header )", "occurrence": 1,
     "remedy": "decompose", "start": 1},
    {"ccn": 2, "crap": 2.5, "end": 23,
     "function": "_split( text , sep )", "occurrence": 1, "remedy": "ok", "start": 20}
  ],
  "file_totals": {"crap_load": 17.98, "functions": 2, "over_target": 1},
  "function": "parse_row( text , strict , sep , header )",
  "gate_rule": {
    "binds": "changed functions only; a ratchet mark pardons standing debt at or under it",
    "ceiling": 6,
    "diff_uncovered_max": 0,
    "mark_age_days": 0,
    "ratchet_mark": 15.4815
  },
  "handle": "parse_row",
  "lane": {
    "artifact": ".crapkit/cov/py.json",
    "command": "python -m pytest -q -p no:cacheprovider --cov=app --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/py-junit.xml",
    "cwd": "", "env": {}, "name": "py", "parser": "coveragepy", "timeout_seconds": 0
  },
  "notes": {"repo": ["app/ is the public seam: no new dependencies below it"], "scope": null},
  "params": [
    {"name": "text", "type": null}, {"name": "strict", "type": null},
    {"name": "sep", "type": null}, {"name": "header", "type": null}
  ],
  "path": "app/parse_csv.py",
  "ratchet_mark": 15.4815,
  "regrowth": {"history": [[1, 11], [2, 7], [3, 11], [4, 11]], "regrown": true},
  "remedy": "decompose",
  "run_id": 4,
  "schema": 1,
  "scored": {
    "ccn": 11, "ccn_mod": 11, "ccn_std": 11, "cognitive": 11, "cov": 0.6666666666666666,
    "crap": 15.481481481481483, "end": 17, "flag": "measured",
    "long_name": "parse_row( text , strict , sep , header )", "nesting": 2,
    "nloc": 17, "occurrence": 1, "params": 4, "path": "app/parse_csv.py", "remedy": "decompose",
    "scope": "app", "start": 1
  },
  "scored_changes": 0,
  "source": "def parse_row(text, strict, sep, header):\n    fields = _split(text, sep)\n    if header and fields and fields[0] == \"id\":\n        return None\n    if strict and len(fields) < 3:\n        raise ValueError(\"short row\")\n    if not strict and not fields:\n        return []\n    out = []\n    for field in fields:\n        if field == \"\":\n            out.append(None)\n        elif field.isdigit():\n            out.append(int(field))\n        else:\n            out.append(field.strip())\n    return out",
  "shallow": false,
  "stale": false,
  "target": 6,
  "uncovered_lines": [6, 8, 12, 14],
  "unmeasured": false,
  "versions": {"analysis_version": 13, "crapkit": "<version>", "lizard": "1.24.0", "python": "3.11.2"}
}
```

### What the session reads

| Key | Type | Nullable | Meaning |
|---|---|---|---|
| `run_id`, `commit` | int, string | no | The scored run and its commit. |
| `path`, `function` | string | no | The resolved function. `function` is always the long name, whichever form you asked with. |
| `handle` | string | no | The short name form for this row: the bare identifier; `NAME#N` in file order when one file gives that long name to several functions, starting at `NAME#1`; the full long name when functions with different signatures share an identifier; or `(anonymous)#N` for a function lizard could not name. Same value and same rules as [`next-item`'s](#item-fields), so a packet and a queue item name one function one way. |
| `remedy` | string | no | `decompose`, `split-lines`, `add-tests` or `ok`. Promoted out of `scored` because it is the branch the session takes; `scored.remedy` carries the same value. Judged against `target`, today's ceiling, as on `next-item`; so is every `remedy` in `file_functions`. |
| `est_splits`, `est_uncovered_paths` | int | no | The budget, from the same code `next-item` publishes it with. Formulas under [`item` fields](#item-fields). |
| `unmeasured` | bool | no | `true` when no measurement stands behind `cov` (`flag` `no-lane` or `cc-only`), so `cov` and `est_uncovered_paths` are stand-ins. Same field, same rule as on [`next-item`](#item-fields). The text form prints `cov not measured` for these rows. |
| `source` | string | no | The function's own text, `start` to `end` inclusive, newlines intact. The packet is editable without a second read of the file. |
| `params` | array of object | no | Its parameters in declaration order, each `{name, type}`: `name` as declared, `type` the annotation as lizard printed it, or `null` when there is none. A new test can call the function without opening the file. `scored.params` is the count of these. It counts a Python signature the way lizard reads it, up to its first `)` at any depth, which keeps the long name, and so the ratchet key, a def has always had: `def defaults(a, bases=(), skip=None)` counts 2. |
| `scored` | object | no | The whole scored row, 17 keys: `scope`, `path`, `long_name`, `start`, `end`, `occurrence`, `ccn`, `ccn_std`, `ccn_mod`, `cognitive`, `nesting`, `nloc`, `params`, `cov`, `flag`, `crap`, `remedy`. `next-item` carries neither `params` nor `ccn_mod`. `ccn_mod` is lizard's modified count (`-m`): a switch and all its `case` arms are one decision. A Rust `match`, a shell `case` and a PowerShell `switch` have no `case` keyword per arm and cost one point per arm in `ccn_mod` as in `ccn_std`. A switch whose only arm is `default` reads one above `ccn_std`, as lizard reads it; `ccn` is the smaller of the two, so the gate never sees that point. |
| `target` | int | no | The scope's effective ceiling: the highest CRAP a function may carry, and so also the highest `ccn`. Same value as `gate_rule.ceiling`. |
| `stale` | bool | no | `true` when `commit` is not HEAD. It judges the commit, not the files; see [`stale` and `scored_changes`](#stale-and-scored_changes-does-the-run-still-describe-the-files). |
| `scored_changes` | int or **null** | **yes** | How many files the run scored hold other content now than the run recorded, as on `next-item`. Not `0` (or `null`, when crapkit cannot compare): run `commands.refresh` before trusting any number here. |
| `shallow` | bool | no | `true` when the checkout is a shallow clone, so `churn` and `gate_rule.mark_age_days` count only the commits the clone holds: in a depth-1 clone every file changed once and every mark is 0 days old. stderr carries one line naming the fix, `set fetch-depth: 0 on the checkout or run git fetch --unshallow`. The numbers keep their values. |
| `file_functions` | array | no | Every scored function in the same file: `function`, `start`, `end`, `occurrence`, `ccn`, `crap`, `remedy`. What an extracted helper lands beside, and what names are already taken. |
| `file_totals` | object | no | That file rolled up: `functions`, `over_target`, `crap_load`. |
| `gate_rule` | object | no | What the gate will judge this edit by. Below. |
| `commands` | object | no | The rest of the loop, filled in for this file and this scope. Below. |
| `lane` | object | **yes** | The lane whose artifact produced `cov` and `uncovered_lines`, as the config declares it, with a `{python}` or `{python:DIR}` token expanded for this OS: `name`, `command`, `artifact`, `parser`, `cwd`, `env`, `timeout_seconds`. A session rerunning the lane by hand needs the cwd and the env as declared; reconstructing them from the command string is how the reruns drift. `null` when no lane covers the scope, which is a `no-lane` row. |
| `versions` | object | no | `{analysis_version, crapkit, lizard, python}`: the tool versions and the metric's own version behind every number in the packet. Marks and scores from two `analysis_version`s are not one series. |
| `notes` | object | no | `{repo, scope}`: the `notes` lines the config carries repo-wide and for this scope, each an array of strings, or `null` where the config declares none. See [configuration.md](configuration.md#crapkit). |
| `attempts` | array | no | Every claim ever taken on this function, oldest first, each `{opened, closed}`: UTC timestamps, `closed` `null` while the claim is still open. `[]` is a first attempt; anything else means a session took it before, and `regrowth.history` says whether its split held. |
| `regrowth` | object | no | `{regrown, history}`. Below. |
| `ratchet_mark` | float | **yes** | `null` when the function carries no mark, and also when the repo has no ratchet file at all. Read under the function's own ratchet key, so twins sharing a long name report their own marks and not each other's. |
| `churn` | object | **yes** | `{commits, authors, weight}`, or `null` when the file has no commits in the window. |
| `coupling` | array | no | Up to 5 partners, each `{path, support, confidence, is_test}`. Empty when nothing clears support 5 and confidence 0.5. |
| `duplication_twins` | array | no | Near-duplicate functions, each with `similarity` and `contained` plus its location. Empty is normal. |
| `uncovered_lines` | array | **yes** | Same null-vs-empty contract as `next-item`. |
| `uncovered_lines_note` | string | conditional | Present only when `uncovered_lines` is null. |

Since 0.4.5 `lane`, `target` and `commands.scoped_tests` all describe one scope: the one
whose declared `paths` entry sits deepest on this file, which is the rule the run scored it
under too. Three readers used to answer that question separately, so a packet on a file under
nested scopes (`src` and `src/web`) could carry the deeper scope's lane and test command
beside the shallower scope's ceiling.

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
| `scoped_tests` | string or null | A `crapkit test-scoped` call for the packet's literal file. It selects the scope's template and executes it from the project root with the inherited environment and literal filename transport. `null` when the scope declares no template; `doctor` warns about the gap. |
| `scoped_tests_note` | string | Present **only** when `scoped_tests` is `null`, naming the scope that declares no template. |
| `verify` | string | The `verify` call. Step 5, the only authoritative one. |
| `refresh` | string | The `coverage --reuse-unchanged` call that refreshes this packet. It reuses a lane only at the same clean HEAD with unchanged configuration, inherited environment and coverage/JUnit bytes, or, for a lane that declares `inputs`, while nothing under those paths, its lane table or its `env` changed and its coverage/JUnit bytes match; otherwise it runs the lane. Run it first when `scored_changes` is not `0` or `stale` is `true`. |
| `refresh_writes_run` | bool | Always `true`. `refresh` appends a scored coverage run to `.crapkit/crap.sqlite`. Other commands can write caches or test artifacts; this field does not promise filesystem read-only execution. |

Each value is a whole command line. Run it as given to preserve filename quoting
and the refresh reuse policy. Simple paths remain readable. When every argument
reads literally inside double quotes in sh, bash, PowerShell and cmd.exe, the
line takes that one form on every OS, so a command written on Linux runs as
printed in cmd.exe. An argument one of those shells rewrites inside double
quotes (`$`, `%`, `!`, a backtick, a backslash, a quote, a line break) takes the
writing OS's form: POSIX shell quoting, or on Windows a form cmd.exe and
PowerShell both read, using an encoded PowerShell command when a filename could
trigger shell expansion.

With a scoped template and without one:

```json
{"gate": "crapkit rescore calc/grade.py --gate",
 "refresh": "crapkit coverage --reuse-unchanged", "refresh_writes_run": true,
 "scoped_tests": "crapkit test-scoped calc/grade.py",
 "verify": "crapkit verify"}

{"gate": "crapkit rescore calc/grade.py --gate",
 "refresh": "crapkit coverage --reuse-unchanged", "refresh_writes_run": true,
 "scoped_tests": null,
 "scoped_tests_note": "no [crapkit.scoped_tests] template for scope 'calc'",
 "verify": "crapkit verify"}
```

All four commands resolve the `crapkit` console script on PATH, including the
Windows encoded form. Activate the intended environment before executing them.
A packet that `uvx crapkit brief` built spells all four `uvx crapkit ...`, and its
encoded form starts `uvx`: uvx puts no `crapkit` on PATH, and the uvx line resolves
in any shell on a machine that has uv.
`test-scoped` then runs the owning scope's configured template; a template with
no `{files}` still runs its declared arguments unchanged.

`refresh` is what `stale: true` and a `scored_changes` above `0` ask for, and the only thing
that answers them. `stale` compares the run's commit against HEAD, so nothing clears it but
a run landing on the current commit, and `scored_changes` falls to `0` only when a run
records the content on disk now. Another `brief` re-reads the same snapshot and reports the
same answers.

### `regrowth`: did this get fixed before?

| Key | Type | Meaning |
|---|---|---|
| `regrown` | bool | `true` when this function's `ccn` fell between two runs in `history` and rose again at any later point. An earlier decomposition did not hold, and repeating it will not either. Coverage plays no part: a function whose `crap` fell because tests arrived has not regrown. |
| `history` | array | One `[run_id, ccn]` pair for every stored run that scored the function, oldest first, whatever the run's kind: an inventory run, a partial run and a refused verify each count. A function one run has seen has one pair. |

### `coupling[]` and `duplication_twins[]`

| Key | Type | Meaning |
|---|---|---|
| `is_test` | bool | On a coupling partner: the path is a test file, by a `test`, `tests` or `__tests__` directory in any case or by a runner's naming convention (`test_x.py`, `x_test.py`, `x_test.go`, `x.test.ts`, `x.spec.ts`), the rule `init` and `doctor` read. A test directory leaves the corpus on its own and the default exclude globs drop those names, so a coupled test does not appear in `file_functions` or the worklist, and it is still the file your edit breaks. |
| `contained` | bool | On a twin: `true` when the twin and the target nest in one file, one defined inside the other. Each is shingled from its own lines, so such a twin shows up only where the enclosing function's own lines copy the nested one's. `duplication --json` drops these pairs. |

### Name resolution

`NAME` takes five forms, all resolving to the same row:

| Form | Example |
|---|---|
| the long name | `"parse_row( text , strict , sep , header )"` |
| the bare identifier | `parse_row` |
| the function's start line | `1` |
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

The start line resolves a function only when that line names one source position.
When several functions start there, the command refuses the numeric selector and lists
their handles. Use the handle to select one. `explain` resolves against the run `brief`
reads, the newest trusted one. When that run dropped the file, `brief` refuses it and
`explain` reads the newest trusted run that still holds it. On a file the newest trusted
run holds, both answer alike:

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
crapkit: no function starts at line 12 in calc/grade.py in the latest scored run - it starts functions at: 1, 24

$ crapkit explain calc/grade.py 12
crapkit: no function matching '12' in calc/grade.py appears in any run
```

The ordinal handle names a function with no name of its own, which every payload prints
as `(anonymous)`. `N` counts the file's anonymous functions from the top, so
`(anonymous)#2` is the second one wherever it has drifted to. That is why `handle` carries
it and not the start line. An ordinal past the end is exit 1 listing the handles the file
does hold:

```
crapkit: no (anonymous)#5 in app/parse_csv.py in the latest scored run - it holds: (anonymous)#1, (anonymous)#2
```

`explain` resolves the handle the same way, against the run `brief` reads.
Note that the store keys a function's identity on its long name, so one file's anonymous
functions share one history there: the handle picks the position, and `explain`'s history
covers every anonymous function in the file.

Twins sharing one long name are one candidate, not an ambiguity: the worst-scoring twin
wins, the same rule the queue ranks on. Anything genuinely ambiguous or absent is exit 1
with the candidates listed:

```
crapkit: no function named 'nope' in calc/grade.py in the latest scored run - it holds: _adjusted, _band, classify, extra, summarize
```

The twin selector picks one of them instead. `NAME#2` is the second function of that name
in file order, `NAME#3` the third, the same ordinals the ratchet keys their marks on, so
`ratchet_mark` in the packet belongs to the function the packet opened. An ordinal past
the last twin is exit 1:

```
crapkit: no __post_init__#5 in calc/iso_cost.py in the latest scored run - it holds 2 function(s) named '__post_init__'
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
  "commands": {"refresh": "crapkit coverage --reuse-unchanged"},
  "commit": "9c7eed1a91d12a4b84b51ecefbbf9e1f5551d216",
  "packets": [{"function": "parse_line( text , strict , sep , header )", "...": "..."},
              {"function": "parse_row( text , strict , sep , header )", "...": "..."}],
  "run_id": 4,
  "schema": 1,
  "scored_changes": 0,
  "shallow": false,
  "stale": false
}
```

| Key | Meaning |
|---|---|
| `packets` | Up to N packets, in `next-item` order (`crap` descending), skipping every function an open claim holds, as `next-item` does. Each one is the object above without `schema`; it keeps its own `run_id`, `commit`, `stale`, `scored_changes` and `shallow`. |
| `run_id`, `commit`, `stale`, `scored_changes`, `shallow` | Repeated on the envelope, because every packet in one call comes from one run and one checkout. The shallow-clone line goes to stderr once per call. |
| `commands` | `{refresh}`, as on `next-item`. |
| `skipped_claimed` | Present only when an open claim hid a queue row: how many it hid, the count `next-item` prints under the same key. Absent, never `0`. |
| `schema` | `1`, as everywhere. |

`--batch` takes no `FILE` or `NAME`: the queue picks the functions. It exists so an
orchestrator pays the store, churn-log and ratchet-file reads once for a whole fleet
instead of once per session, and so every session starts at step 1 with nothing left to
look up. Since 0.4.5 it also shingles the repo once per batch rather than once per packet,
which is what `duplication_twins` costs: a batch of 5 on the 31,459-file corpus the 0.4.5
work was measured against fell from 11.8 s to 5.2 s, output byte-identical. The run's
shingle index now lives in the store: `inventory` and `coverage` write it as they record the
run, the first `brief` or `duplication` on a `verify` run builds and stores it, and every
later packet shingles only its own function. Hand one packet to one session, and see
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
      "authors": 1, "ccn": 7, "ccn_std": 7, "commits": 2, "cov": 0.0, "crap": 56.0,
      "end": 15, "flag": "measured", "function": "render( rows , wide , totals , header )",
      "handle": "render", "nloc": 12, "occurrence": 1, "path": "calc/report.py",
      "ratchet_mark": null, "remedy": "decompose", "risk": 3.5, "scope": "calc", "start": 4,
      "weight": 0.5
    },
    {
      "authors": 1, "ccn": 14, "ccn_std": 14, "commits": 2, "cov": 0.45,
      "crap": 46.60950000000001, "end": 27, "flag": "measured",
      "function": "classify( score , attempts , late , bonus )", "handle": "classify",
      "nloc": 24, "occurrence": 1, "path": "calc/grade.py", "ratchet_mark": null,
      "remedy": "decompose", "risk": 0.0252, "scope": "calc", "start": 4, "weight": 0.0018
    }
  ],
  "active_total": 2,
  "churn_window_months": 12,
  "commands": {"refresh": "crapkit coverage --reuse-unchanged"},
  "commit": "8c14f3daa8e88230c5b702d8f452ee2616d4de30",
  "dormant_count": 0,
  "dormant_top": [],
  "floor": 5,
  "run_id": 1,
  "schema": 1,
  "scored_changes": 0,
  "shallow": false,
  "stale": false
}
```

| Key | Type | Meaning |
|---|---|---|
| `run_id`, `commit` | int, string | The run ranked, and its commit. |
| `stale` | bool | `true` when the run's commit is not HEAD. In plain output this also prints a stderr warning; in JSON it is only this field. |
| `scored_changes` | int or null | How many files the run scored hold other content now than the run recorded; `null` when the run recorded none or git failed reading the tree. In plain output a count above `0` prints a stderr warning naming up to three of the files. See [`stale` and `scored_changes`](#stale-and-scored_changes-does-the-run-still-describe-the-files). |
| `commands` | object | `{refresh}`, as on `next-item`. |
| `shallow` | bool | `true` when the checkout is a shallow clone. `commits`, `authors`, `weight` and `risk` count only the commits the clone holds, so in a depth-1 clone every file reads one commit and the ranking is ccn order. Plain and JSON output both print one stderr line, `warning: churn counts read only the commits this clone holds; this shallow clone does not hold every commit: set fetch-depth: 0 on the checkout or run git fetch --unshallow`, and the Action's comment repeats it above its table. `false` in a full clone. |
| `floor` | int | The effective `worklist_floor`, echoed so a caller need not read the config. |
| `churn_window_months` | int | Same. |
| `active` | array | The queue: files with churn in the window, ranked, capped at `--top` or `worklist_top`. |
| `active_total` | int | Active rows admitted before the cap: what `--top` or `worklist_top` hid. The plain header prints it as `50 of 3980 active (worklist_top 50)`, or `(--top N)` when the flag set the cap. Not the over-ceiling count `trend` and the coverage summary carry: a row is active for its churn, whatever its score. |
| `dormant_count` | int | How many ranked entries have zero churn in the window. |
| `dormant_top` | array | The first 10 dormant entries, same shape. Sleeping hazards, recorded without clogging the queue. |
| `batches` | array | Only with `--batches N`. |

Each entry carries `scope`, `path`, `function`, `start`, `end`, `occurrence`, `ccn`, `ccn_std`, `nloc`,
`commits`, `authors`, `weight`, `risk`, plus `flag`, `remedy`, `crap` and `cov` from the
run that scored it, and `ratchet_mark`: the value of its mark in the marks file in the working
tree, or `null` when the function carries no mark or the repo has no marks file. The mark is read under the
function's own ratchet key, so twins sharing a long name report their own marks and not
each other's. `handle` is the short name form `brief`, `explain` and `claims release` take:
the bare identifier; `NAME#N` in file order when one file gives that long name to several functions, starting at `NAME#1`; the full long name when functions with different signatures share an identifier; or `(anonymous)#N` for a function lizard could not name. A handle is not a ratchet key: the first twin's mark is keyed with no suffix and the second's `long_name#2`, and `ratchet_mark` already reports each twin's own mark. The four run fields are `null` on an inventory-only run, which scored no
verdict. `worklist` still ranks on complexity times churn, never on `crap`: `next-item` is
the queue ordered by score, and `brief` the whole packet.

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
rows the queue will hand you: `no-lane` never, `ok` never, anything else next. Both are
the verdict the run stored, and `next-item` judges `remedy` against the ceiling
`crapkit.toml` holds now. After a ceiling edit no run has scored yet, `next-item`'s
`remedy` decides: lower `target` and it can hand out a row this list calls `ok`; raise it
and a row this list calls `decompose` can leave the queue. The two
payloads on this page come from one run of one repo: `worklist` leads with `render` at
risk 3.5, `next-item` hands out `classify` at crap 46.6. Neither is wrong.

`risk = ccn * weight`, rounded to four decimals. On a repo whose commits share a timestamp
there is no range to weight against, so each commit counts once: every file weighs `1.0`,
the ranking is ccn order, and the hot promotion is off, because a top 10% of equal
weights would be every file.

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
Files go out largest summed `risk` first, a group of co-changing files counting as one, each
to the batch with the least `risk` so far, and on a tie to the one with fewer entries. That is
LPT scheduling (longest processing time first), so the heaviest batch stays within
4/3 - 1/(3N) times the heaviest batch of the best split: 7/6 for two batches. Batches come
highest summed `risk` first, and batches of equal risk in the order of their files.

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

This one is `tests/fixtures/mini_repo` measured once, then verified with a function over the
ceiling appended to `src/app.ts` and not yet committed, so `commit` is the baseline's:

```json
{
  "baseline_commit": "8c780bb18da329dfe039b55d14faa5a6dc9fcb50",
  "baseline_run": 1,
  "changed_files": 1,
  "changed_paths": ["src/app.ts"],
  "commit": "8c780bb18da329dfe039b55d14faa5a6dc9fcb50",
  "committed_findings": 0,
  "counts": {"diff_uncovered_count": 0, "diff_uncovered_max": null},
  "diff_uncovered": [],
  "diff_uncovered_count": 0,
  "diff_uncovered_max": null,
  "dirty_failures": [],
  "dirty_findings": 1,
  "findings": [
    {
      "ccn": 8,
      "cov": 0.0,
      "crap": 72.0,
      "dirty": true,
      "exit_code": 6,
      "fails": true,
      "key_name": "knotty ( n )",
      "kind": "gate_violation",
      "long_name": "knotty ( n )",
      "overridable": true,
      "path": "src/app.ts",
      "remedy": "decompose",
      "rule": "complexity gate",
      "start": 21
    }
  ],
  "forgiven_failures": [],
  "gate_violations": [
    {
      "ccn": 8,
      "cov": 0.0,
      "crap": 72.0,
      "dirty": true,
      "key_name": "knotty ( n )",
      "long_name": "knotty ( n )",
      "path": "src/app.ts",
      "remedy": "decompose",
      "start": 21
    }
  ],
  "lanes_without_baseline_results": [],
  "lanes_without_results": ["unit"],
  "new_failures": [],
  "ok": false,
  "overridden": [],
  "ratchet_changes": null,
  "ratchet_regressions": [],
  "ratchet_sha256": null,
  "ratchet_source": "tree",
  "ratchet_source_commit": null,
  "ratchet_source_sha256": null,
  "retried_passes": [],
  "run_id": 2,
  "schema": 1,
  "tool_versions": {"analysis_version": "13", "crapkit": "<version>", "lizard": "1.24.0"},
  "unmarked_over_target": 1,
  "unread_files": [],
  "unreadable_names": [],
  "untracked_in_scope": []
}
```

### Verdict

| Key | Type | Meaning |
|---|---|---|
| `ok` | bool | The final verdict after allowed overrides and flake retests. It agrees with the stored run verdict and command exit. Remaining findings or an ungranted diff-coverage breach make it `false`. |
| `run_id` | int or null | The run this verify wrote. `null` when verify stopped before any lane ran, on [a scoped file whose name is not UTF-8](#a-scoped-file-whose-name-is-not-utf-8). |
| `baseline_run`, `baseline_commit` | int, string | What it was measured against. |
| `commit` | string | The commit the verified tree is at. Equal to `baseline_commit` when you are verifying uncommitted work. |
| `changed_files` | int | Files in the diff being judged. |
| `unreadable_names` | array of strings | Tracked files no scope takes whose names git gives in bytes that are not UTF-8, left out of the run, each such byte as `\xNN`: the names the `crapkit: left out` lines on stderr give. `[]` when every name is UTF-8. A file a scope takes whose name is not UTF-8 is a finding instead, and stops verify before any lane runs ([below](#a-scoped-file-whose-name-is-not-utf-8)). |
| `changed_paths` | array of strings | Those files, sorted, since 0.8.1. The text form names the first three on a line under the verdict, the files verify scored ahead of the rest, `changed files: app/m.py, app/n.py, tests/test_m.py`, then `and N more`. |
| `untracked_in_scope` | array of strings | Source files inside a scope that git does not track, since 0.8.1. verify's diff and corpus hold git-tracked files only, so these were not judged. The text form warns on stderr, names the first three and says to `git add` them. |

### The findings list

`findings` (since 0.9.0) lists every finding once, one item each. Every item carries the six
fields below, then the fields of its kind. The 0.8.1 per-kind keys under
[Findings](#findings) still print beside it, holding the same entries, until a later release
drops them: read `findings` and `counts`.

| Key | Type | Meaning |
|---|---|---|
| `kind` | string | `unreadable_name`, `gate_violation`, `unread_file`, `ratchet_regression`, `new_failure`, `diff_uncovered` or `overridden`. |
| `fails` | bool | `true` when the item fails the verdict: its kind fires an exit code and the verdict holds it. A `diff_uncovered` item fails only past `diff_uncovered_max`, and an `overridden` one never does. |
| `exit_code` | int or null | The exit code the item fires when it fails; `null` when `fails` is `false`. The first item whose `fails` is `true` names verify's exit, and no item fails at exit 0. |
| `overridable` | bool | `true` for a `gate_violation`, the one kind an `--override` can grant. An override still grants nothing while an item of a kind that refuses one is present (`unreadable_name`, `unread_file`, `ratchet_regression`, `new_failure`), and stderr says why. |
| `dirty` | bool | `true` when the item's file has uncommitted edits or git does not track it; for a `new_failure`, when its test id names such a file. |
| `rule` | string | The label of the item's rule, the words the pull-request comment prints for it. |

| Kind | Its own fields | `exit_code` when it fails | `rule` |
|---|---|---|---|
| `unreadable_name` | `path`, each byte that is not UTF-8 as `\xNN`; `scope`, the scope that takes it; `reason`, the stderr sentence for that one file | 3 | `unreadable name` |
| `gate_violation` | `path`, `long_name`, `start`, `ccn`, `cov`, `crap`, `remedy`, `key_name` | 6 | `complexity gate` |
| `unread_file` | `path`, `reason`: the reader's refusal, naming the line and what to change | 6 | `complexity gate` |
| `ratchet_regression` | `path`, `long_name` (the ratchet key), `recorded`, `fresh_crap` | 7 | `ratchet regressions` |
| `new_failure` | `test`: the `classname::name` test id | 8 | `new test failures` |
| `diff_uncovered` | `path`, `line`: a changed line no test ran | 9, only past `diff_uncovered_max` | `diff-coverage ceiling` |
| `overridden` | the `gate_violation` fields | none: `fails` is `false` and `exit_code` is `null` | `override` |

Items come in this table's order, which is the exit order, and inside a kind in the order
that kind's own list gives them. `diff_uncovered` items appear whenever a changed line ran
in no lane, with `fails` `false` while `diff_uncovered_max` is `null` or not passed.
**`diff_uncovered` items stop at 50; `counts.diff_uncovered_count` does not.**

`counts` holds the numbers beside the items. Each equals the top-level key of the same name.

| Key | Type | Meaning |
|---|---|---|
| `diff_uncovered_count` | int | Every changed line no test ran, where `findings` lists the first 50. |
| `diff_uncovered_max` | int or null | The ceiling `diff_uncovered_count` is judged against (exit 9); `null` when the repo set none. |

### A scoped file whose name is not UTF-8

A tracked file a scope takes whose name git gives in bytes that are not UTF-8 stops verify
before any lane runs: the gate cannot judge such a name, so nothing is measured and no run
is stored. The exit is 3 and stderr carries the one line 0.8.1 printed, naming the first
such file and counting the rest:

```
crapkit: src/caf\xe9.py is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit reads every path as UTF-8; a file a scope takes is refused, not left out, so no gate passes it unread: rename it (git mv) to a UTF-8 name
```

`--json` prints a verify payload, not an error object. `ok` is `false`, `run_id` is `null`,
`findings` holds one `unreadable_name` item per file with `exit_code` 3, and
`counts.diff_uncovered_count` is 0. Each item gives the file's `path`, `scope` and `reason`
beside the six fields every item carries, and no item of another kind is listed.
The other keys hold what verify knew before it stopped: the baseline, `commit` (HEAD),
`tool_versions`, the marks it read and `untracked_in_scope`; `changed_files` is 0, since
no diff was read.
`--sarif` writes one `crapkit/unreadable-name` result per file, level `error`, on line 1,
its `uri` percent-encoding the name's own bytes (`src/caf%E9.py`), and `--github` prints
one `::error` annotation per file naming it `src/caf\xe9.py`. An `--override` grants
nothing and says why on stderr. The fix is `git mv` to a UTF-8 name.

### Findings

| Key | Shape | Fires exit |
|---|---|---|
| `gate_violations` | `{path, long_name, start, ccn, cov, crap, remedy, dirty, key_name}` | 6 |
| `unread_files` | `{path, reason, dirty}`: a changed file no reader could read, so the gate judged none of its functions. `reason` is the reader's refusal, naming the line and what to change | 6 |
| `ratchet_regressions` | `{path, long_name, recorded, fresh_crap, dirty}` | 7 |
| `new_failures` | array of `classname::name` test ids | 8 |
| `diff_uncovered_count` | int, every changed line no test ran, and `diff_uncovered[]` of `{path, line}` | 9, only when `diff_uncovered_max` is set |
| `diff_uncovered_max` | int, or `null` when the repo set none | none itself; it is the ceiling `diff_uncovered_count` is judged against, so a reader of exit 9 can name it |
| `overridden` | gate-violation objects an `--override` granted | none; the run passes |
| `forgiven_failures` | array of test ids the fresh run and the baseline both failed | none; the text form counts them on the OK line as `(N unchanged failures forgiven, first ID)` |
| `retried_passes` | array of new failures that passed their [flake retry](lanes.md#flake-retest) | none; the text form names them on the OK line as `(N new failures passed on rerun, first ID)` |
| `lanes_without_results` | array of lane names that declare no `results_artifact`, so they recorded no test results this run and nothing checked their tests for new failures. A lane that declares one and whose junit `--reuse-artifacts` cannot read is not listed: verify exits 5 naming the lane and the junit, and stores no run | none; stderr names a lane with no `results_artifact` whose command exited nonzero (`warning: lane 'x' exited 1 and declares no results_artifact ...`) |
| `lanes_without_baseline_results` | array of lane names holding a new failure that no trusted run at or behind the baseline recorded a failure list for, so the failure may predate the change | none itself; those failures are `new_failure` items and still fire exit 8, and stderr names each lane |
| `unmarked_over_target` | int: functions over their ceiling that carry no ratchet mark, the standing debt neither the gate (touched functions only) nor the ratchet check (marks only) guards | none; the text form prints one `warning: N function(s) over the ceiling carry no ratchet mark ...` line on stderr when it is not zero, naming the first three as path and function and `ratchet seed` as the fix |

`key_name` on a gate violation is the ratchet key: the `long_name` when one function in
the file holds that name, and `long_name#2` for the second function holding it. It is the
string to look up in `crapkit-ratchet.tsv`, and `long_name` alone is not, whenever a file
gives one name to several functions. A `ratchet_regression` item carries the key in `long_name`
already, because the entry it reports comes from the marks file. Those items list the largest
rise first, and rises equal at 4 places in path order. `gate_violation` items list the highest
`crap` first, and scores equal at 4 places by path, then start line.

**`diff_uncovered` items stop at 50; `counts.diff_uncovered_count` does not.** Above 50 the
two disagree on purpose. Trust the count.

`verify` reports the first of 6, 7, 8, 9 that fires, in that order.

The run a verify stores keeps each lane's `failures` as the lane reported them, first attempt
included. A lane with failures that passed their flake retry also names those ids under
`retried_passes`. A later verify that reads this run as its baseline leaves them out, so it
never forgives them: the baseline did not count them as failing.

Since 0.4.5 the gate pardons a touched function whose fresh CRAP sits at or under its ratchet
mark, the rule `rescore --gate` already applied (#29). So a `gate_violation` item on a
marked function means the edit pushed it past the mark, and one payload can carry that item
and a `ratchet_regression` item for the same function. Exit 6 is the verdict there. Exit 7
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

A baseline lane with no test count or no failure list is compared through the newest
trusted run at or behind the baseline's commit that recorded one, and a stderr line names
that run. A verify run stored by crapkit 0.7.x kept a failure that passed its flake retry
among its `failures`, so its lists are read the same way. A `--baseline-tsv` file carries
the baseline's test results on its stamp line; one written by 0.8.0 or older carries none
and forgives no failure, and verify says so.

### Dirty attribution

A verdict measures the working tree, so a concurrent session's uncommitted edits land in it.

| Key | Meaning |
|---|---|
| `dirty` (on each finding) | The finding's file has uncommitted tracked edits. |
| `committed_findings` | Gate, ratchet, test-failure and breached diff-coverage findings whose file is clean. |
| `dirty_findings` | Findings whose file is not. |
| `dirty_failures` | The test ids of the `new_failure` items whose id names a file with uncommitted edits. The id's file part is matched in each spelling a runner writes: the repo-path form, the same path with backslashes (`web\src\app.test.ts`, as bun's `file` and jest-junit's `{filepath}` write it on Windows), a leading `./` (`./web/src/app.test.ts`, from a runner handed that argument), an absolute path that resolves inside the checkout (jest-junit's `{filepath}` in its absolute form), and pytest's dotted-module form. The id itself keeps the runner's spelling. |

CI should treat any non-zero finding count as a failure. A local pre-push check can
reasonably look at `committed_findings` alone.

### Receipt

| Key | Meaning |
|---|---|
| `tool_versions` | `{"analysis_version", "crapkit", "lizard"}`, all strings (`analysis_version` is `"13"` here, while `doctor --json` and `brief`'s `versions` give the integer 13). Together they are the metric identity behind the numbers. |
| `ratchet_sha256` | Digest of the ratchet file on the tree as read. **`null` when the tree has no ratchet file.** |
| `ratchet_source` | Which marks verify judged against: `"tree"`, the ratchet file as read, or `"committed"`, when that file is missing or blank and verify judged against the newest marks committed since the baseline. |
| `ratchet_source_commit` | The commit whose marks verify judged against when `ratchet_source` is `"committed"`; `null` for `"tree"`. |
| `ratchet_source_sha256` | Digest of the marks verify judged against: equal to `ratchet_sha256` for `"tree"`, the committed file's digest for `"committed"`, `null` when there were no marks at all. Pin it to prove which marks a verdict was measured against. |
| `ratchet_changes` | `{"dropped": N, "tightened": M}` when this run's tighten rewrote the marks file: `dropped` counts marks whose function is now at or under its ceiling, `tightened` marks that fell. **`null` when the tighten wrote nothing**: a failed run, `--no-tighten`, no marks file, or nothing to move. An override's grant is its own write to the marks file and is an `overridden` item in `findings`, not counted here. The text form prints the same two counts on the OK line with the `git add` to run (`restamped` in place of the counts when the only change was the stamp line, `N marks granted` after an override). |

---

## `coverage`

The run summary: corpus size, the five flags counted, the grade, and provenance for every
lane that spoke.

```
$ crapkit coverage --json
```

```json
{
  "by_scope": {"calc": {"crap_load": 124.07, "functions": 3, "grade": "F", "over_target": 2}},
  "cache_hits": 2,
  "cc_only": 0,
  "ceilings": {"default": 6},
  "commit": "9a1d11895c5ff5b791b497a13294494fdab949ce",
  "crap_load": 124.07,
  "db": "/repo/.crapkit/crap.sqlite",
  "empty_scopes": {},
  "excluded": 0,
  "files": 2,
  "functions": 3,
  "grade": "F",
  "kind": "coverage",
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
  "unmeasured_scopes": [],
  "unreadable_names": [],
  "untested": 1
}
```

| Key | Meaning |
|---|---|
| `run_id`, `commit`, `db` | The run written, its commit, and the absolute store path. |
| `files`, `functions` | Corpus size. |
| `cache_hits` | Files served from the content-hash analysis cache. |
| `skipped_max_bytes` | Files dropped by `[exclude] max_file_bytes`. |
| `empty_scopes` | Scope name to its file count, for each declared scope that claims no file or whose every file no reader could read: `0` when its `paths` and `languages` claim no file (a renamed directory, a typo, the wrong language), else the number of files it claims, none of which a reader could read. A scope whose readable files hold no function is not listed: a reader measured each file, and a package of constants has no debt to hide. `{}` when no scope is in either case. Nothing in such a scope counts over the ceiling, so `grade` alone reads as a clean tree; stderr names the scope and what to fix. |
| `unreadable_names` | Tracked files no scope takes whose names git gives in bytes that are not UTF-8, left out of the run, each such byte as `\xNN`. The `crapkit: left out` lines on stderr name the same files. `[]` when every name is UTF-8. |
| `measured`, `untested`, `excluded`, `no_lane`, `cc_only` | The five flags, counted. They sum to `functions`. |
| `over_target` | Functions whose `crap` exceeds their scope ceiling, counted over the measured scopes: on a `partial` run the scopes in `unmeasured_scopes` are left out, since a skipped lane's functions score at cov 0 and would read as this run's debt. On a full run that is every function. The key keeps its name; the ceiling is what the config's `target` sets. |
| `crap_load` | Sum of the CRAP of every function in the scopes this run measured, over or under its ceiling: the same functions `over_target` and `grade` are taken over. Added exactly (`math.fsum`) and rounded to 2 dp; `trend` prints the same number for a run that measured every scope. On a `partial` run a scope in `unmeasured_scopes` carries its load under `by_scope` only: its functions score at the cov-0 stand-in, and summing them put a failed lane's code into this run's load beside 0 over the ceiling. |
| `grade` | The letter for over-ceiling density over the same functions `over_target` counts. `A+` only at exactly zero. |
| `by_scope` | Per scope: `{functions, over_target, crap_load, grade}`. |
| `lanes` | Provenance per lane that succeeded: `artifact_sha256`, the command's `exit_code` (`null` when the artifact was reused), `parser`, `scopes`, plus `results_artifact_sha256`, `failures`, `tests_total` and `tests_skipped` when the lane declares a `results_artifact`. The digests bind coverage and JUnit to the bytes read for this run. Under `--reuse-unchanged` each lane also carries `rerun_reason`: `""` when its artifact was reused, else the sentence its `rerunning:` stderr line gave, such as `the working tree has 1 uncommitted change(s): src/app.ts`. |
| `lane_failures` | Lane name to failure text, for lanes that produced no artifact, or one that reaches none of the paths their scopes declare: measured files outside this checkout (another tree), or absolute paths that resolve under it (this tree, spelled absolutely, which the root-relative join still matches nothing of). Non-empty means the run is typed `partial` and cannot be a baseline. `coverage` exits 5 whenever a lane failed. With at least one lane measured, stdout is this summary with `kind` `partial`. When every lane failed, no run is stored and stdout is the [error object](#errors). The text is plain: escape codes the lane's runner printed are removed, and the lane log keeps them ([lanes.md](lanes.md#the-failure-message-names-its-own-log)). |
| `kind` | `coverage` for a full run, `partial` when a lane was skipped (`--lane`) or failed: the word `runs` lists it under. A partial run is never a baseline. |
| `unmeasured_scopes` | Scopes a declared lane measures that no succeeding lane reached this run, in declaration order; `[]` on a full run. A scope no lane declares at all is not listed: that is a configuration `doctor` names, not this run's shape. |
| `ceilings` | The ceilings in force: `default` (the `[crapkit] target`) and every scope whose own `target` differs from it, `{"default": 6, "reports": 12}`. Scopes at the default are not listed. |

`inventory --json` carries these keys of the summary and no others: `run_id`, `commit`,
`files`, `functions`, `cache_hits`, `skipped_max_bytes`, `empty_scopes`,
`unreadable_names`, `db` and `schema`.

Coverage attribution uses line spans. When distinct functions share the same path,
start line and end line, an artifact that overlaps that span cannot distinguish their
coverage. Every function on such a span scores as `untested` with coverage 0, never the
number a neighbour's measurement carries, and the run continues. It names on stderr how
many spans it met, and the path and line of those holding a function its ceiling fails
at zero coverage, which splitting the definitions onto separate lines measures. Equal
coverage values do not remove the ambiguity. Copies of one function in several scopes
are not a collision. `cc-only`, `no-lane`, and functions with no matching artifact keep
their existing flags.

The plain form prints the same run on one line, zero buckets dropped and the ceiling
labelled, then the command to run next:

```
run 2 @ 9a1d11895c5: 3 functions scored: 2 measured / 1 untested, 2 over ceiling 6, CRAP load 124.07, grade F
-> next: crapkit worklist
```

With a scope at its own ceiling the label reads `over their ceilings (6; reports 12)`. A
partial run opens with `partial run (lane py; web unmeasured; not a baseline)`, a failed
lane named in it as `lane ui failed` and listed after the line as `  lane 'ui' FAILED: ...`,
counts `over` and the grade over the measured scopes only, and ends with `-> rerun changed
lanes: crapkit coverage --reuse-unchanged`. With uncommitted changes in the tree that line
adds ``(the working tree has uncommitted changes, so every lane that lists no `inputs`
reruns)``. When git cannot say whether the tree is clean, the line says so instead and
quotes git's error, since no lane without `inputs` can be reused then either.

---

## `doctor --json`

The only health payload crapkit exposes. It works on a repo that has never run anything.
Captured on a one-lane Python repo after its first `coverage`:

```
$ crapkit doctor --json
```

```json
{
  "analysis_version": 13,
  "lanes": [
    {
      "artifact": ".crapkit/cov/py.json",
      "artifact_present": true,
      "commit": "402d25c96687135d15a5a0d3dc40f571edfa5210",
      "name": "py",
      "refusal": null,
      "seconds": 1.8,
      "toolchain": {"name": "pytest", "source": "command"}
    }
  ],
  "newest_run": {"id": 1, "kind": "coverage", "verdict_ok": null},
  "problems": [],
  "resources": {
    "available_cpus": 24,
    "budget_directory": "/home/dev/.cache/crapkit/workers/1d03af0a77bbda70",
    "coordination": "per-user host primary locks (Windows caller, POSIX guardian) and worker-lifetime companion locks",
    "cpu_probe": "process affinity",
    "default_chunks_per_worker": 4,
    "default_source_bytes_per_worker": 524288,
    "estimated_pool_memory_mb": 840,
    "inherited_analysis_workers": null,
    "log_max_bytes": 16777216,
    "memory_budget_mb": null,
    "memory_is_hard_limit": false,
    "pool_worker_limit": 24,
    "requested_analysis_workers": 0,
    "serial_fallback": true,
    "shared_pool_limit": 24,
    "test_retention_count": 0,
    "test_retention_days": 0,
    "worker_memory_estimate_mb": 35
  },
  "schema": 1,
  "store": {"path": ".crapkit/crap.sqlite", "present": true, "size_bytes": 102400},
  "versions": {"crapkit": "<version>", "lizard": "1.24.0", "python": "3.11.2"},
  "warnings": []
}
```

| Key | Meaning |
|---|---|
| `problems` | The FAIL findings, as text. **Non-empty is exit 1.** |
| `warnings` | The WARN findings as text; exit stays 0. They include unmeasured directories, scopes a lane measures with no `scoped_tests` template, lanes writing their artifacts at the repo root instead of under `.crapkit/`, lanes with no `results_artifact`, a lane whose artifact on disk is the leftover its last attempt failed to replace (the lane's `refusal`, prefixed `lane '<name>': `), a `.crapkit/artifacts.json` crapkit cannot read, a lane whose python is not the one running this doctor, two or more `crapkit` launchers on PATH at different versions, an `[exclude]` glob that matches no tracked file ([configuration](configuration.md#exclude)), a deprecated config key, a marks file with no merge driver set ([ratchet](ratchet.md#the-git-merge-driver)), a container a `coveragepy` lane refuses ([lanes](lanes.md#containers)), marks stamped by another metric version and a tracked package.json doctor cannot read (it then reads the runner of each lane under that file from the lane's command alone). |
| `versions` | crapkit, lizard, python. `lizard` is `null` when it is not importable, which is also a FAIL. |
| `resources` | The worker, memory and log policy doctor resolved for this host, 18 keys, the same policy plain `doctor` prints first. [docs/resources.md](resources.md) says what each budget bounds. |
| `analysis_version` | The analysis semantics version, currently `13`. Together with `lizard` it forms the ratchet's metric stamp. Follow [the upgrade checks](upgrading.md#measure-before-changing-marks) before restamping; changed function identity can require a reviewed mapping. |
| `store` | `.crapkit/crap.sqlite`: whether it exists and how big it is. `present: false` and `size_bytes: 0` on a fresh repo. |
| `newest_run` | `{id, kind, verdict_ok}`, or `null` when nothing has run. `verdict_ok` is `null` for non-verify runs. |
| `lanes` | Per declared lane: `name`, `artifact`, whether the artifact is on disk now, and the `commit` and `seconds` from its stamp. `commit` and `seconds` are `null` for a lane that has never run here. `refusal` (since 0.8.1) is `null`, or the sentence saying why `--reuse-artifacts` will not score the file on disk: the lane's last attempt wrote no artifact, and the file predates that attempt, or `.crapkit/artifacts.json` cannot be read, so crapkit cannot tell whether the file is such a leftover. doctor asks the question `--reuse-artifacts` asks, so both give one answer for a lane. `artifact_present: true` beside a `refusal` is a file reuse will not score, not the lane's output. `toolchain` (since 0.9.0) is `{name, source}`: the runner the lane runs (`pytest`, `vitest`, `jest`, `bun`, `deno`, `cargo llvm-cov`, `go test` or `c8`) and where crapkit read it: `command` (the lane's command names it), `script` (the package.json script the command runs names it) or `package.json` (only devDependencies name it, so no runner check keys on it). Both are `null` when crapkit knows no runner the lane runs, or the lane runs two; no config key names it ([lanes](lanes.md#how-crapkit-reads-a-lanes-runner)). |

`note`-level findings (a file over `max_file_bytes`, no lanes declared, a coverage.py lane
an environment manager heads and doctor therefore did not probe, a lane whose runner is
unknown) appear in the plain output only. They are neither problems nor warnings. The plain
output's runner line per lane (`ok   lane 'py': runs pytest (named in its command)`) is the
same answer as `lanes[].toolchain`.

The `results_artifact` warning is new in 0.4.5 (#26), and it names the two checks the lane
loses rather than the key alone. A repo whose one lane declares neither the artifact nor a
`scoped_tests` template answers:

```json
["lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it; add --junitxml=.crapkit/cov/junit-py.xml to the command and results_artifact = \".crapkit/cov/junit-py.xml\" to the lane",
 "scope 'calc' has a lane but no [crapkit.scoped_tests] template - `crapkit test-scoped` exits 3 on its files, so whoever edits them is handed no command to run their tests; add calc = \"<test command>\" under [crapkit.scoped_tests]"]
```

`crapkit init` writes `--junitxml` and `results_artifact` on the lanes it detects, so this
one fires on a config written by hand or by an older crapkit.

A lane whose first word will not start is a FAIL, not a warning: `doctor` reads the command
with the shell that will run it (sh on POSIX, cmd.exe on Windows), so a quoted interpreter
path is one word and a runner after `&&` is checked too. Each distinct runner is probed
once per directory and environment it starts in, not once per lane.

`doctor --tune` is a different command shape: it prints TOML lines, not JSON, and it
respects neither `--json` nor `--show-files`.

The additive `resources` object in ordinary `doctor --json` reports
`available_cpus`, `cpu_probe`, `requested_analysis_workers`, `shared_pool_limit`,
`pool_worker_limit`, `default_chunks_per_worker`, `default_source_bytes_per_worker`,
`inherited_analysis_workers`, `memory_budget_mb`, `worker_memory_estimate_mb`,
`memory_is_hard_limit`, `estimated_pool_memory_mb`, `budget_directory`,
`coordination` and `serial_fallback`. It also carries `log_max_bytes`.
`test_retention_days` and `test_retention_count` are deprecated and always `0`:
crapkit applies no test evidence retention, and its development runner takes
`--retention-days` and `--retention-count` instead. These fields describe the
effective policy, not sampled utilization. A memory budget is a pool-sizing
estimate, not an operating-system allocation limit.

The two automatic sizing fields describe the active multiprocessing start method:

| Field | Returned value | Meaning |
| --- | --- | --- |
| `default_chunks_per_worker` | `4` for `spawn`, `1` otherwise | Chunks per worker used to calculate the automatic request, rounded up. |
| `default_source_bytes_per_worker` | `524288` for `spawn`, `null` otherwise | With `spawn`, source size can raise the request to one worker per 512 KiB, rounded up. |

Runnable chunks, CPU and configured ceilings, and free slots still cap the pool.
These fields report policy; they are not configuration keys or memory limits.

### `clean --json`

`clean --dry-run --json` previews temporary mutation recovery. Removing
`--dry-run` performs the eligible recoveries. The response has `schema: 1`,
`dry_run`, `test_runs` and `temporary_mutations`.

| Field | Shape |
|---|---|
| `test_runs` | Object with path arrays `removed`, `planned`, `active`, `unproven` and `changed`, always empty. `clean` applies no test evidence retention; crapkit's own development runner does, with `tools/testing/run.py --retention-days N --retention-count N`. The object stays so readers keep every key. |
| `temporary_mutations` | Array of `{path, status, reason}`. Status is `recovered`, `planned`, `active`, `unproven` or `failed`. A failed recovery exits 1. |

Active leases and unrecognized evidence are preserved.
Intentional mutation pools require the existing `mutate --drop-pool` command.

### `doctor --plugin-root PATH`


The plugin and the CLI ship as two artifacts with one version number between them, and
neither notices when they drift. This is the check, and it reads no repo at all.

It compares the plugin's `.claude-plugin/plugin.json` version against **the `crapkit` on
PATH**, and every `--protocol` in its `hooks/hooks.json` against the protocol `claude-hook`
answers, read off a handler's `args` or off its shell-form command string. One line per
disagreement, each naming the command that closes it, and exit 1 when there is one. When
they agree it exits 0 with no such line (a root it found rather than one you typed is still
named first, as `crapkit doctor: checking ROOT`). Two disagreements:

```
$ crapkit doctor --plugin-root crapkit
crapkit doctor: the plugin at crapkit is version 0.3.0, and the crapkit its hooks spawn (/usr/local/bin/crapkit) is <version>. The plugin is behind; update it with `claude plugin marketplace update crapkit`, then `claude plugin update crapkit@crapkit --scope user`, and restart Claude Code's sessions.
crapkit doctor: the plugin at crapkit asks for hook protocol 0; this crapkit answers 1, so `claude-hook` exits 0 silent on every edit. The plugin is behind; update it with `claude plugin marketplace update crapkit`, then `claude plugin update crapkit@crapkit --scope user`, and restart Claude Code's sessions.
```

The version line names the side that is behind and the commands that move it. `claude
plugin install` over an older install prints "already installed" and moves nothing, so the
plugin's repair is Claude Code's update pair, or for a plugin under `~/.codex` (or
`CODEX_HOME`) Codex's `codex plugin marketplace remove crapkit`, then the README's
`codex plugin marketplace add` line at the CLI's release tag, then `codex plugin add
crapkit@crapkit`. The CLI's repair is the upgrade for the installer that owns the launcher:
`uv tool upgrade crapkit`, `pipx upgrade crapkit`, `uv pip install --python <that python>
--upgrade crapkit` in a venv uv made, else `<that python> -m pip install --upgrade crapkit`.
Two plain releases order; a pre-release or a local build names both repairs:

```
crapkit doctor: the plugin at <root> is version 0.9.0, and the crapkit its hooks spawn (/home/you/.local/bin/crapkit) is <version>. The CLI is behind; upgrade it with `uv tool upgrade crapkit`.
```

Claude Code's update runs once per scope `installed_plugins.json` records for the install.
Claude Code keeps one cache directory per version, so a user install and project installs
of one version share it. A project or local install belongs to one project, and
`claude plugin update --scope project` run outside it moves the first project install on
the list, so the line names the directory to run it in:

```
crapkit doctor: the plugin at <root> is version 0.3.0, and the crapkit its hooks spawn (/usr/local/bin/crapkit) is <version>. The plugin is behind; update it with `claude plugin marketplace update crapkit`, then `claude plugin update crapkit@crapkit --scope project` (run in /home/you/app); `claude plugin update crapkit@crapkit --scope user`, and restart Claude Code's sessions.
```

Claude Code loads a plugin from a marketplace added as a local directory in place, and
`claude plugin update` only refreshes the cache copy beside it. For that plugin the repair
is an update of the directory: `git -C <dir> pull` when it is a git checkout, else a copy
of the CLI version's `plugin/` directory over it:

```
crapkit doctor: the plugin at /home/you/crapkit/plugin is version 0.3.0, and the crapkit its hooks spawn (/usr/local/bin/crapkit) is <version>. The plugin is behind; update it with `git -C /home/you/crapkit pull` (Claude Code loads it in place from the local directory marketplace at /home/you/crapkit, and `claude plugin update` does not change it), and restart Claude Code's sessions.
```

Between releases main keeps the release's version string, `claude plugin update` answers
"already at the latest version", and the install keeps the release's files. When the
install and its marketplace's copy (the clone `known_marketplaces.json` names) carry one
version and different files, doctor names the reinstall for each scope that holds the
install, with the project directory for a project or local one, since `claude plugin
install --scope project` writes to the project it runs in:

```
crapkit doctor: the plugin at <root> is version <version>, and so is the marketplace's copy at <clone>/plugin, but 1 file differs between them (skills/crapkit/SKILL.md); `claude plugin update` keeps an install whose version did not move, so reinstall it with `claude plugin uninstall crapkit@crapkit --scope user`, then `claude plugin install crapkit@crapkit --scope user`, and restart Claude Code's sessions.
```

A plugin whose hooks pass `args` also gets a line when the `claude` on PATH is older than
2.1.139, the first release that passes them; a shell-form hook runs as written on any release.

The protocol line orders the protocols the way the version line orders versions: a hook
asking for an older protocol than this CLI answers means the plugin is behind, a newer one
means the CLI is, and the line names the same repair. A hooks file or manifest doctor
cannot read names how the file comes back: the harness's reinstall once per scope that
holds the install (`codex plugin remove crapkit@crapkit`, then `codex plugin add
crapkit@crapkit` for Codex), or for a plugin Claude Code loads in place from a checkout,
`git -C <root> checkout -- <file>`. A directory with no manifest at all is no plugin root,
and its line names the search that finds the installs:

```
crapkit doctor: the plugin at <root> has no readable hooks/hooks.json; reinstall it with `claude plugin uninstall crapkit@crapkit --scope user`, then `claude plugin install crapkit@crapkit --scope user`, and restart Claude Code's sessions before relying on its advisory hook.
crapkit doctor: the plugin at /tmp has no .claude-plugin/plugin.json, so it is no plugin root; name the plugin root or a directory above it, or run `crapkit doctor --plugin-root` with no PATH to check the installs Claude Code and Codex recorded.
```

PATH's `crapkit`, not the module answering the question: `hooks/hooks.json` and `.mcp.json`
both spawn that bare name, so on a machine with a venv crapkit and an older pipx one the
check ran in the first and the hook started the second. The version comes off that
executable's own `--version`, and the line names which executable answered. When PATH
carries no `crapkit` at all, there is nothing to compare and nothing that can start:

```
$ crapkit doctor --plugin-root crapkit
crapkit doctor: FAIL no `crapkit` on PATH - the plugin's hooks/hooks.json and .mcp.json both spawn that bare name, so every PostToolUse edit fires a command that cannot start and the MCP server never comes up. Install it where the PATH the hook inherits can see it (`pipx install crapkit`), or point the plugin at the environment holding it.
```

Exit 1. A `pip install` into a project `.venv` is the usual way to land here: the console
script goes into that venv's `Scripts` and nothing else on the machine sees it.
`pip install --user` is the other: the script goes into `~/.local/bin` or
`%APPDATA%\Python\Python312\Scripts`, which most PATHs lack. When the crapkit running
doctor has its own launcher in such a directory, run by that launcher's full path or as
`python -P -m crapkit`, the line ends by naming it:

```
This crapkit's launcher is in /home/dev/.local/bin, which PATH does not list: add that directory to PATH, then restart the agent.
```

Under `uvx crapkit doctor --plugin-root`, `uv run --with crapkit crapkit doctor
--plugin-root` or `pipx run` the PATH doctor inherits starts with the environments built
for that one command, which the plugin's hooks never inherit, so the lookup leaves out
every environment in uv's cache (any bucket under the root uv tags with `CACHEDIR.TAG`) or
in pipx's, and the FAIL names the one doctor runs in. An environment in uv's cache gets
`uv tool install crapkit`, or `pipx install crapkit` for a `pipx run`: pipx 1.17 on its uv
backend hands the command to `uv tool run`, so nothing in that environment says pipx
started it. One in pipx's own cache (its pip backend) gets `pipx install crapkit`. That
FAIL names no launcher directory to add to PATH, since the tool deletes or rebuilds the
environment that holds it.

A `crapkit` that answers no readable `--version` is a launcher the plugin starts and cannot use,
most often one whose environment lost its python. Each installer's upgrade leaves it
broken, so the FAIL names the reinstall for the install that owns it: `uv tool install
--force crapkit`, `pipx reinstall crapkit`, or pip's `--force-reinstall` for the python
it starts:

```
crapkit doctor: FAIL /home/you/.local/bin/crapkit gave no readable answer to `crapkit --version`. Reinstall the crapkit it belongs to with `uv tool install --force crapkit`, then run this check again.
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
`~/.claude`) and checks every install `installed_plugins.json` records, newest first, each
under its own `checking` line: a user install made at one version and a project install
made at a later one are two cache directories, and sessions run both. With no record on
disk it checks the newest install in Claude Code's cache, then in Codex's plugin cache
(`CODEX_HOME`, else `~/.codex`). A marketplace added
from a local directory is checked in that directory, because Claude Code loads its plugin in
place; the `checking` line says so. When nothing is installed in either, it names both
directories and both harnesses' install lines and exits 1:

```
$ crapkit doctor --plugin-root
crapkit doctor: no installed crapkit plugin under ...\.claude\plugins or ...\.codex. Claude Code installs it with `claude plugin marketplace add JeanFrancoisGagne/crapkit --sparse .claude-plugin plugin`, then `claude plugin install crapkit@crapkit`; Codex with `codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref v0.8.1 --sparse .claude-plugin --sparse plugin`, then `codex plugin add crapkit@crapkit`. For a plugin kept anywhere else, pass --plugin-root PATH.
```

(The absolute path is elided; the line prints it in full.)

A plugin with no manifest gets one line saying so and no protocol check: there is no version
to compare, and the protocol line underneath would bury the fact that explains both. A manifest
that is there but gives no version gets its own line, which names the reinstall for each scope
that holds the install (here a Claude Code user install), so you repair the file rather than
look for one:

```
crapkit doctor: the plugin at PATH has a .claude-plugin/plugin.json that is not a JSON object, so it has no version to compare; reinstall it with `claude plugin uninstall crapkit@crapkit --scope user`, then `claude plugin install crapkit@crapkit --scope user`, and restart Claude Code's sessions.
crapkit doctor: the plugin at PATH has a .claude-plugin/plugin.json with no version string, so it has no version to compare; reinstall it with `claude plugin uninstall crapkit@crapkit --scope user`, then `claude plugin install crapkit@crapkit --scope user`, and restart Claude Code's sessions.
```

The first is a file that does not parse, or parses to a list or a string. The second is an
object whose `version` is absent, null, a number or a list. A plugin shipping no
`hooks/hooks.json` registers no advisory hook, and the output says that instead. It prints no
JSON and ignores `--json`.

`PATH` may be the plugin root itself or any directory above it: `~/.claude`, `~/.claude/plugins`,
the cache root `~/.claude/plugins/cache`, or a marketplace or plugin directory inside it. Claude
Code keeps an install at `cache/<marketplace>/<plugin>/<version>/` and leaves the old version
beside the new one after an update, so among the manifests named `crapkit` under `PATH` the
newest install is the one checked; the other plugins sharing that cache are never read. With no `PATH` at
all, doctor checks every install `installed_plugins.json` records in Claude Code's plugin
directory (`CLAUDE_CONFIG_DIR`, else `~/.claude`), else the newest in that cache, then in
Codex's cache, and names both directories when nothing is installed there. It reads
`installed_plugins.json` as Claude Code writes it today, a list of installs per plugin id,
and as an older Claude Code wrote it, one object per id. An entry of any other shape, or one
with no string `installPath`, records nothing, and the cache scan still finds the install.
A cached version no record names is one an update left behind, and no session runs it.

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
  "shallow": false,
  "uncommitted": 0
}
```

| Key | Meaning |
|---|---|
| `open` | Marks open **on disk**, working tree included. A seed you have not committed counts. |
| `uncommitted` | Marks the working tree and the newest committed version disagree on: added, repaid or tightened but not committed. |
| `dropped_total`, `dropped_last_30d`, `dropped_last_90d` | Repayments, from committed history only. |
| `oldest` | Up to 20 open marks, oldest first, each with `age_days`. |
| `anchor_ts` | Unix seconds of the **newest commit** that touched the ratchet file. Every age and window is measured back from here, never from the wall clock, which is what makes the report deterministic on a fixed history. `0` when no commit has touched the file yet; every age is then 0. |
| `policy_violations` | `null` when no policy was evaluated, `[]` when it ran clean, otherwise the findings. See [ratchet.md](ratchet.md#the-debt-policy). |
| `shallow` | `true` when the checkout is a shallow clone, so every age and repayment counts only the commits the clone holds: in a depth-1 clone every mark is 0 days old and nothing was repaid. stderr carries one line naming the fix. With a debt key set, `--enforce` there prints no report: it exits 4 with the `git` error object. See [ratchet.md](ratchet.md#a-history-the-checkout-does-not-hold). |

A ratchet file renamed with `git mv` keeps its history: the report goes on from the old
name, so `anchor_ts`, every age and every repayment count across the rename, and
`--enforce` judges them as it did before the rename. See
[ratchet.md](ratchet.md#a-history-the-checkout-does-not-hold).

---

## Other payloads

| Command | Shape |
|---|---|
| `runs --json` | `{"runs": [{id, kind, verdict_ok, findings, baseline, commit, lanes[], created_at}]}`. `kind` is `inventory`, `coverage`, `partial`, `verify`, `hook` or `legacy`. Only `coverage`, `legacy` and passing `verify` runs are baseline candidates. `verdict_ok` is `null` on a run that renders no verdict, which is every kind but `verify`. `findings` is how many a verify recorded. `baseline` is true on the one run `verify` compares against today, which is not always the newest candidate: see [the trusted baseline](../README.md#the-trusted-baseline). |
| `runs prune --json` | `{"pruned_runs": 6, "kept_runs": 4, "freed_bytes": 0}`. |
| `trend --json` | `{"runs": [{run_id, commit, created_at, functions, over_target, crap_load, avg, by_scope}], "target": 6}`, trusted runs only. Reads and fills the `run_rollup` cache; see below. |
| `overrides --json` | `{"overrides": [{run_id, commit, created_at, path, function, crap, reason}]}`. |
| `rescore --json` | `{"baseline_run", "baseline_commit", "functions": [{scope, path, function, start, end, occurrence, ccn, cov, flag, crap, remedy, stale_coverage, unmeasured}], "note"}`. Every row carries `stale_coverage: true`: the complexity is the working tree's, the coverage is the baseline run's. `unmeasured: true` marks a row no measurement stands behind: the baseline run holds no row it joins by name (a function added or renamed since that run), or its flag is `no-lane` or `cc-only`. Such a row keeps `cov` 0.0, `flag` `untested` for an added or renamed function, and the `crap` and `remedy` those give; the table prints `-` for its cov and ends the line with `(coverage not measured)`. With `--gate` the payload adds `gate`: `{"ok", "judged", "ceilings": {path: ceiling}, "breaches": [{path, function, start, ccn, cov, crap, remedy, key_name, ceiling}], "untracked": [path], "unread_files": [{path, reason, dirty}]}`. `judged` counts the functions the working tree changed since HEAD (an untracked file in full), `breaches` the judged functions whose `ccn` is over their file's ceiling and that no ratchet mark pardons (a mark pardons only while the function's crap is at or under it), `unread_files` the changed files no reader could read, whose functions were never judged, in the shape of verify's `unread_file` items (`dirty` is always true here: the gate judges the working tree's changes since HEAD), `ok` is `breaches == [] and unread_files == []`, and the exit is 6 when it is false. The text form prints `gate: 2 changed function(s) judged, 0 over ceiling 6` on stdout when the gate passes and the GATE lines on stderr when it does not. |
| `duplication --json` | `{"run_id", "pairs": [{similarity, contained, functions: [{path, long_name, start, end, nloc}, ...]}]}`. Containment scoring: shared shingles over the smaller function. Each function is shingled from its own lines: a nested function's lines past its first line are its own, not the enclosing function's, so a factory never pairs through its closure and one with fewer than `--min-lines` lines of its own pairs with nothing. A pair whose two spans nest in one file is dropped, not ranked: nobody can deduplicate a factory from its own closure. `contained` is therefore `false` on every pair here, and it is emitted so pairs and `duplication_twins` read as one shape. |
| `coupling --json` | `{"window_months", "pairs": [{files: [a, b], support, confidence}]}`. `support` is shared commits, `confidence` is the max-direction ratio. Pairs come highest `support` x `confidence` first, taken over the 4-place `confidence` shown, and pairs that tie come in path order. It reads raw `git log`, so any path in the history can appear, not only scoped source. Ranked pairs are cached; see below. |
| `mutate --json` | `{"mutants", "killed", "survived", "timed_out", "no_verdict", "survivors": [{path, line, op, original, mutated}], "outside_corpus": [path]}`. `mutants` is the count **after** `--max-mutants`; the truncation warning goes to stderr only. `timed_out` counts the mutants whose suite ran out of time: they stay detected, so `killed` includes them. `no_verdict` counts the mutants whose suite collected no tests (pytest exit 5): no test judged them, and they stay inside `killed` too, as in 0.8.0, so `killed` + `survived` is `mutants`. JSON schema 2 leaves them out of `killed` and the rate; schema 1 keeps the field's meaning. `outside_corpus` lists the diff's paths (or `--files`' paths) the scored corpus does not hold, a test file, an excluded path, a file over `max_file_bytes` or a file no scope claims, sorted; they grew no mutants, and a run with `mutants` 0 and a non-empty `outside_corpus` never started the suite. Every worker uses a kept worktree, including one; see [mutation worktrees](configuration.md#mutation-worktrees). |
| `claims --json` | Above. |
| `digest` | **Never JSON.** Plain lines, and silent when nothing changed. |
| `report` | No payload of its own. It writes one self-contained HTML page to `.crapkit/report.html` (or `--out PATH`, repo-relative, or an absolute path you name) and prints that path on stdout, rendering the `worklist` and `trend` payloads above at their defaults. Read those two instead of parsing the page. |
| `explain` | Plain lines by default. `--json` emits the same content as one sorted-keys object with `schema` 1, which is also the `get_function_history` payload. Its keys: `path`, `name`, and `functions[]`, one per matching long name, each with `long_name`; `history[]` of `{run_id, kind, commit, created_at, ccn, cov, crap, flag}`, oldest first; `ratchet_mark`; `ratchet_mark_note`, present only when the repo has no marks file, so a null mark there means no file rather than no mark; `uncovered_lines`, plus `uncovered_lines_note` when it is null; `commits` and `commits_note` under `--history`; `tests` and `tests_note` under `--tests`. That is the score per run, the ratchet mark, and under `--history` the newest 10 commits that touched the function, newest first, each a `{sha, date, subject, body}` object. `subject` and `body` are git's `%s` and `%b` as the commit stores them, control characters and `\r` included; `body` drops only the newlines at either end. `NAME` takes a start line as of 0.4.5, the same form `brief` takes. `--history` reads the span the newest run measured on the working tree, maps it through the uncommitted diff onto HEAD's lines, and asks `git log -L` about those. `commits` is `null` with a `commits_note` when the span holds only uncommitted lines (`pkg/m.py:9-10 holds only uncommitted lines, so no commit has touched it yet`) and when git cannot answer, quoting git's error and ending ``fix what git reports, then run `crapkit explain --history` again``. `--tests` withholds its test ids whenever the file's dark lines are withheld: `tests` is `null` and `tests_note` repeats `uncovered_lines_note`, because the contexts sit on the same stale line numbers. |
| `--version --json` | `{"analysis_version", "commit", "dirty", "schema", "version"}` (since 0.8.1), with the two flags in either order. `version` is the number `crapkit --version` prints. `commit` is the full sha this crapkit was built from: git answers for a source checkout or an editable install, and an installed wheel reads the stamp its build wrote into `crapkit/_build.json` (every build made in a git checkout writes one, and a wheel built from an sdist carries the sdist's). `dirty` is true when that checkout held staged or unstaged edits or a file git neither tracks nor ignores. Both are `null` for a build made with no checkout at hand and for a checkout git cannot read. `analysis_version` is the number `doctor --json` reports and the ratchet's metric stamp carries. The text form is `crapkit X.Y.Z` in a pipe; on a terminal a build that is not a release adds `(commit <sha>, clean)` or `(commit <sha>, dirty)`. |

### Read commands that write

`trend` and `report` are still read commands to their caller, and since 0.4.5 they write to
the store. Both used to re-derive per-run totals from every scored row of every run, twice,
on every invocation: 4.3 M rows on the corpus the 0.4.5 work was measured against, 4.58 s per
`trend`. A run is immutable once written, so its totals are now summed once into a
`run_rollup` table and read back from there: `trend` 4.58 s to 0.04 s warm, `report` down
76%. A prune takes a run's rollup rows with it. The table holds each scope's totals and the
whole run's, each load added exactly from the run's scores, so a run's `trend` row prints
the `crap_load` its `coverage` summary printed when the run measured every scope.

Two consequences for a caller.

- **The rollup write is best effort.** If another process holds the write lock or
  the cache cannot be written, the command still prints the calculated totals.
  Opening an older store can require a schema migration before this cache step.
- **The cache is keyed on the ceiling the totals were decided against**, repo target plus
  per-scope targets. Change a ceiling in `crapkit.toml` and the next `trend` refills under a
  new key rather than reporting the old numbers.

`run_collisions` follows the same pattern for the legacy mark proof. The first reader that
needs a run's same-line collision groups scans that run once and stores them: `worklist`,
`next-item`, `brief`, `verify`, `ratchet seed`, `ratchet prune`, `runs prune`, and the MCP
tools that read marks (`list_worklist`, `get_next_item`, `get_function_brief`). `explain`
and `rescore --gate` prove the few files they read off the path index and fill it only
when they prove more than 64 files. The write is best effort, like the rollup: a locked or
read-only store still answers from the scan. A prune takes a run's collision rows with it.

`brief` writes the run's shingle index, the digests `duplication_twins` is looked up in
(`twin_runs`, `twin_functions` and `twin_postings`). A brief on a run with no stored index
builds it from every scored file and stores it; every later brief, batched or not and in
any process, reads it back and opens only its own function's file. Storing one run's index
drops every older run's, and `runs prune` drops it with its run; on a large consumer repo
one index is 37.8 MB. The write is best effort too: a locked store answers from the index
it built.

### The coupling cache

`coupling`, `brief` and `worklist --batches` rank the same co-change pairs out of the same
window, and each one used to re-cut the churn log and re-count every combination on every
run. Since 0.4.5 the ranked pairs live in `.crapkit/coupling-cache-v2.json` (v1 until
0.8.0), beside `churn-cache-v3.json` and `churn-log-v3.z`. Warm `coupling` on the measured corpus went from
1.05 s to 0.11 s, `worklist --batches` down 62%, a single `brief` down 25%.

What is stored is the ranking at the **default** thresholds, in full order, uncut. `--top`
truncates that order, so it reads the cache. `--min-support` or `--min-confidence` off the
defaults ask a wider question than the file answers and recompute, because serving them a
filtered subset would drop the pairs those thresholds exist to surface. A read ranks the
stored pairs again with the same key the walk sorts by, so a file an older crapkit wrote,
which ranked tied pairs by float noise, comes back in this version's order.

The key is HEAD, the window, the UTC date, the path format, the history depth and a digest
of the tracked set, the churn map's key plus that digest. The window ends at HEAD's commit
date, so the date never changes the pairs. The tracked set is in the key
because ranking drops any pair naming a file `git ls-files` no longer lists, and the index
moves without HEAD: `git rm --cached src/util.py` leaves the sha alone and must still retire
every pair naming that file. Unreadable or unkeyable content reads as cold, never as a crash,
and so does a count or confidence the JSON spells `Infinity` or `NaN`.

The paths are decoded. git spells a non-ASCII name in a log with C-style escapes, and since
0.4.5 all three readers undo that before joining, so a pair names the file `git ls-files`
names rather than a spelling that joins to nothing.

---

## Errors

Under `--json`, a command that dies still prints one object on stdout, so a wrapper reads
the sentence that names the fix instead of an empty stream:

```json
{"error": {"exit": 5, "kind": "tool", "message": "every lane failed (2 of 2); the errors are above"}, "schema": 1}
```

| `exit` | `kind` | Raised when |
|---|---|---|
| 1 | `state` | The store or the tree lacks what the command needs: no run, no scored run, no function matching the name, no open claim. |
| 3 | `config` | `crapkit.toml` is missing, does not parse, or refuses a value; an unknown `--lane` or `--scope` is this too, and so is a file whose name is not UTF-8. |
| 4 | `git` | A git command failed or a commit is missing: no repository, a repository with no commit yet or one git refuses to open, a baseline that is not an ancestor (a rewrite, or a run made on another branch with none behind HEAD), a shallow clone, or `ratchet report --enforce` with a debt key set in a shallow clone, whose history cannot age a mark. |
| 5 | `tool` | A lane or an external tool failed: every lane failed, an artifact the last attempt never wrote, lizard missing. Also a process with no home directory, whose message names the variable to set. |
| 5 | `internal` | An internal check failed before anything was written: a number crapkit computed broke a bound its docs set, such as a CRAP outside `ccn` to `ccn^2 + ccn`. This is a crapkit bug, not a problem in your repo. No run was stored and no ratchet mark changed. Do not retry and do not edit the config: report the message and `crapkit --version` at https://github.com/JeanFrancoisGagne/crapkit/issues. |

`internal` shares exit 5 with `tool`, so a wrapper that reads only the exit code still
fails the job; read `kind` to tell a crapkit bug from a lane that failed. Its stderr
opens with `crapkit stopped: an internal check failed before anything was written.`

`message` is the stderr line without its `crapkit: ` prefix; that line and the exit code
are unchanged. A refusal of a file whose name is not UTF-8 adds `unread_files`, one
`{path, reason, dirty}` object per file, the item shape `rescore --gate --json` and
`verify --json` list unread files in: `path`, each byte that is not UTF-8 spelled `\xNN`;
`reason`, which says to rename it with `git mv`; and `dirty`, true when the file has
uncommitted edits or git does not track it (verify's meaning; a name the index holds and
the working tree lacks, as every such name in a Git for Windows checkout is, is an
uncommitted deletion). The refusals that add it: a file argument naming such a file on
disk (`rescore`, `rescore --gate`, `brief`, `explain`, `mutate --files`, `claims
release`, `ratchet move`), and a scan meeting such a name a scope takes (`inventory`,
`coverage`, `doctor`). `verify` meeting one prints its own payload instead, a finding of
kind `unreadable_name` ([verify](#a-scoped-file-whose-name-is-not-utf-8)). The stderr line
names the first file and counts the rest; `unread_files` lists every one:

```json
{"error": {"exit": 3, "kind": "config", "message": "src/caf\\xe9.py (and 1 more) is in scope 'src', but git names it in bytes that are not UTF-8 and crapkit reads every path as UTF-8; a file a scope takes is refused, not left out, so no gate passes it unread: rename it (git mv) to a UTF-8 name", "unread_files": [{"dirty": false, "path": "src/caf\\xe9.py", "reason": "its name is not UTF-8, and crapkit reads every path as UTF-8: rename it (git mv) to a UTF-8 name"}, {"dirty": true, "path": "src/o\\x92brien.py", "reason": "its name is not UTF-8, and crapkit reads every path as UTF-8: rename it (git mv) to a UTF-8 name"}]}, "schema": 1}
```

Verdict exits are not errors: `verify`'s 6 to 9 and `rescore --gate`'s 6
print their own payloads, with the verdict inside. Without `--json`, stdout stays empty
on an error.

Text copied out of git's history is never an error. `explain --history --json` and the
`get_function_history` tool list each commit that touched the function as `commits[]`,
with `sha`, `date`, `subject` and `body`. A subject or body a commit stored in bytes that
are not UTF-8 (a Latin-1 message with no encoding header) arrives with each such byte as
U+FFFD, so compare a subject by equality only when you know its commit was written in
UTF-8. One stored as UTF-8 arrives as stored, whatever the repo's `i18n.commitEncoding`
or `i18n.logOutputEncoding` says. Churn's author count reads names the same way.

---

## `claude-hook`

The one command on this page your agent runs for you: Claude Code runs it after every Edit
or Write, and so do Cursor, GitHub Copilot CLI and VS Code wherever they load the plugin,
plus after every Bash command wherever you register that matcher. It names functions that
edit pushed over their ceiling, while the session can still act on it.

```
crapkit claude-hook --protocol 1
```

**In:** one PostToolUse event, as JSON on stdin, in the shape the harness sends it.
**Out:** for Claude Code, the advisory on stderr and nothing on stdout, ever, because Claude
Code parses stdout JSON on exit 0. For Cursor, Copilot CLI and VS Code, the same lines as one
JSON object on stdout ([Other harnesses](#other-harnesses)). There is no `--repo`: the root
is the first `crapkit.toml` above the edited file. The plugin registers it async with a
20-second timeout, so no Claude Code edit waits on it.

| Exit | Means | Output |
|---|---|---|
| `0` | nothing to say | stdout and stderr both empty |
| `2` | a changed function is over its ceiling, or a changed file went unjudged, in Claude Code | three or more lines on stderr, which reach the model |
| `0` | the same, in Cursor, Copilot CLI or VS Code | one line of JSON on stdout carrying those lines; stderr empty |

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

A file no reader could read (a TypeScript arrow the reader refuses, a Python def cut off at
its signature) scores as zero functions, and the commit gate refuses it once staged. The
advisory names it instead of reading zero functions as zero breaches:

```
crapkit advisory: src/a.ts could not be read, so no function in it was judged (the edit landed; nothing was blocked)
  UNREAD  src/a.ts: lizard failed on src/a.ts: src/a.ts:1: expression-arrow body has '<' before a comma; lizard cannot distinguish type arguments from an expression separator here; wrap that arrow body in parentheses or a block
the commit gate refuses this file once staged; change what the reason names so a reader can parse the file, or list it under [exclude] globs in crapkit.toml to leave it ungated
```

A tracked file the edit left unchanged against `HEAD` stays silent, as the commit gate
passes an unread file nobody staged.

### Other harnesses

The plugin's hook is one shell command, the one handler field every harness that loads Claude
Code plugins keeps, so Cursor (which imports them), GitHub Copilot CLI and VS Code run it as
written. Each names the edited file its own way and reads exit 2 its own way:

| Harness | The event, and where it names the file | The advisory |
|---|---|---|
| Claude Code | `PostToolUse`, `tool_input.file_path` | stderr and exit 2; `asyncRewake` wakes the model with it |
| GitHub Copilot CLI | `PostToolUse`, `tool_input.path` | JSON on exit 0: Copilot shows exit 2's stderr to the user and never to the model |
| Cursor | `postToolUse`, `tool_input.file_path` | JSON on exit 0: Cursor reads exit 2 as a deny |
| VS Code | `PostToolUse`, `tool_input.filePath`, each `replacements[].filePath`, or the file lines of an `apply_patch` | JSON on exit 0: VS Code reads exit 2 as a blocking error |
| Codex | none: the plugin's Codex manifest registers no hook, since Codex reports an edit as `apply_patch` patch text | none |

The JSON carries the advisory under both keys those three read, top-level `additionalContext`
for Copilot CLI and Cursor, and the nested one for VS Code:

```json
{"additionalContext": "crapkit advisory: 1 function(s) over ceiling 6 in app/m.py (the edit landed; nothing was blocked)\n  ccn 7  app/m.py:1  route( a , b , c , d )\nthe commit gate enforces this; decompose there or mark the debt", "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "..."}}
```

VS Code runs a plugin's hooks on every tool call and ignores the `Edit|Write` matcher, so the
hook applies it there: only VS Code's tools that write a file are judged (`create_file`,
`replace_string_in_file`, `insert_edit_into_file`, `multi_replace_string_in_file`,
`apply_patch`), and a read or a terminal call beside a breaching file stays silent.

Every harness starts it on every edit, whatever the file type. An edit to a file whose suffix
crapkit does not measure (see [Languages](https://github.com/JeanFrancoisGagne/crapkit#languages))
stops at that check, before any config is read.

It judges the functions the edit touched, not the whole file. Judging the file would fire on
every edit in a repo with seeded debt and say nothing new. An untracked file is one
exception: `git diff` can see none of it, so every function in it counts. A file staged
before the repo's first commit is the other: with no commit to diff against, every function
in it is new.

Since 0.8.1 an edit the hook could not judge exits 2 too, in the same three-line shape. A
changed file no reader could read used to score as zero functions, and zero records read as
zero breaches, so a ccn-8 function beside one construct the reader refused passed in
silence. The head line now says `calc/grade.py could not be read, so no function in it was
judged`, the second line is the commit gate's `UNREAD` line with the reader's reason, and the
third says the gate refuses the file once staged and what to change. When
HEAD resolves and git still fails, as with a corrupt index, the head line says `git could
not report what changed in calc/grade.py`, the second line quotes git's own words (`git diff
HEAD -- calc/grade.py: fatal: .git/index: index file smaller than expected`), and no function
is listed: a failed read no longer passes for an untracked file. A file the edit left as
HEAD has it stays silent, readable or not, as the commit gate never judges an untouched file.
A machine with no git at all stays on the silence ladder.

That diff runs root-relative since 0.4.5, the way every other git spawn crapkit makes does,
so a `crapkit.toml` below the git top gets advisories on the paths the commit gate will
judge.

A `Bash` event names no `file_path` (its `tool_input` carries the `command`), so it takes a
working-tree fallback instead: the `*.py` files git reports dirty or untracked, whose mtime
falls inside a **12-second** freshness window, at most **25** of them, each judged through
the same per-file ladder. Each breaching file gets its own advisory block, so one Bash event can print
several; the exit is 2 when any of them breached. That is what catches source written through a shell heredoc or
`python - <<'PY'`, which some harness modes use for every write.

Each bound has its own reason. The window keeps a later `ls` from re-advising a file that was
already dirty before this command ran. The cap is there because PostToolUse waits this
process out, so a large dirty tree would be a stall rather than a reason to judge all of it.

The window judges an mtime, and a touch, a same-bytes rewrite or a test run right after an
Edit moves one with no new content. So since 0.8.1 the hook also remembers what it judged:
each judgement records the sha256 of the bytes it read, per session and per file, under
`<git dir>/crapkit/claude-hook/<session_id>/`, and a fresh file whose bytes match that record
is skipped. An advisory is said once per content per session. A payload with no usable
`session_id` gets no memory and judges as before; a session idle for seven days is pruned
when another starts. The working tree stays byte-identical. Source that lands with an old
mtime is never judged here: a command that ran longer than the window, `cp -p`, `mv`, an
unpacked archive. That is the documented miss, and the commit gate catches it.
And only Python is judged, because every other language stays the commit gate's business,
which is what keeps the fallback cheap enough to pay per shell call. The status read is
`git status --porcelain -z -uall`, so a heredoc that creates a whole new directory of source
arrives as its files rather than as one collapsed `?? newdir/` row.

The shipped plugin registers `Edit|Write` only. A `Bash` matcher is the consumer's choice: a
second entry in your own settings hooks, same command, matcher `Bash`. The fallback answers
the shell tool by name, `Bash` (Cursor maps that matcher onto its `Shell`), so a VS Code
terminal call, which reaches the hook whatever the matcher says, never scans the tree.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "crapkit claude-hook --protocol 1", "timeout": 20}
        ]
      }
    ]
  }
}
```

It costs one `git rev-parse` and one `git status` per shell call in any git repo, measured or
not, which is why it is not the default. Add it when the harness writes source through the
shell; skip it when every write arrives as an `Edit`.

### The silence ladder

Five rungs, each exiting 0 with both streams empty. Any uncaught exception does the same.

| Rung | Silent when |
|---|---|
| protocol | `--protocol` is anything but `1` |
| event | stdin is not one JSON object, or not a `PostToolUse` (`postToolUse` from Cursor) naming the file it wrote or a shell tool's `tool_input.command`, or the file it names has a suffix crapkit does not measure |
| repo | no `crapkit.toml` above the edited file; the walk up stops at any `.git` entry, so a worktree never borrows its parent's config. On a `Bash` event: no git repo above the command's `cwd`, or no changed `*.py` fresh enough to judge |
| git state | mid-rebase, mid-merge or mid-cherry-pick |
| verdict | no scope claims the file, a reader read it and found no functions, no changed function is over the ceiling, or every one that is carries a ratchet mark |

Since 0.4.7 the protocol rung is checked first, ahead of the event shape and ahead of every
git call, so a payload for a protocol this CLI does not answer costs nothing but the read of
stdin. No outcome moved with it. The payload is read before any rung, which is why stdin
that is not one JSON object sits on the event row.

Silence is the design. PostToolUse renders every nonzero exit but 2 invisible, and 47.5% of
the edits this was measured against land in repos with no `crapkit.toml`. A hook that fired
there would be either useless or unbearable.

The hook pardons on a mark's existence, not the numeric rule `verify` applies: the store is never
opened, so the hook holds no CRAP to compare. Same rule as the commit gate, described in
[ratchet.md](ratchet.md#the-commit-gate-skips-marked-functions).

An unknown `claude-*` subcommand exits 0 silently too, so a plugin newer than the installed
CLI degrades to silence instead of an argparse usage dump on every edit.

An argument `claude-hook` does not define, such as a flag a newer plugin passes, also exits
0, with one line on stderr that names the arguments as typed. The edit is not judged,
because this build cannot know what the new flag asks for:

```
$ crapkit claude-hook --protocol 1 --budget 5
crapkit claude-hook: this crapkit does not know `--budget 5`; the hook was written for a newer crapkit, so this edit went unchecked. Upgrade crapkit, then run `crapkit doctor --plugin-root`
```

The flags this build knows, `--protocol` included, are still read. Only an exit 2 hands a
PostToolUse hook's stderr to the model, so the line stays out of the model's context. Any
other subcommand still answers an unknown flag with argparse's usage error.

---

## MCP server

The server reads cancellation and control messages while a tool is running.
Each connection admits one active tool call. An overlapping tool call returns
`isError: true` with a message to retry after the active call finishes.
Cancelling a request stops its owned CLI descendants. Closing stdin ends the
session and stops active work, so clients must keep stdin open until they have
read the replies they need. See [resource policies](resources.md) for process
ownership, pool limits and cleanup scope.

```
crapkit mcp
```

A dependency-free stdio MCP server: JSON-RPC 2.0, one message per line. The handshake
negotiates the protocol revision: a client's offer of `2025-06-18`, `2025-03-26` or
`2024-11-05` is spoken verbatim, and anything else gets `2025-06-18`, the newest this
server implements. Read-only, and declared so: every tool carries `readOnlyHint`,
`idempotentHint` and `destructiveHint: false` annotations, a `title` and, for a
`2025-06-18` client, an `outputSchema` whose fields are described one by one, `initialize` returns `instructions` saying which
tools need `crapkit init` alone, a snapshot store or a coverage run, and naming the four tools a session starts with, and a tool whose text
is a JSON object also carries it parsed as `structuredContent`. Every tool shells to the CLI's own surface, so the MCP
view cannot drift from what the CLI reports, and nothing here writes a baseline, a
ratchet, or a mutant.

The CLI child stream is UTF-8, including non-ASCII filenames and error messages,
on Windows and POSIX. Tool calls can populate disposable caches, open or migrate
the snapshot store, and fill best-effort rollups. Read-only annotations describe
the measurement and debt operations exposed, not a promise of zero filesystem
writes. `get_next_item` takes no claim; `check_gate` runs `rescore` and records no
verification run.

Answering those calls from one long-lived process instead was measured for 0.4.5 and
rejected. A kept process serves a `source` the session has already edited, and a packet whose
`source` is stale is a packet nobody can edit from.

With no `--repo`, the server serves the nearest `crapkit.toml` at or above the directory the
client started it in ([ADR 0002](adr/0002-configuration-is-found-upward-nearest-wins.md)),
so a server started in a monorepo workspace serves the root configuration that claims the
workspace; a tool's `repo` argument is walked the same way, and a `.git` entry without a
configuration stops the walk. A given `--repo` names an exact root, as on every
subcommand: the server serves that directory or refuses it with `no crapkit.toml in
<dir>`, and none of the rules below replaces it. Each tool's command runs at the root the
server found, so `path` stays repo-relative wherever the server was started.

Not every client starts the server in the workspace. VS Code starts a server from the
user profile's `mcp.json` in the home directory and a plugin's server in the plugin's
directory, and GitHub Copilot CLI starts a plugin's server in
`~/.copilot/installed-plugins/<marketplace>/<plugin>`. Three rules cover them:

- A start directory at or below the plugin directory the client names in `PLUGIN_ROOT`,
  `COPILOT_PLUGIN_ROOT` or `CLAUDE_PLUGIN_ROOT` serves nothing, and the server does not
  walk up from it. A plugin loaded from a crapkit checkout would otherwise find crapkit's
  own `crapkit.toml` above it and serve crapkit's repo.
- When the start directory serves nothing and the client declares the `roots`
  capability, the server asks it for `roots/list` once the client sends
  `notifications/initialized`, and again after `notifications/roots/list_changed`. It
  serves the first workspace folder a `crapkit.toml` at or above it claims. A call that
  arrives before the answer waits for it, up to 10 seconds. VS Code answers with the open
  folders.
- After the client's folders, the server serves the folder its GitHub Copilot CLI session
  works in. Copilot CLI declares no roots, and it moves a plugin server's `cwd` back into
  the plugin's install directory when the plugin's config names one outside it, so nothing
  the client sends names the workspace. It gives every MCP server
  `COPILOT_AGENT_SESSION_ID`, and the session keeps its working directory as `cwd:` in
  `session-state/<id>/workspace.yaml` under `COPILOT_HOME` (`~/.copilot` by default). The
  server reads it at each call and walks up from it, so a session moved with `/cwd` is
  followed.

When no session record names a folder either (a Copilot CLI older than the
`COPILOT_AGENT_SESSION_ID` variable, or a config directory set with the deprecated
`--config-dir` instead of `COPILOT_HOME`), the `initialize` instructions and each tool
result ask the model to pass the workspace's absolute path as the tool's `repo` argument.
Copilot CLI 1.0.88 leaves a server's instructions out of the model's prompt in a
`copilot -p` session, so there the tool result is the text the model reads:

```
this crapkit MCP server started in /home/me/.copilot/installed-plugins/crapkit/crapkit, the plugin's install directory, not in your workspace, and the client names no workspace folders. Pass this tool a `repo` argument with the absolute path of the repo you want scored.
```

Where nothing claims the start directory or any folder, the server still starts and
answers `initialize` and `tools/list`. Each `tools/call` there comes
back as a tool result, not a JSON-RPC error, and that result carries `isError: true` with
text naming the missing config and `crapkit init`. When the client named folders, the
text names them after the start directory
(`no crapkit.toml in /home/me or in the workspace folders the client named (/home/me/notes) - nothing measured here. ...`),
and a Copilot CLI plugin's server names the session's folder in place of its install
directory
(`no crapkit.toml in the folder the GitHub Copilot CLI session works in (/home/me/notes) - nothing measured here. ...`):

```json
{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "no crapkit.toml in .../noconfig - nothing measured here. Run `crapkit init` in the repo you want scored, or pass this tool a `repo` argument (or start the server with --repo) pointing at one."}], "isError": true}}
```

Both halves are deliberate. The result keeps the client's session alive, so a global
registration never turns into a dead server in unmeasured repos. `isError` stays true so
nothing reads an unmeasured directory as a repo with nothing to report.

Client wiring: each agent reads its own file, key and fields, and
[Wiring crapkit into your agent](harnesses.md) gives the block for each of 27, with where it
starts the server and what environment it passes. This is the `mcpServers` form:

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

It pastes as it is into Claude Code's `.mcp.json`, Cursor, Kiro, Junie and oh-my-pi; Cline
takes it with `"timeout": 60` added, and Gemini CLI and Qwen Code with `"trust": true`,
without which a headless `gemini -p` or `qwen -p` cannot call the tools. OpenCode, Amp and VS Code's
`.vscode/mcp.json` read other keys and ignore this block without an error, so take theirs
from the page above.

A client that expands variables in this file can pass one, such as Cursor's
`${workspaceFolder}`. A client that does not passes the variable itself: the Cursor agent
CLI expands only `${NAME}` and `${env:NAME}`. A `--repo` that still holds `${...}` is
ignored, the server serves as if none was given, and it says so on stderr:

    crapkit mcp: --repo '${workspaceFolder}' holds a variable the MCP client did not expand; serving the crapkit.toml at or above the directory the client started this server in (/home/me/repo) instead. Give --repo an absolute path, or drop it from the client's config.

Every tool also accepts a `repo` argument that overrides the server's default, so one server
can serve several checkouts.

| Tool | Arguments | Returns |
|---|---|---|
| `list_worklist` | `top` (int), `scope` (array of strings: one declared scope name per element, each becoming its own `--scope`) | JSON text |
| `list_runs` | | JSON text |
| `get_trend` | | JSON text (`trend --json`: per-run totals, oldest first) |
| `get_function_brief` | `path`, `name` | JSON text |
| `list_coupled_files` | `min_support`, `min_confidence` | JSON text |
| `list_duplicate_functions` | `similarity` | JSON text |
| `get_ratchet_report` | | JSON text |
| `list_claims` | | JSON text (`claims list --json`: the open claims) |
| `get_function_history` | `path`, `name`, `history` (bool: adds `commits` per function, the CLI's `--history`), `tests` (bool: adds `tests`, the CLI's `--tests`) | JSON text |
| `check_config` | | JSON text (the `doctor --json` report) |
| `get_next_item` | `top` (int), `exclude` (array of strings: one fragment per element, each becoming its own `--exclude`, so a path fragment reads in any spelling `next-item --exclude` reads: `./pkg/legacy` is `pkg/legacy`, `pkg\legacy` is `pkg/legacy` on a Windows server, and `PKG/Legacy` is `pkg/legacy` where the disk ignores case), `scope` (array of strings, as on `list_worklist`) | JSON text |
| `check_gate` | `path` (repo-relative source file, or absolute inside the repo, in any spelling the [CLI path rules](configuration.md#file-paths-and-root-discovery) read; outside the repo or missing is a config error, and an unchanged or unscoped file judges 0) | JSON text: `rescore PATH --gate --json`, whose `gate` block says whether the edited file clears `rescore --gate`'s rule (`ok`, `judged`, `ceilings`, `breaches`, `untracked`, `unread_files`). A ratchet mark pardons a changed function only while its crap sits at or under the mark, which is stricter than the pre-commit hook, where any mark pardons; the marks file is read only when a changed function breached, so a clean gate never reports a marks file it cannot parse. A breach exits 6 and answers as a result with `gate.ok` false, not a tool error. A `path` naming a file a scope takes whose name is not UTF-8 is refused before any judging, as every gate refuses it: the answer is a result with `gate.ok` false, `judged` 0 and the file in `gate.unread_files`, each item a `path`, the `reason` that says to rename it, and `dirty` true. The server decides it without starting the CLI, and the result carries `baseline_run`, `baseline_commit` and `note` like every other verdict. Such a name no scope takes judges 0 with `gate.ok` true, as any unscoped file does |

Results arrive as MCP text content, and every tool's text is the payload of the CLI's
`--json` form: parse it, or read `structuredContent`, which carries the same object parsed
whenever the call exited 0 and the client negotiated `2025-06-18`, the revision that defines
the field. A client on `2024-11-05` or `2025-03-26` gets the text alone, and `tools/list`
lists no `outputSchema` to it: a client that holds a result to a listed schema, such as one
on the TypeScript SDK 1.12, would find no `structuredContent` to check.

One answer is 7,500 characters or shorter, counted as the text takes them inside a client's
JSON of the result. Cline keeps 8,000 characters of that JSON and cuts the middle out, and a
brief on a 300-line function ran to 15 KB and more. A longer answer loses the end of its
list fields, largest first, then of its string fields, such as a brief's `source`, then
of its objects, the deepest first; a field inside an object counts as much as one at the
top, so `check_gate` cuts `gate.breaches` and keeps `gate.ok`. Each cut field keeps its
start, a list's elements are kept or dropped whole, and the text and `structuredContent`
stay the same object. It then carries `truncated`: `fields` names each cut field by its
keys joined with dots and gives what it `kept` and what it had (`of`), elements for a
list, entries for an object and characters for a string, and `full` is the CLI command
that prints the whole answer:

```json
"truncated": {"fields": {"active": {"kept": 19, "of": 50}}, "full": "crapkit worklist \"--top=50\" \"--repo=/home/me/app\" --json"}
"truncated": {"fields": {"functions": {"kept": 0, "of": 61}, "gate.breaches": {"kept": 34, "of": 60}}, "full": "crapkit rescore --gate \"--repo=/home/me/app\" --json -- calc/big.py"}
```

A field shorter than 500 characters, such as a path or a commit, is never cut. A failing
`check_config`'s report is cut the same way and stays a tool error with no
`structuredContent`.
`isError` is true whenever the underlying CLI call exited
non-zero, and then the text is what the CLI printed: for `doctor` that is still the JSON
report (it exits 1 on any FAIL, so a failing `doctor` answers JSON text with `isError: true`
and no `structuredContent`); for the other `--json` tools it is the [error object](#errors)
the CLI prints, `{"error": {"exit", "kind", "message"}, "schema": 1}`, whose `message` is
the stderr line; `get_next_item`, which has no `--json` flag, answers the stderr line itself.
`check_gate` is the one exception to the exit rule: its exit 6 is the verdict, so a breach answers
`isError: false` with `structuredContent` attached and `gate.ok` false, while exits 3, 4 and 5
(and 1, no scored run yet) stay tool errors. The one exit 3 it answers as a verdict is the
refusal of a file a scope takes whose name is not UTF-8: the CLI's error object lists it in
`unread_files`, and `check_gate` returns `gate.ok` false with that list. The server reaches
that verdict itself and never puts the name on the CLI's command line, where Windows would
hand the CLI U+FFFD in place of each byte that is not UTF-8. `get_function_brief` and
`get_function_history` answer such a `path` the same way the CLI's `brief` and `explain`
do, with the exit-3 error object and its `unread_files` (`isError: true`), which the server
also builds without starting the CLI. `isError` is also
true in the cases where no CLI call runs at all: the missing-config result above, an
unknown tool name, and an argument the tool's own table refuses. A 0.5.x tool name is
unknown too, and its answer names the tool 0.6.0 renamed it to:
`unknown tool 'worklist': renamed list_worklist in 0.6.0, with the same arguments and
result; call list_worklist`. The last case is an upgrade under a running server. Each
call first reads the version in the package directory the server was imported from, and
when `pip install -U` has replaced it, every call answers the restart instead of loading
the new release's files into the old process. The check lives in the running server, so
it starts with a 0.8.1 server; on an upgrade from 0.8.1 to 0.8.2 it reads
`crapkit was upgraded from 0.8.1 to 0.8.2 while this MCP server ran, and the server still
runs 0.8.1's code, which cannot load the new files. Restart the crapkit MCP server
(reconnect it in your client, or start a new session), then call list_runs again.`
A 0.8.0 server does not check, and its first call after the upgrade can fail with a
JSON-RPC `-32603` error instead; the restart fixes that too.

Tool text is plain whatever colour variables the client sets. The CLI runs with the
server's environment, so under `FORCE_COLOR` or `PYTHON_COLORS=1` a Python 3.13 or later
traceback, or a 3.14 usage error, comes out of the CLI coloured; the server removes the
escape codes from the stderr it relays, and the JSON a tool prints on stdout carries none.

Arguments are checked against the served schema before anything is spawned. `tools/list`
declares `required` from each tool's positionals (`get_function_brief` and
`get_function_history` require `path` and `name`). A missing positional answers
`get_function_brief needs name (see inputSchema.required)`, an undeclared key answers
`list_worklist does not take 'bogus'; accepted: repo, top, scope`, and a wrong type answers
`top must be an integer (got "three")`. A string that holds U+0000, or an array item that
does, answers `path must not hold a NUL character (U+0000)` with the argument's own name:
no file name or process argument can carry one, and before 0.8.1 it answered `-32603`.
Arguments that are not an object, by-position ones included, answer with the JSON type
they came as,
`arguments must be an object (got a number)`: MCP takes them by name. Only null or absent
`arguments` read as none given; an empty string, `0`, `false` and `[]` get the same
refusal, such as `arguments must be an object (got a boolean)`. The refusal names the
MCP tool and the argument as the schema spells them, never the CLI command behind the tool.
One undeclared key is not refused: `wait_for_previous`, which Gemini CLI adds to every
tool's schema for its own scheduler and forwards with the call. The server drops it and
runs the call as it would without it. Each is a tool result with
`isError: true` in the tool's own vocabulary, not the protocol's `-32602` error, following
the precedent the missing-config answer set; the reason is recorded in
[ADR 0001](adr/0001-mcp-invalid-arguments-are-tool-results.md). Protocol errors stay
reserved for the protocol: an unknown method, or a `method` that is not a string, answers
`-32601`, a message with an `id` and no `method`, `result` or `error` answers `-32600`, and
an exception escaping the server answers `-32603` and the loop reads on, so no single call
ends the session. `params` that are not an object name no tool to answer for, so
on `tools/call` and `initialize` they answer `-32602` with no result:
`params must be an object naming the tool and its arguments (got an array)` and
`params must be an object carrying protocolVersion (got a string)`; the session reads on.
`params` sent as null or left out read as an empty object: `tools/call` answers
`unknown tool ''` and `initialize` the newest revision the server speaks.
`ping` and `tools/list` read no `params`, and answer whatever they are.
`ping` answers an empty result, so a client's keepalive never reads as an error. A frame
that is not one JSON object, such as a line that is not JSON or an array, gets no reply,
and the server reads the next line.

A string value reaches the command as a value, whatever its first character. The server
passes each option as `--flag=value` and puts the positionals after `--`, so
`get_function_brief` with `path` `-x.py` briefs the file named `-x.py`, a `path` of
`--help` is a path that no run holds (an error object, `isError: true`), and an `exclude`
fragment of `-legacy` excludes what contains `-legacy`.

## Docker

The Dockerfile at the repository root builds the same stdio server as an image, which is
what a client or a registry that starts servers from a Dockerfile needs rather than from
an installed package.

```
docker build -t crapkit .
docker run -i --rm -v "$PWD:/repo" -w /repo crapkit
```

`-i` is the transport, not a convenience: with stdin closed the server reads EOF and exits
before `initialize`. The mount is the checkout being scored. The image serves `/repo`, so a
repo mounted anywhere else needs `--repo` on the command line, and an unmounted container
answers each `tools/call` with the missing-config result above. The image carries git,
because every tool shells to the CLI and the CLI reads git, and it serves as an
unprivileged account, uid 1000.

A bind mount keeps the host's ownership. On a Linux host where your uid is not 1000, the
tools still answer, but the churn and coupling caches they write under `.crapkit/` cannot
be saved, so every call walks the git history again. Run the server as the checkout's
owner instead:

```
docker run -i --rm --user "$(id -u):$(id -g)" -v "$PWD:/repo" -w /repo crapkit
```
