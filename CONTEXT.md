# crapkit

A per-function CRAP scorer: it reads the coverage artifact a repository's own test suite writes, inventories every function's complexity, joins the two into one score per function, ranks the worst by how often their files change, and gates new code against a committed record of accepted debt.

## Language

### The score

**CRAP**:
The per-function score, complexity squared times uncovered risk cubed plus complexity. The number every view exists to show.
_Avoid_: crap score, risk score, grade (a grade is a letter over a scope)

**Ceiling**:
The highest CRAP a function may carry before it is over; one per repository, overridable per scope. Since coverage can at best collapse CRAP to complexity, a ceiling is also a complexity limit.
_Avoid_: target (that is the configuration key that sets a ceiling, not the concept), threshold, limit

**Coverage**:
The share of a function's branches the suite ran, read from the artifact; never measured by crapkit itself. A function with no branches falls back to the share of its statements that ran, and one with no statements to invoked-or-not: 1 if the suite called it, 0 if not. Python's `and` and `or` add to complexity, but coverage.py records no branch arc for them, so a short-circuit the suite never took leaves the share unchanged.

**Unmeasured**:
A row no measurement stands behind: its scope has no lane (`no-lane`) or asks for none (`cc-only`), or rescore finds no row in the run for a function added or renamed since. It scores at coverage 0.0 all the same; payloads carry `unmeasured: true` beside that stand-in, and text says `not measured`.
_Avoid_: untested (an untested function was measured, and no test reached it)

**Risk**:
What ranks the worklist: complexity times recency-weighted churn. Not the CRAP score.

**Remedy**:
The one-word action attached to a scored row: `decompose`, `split-lines`, `add-tests` or `ok`. brief and next-item judge it against the ceiling crapkit.toml holds when they read the row; worklist prints the verdict the run stored.

### Naming a function

**Twin**:
One of several functions that one file gives the same long name. Each takes its own ratchet key: the first keeps the bare name, and later ones take `#2`, `#3` in file order. A bare twin name selects the worst twin.
_Avoid_: duplicate (a near-copy that shingles find)

**Handle**:
The short name a payload prints for a function: the bare identifier, `NAME#N` for a twin, or `(anonymous)#N` for a function lizard could not name. It survives an edit above the function; a start line does not.

### The corpus

**Scope**:
A named set of path prefixes and languages that shares one ceiling and one set of lanes.

**Lane**:
One configured test command that writes one coverage artifact for one scope.

**Lane log**:
The file a lane's output streams to, `.crapkit/lane-<name>.log`, kept as the command wrote it, colour included. A refusal quotes its tail as plain text.

**Launcher token**:
`{python}` or `{python:DIR}` in a lane, scoped-tests, retest or mutation command: the python the command names, spelled so either OS can read the committed file. The loader reads it as `python` on Windows and `python3` elsewhere, or as the launcher inside the venv at DIR (`DIR\Scripts\python.exe`, `DIR/bin/python`). `init` writes it.
_Avoid_: placeholder (that is `{files}` or `{tests}`, filled in when the command runs)

