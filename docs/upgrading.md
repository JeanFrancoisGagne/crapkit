# Upgrading Crapkit

Upgrade the CLI with the installer that owns it, then refresh the measurements in
each repository. Saved runs describe the rules and source they measured; an upgrade
does not turn those runs into measurements of the new reader.

| Installation | Upgrade command |
|---|---|
| pip in the active environment | `python -m pip install --upgrade crapkit` |
| pip with the Python coverage extra | `python -m pip install --upgrade "crapkit[py]"` |
| uv tool | `uv tool upgrade crapkit` |

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

## Measure before changing marks

0.8.1 moves the reader to analysis version 12, so a marks file stamped under 11
needs one re-seed; [analysis version 12](#analysis-version-12) says what moved.
0.8.0 moved it from 10 to 11, and [analysis version 11](#analysis-version-11) says
what that moved. The package upgrade rebuilds the versioned analysis cache
automatically, and the first `inventory` or `coverage` after it analyzes every file
again. That run's
[twin-key note](ratchet.md#twins-one-name-several-functions) names the first five
files that give one name to several functions and ends with `... and N more file(s)
define a name more than once`. Restart each client's MCP session after upgrading so
its running server uses the new code.

Keep a copy of the committed ratchet and its diff before an upgrade. In each repo:

```sh
crapkit doctor
crapkit coverage --export .crapkit/current-functions.tsv
```

Resolve doctor failures, then inspect the fresh run. `coverage` writes a measurement
without applying the ratchet. Compare the saved marks with that measurement before
running `ratchet seed`, and finish with `crapkit verify` after reviewing and committing
any mark changes.

| What changed | Required action |
|---|---|
| Analysis or lizard stamp | Follow the [metric stamp rules](ratchet.md#the-metric-stamp). Comparisons refuse incompatible stamps. |
| Function membership or same-line identity | Review the [saved-mark mapping](ratchet.md#reconcile-saved-marks) before changing keys or stamps. |
| Coverage or JUnit producer | Run a fresh lane and resolve [artifact admission errors](lanes.md#a-junit-that-says-the-run-did-not-finish). |
| Shared exports or portable baselines | Upgrade readers before writing [encoded records](portable-records.md) for them. |

### 0.8.1 on coverage 7.6 to 7.13.0

Install coverage.py 7.13.1 or newer where each coverage.py lane runs:
`pip install "coverage>=7.13.1"`, or `pip install "crapkit[py]"` when crapkit shares the
suite's venv. 0.8.1 reads a function's span from the `start_line` coverage.py writes from
7.13.1, and a lane whose report has none fails at exit 5 with
`install coverage>=7.13.1 and rerun the lane`. A report that carries `start_line` scores as
it did in 0.8.0.

On the older coverage a nested function took its encloser's coverage. On the new one it
scores its own region, which is the version 12 change below: re-seed once as that section
says. A mark such a function already carried can sit under its new score, and `ratchet
seed` never raises a mark, so `verify` names it in a `RATCHET` line at exit 7 on a function
the diff never touched. Compare the export above with the marks before you seed, so each
such function is on your list; the ratchet page says how a rise is
[accepted where a reviewer sees it](ratchet.md#overrides-and-the-audit-trail).

### Analysis version 12

0.8.1 reads a coverage.py function region from the `start_line` coverage.py writes on
every function from 7.13.1, and refuses a report without it at exit 5. Coverage 7.6 to
7.13.0 write none, and 0.8.0 took a region's start from its body: a nested function's
`def` line sits in its encloser's region, so a nested function that never ran joined its
encloser and scored as half covered. A report that carries `start_line` scores as it did
in 0.8.0, so a repo already on coverage 7.13.1 or newer sees no score move there.

It also reads two kinds of source bytes as text where 0.8.0 did not, and each changes
some functions' names or numbers:

- A source file that opens with a UTF-16 byte-order mark, as PowerShell 5.1's
  `Out-File` and the ISE save it, scores its functions. 0.8.0 read it as empty, so its
  functions appear for the first time and one over its ceiling fails the gate the next
  time its file changes.
- An identifier that holds one of the five bytes cp1252 leaves undefined (0x81, 0x8D,
  0x8F, 0x90, 0x9D) keeps its name, the byte read as the letter U+01NN. 0.8.0 keyed
  such a function as `�( x )`, `(anonymous)` or, in C, `if( x)` at ccn 1; it now
  keys as `cafƁ( x )` at its own ccn, so a mark under the old key names a function the
  run lacks.

The stamp records the rules either way, so every marks file re-seeds once. The first
`inventory` or `coverage` after the upgrade analyzes every file again.

After upgrading coverage.py where a lane needs it, in each repo:

```sh
crapkit coverage
crapkit ratchet prune
crapkit ratchet seed
```

`coverage` measures under version 12, and `ratchet seed` stamps the marks with the metric
of the run it reads, so a seed from a run 0.8.0 measured keeps the old stamp and `verify`
keeps refusing. `ratchet prune` goes first, as it did for version 11, and changes nothing
while every marked function is still in the run; where a UTF-16 source or a name holding
one of those five bytes moved a key, it drops the mark left under the old one. When a failed verify pins the baseline,
seed and prune both read the pinned run: pass the new run's id to each, `crapkit ratchet
prune --baseline N` then `crapkit ratchet seed --baseline N`; their lines and verify's
refusal name it. Review the diff and commit it before the next `crapkit verify`.

### Analysis version 11

0.8.0 reads Python defs in five new ways. Each one changes some functions' names or
numbers, and the stamp records the rules, so every marks file re-seeds once:

- A def with a PEP 695 type parameter list is named by its name. `def f[T](a: int):`
  read `]( a : int )`, and every generic def in a file that took the same parameters
  collided on that key. A file refused for a generic def with no annotated parameter
  now scores.
- A def nested three or more deep names each enclosing def once: `a.b.c( x )`, where
  it read `a.a.b.c( x )`.
- A def whose body sits on its colon line, such as `def one(x): return x`, is listed
  as its own function. Before, no report showed it and the lines after it counted
  toward it. A def that encloses one can gain conditions it had lost. A same-named
  def after it moves to the next twin key: where a one-line `f( x )` sits above a
  multi-line `f( x )`, the multi-line def is now `f( x )#2`, a mark recorded under
  `f( x )` binds the one-line def, and `ratchet seed` marks `f( x )#2` if it is over
  its ceiling.
- Cognitive complexity and nesting count a def's body from the colon that ends its
  signature, so a one-line body counts and a signature's continuation lines do not.
- A Python file that ends inside a def's signature is refused and names that def;
  before, only a nested def was, and the file scored without it.

The one-line change moves `ccn` for the def and for the defs whose lines it used to
take, and a generic def with a constrained bound and a line break after a default,
which read two lines at ccn 1, now reads its whole body. A newly listed def, or an
enclosing def that read short before, can be over its ceiling and fails the gate the
next time its file changes. Under a coverage.py lane a def whose body starts on the
line its signature ends, a one-line def or a body on the last line of a signature
that spans several lines, scores as uncovered with remedy `split-lines`, because
coverage.py reads that body as the `def` statement that runs at import.

Version 11 also reads JavaScript and TypeScript template literals whole. lizard ended
a template at the first backtick inside it, so a template nested in another's `${...}`,
an escaped backtick, or a brace inside a string within `${...}` hid every function
after it in the file: each was folded into the function around it or not listed at
all. Those functions are now listed, and the function that held them reads only its
own lines and branches. A newly listed function can be over its ceiling and fails the
gate the next time its file changes. A function written inside `${...}` is still not
listed.

After upgrading, in each repo:

```sh
crapkit coverage
crapkit ratchet prune
crapkit ratchet seed
```

`coverage` measures under version 11, and `ratchet prune` drops the marks left under
the old names. `ratchet seed` then stamps the marks with the metric of the run it
reads, so a seed from a run 0.7.x measured keeps the old stamp. Prune goes first
because a marks file with no `# crapkit-keys=1` line keeps the old key format while
any mark names a function the run lacks, and that format cannot key a function that
shares its start line with another, so seed refuses to add one. When a failed verify
pins the baseline, seed and prune both read the pinned run: pass the new run's id to
each, `crapkit ratchet prune --baseline N` then `crapkit ratchet seed --baseline N`;
their lines and verify's refusal name it. Review the diff and commit it before the
next `crapkit verify`.

### Analysis version 10

Analysis version 10, in 0.7.0, replaced version 9 from 0.6.0. It separates
JavaScript and TypeScript expression callbacks that older readers missed. Current
rows also carry an `occurrence` for functions sharing a start line. These changes can
shift anonymous ordinals even when functions begin on different lines. Fresh
coverage cannot prove which old function owned a mark. A refusal asks for that
review; replacing a stamp alone does not complete it.

Unambiguous legacy keys remain readable. Ambiguous groups and anonymous JS/TS marks
without current reader proof require the procedure in
[same-line function identity](ratchet.md#same-line-function-identity). Existing claims
without that proof continue to hold their whole name group until released,
removed by an explicit `runs prune` under its age rule, or the whole group becomes
healthy. Ordinary queue reads do not expire claims.

## Saved state and command behavior

Keep `.crapkit/crap.sqlite`: it holds run history, test baselines and override audits.
Commands manage disposable analysis and history caches themselves. Use
`crapkit runs list` to inspect the trusted baseline; a failed verify still prevents
a newer coverage run from silently becoming the baseline.

Automatic reuse requires the same clean HEAD and unchanged configuration,
environment and artifact bytes. Since 0.8.0 a lane can list the paths its command
reads as `inputs`, and `--reuse-unchanged` then reuses it across commits while
nothing under those paths, its lane table or its `env` changed. The reuse proof
covers that field, so the first `--reuse-unchanged` after upgrading to 0.8.0 reruns
every lane once, and an older stamp without the proof reruns its lane; each rerun
prints `lane 'x': rerunning:` and the reason. Ignored files, installed dependencies and
external services remain outside this proof. See
[artifact reuse](lanes.md#reusing-artifacts) before choosing an explicit
saved-artifact read.

`test_retention_days` and `test_retention_count` are ignored. `crapkit doctor` warns
once for each key a config sets; delete them. Test evidence retention is now the
development runner's `--retention-days` and `--retention-count`, and
`crapkit clean` recovers abandoned mutation checkouts only.

Every mutation worker uses a detached worktree, including a single worker.
The normal pool is retained; concurrent callers use temporary worktrees that
cleanup removes. Budget disk space for the pool and use
`crapkit mutate --drop-pool` to reclaim it.
[Mutation worktrees](configuration.md#mutation-worktrees) owns the input, link,
concurrency and cleanup rules. [Command cleanup](lanes.md#the-kill-takes-the-whole-process-tree)
describes Windows Jobs and POSIX process groups. These are process-lifetime controls,
not a sandbox for configured test commands.

Git filenames retain their literal identity through scoring and output. Coverage
paths still have to name the measured tree. Use the documented
[CLI path rules](configuration.md#file-paths-and-root-discovery) and
[portable record reader](portable-records.md) when automating around exports.
JSON stays at `schema: 1`; consumers must accept added fields.

## Missing values that 0.8.1 names

Before 0.8.1 some commands read a value nobody measured as zero, empty or passing.
0.8.1 names each one instead. These changes can move an exit code; each has its own
paragraph below with what to change:

| What the job meets | Command | 0.8.0 exit | 0.8.1 exit |
|---|---|---|---|
| a changed file no reader could read | commit hook, `rescore --gate`, `check_gate`, `verify` | 0 | 6 |
| an edit that leaves a file no reader can read | `claude-hook` | 0 | 2 |
| a declared junit it reused and cannot read | `verify --reuse-artifacts` | 0 | 5 |
| a debt policy key in a shallow clone | `ratchet report --enforce` | 0 or 1 | 4 |
| a coverage artifact missing a count | `coverage`, `verify` | 0 | 5 |
| a `.crapkit/artifacts.json` that cannot be read | `coverage --reuse-artifacts`, `verify --reuse-artifacts` | 0 | 5 |
| a deleted or emptied marks file, and a marked function that rose | `verify` | 0 | 7, or 4 when the clone lacks the history |
| a failure the baseline's own commit had, where the baseline recorded no failure list | `verify` | 8 | 0 |
| a failure a 0.7.x verify retried to a pass, failing again | `verify` | 0 | 8 |

The mutate counts below keep their meaning and gain two fields.

**Files no reader could read.** A file crapkit's readers refuse, such as a TypeScript
expression-arrow body with `<` before a comma, still scores as zero functions in a run.
Every gate read those zero records as nothing over the ceiling, so a function beside
the refused construct passed the commit hook, `rescore --gate`, `check_gate` and verify.
From 0.8.1 each of them exits 6 on such a file when a change touches it, and
`claude-hook` exits 2 after the edit. Before you upgrade the hook or CI, run
`crapkit coverage` and read the files it names in
`crapkit: N file(s) could not be tokenized; ...` (or the WARN lines of
`crapkit doctor`), then for each one change what the reason names so a reader can parse
it, or list it under `[exclude]` globs in `crapkit.toml` to leave it ungated. A file no
change touches blocks nothing.

**verify over a junit it cannot read.** `verify --reuse-artifacts` over a lane that
declares a `results_artifact` it finds missing or unreadable passed at exit 0 and stored
a trusted run. It now exits 5 and stores no run, as a real run of that lane does, and
the refusal ends `run verify without --reuse-artifacts so the lane writes it again`. A
CI job whose `verify --reuse-artifacts` found no readable junit for such a lane now
fails where it passed. `coverage --reuse-artifacts` over the same junit still warns and
scores on. A lane that declares no `results_artifact` still passes, with a stderr line
and the lane under `lanes_without_results` in `verify --json`.

**Shallow clones.** A depth-1 checkout holds one commit, so churn counts one commit per
file, every ratchet mark reads 0 days old and no repayment shows.
`ratchet report --enforce` with `debt_max_age_months` or `repayment_min_per_30d` set now
refuses there with exit 4, ending
`set fetch-depth: 0 on the checkout or run git fetch --unshallow`. A CI job that ran it
on a default `actions/checkout` judged its policy on those zeros: an age limit passed
and a repayment quota failed. Set `fetch-depth: 0` on the checkout. `worklist`,
`next-item`, `brief` and `ratchet report` without `--enforce` still answer, print one
stderr line that names what they counted, such as
`warning: churn counts read only the commits this clone holds` from `worklist`, and add
`shallow: true` to their JSON (`false` in a full clone).

**Mutants with no test verdict.** `mutate` counted a mutant whose suite exited 5, which
means no test ran, as killed, and printed nothing else. It still counts as killed, so
`killed`, `survived`, `mutants` and the rate read as they did in 0.8.0. The progress
line now says `no verdict: the suite ran no test (exit 5), counted killed`, the text
summary adds `no verdict: N of the K killed ran no test (exit 5), so no test caught
them`, and `--json` adds `no_verdict`, a count inside `killed`. A mutant whose suite
timed out is counted the same way under `timed_out`. A script that wants the rate over
the mutants a test judged divides `killed - no_verdict` by `mutants - no_verdict`. JSON
schema 2, in a later release, takes no-verdict mutants out of `killed` itself.

**Coverage artifacts missing a count.** coverage.py and istanbul write every count a
function's score reads. A report something else rewrote, such as a hand merge of shards
or a format converter, could drop one, and crapkit read the gap as zero: a function that
ran scored cov 0, or a dropped branch counter moved a function from `add-tests` to `ok`,
at exit 0. From 0.8.1 the lane fails, so `coverage` and `verify` exit 5. The refusal names
the report, the file and the function or id, and ends
`` regenerate the report with `coverage json` `` for coverage.py, or `regenerate the
artifact with the coverage tool, or merge shards with one that keeps every counter` for
istanbul. A report the coverage tool wrote is never refused for this;
[what the istanbul parser reads](lanes.md#what-the-istanbul-parser-reads) lists each form
for both formats.

**An unreadable `.crapkit/artifacts.json`.** The record that a lane's last attempt failed
lives in that file. A file that did not parse, or whose lane entry was not an object, read
as no record, so `--reuse-artifacts` scored the artifact the failed attempt left.
`coverage --reuse-artifacts` and `verify --reuse-artifacts` now exit 5 on each lane while
the file cannot be read, and name both fixes: rerun the lane
(`crapkit coverage --lane NAME`), or delete `.crapkit/artifacts.json` to reuse the files
as they stand.

**A deleted or emptied marks file.** verify read a missing `crapkit-ratchet.tsv`, or one
holding only blank lines, as a repo that never marked any debt, so a commit that deleted
it let a marked function's CRAP rise at exit 0. verify now judges against the newest marks
the history since the baseline committed and exits 7 on a rise. It prints
`warning: crapkit-ratchet.tsv is missing, but commit C, the newest since the baseline to
hold it, has N mark(s)` with the `git checkout` that restores the file, and never writes
those marks back. A clone that does not hold that history exits 4 and says what it could
not read, ending with the fetch that brings it when the clone is shallow.

**Failure lists read from an older run.** When the baseline recorded no failure list for a
lane, verify now reads the newest trusted run behind it that recorded one, and a line
names that run. A test that already failed at the baseline's own commit came back as
`NEW FAILURE`, exit 8; it is now forgiven when that older run recorded it failing. When no
run recorded a list, every failure still counts as new, exit 8, and verify says it may
predate the change. A verify stored by 0.7.x kept a failure that passed its flake retry in
its failure list, and read as a baseline it forgave a later real failure of that test at
exit 0; verify now reads that run's list from the run behind it, so the failure exits 8.

**Artifact stamps from 0.4.15 or older.** The record that stops `--reuse-artifacts` from
scoring the artifact a failed lane left behind lives in `.crapkit/artifacts.json`, and
crapkit writes it from 0.5.0 on. A stamps file written by 0.4.15 or older holds no such
record, and nothing else on disk says the last attempt failed, so the first
`coverage --reuse-artifacts` after the upgrade scores that leftover as a good run. After
upgrading from 0.4.15 or older, run `crapkit coverage` once without `--reuse-artifacts`
before any reuse. Every lane runs: one that works writes its artifact and stamp again, and
one that still writes nothing exits 5 and records the refusal the old release never
wrote, so the next reuse refuses it with `wrote no artifact on its last attempt`.
## Text that is not UTF-8

In 0.8.1 a byte that is not UTF-8 in a commit, a file name, a report or an MCP frame
reads as U+FFFD or is refused by name; 0.8.0 ended the command with a traceback. These
answers change for automation that reads exit codes, lane output or the files crapkit
writes:

| What | 0.8.0 | 0.8.1 | Action |
|---|---|---|---|
| A file a scope takes whose name git holds in bytes that are not UTF-8 | every command exited 1 with a traceback | `inventory`, `coverage`, `verify`, `doctor`, `watch` and `hook-precommit` exit 3 naming the file and `git mv`; a name no scope takes is a warning, listed in `unreadable_names` under `--json` | Rename the file to UTF-8 ([file paths](configuration.md#file-paths-and-root-discovery)) |
| An untracked file named in bytes that are not UTF-8 under a lane's `inputs` | `coverage` exited 1 with a traceback | `coverage --reuse-unchanged` reruns the lane, as for any other new file | None |
| A POSIX locale that is not UTF-8 (`LANG=en_US.ISO-8859-1`) | a path with an accent named no file, so `coverage` skipped it as missing | `crapkit` restarts itself once as `python -X utf8`; lane and mutation children keep your locale and environment, and a coverage.py key the child spelled in the locale's encoding reads back as the file it names | None ([file paths](configuration.md#file-paths-and-root-discovery)) |
| Lane, flake-retest and mutation children | wrote in their locale's encoding (cp1252 on most Windows machines), so a test printing an emoji failed under crapkit and passed in a terminal | start with `PYTHONIOENCODING=utf-8` on every OS, over any value inherited from the shell | A child that must write another encoding sets it in the lane's `env` (`env = { PYTHONIOENCODING = "cp1252" }`), which crapkit leaves alone ([lanes](lanes.md#a-python-child-writes-its-log-in-utf-8)) |
| The measurement owner's stderr | discarded | `.crapkit/owner.log`, empty after a run that ends normally; each exit-5 `measurement owner stopped` line names it | Read the file the line names ([lanes](lanes.md#when-the-measurement-owner-stops)) |
| A marks file holding a cp1252 byte, or saved as UTF-16 | every reader exited 3 | read with that byte as U+FFFD, or as UTF-16; a write that would save U+FFFD in place of a name exits 3 naming the byte | Fix the byte in the mark's name ([ratchet](ratchet.md#how-the-file-is-read)) |
| A root `package.json` in UTF-16 or holding a byte that is not UTF-8 | `init` exited 1 with a traceback, after it wrote `crapkit.toml` | `init` exits 3 before it writes any file | Save it as UTF-8, then run `init` again |
| `init` over an existing `crapkit.toml` | always exited 3 | exits 0 when it had `.gitignore` entries to add, and leaves `crapkit.toml` as it was | A script that read exit 3 as "already set up" reads the file instead |

## Plugin and MCP clients

After upgrading the intended CLI, refresh Claude Code's marketplace before updating
its user-scope plugin:

```sh
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
crapkit doctor --plugin-root
```

Restart existing Claude Code sessions to apply the plugin update. The doctor check
compares the installed plugin with the `crapkit` launcher on PATH. It does not reload
an existing session. A failed, malformed or undecodable launcher probe is a failure,
not a version match.

For an installed Codex plugin, refresh its marketplace and install the current copy:

```sh
codex plugin marketplace upgrade crapkit
codex plugin add crapkit@crapkit
codex plugin list --marketplace crapkit --json
crapkit doctor --plugin-root PATH
```

Use the installed Codex plugin directory for `PATH`, not the marketplace's source
checkout. In the default cache this is
`~/.codex/plugins/cache/crapkit/crapkit/VERSION`, using the installed version from
the listing. With no explicit path, doctor checks Claude Code's cache instead.
Use the three skills and MCP server in Codex. The advisory hook instructions in
the README configure Claude Code's PostToolUse event. Start a new Codex task to
load updated plugin skills and tools.

Start fresh MCP sessions after upgrading so their server uses the installed code. Other
MCP clients use the [stdio setup](agent-json.md#mcp-server); skill copies and custom
hook entries need their own update. Run packet commands as supplied, in the
environment that owns the intended CLI, to retain literal arguments and exit codes.

## Windows launcher locks

A running `crapkit.exe mcp` can hold the console launcher open. An upgrade then
fails with Windows error 32 even if some package files were already updated.

1. Stop the Crapkit MCP server or the agent session that owns it.
2. Rerun the same upgrade command and require a successful installer result.
3. Check `crapkit --version`, restart the client, and check plugin compatibility.

Use the installer to repair the launcher instead of copying executables between
environments. The CLI version alone does not prove an interrupted install finished.

## Release evidence

The [implementation report](architecture/2026-09-07-implementation/REPORT.md)
records complete Windows source and Linux installed-wheel verification, independent
reviews and repeatable performance probes. Its benchmark tables distinguish
synthetic duplication input, fixture setup and a fixed unit subset from complete
suite runs. They do not claim a whole-suite speedup. Hosted CI dispatch and macOS
runtime execution were outside that local verification.
