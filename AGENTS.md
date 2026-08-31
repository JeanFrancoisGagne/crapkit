# crapkit for agents

crapkit scores every function as CRAP = ccn^2 x (1 - cov)^3 + ccn and refuses to let
that score get worse. This file is the working contract for agents. Command, config
and output reference lives in [README.md](README.md); this file does not repeat it.

Two audiences, two sections. Read the one that matches the repo you are in:

- **[Burning down debt in your repo](#burning-down-debt-in-your-repo)**: you are an
  agent working in a repo that has crapkit wired up.
- **[Contributing to crapkit](#contributing-to-crapkit)**: you are changing crapkit
  itself.

Every command below runs as `crapkit <sub>` (console script) or
`python -m crapkit <sub>`. Every subcommand takes `--repo PATH`, default `.`, except
`claude-hook`, which reads its root from the hook payload on stdin.

---

# Burning down debt in your repo

The loop is five steps, and the first one is a payload. Run them in order, one item at a
time.

    crapkit brief PATH "FUNCTION" --json    # 1. the packet
    <edit>                                  # 2. decompose or add tests
    <commands.gate>                         # 3. gate the edit
    <commands.scoped_tests>                 # 4. the owning scope's tests
    <commands.verify>                       # 5. the verdict

Steps 3 to 5 are strings the packet hands you. `commands.gate`, `commands.scoped_tests`
and `commands.verify` come back filled in for this file and this scope; run them as
given rather than retyping them, which is how a lane flag or a scope's own test template
gets dropped. They are spelled as the console script (`crapkit rescore PATH --gate`),
which is the spelling that resolves from an activated venv on Windows: bare `python`
there can reach the WindowsApps stub or the base interpreter the venv wraps.

`commands.refresh` is the fourth string and the only one that writes: it is a `coverage`
run, reusing the artifacts of every lane whose scope files have not moved. That is what
`stale: true` asks for. Nothing else clears it, because nothing else lands a run on the
current commit. `commands.refresh_writes_run: true` marks it, so a session with a
read-only checkout can tell the one command it must not run from the three it may.

`PATH` and `FUNCTION` come from `crapkit next-item --claim`, or from one entry of a
`crapkit brief --batch N --json` an orchestrator already ran.

Read commands need a run in the store and exit 1 with the command that makes one when
there is none (`next-item` and `brief` say `no scored run in <root> — run \`crapkit
coverage\` first`; `worklist` says `no snapshot`). Run what the message names, then retry.

## 1. The packet

    crapkit brief calc/grade.py "curve( scores , mode , floor , ceiling , skip_none )" --json

One call, and the session holds everything the edit needs. Do not open the file first,
do not grep for callers, do not run `git log`: the payload already carries the
function's own text, every function in the file, the churn, the coupled files, the
twins, the ceiling the edit is judged against, and the commands for steps 3 to 5. Read
the packet, then edit. Field-by-field semantics live in
[docs/agent-json.md](docs/agent-json.md#brief); these are the ones that change what you
do next.

| Field | What you do with it |
|---|---|
| `source` | the function's own text, `start` to `end`. Edit from this, not from a fresh read |
| `handle` | the name to pass back to `brief`, `explain` and `claims release`. It survives your own edit; `start` does not |
| `remedy` | `decompose`, `add-tests` or `ok`, at the top level: the same verdict `next-item` prints |
| `est_splits`, `est_uncovered_paths` | the same two budget numbers `next-item` prints, out of the same code |
| `params` | its parameter names in order, so a new test can call it without opening the file |
| `notes` | the repo's and the scope's house rules, carried in from crapkit.toml |
| `gate_rule` | `ceiling` is the number step 3 judges ccn against, `binds` names which limit fires first |
| `commands` | the literal strings for steps 3, 4 and 5, plus `refresh` and `refresh_writes_run` |
| `stale` | `true` means the run predates HEAD: run `commands.refresh` before trusting `cov` |
| `file_functions`, `file_totals` | the siblings an extracted helper lands beside, and the file's rollup |
| `regrowth` | `regrown: true` says an earlier decomposition of this function did not hold |
| `attempts` | sessions that already claimed it. Above 0, read `regrowth.history` before repeating their split |
| `coupling` | files that keep landing in the same commits: edit them in this session or not at all. `is_test: true` marks the ones outside the scored corpus |
| `duplication_twins` | near-duplicates. `contained: true` means one already fits inside the other, so one can call the other |
| `uncovered_lines` | the exact lines to cover, same null-vs-`[]` contract as next-item |

### Naming the function

`NAME` takes five forms and they all resolve to the same row:

- the long name next-item printed, passed verbatim
- the bare identifier (`curve`)
- the function's start line (`67`)
- the ordinal handle on a function with no name (`"(anonymous)#2"`)
- the twin selector on a name one file gives to several functions (`"__post_init__#2"`)

Exact first: a NAME that IS a function's long name or bare identifier resolves to that
function alone, even when other names contain it — `route` is `route`, never
`route_chain`. A NAME that names no function falls back to a substring search, so a
half-remembered fragment still finds what holds it. `brief` and `explain` run the same
rule on the same string.

A name that two functions answer to exits 1 and lists the candidates. Pass the long
name or the start line instead:

    crapkit: 'render' in calc/report.py is ambiguous — candidates: render( counts , width , header , sort_desc ), render( self , rows )

A function lizard could not name shows as `(anonymous)` in every payload, and every
anonymous function in one file prints that same string. Its `handle` is the ordinal
form: `(anonymous)#2` is the file's second anonymous function counting from the top,
whatever line it sits on. Prefer it over the start line, which your own edit moves:
extract a helper above line 41 and `crapkit brief calc/report.py 41 --json` opens
something else, while `"(anonymous)#2"` still opens the callback you claimed. `brief`,
`explain` and `claims release` all take it. An ordinal past the end exits 1 and lists
the handles the file does hold:

    crapkit: no (anonymous)#5 in calc/report.py in the latest scored run — it holds: (anonymous)#1, (anonymous)#2

One file can also give one name to several NAMED functions: several dataclasses each
with a `__post_init__`, both arms of an `#ifdef` fork. A bare name resolves to the worst
of them, which is the one the queue ranks. `NAME#2` selects the second in file order and
`NAME#3` the third — the same ordinals the ratchet keys their marks on, so the mark in a
packet is the mark on the function that packet opened. An ordinal past the last twin
exits 1:

    crapkit: no __post_init__#5 in calc/iso_cost.py in the latest scored run — it holds 2 function(s) named '__post_init__'

### One call for a batch of packets

    crapkit brief --batch 5 --json

`{"schema": 1, "run_id": ..., "commit": ..., "stale": ..., "packets": [...]}`: the top N
of the queue as N packets, `crap` descending, built from one read of the store, the
churn log and the ratchet file. Hand one packet to one session.

## 2. Do the work

Everything here reads off the packet: `remedy` says which branch you are on, `source` is
what you are editing, `gate_rule.ceiling` is the number to land under.

- `remedy: decompose`: extract helpers until every piece sits at or under `target`.
  Comprehension `for`/`if`, ternaries, and `and`/`or` all count toward ccn. Exactly the
  target passes, one over does not. `file_functions` is what the file already holds, so
  a new helper does not collide with a name that is there.
- `remedy: add-tests`: write the failing test first, at the public seam, then cover the
  lines `uncovered_lines` names. `params` gives the call signature.
- New file: `rescore --gate` gates it in full (every function, with an `untracked`
  warning on stderr) because git diff cannot scope it. `git add` it so later runs judge
  only your edits; the pre-commit hook only ever sees staged content.

## 3. Gate the edit

    crapkit rescore calc/grade.py --gate      # commands.gate, verbatim

Prints the rescored table on stdout (`--json` for one object), violations on stderr:

    crapkit gate: 1 rescored function(s) over their scope ceiling:
      GATE  crap    132.0  ccn  11 cov 0%  calc/grade.py:54  curve( scores , mode , floor , ceiling , skip_none )  -> decompose

Three rules decide what it judges:

- **Scope**: only functions whose text changed against HEAD, index included. Untouched
  legacy functions in the same file are not judged.
- **Metric**: ccn against the file's scope ceiling, coverage ignored. Same question the
  pre-commit hook asks.
- **Exemption**: a function carrying a ratchet mark it has not exceeded passes. Push it
  past its mark and it fails here, ahead of verify's exit 6. Verify keeps exit 7 for a
  mark that rose in a function the diff never touched.

| Exit | Meaning | Next action |
|---|---|---|
| 0 | every changed function is at or under its ceiling | go to step 4 |
| 6 | the listed functions are over | decompose them, rerun |

Exit 0 is not a verify. `rescore` overlays fresh complexity on the last run's stale
coverage and writes no run, so a new function at exactly the ceiling with no tests
passes here and still fails verify on CRAP.

## 4. Run the owning scope's tests

    python -m pytest "calc/grade.py" -q -p no:cacheprovider   # commands.scoped_tests, verbatim

`commands.scoped_tests` is not a `crapkit test-scoped` call. It is the owning scope's own
`[crapkit.scoped_tests]` template with `{files}` already replaced by this packet's file,
double-quoted, so what you run is the runner the scope declared. Without a packet in hand,
`crapkit test-scoped calc/grade.py` fills the same template from the same config, and the
exit codes below are its.

`commands.scoped_tests` is `null` when this scope declares no template, and then there
is no step 4 to run: go to step 5. `crapkit doctor` warns about every scope a lane
measures with no template behind it, which is the gap to close.

This needs one template per scope in crapkit.toml. `crapkit init` leaves the block
commented out at the end of the file, one line per scope it found, so uncommenting is
usually the whole job:

    [crapkit.scoped_tests]
    calc = "python -m pytest {files} -q -p no:cacheprovider"

- Key is the `name` of a `[[scope]]`. Value is a shell command.
- crapkit routes each file you name to the scope whose `paths` entry matches deepest,
  substitutes `{files}` with that scope's files (each double-quoted, in the order you
  passed them), and runs one command per scope, scopes in name order.
- A file only routes if it sits under a scope's `paths`. Keep test files inside a scope
  path if you want to name them here; `[exclude] globs` still keeps them out of scoring.

### Several scopes, tests in a top-level tests/

A test file outside every scope routes to the single scope that declares a template. With
two templated scopes there is no single owner and `crapkit test-scoped tests/test_stats.py`
exits 3. Naming a source file routes fine, and then `{files}` hands pytest a source path to
collect tests from: no tests ran, runner exit 5, crapkit exit 1.

Drop `{files}` for those scopes. A template without it runs exactly as written, so the
scope runs its whole suite whichever of its files you name:

    [crapkit.scoped_tests]
    calc = "python -m pytest tests/test_grade.py -q -p no:cacheprovider"
    util = "python -m pytest tests/test_stats.py -q -p no:cacheprovider"

`crapkit test-scoped util/stats.py` then runs util's suite, and naming files from both
scopes runs both commands, scopes in name order. Use `{files}` when a scope's tests live
under its own `paths` and you want only the files you named to run.

| Exit | Meaning | Next action |
|---|---|---|
| 0 | every scope's runner passed | go to step 5 |
| 1 | a runner failed (crapkit remaps the runner's own code so it cannot collide with 3/5/6/7/8) | fix the test or the code |
| 3 | config: a file under no scope, or a scope with no template | fix crapkit.toml |

## 5. Verify

    crapkit verify                            # commands.verify, verbatim

Runs every lane, scores the working tree, and judges it against the trusted baseline.
This is the slow step and the only authoritative one.

| Exit | Verdict | Next action |
|---|---|---|
| 0 | pass | baseline advanced, ratchet tightened, finished claims released. Commit |
| 5 | a lane produced no artifact, or one that measured a different tree | tooling, not your code: read the lane log the message names, then fix the lane command in crapkit.toml |
| 6 | gate: a touched function is over its ceiling on CRAP and above any ratchet mark it carries | decompose it, or cover it |
| 7 | ratchet: a recorded score got worse | restore that function below its mark |
| 8 | a test that passed in the baseline fails now | fix the test or the code |
| 9 | more uncovered changed lines than `diff_uncovered_max` | cover the changed lines |

One verdict per run, in that order: 6 beats 7 beats 8, and 9 fires only when nothing
else did. A run that exits non-zero never becomes a baseline and never tightens the
ratchet, exit 9 included, and neither does any run taken after it until some verify
passes. A `coverage` run on the refused tree would otherwise become the baseline and
retire the finding. verify prints the run it refused and the two ways past it: fix the
findings, or pass `--baseline ID` to accept the newer run deliberately.

`--baseline ID` naming a run that exists and still cannot serve says which run it is,
why, and which runs can:

    crapkit: run 1 is an inventory run (no coverage was measured) and cannot serve as a baseline; trusted runs: 2; pass `--baseline 2` for the newest

The other reasons that line gives are a failed verify, a hook run and a partial run (a
lane subset, or a lane that failed).

The lines verify prints, one per finding kind, collected here from separate runs:

    verify OK @ f6e9bde18a7 vs baseline f6e9bde18a7 (1 changed files)
    crapkit: lane 'py' FAILED: lane 'py' produced no artifact at .crapkit/cov/py.json (command exit 4); full log: /repo/.crapkit/lane-py.log; last output: ...
    verify FAILED @ 3a45b8a9b6c vs baseline 03d9cac1397 (1 changed files)
      GATE  crap     42.0  ccn   6 cov 0%  calc/report.py:22  bucket( counts , low , high , invert , label )  -> add-tests  [dirty]
      RATCHET  calc/report.py  spread( counts , low , high , invert , label , pad ): 8.0 -> 72.0
      NEW FAILURE  tests.test_curve::test_normalized  [dirty]
      findings: 1 committed / 0 dirty (uncommitted edits and untracked files)
    diff coverage: 3 uncovered changed line(s) over the ceiling 0

That GATE line is the difference between step 3 and step 5: ccn 6 sits at the ceiling,
so `rescore --gate` passes it, and CRAP 42 at 0% coverage still fails verify.

Findings tagged `[dirty]` come from uncommitted edits and untracked files, and the
summary line splits them. In a shared checkout, dirty findings may not be yours.

## The advisory hook

With the Claude Code plugin installed, `crapkit claude-hook` runs after every edit you
make and writes three lines to stderr when that edit pushed a function over its ceiling:

    crapkit advisory: 1 function(s) over ceiling 6 in calc/grade.py (the edit landed; nothing was blocked)
      ccn 9  calc/grade.py:67  curve( scores , mode , floor , ceiling , skip_none )
    the commit gate enforces this; decompose there or mark the debt

Nothing was blocked and nothing was written. Read it as the earliest warning that step 3
will fail, not as a rejected edit. A function the committed ratchet already marks never
triggers it, and a repo with no `crapkit.toml` never hears from the hook at all.

## When a lane will not start

Exit 5 says a lane produced no artifact. The first question is whether the command could
run at all, and `crapkit doctor` answers it. doctor reads each lane command with the
shell that will run it, sh on POSIX and cmd.exe on Windows, and FAILs a lane whose first
word will not start:

    FAIL lane 'py': cmd.exe cannot run 'python' (exit 9009) — the lane cannot start, so its scopes can only ever score no-lane

9009 is cmd.exe saying it could not start that name. The usual cause is the Store
`python.exe` alias a stock Windows PATH carries with no Store app behind it: it resolves,
so anything that only looks at PATH clears it. Point the lane at a python that runs.

Quote lane values with double quotes. cmd.exe does not treat `'` as a quote, so a
single-quoted value reaches the runner one word per space, and the guard refuses the lane
with that as the reason:

    crapkit: lane 'py': positional argument 'slow'' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately (cmd.exe does not treat ' as a quote: write the value in double quotes)

A chained command is read one argv per `&&`, `||`, `&` and `|` segment, and every segment
that runs the runner is checked, so a narrowing flag after the operator is refused too.
doctor reads a lane the same way: a quoted interpreter path stays one word, and the runner
of every segment is checked for resolving on PATH. Starting a runner is the narrower
check: doctor starts the line's own first word, once per distinct word. A runner after
`&&` that resolves and then refuses to run clears doctor and fails the lane.

doctor also stops at the lane command. A lane written as `npm run test -- --coverage ...`
is checked as far as `npm`, never the runner the package script names, so a package
listing `vitest` in `devDependencies` with no `node_modules` on disk passes doctor and
then fails the lane with `'vitest' is not recognized` in the log tail. That is a missing
install, not a misconfigured lane.

doctor also WARNs on a coveragepy or istanbul lane that names no `results_artifact`:

    WARN lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it; add --junitxml=.crapkit/cov/junit-py.xml to the command and results_artifact = ".crapkit/cov/junit-py.xml" to the lane

Coverage is measured either way. What the lane cannot do without a results file is feed
the two checks that read one, so exit 8 can never fire for its scopes and nothing else
would have said so. `crapkit init` writes both on the lanes it detects.

## When crapkit's root sits below the git top

`--repo` may name a directory below the repository's top. Every git spawn runs with
`diff.relative=true` and `core.quotePath=false`, so scored rows, churn, the commit gate,
verify's changed files, lane reuse, `mutate`'s targets and the advisory hook all join on
root-relative, unquoted paths. They used to join two spellings, which is why a dirty file
with a non-ASCII name was invisible to lane reuse: git quoted the name and `ls-files` did
not.

A staged file above the crapkit root is outside the diff by design, and the gate does not
name it. `.git` is found by walking up from the root, so the HEAD fast path fires down
here too.

## Which scope owns a file

One predicate answers that, and it answers for scoring, `test-scoped` routing, lane reuse
and the packet alike: the deepest declared scope `paths` entry wins. Three of those used
to answer separately, and `brief` could hand you a function's lane and test command from
one scope and its ceiling from another.

If `crapkit.toml` declares nested scopes (`src` and `src/web`), files may move between
scopes on the next scan, and the per-scope rollups and ceilings move with them. A config
with no nested scopes sees no change. A scope path naming a file rather than a directory
(`paths = ["core/hot.py"]`) owns that file, and editing it marks that scope's lane
changed.

## Picking an item

Where a packet's `PATH` and `FUNCTION` come from when no orchestrator handed you one.

    crapkit next-item --claim

`next-item` always prints one JSON object on stdout and has no `--json` flag. One real
payload, one line, sorted keys:

    {"commit": "f6e9bde18a7b4a4d4a0610c16b0526bd9aefc6c6", "empty": false, "item": {"authors": 1, "ccn": 11, "ccn_std": 11, "cognitive": 15, "commits": 6, "cov": 0.0, "crap": 132.0, "end": 84, "est_splits": 2, "est_uncovered_paths": 11, "flag": "measured", "function": "curve( scores , mode , floor , ceiling , skip_none )", "handle": "curve", "nesting": 6, "nloc": 17, "path": "calc/grade.py", "remedy": "decompose", "scope": "calc", "start": 67, "target": 6, "uncovered_lines": [69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84]}, "run_id": 5, "schema": 1, "skipped_no_lane": 0, "stale": false}

Act on these fields:

| Field | Use it for |
|---|---|
| `remedy` | `decompose` splits the function, `add-tests` covers it, `ok` needs nothing |
| `est_splits` | pieces a decomposition needs: `0` when `ccn <= target`, else `ceil(ccn / target)` |
| `est_uncovered_paths` | decision paths no test walks: `round((1 - cov) * ccn)` |
| `uncovered_lines` | the exact line numbers to cover |
| `target` | the scope's ceiling; ccn above it cannot be saved by coverage |
| `function` | pass verbatim to `brief` and `claims release` |
| `handle` | the shorter name form, and the one to use on a function printed as `(anonymous)`: `(anonymous)#2` names a position in the file, so it outlives your own edit |
| `start` | the other name form `brief` takes; a line number, so an edit above it invalidates it |
| `stale` | `true` means the run predates HEAD; rerun `crapkit coverage` before acting on `cov` |

`uncovered_lines: null` with a sibling `uncovered_lines_note` means no artifact could name
line numbers for that file. The note names which case, and `flag` is the same answer in one
word:

- `flag: "untested"`: no test imports the file, so no artifact ever mentions it and its
  whole span is dark. The move is to write the first test at the public seam and rerun
  `crapkit coverage`; the lines then appear. Committing changes nothing here, and neither
  does verify. This is the common case on a clean tree.
- `flag: "measured"`: a lane did measure this file, but its artifact no longer matches the
  tree, usually because files in the lane's scopes carry uncommitted edits. Commit or
  revert the edits, then rerun `crapkit coverage`. Committing alone does not bring the
  lines back: nothing rereads the artifact until a run does.
- `flag: "cc-only"`: the scope sets `coverage_optional = true`, so no artifact was ever
  going to name lines for it. Nothing clears this one, and nothing should: `crap` is `ccn`
  and the only remedy is `decompose`.

`[]` means the artifact answered and nothing is dark. The note is prose and may be
reworded; `flag` is the contract.

`--top N` replaces `item{}` with `items[]`. `--exclude FRAG` (repeatable) skips items whose
path or function name contains FRAG. `--scope NAME` (repeatable) restricts to the named
configured scopes, matched exactly rather than as a substring.

## The termination rule

**Stop looping when all three of these hold in one bare `crapkit next-item`, and not
before:**

    empty                        true
    skipped_claimed              0, or absent
    reasons.no_lane_over_target  0, or absent

    {"commit": "8d10c13303dfd9ef4172d9f736582ff4ffa96e60", "empty": true, "reasons": {"all_remaining_at_or_under_target": 4, "below_floor": 1, "churn_window_months": 12, "excluded_by_flag": 0, "no_churn_in_window": 0, "no_lane": 0, "no_lane_over_target": 0}, "run_id": 3, "schema": 1, "skipped_no_lane": 0, "stale": false}

That payload is a finished burn-down. `empty: true` on its own is not: it says the queue
has nothing to hand out, and two things stop it handing out work that still exists. A
claim hides a row from every session, yours included. A `no-lane` row never reaches this
queue, because its `cov = 0` is a tooling gap rather than a testing one, and
`no_lane_over_target` counts how many of those rows are over their ceiling anyway. Either
count non-zero means work is left somewhere the queue cannot reach. `crapkit worklist` does
rank those rows, marked `no-lane`, so the gap stays visible somewhere.

The two read the same run, the newest trusted one, so where they disagree it is about
ranking and never about which snapshot each is describing. `ratchet seed` and `prune` pick
their run by the same rule `verify` uses, so neither signs marks off a run verify refused.
They print which run they took and which they passed over:

    crapkit-ratchet.tsv: added 1, tightened 0 — 1 mark(s) vs run 3 (86fb0cc6bce), skipped failed verify run 4

The `worklist_floor` is not part of the judgement: a function under the floor whose CRAP
is over its ceiling is queued like any other, so an empty queue is never the floor hiding
debt.

`reasons` says which ending you got. `all_remaining_at_or_under_target: N` means N
candidates passed every filter and all N sit at or under their ceiling with `remedy: ok`;
handing those rows back is what makes a burn-down run forever. Without that key, a filter
emptied the queue rather than the work being finished:

| Key | What it counts | Your move |
|---|---|---|
| `below_floor` | ccn under `worklist_floor` (default 5) | nothing: a row over target is queued whatever its ccn, so every row counted here is at or under its ceiling |
| `no_lane` | scored rows no lane covers | wiring gap: `crapkit doctor` names the scope, then declare a `[[lane]]` for it |
| `no_lane_over_target` | of those, the ones over their ceiling | the same wiring gap, now blocking the stop condition: declare the lane, or set `coverage_optional` if the scope is meant to go unmeasured |
| `no_churn_in_window` | file has no commits inside `churn_window_months` | nothing, cold code; `crapkit worklist` lists it as dormant when you want to look |
| `excluded_by_flag` | your own `--exclude` | drop the flag to see them |
| `skipped_claimed` (sibling of `reasons`) | items an open claim hides, possibly your own | `crapkit claims` to see them, `claims release` to hand one back |

Check termination with a bare `crapkit next-item`. Under `--exclude` or `--scope`,
`all_remaining_at_or_under_target` describes the filtered slice only, and both it and
the filter counts can appear in the same payload.

## Claims

A claim hides one function from every session's `next-item`, including yours. Take one
when another session might be working the same repo.

    crapkit next-item --claim          # claims exactly the items it hands out
    crapkit claims                     # list them
    crapkit claims release calc/report.py "spread( counts , low , high , invert , label , pad )"
    crapkit claims release --all

Release takes either the bare identifier or the long name next-item printed. `claims`
prints one line per claim, `claims --json` one object:

    1 open claim(s)
      2026-08-23T01:43:36Z  2bac6bded4d  calc/report.py  spread( counts , low , high , invert , label , pad )

    {"claims": [{"commit": "2bac6bded4d9853f5749017a6b897a7f942437fa", "created_at": "2026-08-23T01:43:36Z", "id": 4, "long_name": "spread( counts , low , high , invert , label , pad )", "path": "calc/report.py"}], "open": 1, "schema": 1}

Two things close a claim for you: `verify` releases it once the function sits at or
under its ceiling, or once the commit you claimed at leaves the history. Everything
else you hand back with `claims release`. A `next-item` call whose queue a claim thinned
says so with `skipped_claimed: N`.

Hand a claim back before you abandon an item. An unreleased claim on the worst function
in the repo hides it from every future queue until someone finishes the work.

## Multi-agent sessions

    crapkit worklist --batches 3 --json

`--batches N` adds a `batches[]` key to the normal worklist payload (`active[]`,
`dormant_top[]`, `floor`, `run_id` and the rest stay). Each batch is
`{"files": [...], "entries": [...]}`. Plain output prints one line per batch:

    batch 1: 2 items in 1 files: calc/report.py
    batch 2: 4 items in 1 files: calc/grade.py

Rules the split guarantees:

- Batches share no file. A file is indivisible, and files that keep changing together
  stay in the same batch.
- You may get fewer than N batches. Empty ones are dropped.
- The split is a pure function of the store and the git history: same tree, same
  batches, so every agent can compute the same cut.

Give one batch to one agent, each in its own worktree or branch. Disjoint file sets are
what make the resulting diffs merge: two agents decomposing different functions in one
file produce overlapping hunks no matter how the queue was ranked.

    crapkit brief --batch 3 --json

is the other half of the same move: one call returns N ready packets instead of N
`brief` calls, so every session starts at step 1 with no further reads.

The committed ratchet file is the one file every batch writes. Register
`crapkit ratchet merge` as its git merge driver (README) so parallel branches merge
marks instead of hand-resolving them, which is how a mark silently rises.

## The MCP server

    crapkit mcp --repo /abs/path/to/repo

Stdio JSON-RPC, newline-delimited, no SDK dependency. Client config:

    {
      "mcpServers": {
        "crapkit": {
          "command": "crapkit",
          "args": ["mcp", "--repo", "/abs/path/to/repo"]
        }
      }
    }

`--repo` sets the default root; every tool also takes an optional `repo` argument that
overrides it per call. `initialize` reports protocol `2024-11-05` and server name
`crapkit`.

Nine tools:

| Tool | Arguments | Returns |
|---|---|---|
| `worklist` | `top` | JSON |
| `brief` | `path`, `name` | JSON |
| `runs` | none | JSON |
| `coupling` | `min_support`, `min_confidence` | JSON |
| `duplication` | `similarity` | JSON |
| `ratchet_report` | none | JSON |
| `explain` | `path`, `name` | plain text |
| `doctor` | none | plain text |
| `next_item` | `top` (int), `exclude` (array of strings: one fragment per element, each becoming its own `--exclude`) | JSON text |

The server is read-only. Every tool shells to a read command, so nothing it exposes
writes a run, a baseline, a ratchet or a mutant. `coverage`, `verify`, `ratchet`,
`mutate` and `claims` stay in the CLI.

`brief`, `worklist` and `coupling` do fill the ranked-pairs cache under `.crapkit/` on a
cold run, and the store fills a per-run rollup the first time `trend` or `report` asks.
Those are caches: deleting one costs a walk, never a verdict. A session that must write
nothing at all still has `commands.refresh_writes_run` to tell the one command that
lands a run from the ones that do not.

---

# Contributing to crapkit

## Setup

    pip install -e ".[dev]"
    git config core.hooksPath git-hooks

The dev extra ships `pytest`, `pytest-cov`, `pytest-xdist` and `coverage`. None of the
four is a convenience.

`coverage>=7.10.6` is the floor `[tool.coverage.run] patch = ["subprocess"]` needs, and
that key is what measures the CLI at all: tests/e2e drives every `cmd_*` through
`subprocess.run`, pytest-cov 7.0.0 dropped its own subprocess measurement, and without
the patch every entry point reads 0% with nothing said. An older coverage warns about the
key and ignores it, so the floor is the half that keeps the warning from being the whole
story. Measured on `tests/e2e/test_init_doctor_e2e.py`: `cli/admin.py` scores 0/498
statements without it under pytest-cov 7.1.0, 317/498 with it under 7.1.0 and 6.3.0 alike.

xdist is not a convenience either: `tests/fixtures/mini_repo` declares a lane that shells out to
`pytest ... -n 2`, and without it that subprocess dies on an unrecognized `-n`, failing
the e2e tests that assert the lane exited 0. CI installs this extra and nothing else, so
a pytest plugin a committed fixture lane needs belongs in it.

The second line arms the complexity gate. Without it your commits pass locally and get
rejected in review.

## Tests

    python -m pytest                      # both suites
    python -m pytest tests/unit           # 2,283 tests, about a minute (16 s at -n 8)
    python -m pytest tests/e2e -n 8       # 595 tests, about 1m30

`[tool.pytest.ini_options]` in pyproject.toml sets `testpaths = ["tests"]` and
`addopts = "-q --tb=short -p no:cacheprovider"`. Nothing else: no xdist and no
randomization, so the `-n 8` above is yours to pass and no `-n 0` is needed to isolate a
failure. Pass it: the e2e suite runs about 1m30 at `-n 8` against about 8 minutes
serially, the pair CI, the PR template and pyproject.toml quote. If you have
pytest-randomly installed globally, add `-p no:randomly` to pin the order.

`tests/unit` covers pure seams, and that now includes `cli/verifying.py` and
`cli/scoring.py`, driven in process rather than through a subprocess. `tests/e2e` drives
`python -m crapkit` against real git repos in tmp dirs and asserts through the CLI only.
It spawns that child one way, `run_cli` in `tests/e2e/conftest.py`. Bind your file's
contract once at the top with `cli_runner(...)` rather than writing another
`subprocess.run`; before that file there were 42 copies of those four lines, 23 of them
different, with nothing to say which differences were deliberate. Each e2e command
injects its own git identity, so no global git config is required.

## Where code goes

`src/crapkit/` is the pure core: analysis, scoring, the store, git, the ratchet, the
report renderers. One module per concern, and none of them knows about argparse.

Four of them answer a question the whole tree asks, so nothing reimplements the answer:

| Module | What it answers |
|---|---|
| `universe.py` | which scope owns a path. `owning_scope` is the only predicate, and the deepest declared `paths` entry wins |
| `config.py` | what words a lane command holds. `shell_words` and `shell_segments` read it the way the shell that runs it reads it |
| `procs.py` | how a spawn with a deadline dies. `run_bounded` kills the whole process tree and reaps it |
| `coupling_cache.py` | which files keep landing in the same commits. `coupling`, `brief` and `worklist --batches` all read this one door, and it caches the ranked pairs in `.crapkit/coupling-cache-v1.json` beside the churn caches |

`store.py` gained a `run_rollup` table: one row per run per scope, filled the first time
something asks and pruned with its run. `trend` and `report` read it instead of
rescanning every scored row of every run, which makes both of them writers. The fill is
best effort, because two crapkit processes on one store can collide on it: losing the
cache is a cost, losing the command is a bug.

`src/crapkit/cli/` is the command layer, split into ten family modules. `cli/__init__.py`
holds no logic. It carries `_OWNER`, a name-to-module map, and a `__getattr__` that loads
one family the first time a name is read. Eight eager imports used to load every family on
every invocation, which put the cost of every subcommand on `crapkit --version`.

| Family | Subcommands |
|---|---|
| `parser.py` | `main` and the argparse tree; owns no subcommand itself |
| `scoring.py` | `inventory`, `coverage`, `rescore` |
| `verifying.py` | `verify`, `hook-precommit`, `test-scoped` |
| `queue.py` | `worklist`, `next-item`, `brief`, `claims` |
| `reports.py` | `runs`, `trend`, `digest`, `explain`, `overrides`, `report` |
| `ratchet_cmds.py` | `ratchet` |
| `analyses.py` | `duplication`, `coupling`, `mutate`, `mcp` |
| `admin.py` | `init`, `doctor`, `watch` |
| `claude_hook.py` | `claude-hook` |
| `_shared.py` | helpers more than one family reads |

`claude_hook.py` carries two rules the other families do not, and both are load-bearing.
Its module scope imports stdlib only, because every edit on the machine pays for it. And
it never opens the snapshot store: `SnapshotStore.__init__` runs migrations, so a per-edit
hook would rewrite the schema of whatever store it touched.

Four reader modules sit beside the core, all registered in `analyze.py`'s
`deferred_pygments()` block:

| Module | What it does |
|---|---|
| `lizardcognitive.py` | Sonar-spec cognitive complexity as a lizard token-stream extension, so every language pays the same rules with no second parse |
| `lizardrust.py` | counts Rust `match` arms, which lizard does not (lizard #494) |
| `lizardshell.py` | a shell reader, because lizard ships none and answers `.sh` with `CLikeReader` instead of an error |
| `lizardpowershell.py` | a PowerShell reader, same reason, plus a cp1252 decode fallback |

Registration belongs at that module scope and nowhere else. A `ProcessPoolExecutor` child
imports `analyze.py`, so a reader registered anywhere later leaves spawned workers
measuring with the readers lizard shipped and reporting plausible wrong numbers.

One module is neither: `discover.py` has no importer and has not had one since it was
added. It is either wired into the packet or removed in a later release. Do not build on
it before that lands.

## Standing rules

Six rules the suite cannot fully police. Break one and the failure surfaces somewhere
else, usually later, usually as a plausible wrong number.

- **Every function you add or edit sits at ccn 6 or below.** The pre-commit gate refuses
  the rest; the section below says what a refusal means.
- **A name added to a family module gets its `_OWNER` entry in the same commit.** A name
  with no entry is not re-exported, and the suite reaches dozens of these helpers by name
  through `crapkit.cli`.
- **Change what a metric measures and bump `ANALYSIS_VERSION` in `analyze.py`.** The
  ratchet stamps every marks file with the version that produced it, and `verify` refuses
  to weigh fresh scores against marks another version signed. 0.4.5 bumped it to 8,
  because shell blocks now nest. Without the bump nothing refuses, and 40k marks are
  quietly compared against numbers they never described.
- **Read a lane command with `config.shell_words` or `config.shell_segments`, never
  `str.split()`.** A whitespace split breaks a quoted interpreter path at its space and
  reads `-k "not slow"` as three positionals. The full-suite guard, `doctor` and the
  pytest-cov probe all go through those two, and they read the command the way the shell
  that will run it reads it: sh on POSIX, cmd.exe on Windows.
- **Any spawn with a timeout goes through `procs.run_bounded`.** `shell=True` makes the
  shell the child and the real program a grandchild, so `subprocess.run`'s own timeout
  kills the shell and leaves the suite running with nothing waiting on it. `mutate` left
  one orphan suite per killed mutant that way. `run_bounded` starts the shell in its own
  process group and kills the group, `taskkill /T` on Windows and `killpg` on POSIX.
- **Ask `universe.owning_scope` which scope owns a path.** Ownership was decided three
  ways once, and `brief` handed out a function's lane and test command from one scope and
  its ceiling from another.

## Tests first

Write the failing test at the public seam before the fix. New behavior lands with a test
that fails on the parent commit. A bug fix lands with the test that reproduces it.

## The cc <= 6 gate

Every function you add or edit must sit at min-CCN 6 or below. The pre-commit hook runs
`python -m crapkit hook-precommit` over the staged blobs:

    crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:
      ccn   7  calc/report.py:39  tally( rows , low , high , invert , label , pad , strict )
    decompose before committing (coverage cannot save a function above the target).

Exit 6 blocks the commit. Decompose until every touched function passes. A refusal is
design feedback, not a threshold to widen.

A function the committed ratchet already carries a mark for is exempt, and the hook
reports the count on stderr. Touching signed debt does not refuse the commit; `crapkit
verify` is what fails a mark that rises.

Two gates, two exemptions, and the difference is on purpose. The pre-commit hook exempts
a marked function whatever its fresh score, because it reads staged blobs and a staged
blob has no coverage behind it: the hook cannot tell a mark that held from one that
rose. `crapkit rescore --gate` and `crapkit verify` exempt only a touched function whose
fresh CRAP sits at or under its mark. So an edit that pushes signed debt past its mark
still commits, and verify then refuses it with exit 6. Exit 7 stays for a mark that rose
in a function the diff never touched.

`CRAPKIT_OVERRIDE_REASON` is a human granting audited debt (alert, ratchet entry and
snapshot record, all three or nothing). Leave it alone.

## Determinism is the product

- Marks only fall. `ratchet seed` admits new debt, `prune` drops gone code, `merge` is
  the git driver. None of them raises a mark.
- No wall clock in scoring paths. Churn weights and burn-down ages anchor on the newest
  commit in the log, so a fixed tree reports byte-identically.
- JSON is sorted-keys and carries no timestamps in rows.

## The docs are pinned to the code

`tests/unit/test_cli_docs_contract.py` diffs README's `## Subcommands` table against the
argparse parser in both directions. Add, rename or drop a subcommand and you update that
table in the same commit, or the suite fails.

`test_docs_claims_contract.py` and `test_handle_docs_contract.py` go further: a transcript
quoted in the docs is compared against the string the code emits, and a documented field
name against the payload that carries it. Reword a message and the page that quotes it
fails, not a reader.

## crapkit scores itself

`crapkit.toml` and `crapkit-ratchet.tsv` at the repo root are live.

    python -m crapkit coverage
    python -m crapkit verify

must stay green on your branch.