**Inputs**:
The root-relative paths a lane declares its command reads. While none of them changed since the artifact's commit, `--reuse-unchanged` reuses the lane instead of rerunning it.
_Avoid_: dependencies, sources (a scope's paths are its sources)

**Artifact**:
The coverage file a lane writes and crapkit reads.
_Avoid_: report (a report is crapkit's own HTML page)

**Stamp**:
What a lane run records beside its artifact in `.crapkit/artifacts.json`: the commit, the reuse proof or why it did not hold, and the lane's content record. Read once per command.

**Content record**:
The git blob id of each file, the id `git add` would store, taken when a lane or a scored run read it. Freshness compares these ids with the tree; git's index answers for a file its stat cache calls unchanged, so a same-size edit under a restored modification time is a named limit. It holds on Windows, where the change time is the creation time, and under `core.trustctime=false`; on Linux and macOS git sees the edit once the change time moves a second past the one it recorded.
_Avoid_: digest, snapshot (a snapshot is a run in the store)

**Leftover**:
The artifact a lane's failed attempt left in place, which is the previous run's file. Reuse refuses it while it holds the same bytes.

**Exclude**:
A glob that removes files from the corpus before inventory.

**Unanalyzable file**:
A source file the analysis names on stderr and scores as zero functions, because lizard failed on it or a Python def in it was read no further than its signature. Every run tries it again. A gate refuses a changed one, because it judged none of its functions, and no override grants past it; the advisory hook names it after the edit, and doctor WARNs about each one the newest run could not read. JSON calls it an unread file.
_Avoid_: skipped file (nothing about it is silent)

**Unreadable name**:
A file name git gives in bytes that are not UTF-8, so no row, mark or cache can be keyed on it. git's listings hand it on as a value; the scope assignment judges it. When a scope takes the name, the command refuses with exit 3 and the `git mv` fix, and a `--json` error object lists it in `unread_files`, each item `{path, reason, dirty}` as in a gate verdict; `check_gate` returns that refusal as a verdict with `gate.ok` false. Any other tracked or staged one is left out, named once on stderr and listed in `unreadable_names`; a `rescore` argument no scope takes is left out with one stderr line, and `check_gate` judges it 0. `explain`, `brief` and `ratchet move` answer for the one file they are handed, so they refuse such a name whether a scope takes it or not. An untracked one is a change to the lanes that read it.
_Avoid_: unanalyzable file (lizard read that one)

### Runs

**Run**:
One scored snapshot: an inventory joined with the artifacts of the lanes that ran.

**Partial run**:
A run in which some declared lanes did not run; never a baseline.

**Legacy run**:
A stored run written before crapkit recorded where same-line functions sit. Its same-line twins cannot be told apart: a function's history leaves the run out, and a command that must read the twins from it, such as a seed, refuses and names the run.

**Stale**:
The run's commit is not HEAD. It judges the commit, not the files: an amend that moves no byte makes a run stale, and an uncommitted edit leaves it fresh. Schema 2, planned for 0.9.0, redefines it as the content question.
_Avoid_: out of date (say whether the commit or the content moved)

**Scored changes**:
How many files a run scored hold other content now than the run recorded, deleted files included; null when crapkit cannot compare. `0` is the only value that says the run's numbers describe the files on disk.

**Baseline**:
The trusted earlier run a verdict compares against.

**Newly scored**:
What the digest calls a function that the older run of its pair holds no row for, when that run scored no function in the function's scope: a scope added to crapkit.toml since then, say. The code may be years old; only its measurement is new.
_Avoid_: new (a new function sits in a scope both runs scored)

**Named baseline**:
A run that `--baseline ID` names for verify, ratchet seed or ratchet prune. It steps past the rule that a failed verify taints later runs, and nothing else: a failed verify, a hook run, a partial run or an inventory run is still refused.
_Avoid_: forced baseline, override baseline

**Verdict**:
The outcome of `verify`: the gate result, ratchet regressions and new test failures against the baseline. A lane whose declared junit `verify --reuse-artifacts` reused and could not read leaves no verdict: verify exits 5 and stores nothing.

**Forgiven failure**:
A test failure the fresh run and the baseline both have. It is not new, so it fails no verdict; the OK line counts it. When the baseline recorded no failure list for a lane, the newest trusted run at or behind it that did stands in for that lane.
_Avoid_: known failure, ignored failure

**Flake retry**:
verify's rerun of its new failures through a lane's `retest_command`, before it decides exit 8.

**Retried pass**:
A new test failure that passed its flake retry. It fails no verdict, the OK line and `retried_passes` name it, and a later verify never forgives it as a baseline failure.
_Avoid_: flaky failure, forgiven failure

**Gate**:
The rule that a new or changed function may not exceed its ceiling; enforced by the pre-commit hook, `verify` and the Action.

**Advisory**:
What `claude-hook` prints after an agent's edit lands: exit 2 and stderr naming each changed function over its ceiling, or naming a changed file it could not judge because no reader could read it or git could not report the change. It blocks nothing. After a `Bash` event it judges each file's bytes once per session.
_Avoid_: gate, block (the edit is already on disk)

### Debt

**Ratchet mark**:
A committed record that one function is allowed to sit at a known CRAP; it may only tighten.
_Avoid_: exemption, baseline entry, whitelist

**Metric stamp**:
The marks file's first comment line, `crapkit-analysis=N lizard=X.Y.Z`: the rules that produced the marks' numbers. `ratchet seed` sets it to the metric of the run it read, and verify refuses marks whose stamp differs from the running metric.
_Avoid_: version header

**Pardon**:
A ratchet mark lifting a gate breach. `rescore --gate` and `verify` pardon a changed function only while its CRAP sits at or under its mark; the pre-commit hook pardons any marked function, because a staged blob has no coverage to score.

**Ratchet regression**:
A marked function whose CRAP rose above its mark; never overridable.

**Claim**:
A session's hold on a function it is refactoring, so two sessions do not take the same item.

**Override**:
A written reason attached to a verdict that accepts a gate violation once; recorded in the audit trail.

### The worklist

**Worklist**:
The ranked view of every admitted function: active rows first, dormant rows after.

**Floor**:
The minimum complexity for admission to the worklist.

**Hot promotion**:
Admission under the floor because the file changes often.

**Active / dormant**:
Active rows are ranked by risk; dormant rows have no recent churn.

**Churn window**:
The months of history churn reads (`churn_window_months`), counted back from HEAD's commit date, never from today's. A commit counts while its commit date is at or after the window's cutoff; its recency weight reads the author date. The cutoff is that many months before HEAD's commit date on the UTC calendar, the same instant in every time zone.
_Avoid_: floor for the window's start (Floor is worklist admission); call it the cutoff

**Shallow clone**:
A checkout that holds only part of its history (`git clone --depth N`, the `actions/checkout` default). Churn, mark ages and repayments count only the commits it holds, so worklist, next-item, brief and ratchet report carry `shallow: true` and print one line naming `fetch-depth: 0`, and `ratchet report --enforce` refuses to judge the debt policy there.

### Mutation

**Killed mutant**:
A mutant whose suite failed a test, or ran past `mutation_timeout_seconds`. A timeout counts as killed and is reported apart, as `timed_out`.

**No verdict**:
A mutant whose suite ran no test (exit 5, pytest's "no tests collected"). No test judged it, so it is in neither `killed` nor `survived`, and the kill rate leaves it out.
_Avoid_: killed, for a suite that never ran a test

### What crapkit prints

**Next step**:
The command a refusal or note tells its reader to run next. It names `crapkit` when PATH finds this installation's console script, and otherwise the running interpreter spelled with forward slashes, so Git Bash, cmd.exe and PowerShell run it as printed. An interpreter path that holds a space is quoted, and PowerShell runs that line with `& ` typed in front. The brief packet's `commands.*` always say `crapkit`.

**Typed path**:
A path a message quotes back the way the reader typed it, in single quotes with one backslash where they typed one. A lane or scope name keeps its repr.
