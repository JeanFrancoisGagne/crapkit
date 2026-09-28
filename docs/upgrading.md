# Upgrading Crapkit

Upgrade the CLI with the installer that owns it, then refresh the measurements in
each repository. Saved runs describe the rules and source they measured; an upgrade
does not turn those runs into measurements of the new reader.

| Installation | Upgrade command |
|---|---|
| pip in the active environment | `python -m pip install --upgrade crapkit` |
| pip with the Python coverage extra | `python -m pip install --upgrade "crapkit[py]"` |
| pip --user | `python -m pip install --user --upgrade crapkit` |
| pipx | `pipx upgrade crapkit` |
| uv tool | `uv tool upgrade crapkit` |
| uvx | `uvx crapkit@latest --version` |
| pip from the git URL | `python -m pip install --force-reinstall --no-deps git+https://github.com/JeanFrancoisGagne/crapkit.git` |
| the Copilot CLI plugin | `copilot plugin marketplace update crapkit`, then `copilot plugin update crapkit@crapkit` ([below](#plugin-and-mcp-clients)) |
| the Docker image | in the crapkit clone it was built from, `git pull`, then `docker build -t crapkit .`, then restart the client |
| the pre-commit framework (Route 3) | `pre-commit autoupdate`, or set `rev` to the new tag |
| the GitHub Action | move `uses: JeanFrancoisGagne/crapkit@...` to the new tag |

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

The last four rows move what runs crapkit rather than the CLI. The Docker image holds its
own crapkit, installed from the clone at build time, and a container keeps the image it
started from. pre-commit and the Action install the release their `rev` or `uses:` tag
names, so move each one in the commit that re-seeds, as [the next
section](#a-team-upgrades-every-reader-before-the-re-seed-lands) says. prek reads the
same `.pre-commit-config.yaml` and its `rev`.

`pip --user` puts the launcher in the user scripts directory: `~/.local/bin` on Linux,
`~/Library/Python/3.12/bin` for a python.org Python 3.12 on macOS, and
`%APPDATA%\Python\Python312\Scripts` for Python 3.12 on Windows. When that directory is
not on PATH, pip says so on install, `WARNING: The script crapkit is installed in '...'
which is not on PATH`, and the `crapkit` your shell finds is some other install, or none.
Add the directory it names to PATH before checking the version.

Once uvx has fetched crapkit, plain `uvx crapkit` keeps running that release after a
newer one ships, and so does an agent config that starts `uvx crapkit mcp`. The uvx row
asks for the newest release by name; from then on plain `uvx crapkit` runs that one.
Restart each client whose MCP entry runs `uvx crapkit mcp`, since its running server
keeps the release it started with.

The git row is for an install from the tip of `main`. Commits there carry the last
release's version string until the next release, and pip keeps an installed crapkit
whose version matches, so the install line alone leaves the old code in place;
`--force-reinstall` replaces it and `--no-deps` leaves lizard as it is.

## Downgrading

Install the older release by its number, with the installer that owns crapkit:

- pip: `python -m pip install "crapkit==0.7.6"`
- pipx: `pipx install --force "crapkit==0.7.6"`
- uv tool: `uv tool install "crapkit==0.7.6"`

The older release reads the store a newer one wrote: `doctor`, `coverage` and
`next-item` work as before. A downgrade across an analysis version stops `verify` at
exit 3, because the committed marks carry the newer stamp; the refusal names both
stamps. Measure with the older release, then re-seed:

```sh
crapkit coverage
crapkit ratchet seed
crapkit verify
```

`ratchet seed` rewrites the marks file's stamp to the older analysis version. Commit
that only when the whole team moves back: a clone on the newer release refuses the
file in turn, with the same exit 3. From 0.8.1 on, `ratchet seed` and `ratchet prune`
refuse marks a newer release wrote; going back to such a release, restore the
`crapkit-ratchet.tsv` it last committed from git history instead of re-seeding.

## A team upgrades every reader before the re-seed lands

The marks file carries the analysis version of the crapkit that seeded it, and an older
crapkit refuses marks a newer one seeded. So the commit that re-seeds under a new analysis
version is the last step of a team's upgrade, not the first. Upgrade every clone, the CI
pin and the pre-commit `rev` before you commit marks seeded under the newer analysis, and
move the Action's `uses:` pin in the same commit as the re-seed, so no job measures those
marks with the older release.

The [launcher token](configuration.md#the-launcher-token) waits for the same readers.
0.8.0 does not know `{python}` or `{python:DIR}` and hands the shell the token as
written, so every lane whose command holds one fails at exit 5 in a clone, a CI job, an
Action pin or a pre-commit `rev` still on 0.8.0. The lane's `last output: $ {python:.venv}
-m pytest ...` is followed by `The filename, directory name, or volume label syntax is
incorrect.` under cmd.exe, or by `/bin/sh: 1: {python:.venv}: not found` and `(exit 127)`
under sh, then `crapkit: every lane failed`. Upgrade every clone, the Action's `uses:` pin
and the pre-commit `rev` before you commit a lane `command`, `retest_command`,
`[crapkit.scoped_tests]` template or `mutation_command` that holds the token: commit it
with the re-seed or after it. A teammate who meets those lines upgrades crapkit.

A reader you missed exits 3 on the committed marks. From 0.8.1 an older release that meets
marks a newer one wrote says so, `the marks come from a newer crapkit than this install`,
asks for an upgrade, and its `ratchet seed` and `ratchet prune` refuse the file. 0.8.0 and
older releases instead name `crapkit ratchet seed`, and that seed restamps the team's marks
under the older analysis, after which every upgraded teammate's verify refuses them. Upgrade
that reader rather than follow the line.

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

The server that outlives this upgrade runs 0.8.0, and a server from 0.8.0 or earlier
does not check. Its first call after the upgrade can fail with a JSON-RPC `-32603` error
such as `TypeError: _operation() takes 2 positional arguments but 3 were given` or
`ToolError: measurement owner stopped before confirming ownership`, and the restart
fixes that. A `crapkit watch` from 0.8.0 can end in a Python traceback at its next
rescore; start it again.

From 0.8.1 on, the server checks. On the next upgrade after this one, a 0.8.1 server
that outlived it answers every tool call with the restart instead of running it:

    crapkit was upgraded from 0.8.1 to 0.8.2 while this MCP server ran, and the server still runs 0.8.1's code, which cannot load the new files. Restart the crapkit MCP server (reconnect it in your client, or start a new session), then call list_runs again.

A 0.8.1 `crapkit watch` stops at its next rescore after such an upgrade, exits 1 and says
to restart it.

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
  0x8F, 0x90, 0x9D) keeps its name, the byte read as the letter U+01NN. In Python,
  TypeScript and C, 0.8.0 keyed such a function as `�( x )`, `(anonymous)` or `if( x)`
  at ccn 1; it now keys as `cafƁ( x )` at its own ccn, so a mark under the old key names
  a function the run lacks. In Go, Java, Rust, Swift, shell and PowerShell, and in a C
  function whose `if` has no braces, 0.8.0 scored no such function at all: it appears
  for the first time, as a UTF-16 source's functions do, and one over its ceiling fails
  the gate the next time its file changes.

The stamp records the rules either way, so every marks file re-seeds once. The first
`inventory` or `coverage` after the upgrade analyzes every file again.

Two coverage joins move scores on the same tree and config without moving the stamp. On
a case-insensitive disk, a coverage.py key in another letter case than the directories
list, such as `PKG/mod.py` for git's `pkg/mod.py` (coverage.py on macOS keeps the case
the import system handed it), and an istanbul key whose directories below the checkout
are in another case, such as `SRC/app.ts` for git's `src/app.ts`, now join git's file.
0.8.0 left that file's functions `untested` at cov 0, so they move to `measured` and
their CRAP falls. A mark set while they read untested sits above the new number, and
`verify` tightens it as it tightens any mark that falls.

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

Open claims need no step. A claim taken before the upgrade on a def nested three or
more deep saved the old name, `a.a.b.c( x )`. Once `coverage` has measured under
version 11, `next-item` and `brief --batch` skip that def under its new name, `verify`
closes the claim when the def reaches its ceiling, `brief` counts the claim among the
def's `attempts`, and `crapkit claims release PATH NAME` takes either name. `crapkit
claims` still lists the name the claim was taken under. Two of the renames leave a
claim nothing to follow: a generic def that read `]( a : int )`, since that name holds
no def name, and a def that moved to the next twin key because a one-line def of the
same name above it is now listed. Release a claim on either before upgrading, and take
the def again with `crapkit next-item --claim` after the first `coverage`.

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
prints `lane 'x': rerunning:` and the reason. Since 0.8.1 every lane's proof also holds
the crapkit version, so each upgrade reruns every lane once with `the crapkit version
changed`. Ignored files, installed dependencies and external services remain outside
this proof, and the line that reuses a lane names what its proof leaves out. See
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
| a marks file renamed with `git mv`, a mark past `debt_max_age_months` | `ratchet report --enforce` | 0 | 1 |
| a marks file renamed with `git mv`, `repayment_min_per_30d` met before the rename | `ratchet report --enforce` | 1 | 0 |
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

**A renamed marks file.** After `git mv crapkit-ratchet.tsv debt.tsv`, the marks file's log
started at the rename, so every mark read 0 days old and no earlier repayment counted:
`ratchet report --enforce` passed an age limit the whole history fails, and failed a
repayment quota it meets. The report now reads the old name's log on from the rename, so
a job that renamed its marks file is judged on the whole history again, and an age limit
it passed may now exit 1: repay the marks it names, or raise `debt_max_age_months`.

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

**Coverage artifacts with a field of the wrong shape.** 0.8.0 read a coverage.py file
entry whose `functions` was an array, such as `"functions": []`, as a report without
branch data, and a `missing_lines` entry that was not an integer, such as `"5"`, as a dead
line that matched no line, both at exit 0. From 0.8.1 the lane fails, so `coverage` and
`verify` exit 5, naming the file, the field and the JSON type it holds:
`src/a.py: functions holds an array, not an object` or
`src/a.py: missing_lines[0] holds a string, not a line number`, then
`` regenerate the report with `coverage json` ``. A report coverage.py wrote holds neither.

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

## The commit gate in 0.8.1

Two changes move the commit hook's exit code, in CI and at the top of a monorepo:

| What the hook meets | 0.8.0 exit | 0.8.1 exit |
|---|---|---|
| `pre-commit run --all-files`, the form pre-commit.ci and pre-commit/action run, on a tree holding a function over its ceiling that no ratchet mark signs | 0 | 6 |
| a commit in a repo whose `crapkit.toml` sits in a directory below the git top, or whose hook was armed before `crapkit init` | 3 on every commit | 0, or 6 when a staged file in a crapkit root below breaches |

**`--all-files` judges every tracked file.** pre-commit's `--all-files` stages nothing and
starts no commit, so 0.8.0 judged an empty diff and passed. 0.8.1 tells a commit by
`GIT_INDEX_FILE`, which git sets for the hooks a commit runs. Outside a commit, with
nothing staged, the hook prints `crapkit gate: nothing is staged and no commit is running,
so every tracked file was judged` and exits 6 on each function over its ceiling that the
committed ratchet does not mark. Before you move the `rev` to the 0.8.1 tag, run
`crapkit hook-precommit` by hand with nothing staged: seed the debt it names with
`crapkit ratchet seed` and commit the marks, or decompose those functions. A commit still
judges only what it stages.

**A hook at the top of a monorepo.** git runs the hook at the repository's top. With
`crapkit.toml` only in a directory below, 0.8.0 refused every commit there with exit 3
and `no crapkit.toml at TOP`, a docs-only commit included, and so did a hook armed before
`crapkit init`. 0.8.1 runs the gate in each crapkit root below that owns a staged file
and passes a commit that stages nothing under any `crapkit.toml`, with one note on
stderr. A team that committed with `--no-verify` to get past the exit 3 can stop, and a
staged breach under a root below now exits 6.

## Other exit codes that move in 0.8.1

The tables above and [text that is not UTF-8](#text-that-is-not-utf-8) cover the missing
values, the commit gate and bytes that are not UTF-8, and [config
paths](#config-paths-that-081-reads-on-every-os) ends with three more. These are the
rest:

| What the job meets | Command | 0.8.0 exit | 0.8.1 exit | Action |
|---|---|---|---|---|
| `git checkout main` after a passing verify on a feature branch | `verify` | 4, blaming a rebase or amend | its verdict against main's own run; 4 only when no trusted run sits behind HEAD | None |
| a pytest lane whose coverage.py is older than 7.13.1 | `doctor` | 0 | 1, a FAIL naming the install line | Install `coverage>=7.13.1` where the lane runs |
| a run under uvx, `uv run --with` or `pipx run` | `doctor --plugin-root` | 0 | 1, ``FAIL no `crapkit` on PATH`` | `uv tool install crapkit`, or `pipx install crapkit` |
| a user-scope plugin older than a project-scope one, no PATH given | `doctor --plugin-root` | 0 | 1 | Update the older install |
| a flag this crapkit does not know | `claude-hook` | 2, with the usage block | 0, with one line naming the version skew | Upgrade crapkit, then run `crapkit doctor --plugin-root` |
| an advisory under Cursor, Copilot CLI or VS Code | `claude-hook` | 2 | 0, the advisory as one JSON object on stdout | A wrapper reads `additionalContext`; Claude Code keeps exit 2 |
| a file an agent's shell wrote under a scope, named in bytes that are not UTF-8 | `claude-hook` | 0 | 2, an advisory naming the file and the rename | Rename the file to UTF-8 |
| a file argument spelled `SRC\app.ts` on a case-insensitive disk, or `/c/...`, `/mnt/c/...` or `\\?\C:\...` on Windows, holding a breach | `rescore --gate`, `check_gate` | 0, 0 functions judged | 6, and `gate.ok` false | Decompose the function it names |
| `python -m pytest Tests` under `testpaths = ["tests"]` on a case-insensitive disk | `coverage` | 3, refused as narrowing | runs as the whole suite | Drop a `full_suite = false` set only to get past it |
| an istanbul key naming this checkout by an 8.3 name, a junction or symlink, a lower-case drive or a `\\?\` prefix | `coverage`, `verify` | 5, the lane FAILED | 0, the lane scores | None |
| a coverage artifact that starts with a UTF-8 byte-order mark | `coverage`, `verify` | 5 | 0 | None |
| a coverage.py region with no `summary` object, or an istanbul `fnMap` entry with no `loc.end.line` | `coverage`, `verify` | 0 | 5 | Regenerate the report with the coverage tool |
| Windows, a process started with no `USERPROFILE`, `HOMEDRIVE` or `HOMEPATH` | `doctor`, `coverage`, `check_config` | 1, `RuntimeError: Could not determine home directory.` | 0, from the profile folder Windows reports; 5 with `no home directory` when nothing names one | Set `USERPROFILE` where nothing names a home |

## Values that move without an exit code

Three changes move text or JSON a script reads, with no exit code to flag them:

- Every message crapkit writes spells a dash ` - ` where 0.8.0 printed an em dash, the
  `message` of a `--json` error object included. A path or a function name crapkit quotes
  keeps its own characters. A script that matches a line matches ` - ` now.
- The churn window counts on the UTC calendar. On a machine whose local date is not the
  UTC date, a commit on the window's first day can move in or out of churn once, so
  `risk`, `weight` and `commits` in `worklist --json`, `next-item` and `brief --json` can
  differ from 0.8.0 on that machine. Scores do not move.
- A partial run's `crap_load` sums only the scopes it measured, the functions its
  `over_target` and `grade` count. 0.8.0 added a failed or skipped lane's functions at the
  cov-0 stand-in, so a run line read `0 over ceiling 6, CRAP load 32.0` where the measured
  scopes held 2.0. `coverage --json` and the run line report the smaller number, and
  `by_scope` still carries each unmeasured scope's own load.

## Text that is not UTF-8

In 0.8.1 a byte that is not UTF-8 in a commit, a file name, a report or an MCP frame
reads as U+FFFD or is refused by name; 0.8.0 ended the command with a traceback. These
answers change for automation that reads exit codes, lane output or the files crapkit
writes:

| What | 0.8.0 | 0.8.1 | Action |
|---|---|---|---|
| A file a scope takes whose name git holds in bytes that are not UTF-8 | every command exited 1 with a traceback | `inventory`, `coverage`, `verify`, `doctor`, `watch` and `hook-precommit` exit 3 naming the file and `git mv`; a name no scope takes is a warning, listed in `unreadable_names` under `--json` | Rename the file to UTF-8 ([file paths](configuration.md#file-paths-and-root-discovery)) |
| A `rescore` or `rescore --gate` argument naming a file whose name is not UTF-8 | ended with a traceback | exit 3 with the rename sentence when a scope takes the file; when no scope takes it, one `crapkit: left out` line on stderr, exit 0, and the gate judges 0 | Rename a scoped file to UTF-8. A script that expected a refusal for an unscoped one reads the stderr line |
| The `check_gate` MCP tool on a `path` whose name is not UTF-8 | `isError: true` with a Python traceback | a verdict: `gate.ok` false, `judged` 0 and the file in `gate.unread_files` when a scope takes it; `gate.ok` true, `judged` 0 and `unread_files` `[]` when none does; `baseline_run`, `baseline_commit` and `note` as in every verdict | Read `gate.unread_files` where the client caught the error |
| An untracked file named in bytes that are not UTF-8 under a lane's `inputs` | `coverage` exited 1 with a traceback | `coverage --reuse-unchanged` reruns the lane, as for any other new file | None |
| A POSIX locale that is not UTF-8 (`LANG=en_US.ISO-8859-1`) | a path with an accent named no file, so `coverage` skipped it as missing | `crapkit` restarts itself once as `python -X utf8`; lane and mutation children keep your locale and environment, and a coverage.py key the child spelled in the locale's encoding reads back as the file it names | None ([file paths](configuration.md#file-paths-and-root-discovery)) |
| Lane, flake-retest and mutation children | wrote in their locale's encoding (cp1252 on most Windows machines), so a test printing an emoji failed under crapkit and passed in a terminal | start with `PYTHONIOENCODING=utf-8` on every OS, over any value inherited from the shell | A child that must write another encoding sets it in the lane's `env` (`env = { PYTHONIOENCODING = "cp1252" }`), which crapkit leaves alone ([lanes](lanes.md#a-python-child-writes-its-log-in-utf-8)) |
| The measurement owner's stderr | discarded | `.crapkit/owner.log`, empty after a run that ends normally; each exit-5 `measurement owner stopped` line names it | Read the file the line names ([lanes](lanes.md#when-the-measurement-owner-stops)) |
| A marks file holding a cp1252 byte, or saved as UTF-16 | every reader exited 3 | read with that byte as U+FFFD, or as UTF-16; a write that would save U+FFFD in place of a name exits 3 naming the byte | Fix the byte in the mark's name ([ratchet](ratchet.md#how-the-file-is-read)) |
| A root `package.json` in UTF-16 or holding a byte that is not UTF-8 | `init` exited 1 with a traceback, after it wrote `crapkit.toml` | `init` exits 3 before it writes any file | Save it as UTF-8, then run `init` again |
| `init` over an existing `crapkit.toml` | always exited 3 | exits 0 when it had `.gitignore` entries to add, and leaves `crapkit.toml` as it was | A script that read exit 3 as "already set up" reads the file instead |

## Config paths that 0.8.1 reads on every OS

A committed `crapkit.toml` is read on every OS its collaborators use, and 0.8.0 read
some of its paths as the text typed. 0.8.1 reads each one the way git spells a path
([the rules](configuration.md#file-paths-and-root-discovery)). Three of those changes
can move what an existing config scores. None of them moves the analysis version or
asks for a re-seed of its own: the re-seed for [analysis version
12](#analysis-version-12) is the one 0.8.1 asks for. Before
upgrading, save `crapkit doctor --show-files` and a `crapkit coverage --export`; after,
run both again and compare the per-scope file counts and scores.

**Scopes that scored 0 files.** On a disk that ignores case, `paths = ["Src"]` for a
directory git lists as `src/` claimed nothing, and the scope scored 0 functions in 0
files while `doctor` FAILed it. It now claims `src/`. A scope path spelled absolutely
(`/home/dev/repo/web`, `/c/repo/web`, `/mnt/c/repo/web`, `\\server\share\web`,
`//server/share/web`) scored 0 files the same way; the loader now refuses it with exit
3 and names the relative path to write when it lands in this checkout. Once the path
names the directory, the scope's functions score, and each one over its ceiling fails
the gate the next time a change touches it. To mark that debt as it stands, run
`crapkit ratchet seed` after the first `crapkit coverage`, as for any newly scored file:
seed adds marks for functions that have none and never raises a mark it finds.

**`[exclude]` globs start excluding.** `src\gen\**`, `./src/gen/**`, `/src/gen/**` and
`src/gen/` excluded nothing in 0.8.0, so generated files stayed scored. They now read
`src/gen/**`, the files leave the corpus, and their rows leave the worklist. Ratchet
marks on functions that leave the corpus are held, not dropped. `doctor` now WARNs on
each glob that matches no tracked file, so a glob that still excludes nothing says so.

**`path_prefix` starts measuring.** `api\`, `./api/`, `.\api\`, `/api/` and, where the
disk ignores case, `API/` glued their own text onto every coverage key, so every
function in the lane's scopes scored untested. They now read `api/`, the lane's
coverage joins, and CRAP falls. Marks set while those functions scored untested sit
above the new numbers, and `verify` tightens them as it tightens any mark that falls.
On a commit 0.8.0 already measured, a drop past `tighten_max_jump` holds the mark with
a `NO TIGHTEN` line ([damping](ratchet.md#damping-a-measurement-that-bounces)), and the
next commit tightens it.

**The venv launcher `init` wrote.** A config that `crapkit init` wrote under 0.8.0
names the launcher of the OS it ran on: `.venv\\Scripts\\python.exe` on Windows (as the
TOML string spells it) or `.venv/bin/python` elsewhere, and a bare `python` or
`python3` where no venv carried pytest. Each can fail every lane elsewhere: a Windows
venv path on Linux, a Linux one under cmd.exe, a bare `python` on an Ubuntu without
python-is-python3. Swap
the venv launcher for `{python:.venv}`, with the venv's own directory in place of
`.venv`, and a bare name for `{python}`, in each lane `command`, `retest_command`,
`[crapkit.scoped_tests]` template and `mutation_command`. Then run `crapkit doctor` on
each OS. [The launcher token](configuration.md#the-launcher-token) lists what each
token becomes. 0.8.0 cannot read the token, so commit the swap only once every reader
runs 0.8.1, as [a team upgrades every reader](#a-team-upgrades-every-reader-before-the-re-seed-lands)
says.

Three exit codes change for scripts that read them. A root on a Windows network share
(`--repo \\server\share\repo`, or a working directory there) exits 3 before any lane
starts, where 0.8.0 ran every lane in `C:\Windows`; the refusal gives the `net use`
line that maps the share to a drive letter. A lane whose `cwd` names no directory
fails as a lane, `cwd <path> is not a directory, so the command never ran; fix cwd =
'nope' for this lane in crapkit.toml, or create that directory`, and a run with no lane
left exits 5, where 0.8.0 ended in a Python traceback and exit 1. A lane with
`path_prefix`, or one whose scope is the root (`.`), fed a report from another checkout
fails with the wrong-tree refusal ([another
tree](lanes.md#an-artifact-that-measured-a-different-tree)), and a run with no lane left
exits 5. 0.8.0 scored every function in its scopes untested and exited 0 when a
`path_prefix` lane read such a coverage.py report, and when a root-scoped lane read
such a coverage.py or istanbul report.

## Freshness in 0.8.1

0.8.1 decides whether a run, a lane or an edit still describes the tree by its
content, where 0.8.0 read a commit, a modification time or a count. Most of that
needs nothing from you. These parts change what a script or an agent loop reads.

**Agent loops read `scored_changes`.** `next-item`, `brief`, `brief --batch`,
`worklist --json` and the MCP tools add `scored_changes`: how many files the ranked
run scored hold other content now, your own uncommitted edits included. `stale`
keeps its 0.8.0 meaning, HEAD moved past the run's commit, so an amend sets it with
no byte moved and an uncommitted edit leaves it `false`. The stop rule gains a
fourth clause, `scored_changes == 0`: anything but `0`, `null` included, means run
`commands.refresh` and ask again. A run 0.8.0 wrote recorded no content, so it reads
`null` until the first `crapkit coverage` after the upgrade. A loop written against
the three-clause rule in [AGENTS.md](../AGENTS.md#the-termination-rule) or
[docs/agent-json.md](agent-json.md#reasons-and-the-stop-condition) needs the fourth.

**Lane stamps hold blob ids.** A lane's stamp in `.crapkit/artifacts.json` now records
the git blob id of each file under its scopes (`blobs`). A stamp 0.8.0 wrote records
only its commit, and crapkit judges it by that commit, as 0.8.0 did, until the lane runs
again. Run `crapkit coverage` once after the upgrade to write stamps in the new form.

**A failed lane's leftover stays refused until new bytes replace it.** When a lane's
attempt fails and leaves the previous run's artifact in place, `--reuse-artifacts` and
`--reuse-unchanged` refuse that file. 0.8.0 keyed the refusal on its modification time,
so a `touch`, a copy of the checkout that drops times, or a same-bytes rewrite lifted it.
0.8.1 keys it on the file's sha256 and keeps a copy in `.crapkit/crap.sqlite`: a touch
keeps the leftover refused, deleting `.crapkit/artifacts.json` does not lift it, and new
bytes lift it, from a run of the lane or a salvage you write. A refusal 0.8.0 recorded
still holds by its modification time until the lane runs again.

**A lane's declared outputs sit under `.crapkit/aside/` while it runs.** crapkit moves
the artifact and results files a lane declares out of the way before its attempts start,
and puts one back only where no attempt wrote a new one. A lane command that reads or
appends to its own previous report finds nothing at that path; write the report fresh
each run. When crapkit is killed while a lane runs, the next command that measures or
reuses that lane puts the files back before it reads them, and prints a line for each.

**A same-size edit that keeps the old modification time can pass unseen.**
`cp -p`, `tar -x`, `rsync -t` and `touch -r` write new bytes under the file's old
mtime. crapkit's analysis cache and `watch` compare the mtime and size before they
read a file, and git answers "unchanged" from its index's stat data for lane reuse,
verify's changed files and its split of committed and dirty findings, `rescore --gate`,
the commit hook's note that a staged file differs from the working tree, and the files
`mutate` copies into its workers. git's part of the limit holds on Windows, where the
change time is the creation time, and under `core.trustctime=false`. On Linux and macOS
git's default stat check also compares the change time, which no copy puts back, so git
sees the edit once the change time moves a second past the one it recorded; the
analysis cache and `watch` still miss it there. `touch` the files after restoring them
that way, and every reader compares their content. 0.9.0 measures what hashing every
file costs before it changes this.

**claude-hook writes one directory.** Its `Bash` fallback records the bytes each
advisory judged under `.git/crapkit/claude-hook/<session_id>/`, one directory per
Claude Code session, and removes a session idle for 7 days when another one starts.
Delete the directory to make the hook judge those files again.

**`ratchet prune` can exit 4 in a shallow clone.** Its rename diff starts at the
store's first run, and a depth-1 CI checkout with `.crapkit/` restored lacks that
commit. 0.8.0 read the failed diff as "nothing was renamed" and dropped a renamed
file's marks as repaid. 0.8.1 reads renames from the oldest run whose commit the
clone holds, and when a marked file left the checkout before that run it exits 4
before writing anything, naming the commit and the `git fetch` that brings it back.
Run prune in a clone that holds the store's first commit, or run that fetch first.
When git cannot say whether a run's commit is in the clone at all, as with a corrupt
object, prune also exits 4 before writing anything, quotes git's error and says to fix
what git reports and run prune again; 0.8.0 read that failure as "nothing was renamed"
too.

**verify names a baseline commit the clone never fetched.** It still exits 4. It
now says `baseline commit ... is not in this clone` and names the `git fetch origin`
that brings the commit, where it blamed a rebase or an amend. When git cannot say
whether the clone holds the commit (a corrupt object), verify exits 4 with git's error
instead of the fetch.

## Teammates' clones

Two settings live in each clone and never in a commit: the merge driver's
`git config merge.crapkit-ratchet.driver` line ([merge driver](ratchet.md#the-git-merge-driver))
and Route 2's `git config core.hooksPath` line. A clone without the driver merges
`crapkit-ratchet.tsv` with git's text merge, and a conflict there gets resolved by
hand, which is how a mark rises. Put both lines in your CONTRIBUTING setup steps. After
an upgrade that re-seeds the marks under a new analysis version, a teammate still on
the older release gets `verify`'s exit 3 on them until they upgrade too.

## Plugin and MCP clients

The installed plugin moves only at a release. After upgrading the intended CLI,
refresh Claude Code's marketplace before updating its user-scope plugin:

```sh
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
crapkit doctor --plugin-root
```

`claude plugin update` compares version strings, and main carries the last release's
version until the next one, so between releases it reports the plugin up to date.
Restart existing Claude Code sessions to apply the plugin update. The doctor check
compares the installed plugin with the `crapkit` launcher on PATH. It does not reload
an existing session. A failed, malformed or undecodable launcher probe is a failure,
not a version match. When the two disagree, its line says which side is behind and
prints the commands that move that side: these update lines for the plugin, or the
upgrade for the installer that owns the launcher (`uv tool upgrade crapkit`, `pipx
upgrade crapkit`, or pip for that launcher's python). A plugin installed with `--scope
project` or `--scope local` gets that scope and the project directory to run the update in,
and a plugin from a marketplace added as a local directory, which Claude Code loads in
place, gets `git -C <that directory> pull`. Between releases the plugin keeps
the release's version string and `claude plugin update` answers "already at the latest
version"; doctor then compares the installed files with the marketplace's copy and, when
they differ, prints the `claude plugin uninstall` and `claude plugin install` lines that
replace them.

`crapkit doctor` in a repo names every `crapkit` launcher on PATH, with its version, once
there are two or more: a WARN when their versions differ, a note while they agree. The
shell, a git hook, the plugin's hooks and an MCP client each run the first their own PATH
lists, so an upgrade has to reach each of them.

A Claude Code marketplace added without `--sparse` is a clone of the whole repository,
61 MB, where the plugin needs 0.8 MB. Removing it also uninstalls the plugin, so add
it back sparse and install again:

```sh
claude plugin marketplace remove crapkit
claude plugin marketplace add JeanFrancoisGagne/crapkit --sparse .claude-plugin plugin
claude plugin install crapkit@crapkit
```

A marketplace added at a tag stays there: `codex plugin marketplace upgrade` keeps it
at that tag, and Codex refuses to add it at another tag while it is configured. For an
installed Codex plugin, remove the marketplace, add it at the tag of the CLI you
upgraded to (`v` and the version `crapkit --version` prints), and install the plugin
from it:

```sh
codex plugin marketplace remove crapkit
codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref v0.8.0 --sparse .claude-plugin --sparse plugin
codex plugin add crapkit@crapkit
codex plugin list --marketplace crapkit --json
crapkit doctor --plugin-root PATH
```

These lines need Codex 0.131.0 or newer, and the listing's `--json` needs 0.137.0.
Removing the marketplace keeps the installed plugin at its old version until
`codex plugin add` installs the new one. The same lines move a marketplace added
without `--ref` onto the tag. Unpinned, it follows main: Codex 0.156.1 checks it each
time it starts and reinstalls the plugin once main moved, which takes the plugin past
the CLI with no command from you.

On Windows, `codex plugin add` at the version already installed exits 1 with
`failed to back up plugin cache entry: Access is denied. (os error 5)` while a program
holds a file of that copy open. That copy is already the one you asked for, and the
listing shows its version.

Use the installed Codex plugin directory for `PATH`, not the marketplace's source
checkout. In the default cache this is
`~/.codex/plugins/cache/crapkit/crapkit/VERSION`, using the installed version from
the listing. With no explicit path, doctor checks Claude Code's cache, and Codex's
when Claude Code has no install. A version gap on a Codex install names Codex's refresh
lines above, never a `claude` command.
Use the three skills and MCP server in Codex. Codex loads no crapkit hook: the
plugin's Codex manifest leaves hooks out. Start a new Codex task to load updated
plugin skills and tools.

A marketplace added without `--ref`, the line 0.8.0 and earlier printed, refreshes on
its own. Each Codex start upgrades it and reinstalls the plugin from it: with Codex
0.156.1, one `codex app-server` start after a release replaced
`~/.codex/plugins/cache/crapkit/crapkit/0.8.0` with the new version's directory. On
such a marketplace, upgrade the CLI before the next Codex start, or the plugin runs
ahead of it, then move the marketplace onto the tag with the lines above and run
`crapkit doctor --plugin-root PATH` to confirm the two agree.

A GitHub Copilot CLI plugin moves from its marketplace, in two steps:

```sh
copilot plugin marketplace update crapkit
copilot plugin update crapkit@crapkit
crapkit doctor --plugin-root ~/.copilot/installed-plugins/crapkit/crapkit
```

`doctor --plugin-root` with no PATH checks Claude Code's install and Codex's, never
Copilot's, so give it the Copilot copy's directory, as above. Start a new `copilot`
session to load the updated skills and hook. Measured with Copilot CLI 1.0.88.

### MCP answers in 0.8.1

Three changes reach an MCP client that parses answers:

- A client that negotiates `2024-11-05` or `2025-03-26` no longer gets
  `structuredContent`, and `tools/list` lists no `outputSchema` to it, since neither
  revision defines them. Read the text content, which carries the same JSON object. A
  client on `2025-06-18` gets both, as before.
- An answer longer than 7,500 characters loses the end of its list fields, then of its
  string fields, then of its objects, and gains `truncated`: which fields it cut, what
  each kept of what it had, and `full`, the CLI command that prints the whole answer. Run
  `truncated.full` when you need every row
  ([the rule](agent-json.md#mcp-server)).
- JSON-RPC codes move. `params` that are not an object answer `-32602` where 0.8.0
  answered `-32603`; a `method` that is an object or an array answers `-32601` where it
  answered `-32603`; a message with an `id` and no `method`, `result` or `error` answers
  `-32600` where it answered `-32601`; a response the server never asked for gets no
  reply where it got `-32601`; `arguments` that are not an object get a tool result with
  `isError: true` where they got `-32603`; and Gemini CLI's `wait_for_previous` argument
  is dropped and the call runs, where 0.8.0 refused it as an undeclared key. A client
  that retried on `-32603` handles `-32602` and `-32600` too.

Start fresh MCP sessions after upgrading so their server uses the installed code. Every
other agent restarts its server its own way: the After an upgrade row of its section in
[Wiring crapkit into your agent](harnesses.md) says how. Skill copies and custom hook
entries need their own update. Run packet commands as supplied, in the
environment that owns the intended CLI, to retain literal arguments and exit codes.

0.6.0 renamed every MCP tool to verb_noun. A client that still sends a 0.5.x name, from
a Codex `enabled_tools` list, a `mcp__crapkit__worklist` allowlist entry or a script,
gets a tool error that names the new tool:

    unknown tool 'worklist': renamed list_worklist in 0.6.0, with the same arguments and result; call list_worklist

Replace the old name where the client lists it. The 0.6.0 entry of the
[changelog](../CHANGELOG.md) has the full table.

## Library callers

crapkit publishes no list of its Python names, so code that imports its modules can
meet a moved name with no warning. Three names warn for one release before they go, and
this section names them. 0.8.1 also moved these with no warning:

| 0.8.0 | 0.8.1 |
|---|---|
| `crapkit.gitio.file_log_patches` | gone: `ImportError` |
| `crapkit.gitpaths.history_line` | gone: `ImportError` |
| `crapkit.lanes.SUITE_DROP_FRACTION` | `crapkit.lane_results.SUITE_DROP_FRACTION` |
| `mcp_server.build_argv(tool, arguments)` | `build_argv(tool, arguments, repo)`: the old call raises `TypeError` |
| `mutate_pool.run_one` and `run_mutants` return `True` for a killed mutant | they return `MutantVerdict`, and `bool(MutantVerdict.SURVIVED)` is `True`, so an `if run_one(...)` reads a survivor as killed: compare with the enum |

The three that warn: 0.8.1 moved the suite-drop check into `lane_results`, where it
walks the trusted runs behind the current one. `crapkit.lanes.suite_drops(previous, current)`
still answers for one last trusted run's lane provenance and raises a
DeprecationWarning; call `crapkit.lane_results.suite_drops(behind, current)`, where
`behind` returns the trusted runs newest first, each with its lane provenance under
`lanes`. The old name goes in 0.9.0.

`lanes.lane_sources_unchanged` keeps its 0.8.0 arguments and
its bool answer through 0.8.x and warns with a `DeprecationWarning` when called;
0.9.0 removes it. Read `lane_freshness.Freshness(root, lanes, scope_paths).lines(lane)`
instead: an empty string means fresh, any other string is the reason.
`lanes.staleness_reads` stays through 0.8.x on the same terms: it warns, and the value
it yields is what `lane_sources_unchanged` takes as `git`. Replace the `with` block with
`with lane_freshness.Freshness(root, lanes, scope_paths) as fresh:`, which reads the
stamp file once for every lane.
`lanes.uncommitted_changes` raises `GitError` when git cannot say which changes the
checkout holds, where 0.8.0 returned `[]`, which read as a clean tree.

## Windows launcher locks

A running `crapkit.exe mcp` holds its console launcher open, and each installer meets
that lock its own way. Measured on Windows 11 with pip 26.2.1, pipx 1.17.6 and uv
0.12.18, upgrading 0.7.6 to 0.8.0 while a server from the same install ran; the pip
22.3.1 row upgraded 0.8.0 to 0.8.1 under a running 0.8.0 server, and pip 23.3, 23.3.2,
24.0 and 25.0.1 did what the first row says:

| Command | Exit | What it printed and left behind |
|---|---|---|
| `python -m pip install --upgrade crapkit` | 0 | `Successfully installed crapkit-0.8.0`, then `WARNING: Failed to remove contents in a temporary directory`. pip moved the busy `crapkit.exe` into that directory: `crapkit --version` says 0.8.0, and the running server still answers as 0.7.6 |
| `python -m pip install --upgrade crapkit`, pip 22.3.1, the pip a Python 3.11.2 venv ships | 1 | `ERROR: Could not install packages due to an OSError: [WinError 5] Access is denied: '...\pip-uninstall-...\crapkit.exe'`. pip puts the old release back, and `pip list` still shows it. Run `python -m pip install --upgrade pip` first, then the upgrade |
| `pipx upgrade crapkit`, pipx using pip | 0 | `upgraded package crapkit from 0.7.6 to 0.8.0`, and the rest as for pip |
| `pipx upgrade crapkit`, pipx using uv (uv on PATH) | 1 | `error: failed to remove file ...\Scripts/crapkit.exe: Access is denied. (os error 5)`. 0.7.6 stays installed and runs |
| `uv tool upgrade crapkit` | 1 | `failed to copy file ... The process cannot access the file because it is being used by another process. (os error 32)`. The package is already 0.8.0; the rerun says `Nothing to upgrade` |
| `uv tool install crapkit@latest` | 2 | `error: failed to remove directory ...\Scripts: Access is denied. (os error 5)`. The old package is gone, and `crapkit` fails with `ModuleNotFoundError: No module named 'crapkit'` until the rerun |

After an exit 0, restart the client or agent session that owns the server; nothing else
is left to do. After Windows error 32 or error 5:

1. Stop the Crapkit MCP server or the agent session that owns it.
2. Rerun the same upgrade command and require a successful installer result.
3. Check `crapkit --version`, restart the client, and check plugin compatibility.

Use the installer to repair the launcher instead of copying executables between
environments. The CLI version alone does not prove an interrupted install finished.

## Removing crapkit

Take out what calls crapkit before the package. The commit hook and the merge driver
both run it. After `pip uninstall crapkit` alone, the sh hook README's Route 1 and
Route 2 write keeps judging every commit through `uvx crapkit` on a machine with uv, with
whichever crapkit release uv has cached or can download, and on a machine without uv
stops every commit on `No module named crapkit`, or on `exec: python: not found` where
there is no `python` at all, as on a Debian, Ubuntu or macOS that has only `python3`. The
PowerShell hook README's Route 1
and the handbook write names the launcher that `pip uninstall crapkit` deletes, so it
stops every commit on `No such file or directory`, with uv or without it. Every merge that touches `crapkit-ratchet.tsv` conflicts after the
driver's `crapkit: not found`.

### From a repo

Git keeps the hook and the driver in each clone's `.git`, not in a commit, so every
clone runs the per-clone lines. For a repo armed with Route 1 and the merge driver:

```sh
rm .git/hooks/pre-commit
git config --unset merge.crapkit-ratchet.driver
git config --unset merge.crapkit-ratchet.name
```

Then delete the line `crapkit-ratchet.tsv merge=crapkit-ratchet` from `.gitattributes`,
and the `# crapkit` block `crapkit init` added to `.gitignore`, and commit that with the
files crapkit wrote:

```sh
git rm crapkit.toml crapkit-ratchet.tsv
git add .gitattributes .gitignore
git commit -m "remove crapkit"
rm -rf .crapkit
```

`.crapkit/` holds the run store and caches, untracked; `rmdir /s /q .crapkit` removes it
from cmd.exe. A branch cut before the removal that changed `crapkit-ratchet.tsv` meets
`CONFLICT (modify/delete)` when it merges; keep the deletion with
`git rm crapkit-ratchet.tsv`.

The other routes leave their own pieces:

| Piece | Where it lives | How it goes |
|---|---|---|
| Route 1 hook that runs other checks too | `.git/hooks/pre-commit`, per clone | delete the lines that run `crapkit hook-precommit` instead of the file: three in the sh form, one in the PowerShell form |
| Route 2 hook | `githooks/pre-commit` and its `githooks/pre-commit text eol=lf` line in `.gitattributes`, committed; `core.hooksPath`, per clone | `git rm githooks/pre-commit`, delete the line, and `git config --unset core.hooksPath` in each clone |
| Route 3 hook | the `crapkit-gate` entry in `.pre-commit-config.yaml`, committed | delete the entry; `pre-commit uninstall` when no hook is left |
| Route 4 and the GitHub Action | your CI workflow, and a committed baseline such as `crapkit-baseline.tsv` | delete the step that installs crapkit and runs `crapkit verify`, or the one that `uses: JeanFrancoisGagne/crapkit`, and `git rm` the baseline |

### From the machine

| Installed with | Remove it with |
|---|---|
| pip, pip --user or pip from the git URL | `python -m pip uninstall crapkit`, which leaves lizard installed |
| pipx | `pipx uninstall crapkit`, which deletes crapkit's venv and its command |
| uv tool | `uv tool uninstall crapkit`, which deletes the tool's environment and its command |
| uvx | `uv cache clean crapkit`, which drops the releases uvx cached; a Route 1 or Route 2 hook left in place fetches crapkit again at the next commit |
| the Claude Code plugin | `claude plugin uninstall crapkit@crapkit`, then `claude plugin marketplace remove crapkit` |
| the Codex plugin | `codex plugin remove crapkit@crapkit`, then `codex plugin marketplace remove crapkit` |
| the Copilot CLI plugin | `copilot plugin uninstall crapkit@crapkit`, then `copilot plugin marketplace remove crapkit` |
| the Docker image | delete the client's `docker run ... crapkit` server entry, then `docker rmi crapkit` |
| another MCP client | delete the `crapkit` server entry from its config ([its section](harnesses.md)) |

Remove the plugins with the package. The Claude Code, Codex and Copilot CLI plugins all
start the bare `crapkit` command, so with the package gone and the Claude Code plugin
still installed, `claude mcp list` shows
`plugin:crapkit:crapkit: crapkit mcp - ✘ Failed to connect`.

## Release evidence

The [implementation report](architecture/2026-09-07-implementation/REPORT.md)
records complete Windows source and Linux installed-wheel verification, independent
reviews and repeatable performance probes. Its benchmark tables distinguish
synthetic duplication input, fixture setup and a fixed unit subset from complete
suite runs. They do not claim a whole-suite speedup. Hosted CI dispatch and macOS
runtime execution were outside that local verification. Hosted CI now runs the
source suite on Python 3.11, 3.12, 3.13 and 3.14 on Ubuntu and Windows, and on
Python 3.13 on macOS for the letter-case rows.
