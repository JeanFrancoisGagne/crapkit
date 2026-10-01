# Upgrading crapkit

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
| the Docker image | in the crapkit clone it was built from, `git fetch --tags`, then `git checkout vX.Y.Z` for the release you move to, then `docker build -t crapkit .`, then restart the client |
| the pre-commit framework (Route 3) | `pre-commit autoupdate`, or set `rev` to the new tag |
| the GitHub Action | move `uses: JeanFrancoisGagne/crapkit@...` to the new tag |

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

The last four rows move what runs crapkit rather than the CLI. The Docker image holds its
own crapkit, installed from the clone at build time, and a container keeps the image it
started from. Build from the release tag: the tip of `main` runs code no release shipped
yet, under the last release's version string. pre-commit and the Action install the
release their `rev` or `uses:` tag names, so move each one in the commit that re-seeds,
as [a team upgrades every
reader](#a-team-upgrades-every-reader-before-the-re-seed-lands) says. prek reads the same
`.pre-commit-config.yaml` and its `rev`.

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

## 0.8.0 to 0.8.1, in order

Each step links the section that explains it.

1. Upgrade coverage.py where each Python lane runs: `pip install -U "coverage>=7.13.1"`,
   or `pip install -U "crapkit[py]"` where crapkit shares that environment. 0.8.1 refuses
   a report from coverage 7.6 to 7.13.0 at exit 5.
   [0.8.1 on coverage 7.6 to 7.13.0](#081-on-coverage-76-to-7130)
2. Before you upgrade crapkit, run `crapkit doctor --show-files > before-doctor.txt` and
   `crapkit coverage --export before.tsv`, to compare the per-scope file counts after.
   [Config paths that 0.8.1 reads on every OS](#config-paths-that-081-reads-on-every-os)
3. Upgrade the CLI in every clone with the installer that owns it, from the table above.
   On Windows, stop every crapkit MCP server first: a running `crapkit.exe mcp` holds the
   launcher. [Windows launcher locks](#windows-launcher-locks)
4. Measure with the new release and re-seed each repo once: `crapkit coverage`, then
   `crapkit ratchet prune`, then `crapkit ratchet seed`. When a failed verify pins the
   baseline, pass the new run to both: `crapkit ratchet prune --baseline N`, then
   `crapkit ratchet seed --baseline N`.
   [Measure before changing marks](#measure-before-changing-marks),
   [Analysis version 13](#analysis-version-13)
5. Run `crapkit hook-precommit` with nothing staged, then record each function it names
   with `crapkit coverage`, then `crapkit ratchet seed`, or decompose it.
   [The commit gate in 0.8.1](#the-commit-gate-in-081)
6. Commit the new marks together with every pin that runs crapkit: the CI install pin,
   the Action's `uses:` pin and the pre-commit `rev`.
   [A team upgrades every reader](#a-team-upgrades-every-reader-before-the-re-seed-lands)
7. Before you push that commit, run `crapkit verify`, and raise by hand each mark a
   `RATCHET` line names, to the number verify prints.
   [0.8.1 on coverage 7.6 to 7.13.0](#081-on-coverage-76-to-7130)
8. Swap a launcher 0.8.0's `init` wrote for `{python}` or `{python:.venv}`, once every
   clone and pin runs 0.8.1.
   [The venv launcher `init` wrote](#config-paths-that-081-reads-on-every-os)
9. Write a commit hook from the 0.8.0 README again from README Route 1 or Route 2.
   [Teammates' clones](#teammates-clones)
10. Read the exit codes that move before you move a CI job:
    [missing values](#missing-values-that-081-names),
    [the commit gate](#the-commit-gate-in-081),
    [text that is not UTF-8](#text-that-is-not-utf-8) and
    [the rest](#other-exit-codes-that-move-in-081).
11. Update scripts that parse crapkit's output:
    [values that move without an exit code](#values-that-move-without-an-exit-code) and
    [freshness](#freshness-in-081).
12. Update the plugin with the CLI and restart every MCP session.
    [Plugin and MCP clients](#plugin-and-mcp-clients),
    [MCP answers in 0.8.1](#mcp-answers-in-081)
13. Code that imports crapkit's modules reads [library callers](#library-callers).

## A team upgrades every reader before the re-seed lands

The marks file carries the analysis version of the crapkit that seeded it, and an older
crapkit refuses marks a newer one seeded. So the commit that re-seeds under a new analysis
version is the last step of a team's upgrade, not the first. Upgrade every clone first.
Then commit the re-seeded marks, and the marks for any debt `crapkit hook-precommit` names,
together with every pin that runs crapkit: the CI install pin, the Action's `uses:` pin and
the pre-commit `rev`. 0.8.1's `verify` refuses marks stamped 11 at exit 3, and 0.8.0's
refuses marks stamped 13, so a pin that moves alone turns CI red until the other lands.
The commit hook reads no stamp, but under `pre-commit run --all-files` the 0.8.1 hook
judges every tracked file and exits 6 on unmarked debt ([the commit gate in
0.8.1](#the-commit-gate-in-081)), so the `rev` waits for those marks too.

The [launcher token](configuration.md#the-launcher-token) waits for the same readers.
A release before 0.8.1, 0.8.0 included, does not know `{python}` or `{python:DIR}` and
hands the shell the token as written, so every lane whose command holds one fails at
exit 5 in a clone, a CI job, an Action pin or a pre-commit `rev` still on that release,
and its `doctor` FAILs the lane. The lane's
`last output: $ {python:.venv} -m pytest ...` is followed by
`The filename, directory name, or volume label syntax is incorrect.` under cmd.exe, or by
`/bin/sh: 1: {python:.venv}: not found` and `(exit 127)` under sh, then
`crapkit: every lane failed`. Commit a lane `command`, `retest_command`,
`[crapkit.scoped_tests]` template or `mutation_command` that holds the token, whether
0.8.1's `init` wrote it or you swapped it in ([config
paths](#config-paths-that-081-reads-on-every-os)), once every clone, the CI pin, the
pre-commit `rev` and the Action's `uses:` pin run 0.8.1: with the re-seed or after it.
Until then each lane names the launcher the token stands for, as
[Downgrading](#downgrading) lists them. A teammate who meets those lines upgrades
crapkit.

A reader you missed exits 3 on the committed marks. From 0.8.1 an older release that meets
marks a newer one wrote says so, `the marks come from a newer crapkit than this install`,
asks for an upgrade, and its `ratchet seed` and `ratchet prune` refuse the file. 0.8.0 and
older releases instead name `crapkit ratchet seed`, and that seed restamps the team's marks
under the older analysis, after which every upgraded teammate's verify refuses them. Upgrade
that reader rather than follow the line.

## Measure before changing marks

0.8.1 moves the reader from analysis version 11 to 13, so a marks file stamped
under 11 needs one re-seed; [analysis version 13](#analysis-version-13) says what
moved. 0.8.0 moved it from 10 to 11, and [analysis version 11](#analysis-version-11)
says what that moved. The package upgrade rebuilds the versioned analysis cache
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

After upgrading, measure with the new release before you change any mark; git keeps the
committed `crapkit-ratchet.tsv` as the old marks. In each repo:

```sh
crapkit doctor
crapkit coverage --export .crapkit/current-functions.tsv
```

Resolve doctor failures, then inspect the fresh run. `coverage` writes a measurement
without applying the ratchet. After `ratchet prune`, check each mark it dropped against
that export: a dropped mark names a function the export no longer lists. Finish with
`crapkit verify` after reviewing and committing any mark changes.

| What changed | Required action |
|---|---|
| Analysis or lizard stamp | Follow the [metric stamp rules](ratchet.md#the-metric-stamp). Comparisons refuse incompatible stamps. |
| Function membership or same-line identity | Review the [saved-mark mapping](ratchet.md#reconcile-saved-marks) before changing keys or stamps. |
| Coverage or JUnit producer | Run a fresh lane and resolve [artifact admission errors](lanes.md#a-junit-that-says-the-run-did-not-finish). |
| Shared exports or portable baselines | Upgrade readers before writing [encoded records](portable-records.md) for them. |

### 0.8.1 on coverage 7.6 to 7.13.0

Install coverage.py 7.13.1 or newer where each coverage.py lane runs:
`pip install -U "coverage>=7.13.1"`, or `pip install -U "crapkit[py]"` when crapkit shares
the suite's venv. Without `-U`, `pip install "crapkit[py]"` finds crapkit installed and
leaves it and its coverage.py as they are. 0.8.1 reads a function's span from the
`start_line` coverage.py writes from 7.13.1, and a lane whose report has none fails at exit
5 with
`install coverage>=7.13.1 and rerun the lane`. A report that carries `start_line` scores as
it did in 0.8.0.

`crapkit doctor` names such a lane before `coverage` runs it, with the install line for
that interpreter, and exits 1:
`FAIL lane 'py' runs coverage 7.10.0 (...), which writes no function start lines, ...`.
0.8.0 printed `doctor: no problems found` there and exited 0, so a CI step that runs
`crapkit doctor` on such a lane fails from the upgrade until the lane's coverage is 7.13.1
or newer.

On the older coverage a nested function took its encloser's coverage. On the new one it
scores its own region, which is the version 13 change below: re-seed once as that section
says. A mark such a function already carried can sit under its new score, and `ratchet
seed` never raises a mark, so `verify` names it in a `RATCHET` line at exit 7 on a function
the diff never touched. After the seed, commit and run `crapkit verify`: each `RATCHET`
line names a mark whose function now scores higher. Raise each by hand to the number
verify prints, in a commit where a reviewer sees it; the ratchet page says how a rise is
[accepted where a reviewer sees it](ratchet.md#overrides-and-the-audit-trail).

### Analysis version 13

0.8.1 moves the reader from analysis version 11 to 13; no release shipped version
12. Scores move on functions nobody edited. The three commands below re-seed each marks
file once, and [what analysis version 13 moves, section by
section](#what-analysis-version-13-moves-section-by-section) says what else moves.

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
- In a source that is not UTF-8, such as one saved as cp1251, cp932 or cp936, an
  identifier that holds one of the five bytes cp1252 leaves undefined (0x81, 0x8D, 0x8F,
  0x90, 0x9D) keeps its name, the byte read as the letter U+01NN. In Python and
  TypeScript 0.8.0 keyed such a function as `�( x )` or `(anonymous) ( x )` at its own
  ccn; it now keys as `cafƁ( x )`, so only the key moves, and a mark under the old key
  names a function the run lacks. In C 0.8.0 split it into rows such as `if( x>1)` at
  ccn 1; it now scores one function at its own ccn. In Go, Java, Rust, Swift, shell and
  PowerShell, and in a C function whose `if` has no braces, 0.8.0 scored no such function
  at all: it appears for the first time, as a UTF-16 source's functions do, and one over
  its ceiling fails the gate the next time its file changes. A UTF-8 source keys such a
  name the same in both releases.

The stamp records the rules either way, so every marks file re-seeds once.

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

`coverage` measures under version 13, and `ratchet seed` stamps the marks with the metric
of the run it reads, so a seed from a run 0.8.0 measured keeps the old stamp and `verify`
keeps refusing. `ratchet prune` goes first, as it did for version 11, and changes nothing
while every marked function is still in the run. Where 0.8.1 moved a key, it drops the
mark left under the old one: a UTF-16 source, a name holding one of those five bytes, and
the rows that [Rust rows](#rust-rows), [C, C++, Objective-C and Java
rows](#c-c-objective-c-and-java-rows), [the Swift and Rust
readers](#the-swift-and-rust-readers) and [Shell and PowerShell
rows](#shell-and-powershell-rows) rename or drop. When a failed verify pins the baseline,
seed and prune both read the pinned run: pass the new run's id to each, `crapkit ratchet
prune --baseline N` then `crapkit ratchet seed --baseline N`. Their lines, and the warning
verify prints above its refusal, name it. Review the diff and commit it before the next
`crapkit verify`.
Until the re-seed, `crapkit doctor` WARNs on the marks file with the refusal `verify`
prints and still exits 0, so the doctor step under
[Measure before changing marks](#measure-before-changing-marks) passes, and the three
commands above clear both.

## Saved state and command behavior

Keep `.crapkit/crap.sqlite`: it holds run history, test baselines and override audits.
Commands manage disposable analysis and history caches themselves. Use
`crapkit runs list` to inspect the trusted baseline; a failed verify still prevents
a newer coverage run from silently becoming the baseline.

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

### What 0.8.1 changes in saved state

Automatic reuse requires the same clean HEAD and unchanged configuration,
environment and artifact bytes. Since 0.8.0 a lane can list the paths its command
reads as `inputs`, and `--reuse-unchanged` then reuses it across commits while
nothing under those paths, its lane table or its `env` changed. The reuse proof
covers that field, so the first `--reuse-unchanged` after upgrading to 0.8.0 reruns
every lane once, and an older stamp without the proof reruns its lane; each rerun
prints `lane 'x': rerunning:` and the reason. Since 0.8.1 every lane's proof also holds
the crapkit version, so each upgrade reruns every lane once with `the crapkit version
changed`. Upgrading from 0.8.0, a lane that lists `inputs` prints
`its lane table or env differs from the one it was measured with` instead, because 0.8.0
recorded no parts for such a lane's proof; nothing in your config moved. Ignored files, installed dependencies
and external services remain outside this proof, and the line that reuses a lane names
what its proof leaves out. See [artifact reuse](lanes.md#reusing-artifacts) before
choosing an explicit saved-artifact read.

The full-suite guard reads a lane command the way the shell that runs it does, operators
and redirections that touch a word included. Lanes it refused for `py.json&& python -m
coverage json`, `>"lane.log"` or `tests>lane.log` now load, and so do these Windows
shapes: `>lane.log ; 2>&1`, where cmd.exe drops the `;` between the two redirections,
and a block such as `(python -m pytest --cov=src ) > lane.log`, where `doctor` now finds
python instead of naming `(python`. One Windows shape that loaded now exits 3: a
caret-escaped quote around an `&`, as in `-k ^"x & python -m pytest pylib/unit^"`,
because cmd.exe starts a second pytest there and the coverage came from its narrowed
run. [How a lane command is read](lanes.md#how-a-lane-command-is-read) has every rule.
No score moves.

A portable baseline from `verify --emit-baseline` carries each lane's test count and
failure list on its stamp line, and `verify --baseline-tsv` forgives those failures as a
verify against the store does. Re-emit a committed baseline file once on the default
branch. Until then it forgives no failure, as before, and verify says so once and names
the command that rewrites it. [The portable baseline's stamp
line](portable-records.md#the-portable-baselines-stamp-line) gives the format.

Each run keeps the shingle index `duplication` and `brief` read. When an upgrade changes
how functions are shingled, the stored index no longer matches and the first
`duplication` or `brief` after upgrading rebuilds it for the newest run, which takes
seconds on a large repo. The pairs and twins it lists can change; no score or mark does,
so nothing needs re-seeding.

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
| an istanbul `fnMap` entry without `loc.end.line` | `coverage`, `verify` | 0 | 5 |
| an istanbul `branchMap` entry with neither `loc.start.line` nor `line` | `coverage`, `verify` | 0 | 5 |
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

**Istanbul entries without a line.** 0.8.0 read an istanbul `fnMap` entry with no
`loc.end.line` as a function one line long, so the body's branches attached to nothing
and a function that was called scored as covered. It left out a `branchMap` entry with
neither `loc.start.line` nor the `line` beside it, so the function that branch sat in
scored without its arms. Both ran at exit 0. From 0.8.1 the lane fails, so `coverage`
and `verify` exit 5, naming the file and the entry, such as
`src/app.ts: fnMap['0'] has no loc.end.line` or
`src/app.ts: branchMap['1'] has no loc.start.line and no line`, and ending
`regenerate the artifact with the runner's reporter`. Every istanbul reporter writes
both lines, so look at what rewrote the artifact (a converter or a hand merge) and
regenerate it with the test runner's own reporter.

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
marks file in the working tree does not mark. Before you move the `rev` to the 0.8.1 tag, run
`crapkit hook-precommit` by hand with nothing staged: record the debt it names with
`crapkit coverage`, then `crapkit ratchet seed`, and commit the marks, or decompose those
functions. A commit still judges only what it stages.

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
| `--override REASON` where `crapkit.toml` sets no `alert_command` | `verify` | the verdict's: 0 on a passing tree, 7 on a ratchet regression, and 3 after every lane ran on a gate breach the override would grant | 3 before any lane runs, `no alert_command configured` | Set [`alert_command`](ratchet.md#overrides-and-the-audit-trail), or drop `--override` |
| a pytest lane whose coverage.py is older than 7.13.1 | `doctor` | 0 | 1, a FAIL naming the install line | Install `coverage>=7.13.1` where the lane runs |
| a run under uvx, `uv run --with` or `pipx run` | `doctor --plugin-root` | 0 | 1, ``FAIL no `crapkit` on PATH`` | `uv tool install crapkit`, or `pipx install crapkit` |
| a user-scope plugin older than a project-scope one, no PATH given | `doctor --plugin-root` | 0 | 1 | Update the older install |
| a flag this crapkit does not know | `claude-hook` | 2, with the usage block | 0, with one line naming the version skew | Upgrade crapkit, then run `crapkit doctor --plugin-root` |
| an advisory under Cursor, Copilot CLI or VS Code | `claude-hook` | 2 | 0, the advisory as one JSON object on stdout | A wrapper reads `additionalContext`; Claude Code keeps exit 2 |
| a file an agent's shell wrote under a scope, named in bytes that are not UTF-8 | `claude-hook` | 0 | 2, an advisory naming the file and the rename | Rename the file to UTF-8 |
| a file argument spelled `SRC\app.ts` on a case-insensitive disk, or `/c/...`, `/mnt/c/...` or `\\?\C:\...` on Windows, holding a breach | `rescore --gate`, `check_gate` | 0, 0 functions judged | 6, and `gate.ok` false | Decompose the function it names |
| a file argument that names a directory (`src`, `src/`, `.`, `""`) holding a breach | `rescore --gate`, `check_gate` | 0, 0 functions judged, and `gate.ok` true | 3, `src is a directory; name the source files in it`; `check_gate` answers `isError` true, and `rescore` and `explain` refuse the same way | Name the files, as `git ls-files src` lists them |
| a writer flag (`--export`, `--sarif`, `--emit-baseline`, `report --out`) that names a directory | the command that takes it | 1, a `PermissionError` traceback; `verify --sarif` had stored its run | 3 before any run is stored, `--sarif 'out' is a directory; name a file to write` | Name a file |
| `python -m pytest Tests` under `testpaths = ["tests"]` on a case-insensitive disk | `coverage` | 3, refused as narrowing | runs as the whole suite | Drop a `full_suite = false` set only to get past it |
| an istanbul key naming this checkout by an 8.3 name, a junction or symlink, a lower-case drive or a `\\?\` prefix | `coverage`, `verify` | 5, the lane FAILED | 0, the lane scores | None |
| a coverage artifact that starts with a UTF-8 byte-order mark | `coverage`, `verify` | 5 | 0 | None |
| a coverage.py region with no `summary` object | `coverage`, `verify` | 0 | 5 | Regenerate the report with the coverage tool |
| Windows, a process started with no `USERPROFILE`, `HOMEDRIVE` or `HOMEPATH` | `doctor`, `coverage`, `check_config` | 1, `RuntimeError: Could not determine home directory.` | 0, from the profile folder Windows reports; 5 with `no home directory` when nothing names one | Set `USERPROFILE` where nothing names a home |
| a lane whose `bash -c` or `sh -c` payload hands pytest a positional that narrows the suite, or hands vitest a file filter beside `--coverage` | `doctor`, `coverage` | 0, the narrowed suite ran | 3 at load, `positional argument 'tests/test_mod.py' narrows a full-suite coverage run` | Drop the positional, or set `full_suite = false` |
| a lane named `unit?`, `a:b` or `nul`, or with a trailing dot or space, or two lanes whose names differ only in case | `doctor`, `inventory`, `coverage` | 0; `coverage` exited 1 with a traceback on Windows for `unit?` | 3 at load | Rename the lane |
| a lane whose `artifact` is `""` or `"."`, or whose `results_artifact` is `"."` | `doctor`, `inventory`, `coverage` | 0; `coverage` exited 5, `every lane failed` | 3 at load | Name the report file |
| a staged file with an upper-case extension (`src/MAIN.CPP`, `src/Tool.PY`) holding a function over its ceiling | commit hook | 0, the file was not scored | 6, a `ccn` line naming the function | Decompose it, or run `crapkit coverage` then `crapkit ratchet seed` |
| a `test_*.py`, `*.test.*` or `*.spec.*` file outside every scope and outside a `test`, `tests` or `__tests__` directory | `test-scoped` | 0, the scope's template ran it | 3, `... belongs to no declared scope, and only a file under a test, tests or __tests__ directory runs without one` | Move the file under a scope's `paths` or into `tests/` |
| the Action's `top` input set to `"ten"`, `"5.0"` or `""` | the GitHub Action | 2, `invalid int value`, and no comment | 0, a comment with 5 rows, and a `::warning` line naming the input when it is not empty | Set `top` to a whole number, or leave it out |
| a mark typed by hand with more than four decimals | `verify` | judged against the typed value: 7 for a mark of 29.99996 over a CRAP of 30.0 | judged against the four-decimal value every rewrite of the file already gave it: 0 there. A mark that rounds down, such as 30.00004 over a CRAP of 30.0, exits 0 under both | Write the mark with four decimals |

## Values that move without an exit code

These changes move text or JSON a script reads, with no exit code to flag them:

- Every message crapkit writes spells a dash ` - ` where 0.8.0 printed an em dash, the
  `message` of a `--json` error object included. A path or a function name crapkit quotes
  keeps its own characters. A script that matches a line matches ` - ` now.
- The churn window counts back from HEAD's commit date on the UTC calendar, never from
  the day of the run, so `risk`, `weight` and `commits` in `worklist --json`, `next-item`
  and `brief --json` can differ from 0.8.0 on a repo whose HEAD commit is older than the
  day it is measured, and on a machine whose local date is not the UTC date. Scores do not
  move; [the churn window ends at HEAD's commit
  date](#the-churn-window-ends-at-heads-commit-date) says what does.
- A partial run's `crap_load` sums only the scopes it measured, the functions its
  `over_target` and `grade` count. 0.8.0 added a failed or skipped lane's functions at the
  cov-0 stand-in, so a run line read `0 over ceiling 6, CRAP load 32.0` where the measured
  scopes held 2.0. `coverage --json` and the run line report the smaller number, and
  `by_scope` still carries each unmeasured scope's own load.
- A run that scored no function prints `CRAP load 0.0` on its run line, where 0.8.0
  printed `CRAP load 0`.
- `worklist` text ends with a `-> next:` line naming the command to run next:
  `crapkit coverage` when the run it ranked cannot serve as a baseline,
  `crapkit ratchet seed` while the repo has no marks file, and `crapkit next-item` after
  that. 0.8.0 ended at the
  last row. `worklist --json` prints the map alone, as before.
- A `worklist` row for a function no lane measures, in a `no-lane` or `cc-only` scope,
  prints `-` in its cov column (`cov    -`), where 0.8.0 printed `cov   0%`, and
  `rescore --gate` prints `cov -` on its GATE line. `worklist --json` keeps such a row's
  `cov` at `0.0` and adds no key: its `flag` reads `no-lane` or `cc-only`. `brief --json`,
  `next-item` and `rescore --json` keep `cov` and add `unmeasured: true`.
- The SARIF log `--sarif` writes names `$schema`
  `https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json`,
  where 0.8.0 named
  `https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json`,
  which answers HTTP 404. The results and rules do not change.
- `brief --json` marks a coupling partner's `is_test` by the test-file rule `init` and
  `doctor` read, so it flips for some files: `x_test.go` and `x_test.py` read `true` where
  0.8.0 said `false`, and `tools/test_deploy.sh` reads `false` where 0.8.0 said `true`.
- `explain --history --json` keeps a `\r` in a commit's subject or body, and a form feed,
  `\x1c` or `\x85` in its body, as committed. 0.8.0 split the message at each of them as
  a line break. The text output still starts a new indented line at each.

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
13](#analysis-version-13) is the one 0.8.1 asks for. Before upgrading, run
`crapkit doctor --show-files > before-doctor.txt` and
`crapkit coverage --export before.tsv`; after, run both again into `after-doctor.txt` and
`after.tsv` and compare the per-scope file counts and scores.

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
token becomes. A release before 0.8.1 cannot run the token, so commit the swap only once
every reader runs 0.8.1, as [a team upgrades every
reader](#a-team-upgrades-every-reader-before-the-re-seed-lands) says.

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

A hook written from the 0.8.0 README runs `exec python -m crapkit hook-precommit` alone,
so it refuses every commit once crapkit lives in pipx or uv tool, where that `python`
does not import it, and on a machine with no `python` at all. Write it again from README
[Route 1](../README.md#route-1-githookspre-commit-local-not-committed) or
[Route 2](../README.md#route-2-a-committed-hooks-directory) in each clone; a Route 2 hook
is one committed file, so one commit rewrites it for every clone.

## The churn window ends at HEAD's commit date

The churn window reaches `churn_window_months` back from HEAD's commit date, never
from the day of the run. 0.8.0 and earlier cut it with `git log --since=12.months.ago`,
which git reads against today's date, so a repo measured a year after its last commit
had no churn and `worklist` listed every file as dormant.

- Churn weights, `risk`, the order `worklist` and `next-item` hand out, and the pairs
  `coupling`, `brief` and `worklist --batches` rank change for any repo whose HEAD
  commit is older than the day it is measured. The longer the repo has sat still,
  the more history enters the window. CRAP scores and ratchet marks do not change,
  so nothing re-seeds.
- The churn caches move to `churn-cache-v3.json`, `churn-log-v3.z` and
  `coupling-cache-v2.json`. The files 0.4.5 to 0.8.0 wrote hold a window cut at the
  wall clock, so this version never reads them: its first churn read walks the
  window once, seconds on a large history, then stays warm. It leaves those files
  alone, so an older crapkit that shares the working tree, a pinned CI install say,
  keeps its caches warm. Once no older crapkit runs there, delete
  `.crapkit/churn-cache-v2.json`, `.crapkit/churn-log-v2.z`,
  `.crapkit/churn-log-v2.json` and `.crapkit/coupling-cache-v1.json`; the log alone
  can hold several megabytes.

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

`crapkit doctor --plugin-root` exits 1 in two setups where 0.8.0 exited 0, so a script
that runs it can fail after the upgrade. With no PATH, it checks every install
`installed_plugins.json` records. 0.8.0 checked only the newest, so a user install
behind the CLI beside a project install at the CLI's version passed while every session
outside that project ran the older plugin; update the install the line names. Run
through `uvx`, `uv run --with` or `pipx run`, it no longer counts the launcher that
runner put on its own PATH, which the plugin's hooks never inherit, and prints
``FAIL no `crapkit` on PATH outside the environment uv built for this one command``
(pipx under `pipx run`). Install crapkit where the hooks' PATH sees it, with
`uv tool install crapkit` or `pipx install crapkit`.

`crapkit doctor` in a repo names every `crapkit` launcher on PATH, with its version, once
there are two or more: a WARN when their versions differ, a note while they agree. The
shell, a git hook, the plugin's hooks and an MCP client each run the first their own PATH
lists, so an upgrade has to reach each of them.

A Claude Code marketplace added without `--sparse` is a clone of the whole repository,
where the plugin needs two small directories of it. Removing it also uninstalls the
plugin, so add it back sparse and install again:

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

1. Stop the crapkit MCP server or the agent session that owns it.
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
PowerShell hook README's Route 1 and the handbook write names the launcher that
`pip uninstall crapkit` deletes, so it stops every commit on `No such file or directory`,
with uv or without it. A hook written from the 0.8.0 README has no uvx line: after
`pip uninstall crapkit` it stops every commit on `No module named crapkit`, with uv or
without it. Every merge that touches `crapkit-ratchet.tsv` conflicts after the driver's
`crapkit: not found`.

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
| Route 1 hook that runs other checks too | `.git/hooks/pre-commit`, per clone | delete the lines that run `crapkit hook-precommit` instead of the file: three in the sh form (one in a hook written from the 0.8.0 README), one in the PowerShell form |
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

## Downgrading

Install the older release by its number, with the installer that owns crapkit:

- pip: `python -m pip install "crapkit==0.8.0"`
- pipx: `pipx install --force "crapkit==0.8.0"`
- uv tool: `uv tool install "crapkit==0.8.0"`

Write the number of any older release in place of 0.8.0 to go further back.

A release before 0.8.1 does not read the [launcher
token](configuration.md#the-launcher-token) that 0.8.1's `init` writes into each lane it
detects. It hands `{python} -m pytest` to the shell as written, so its `doctor` FAILs the
lane with `executable '{python}' does not resolve on PATH` and its `coverage` exits 5.
Before the older release runs, write each token in `crapkit.toml` back as the launcher it
stands for on your OS, spelled as the TOML string holds it:

| Token | Windows | Linux and macOS |
|---|---|---|
| `{python}` | `python` | `python3` |
| `{python:.venv}` | `.venv\\Scripts\\python.exe` | `.venv/bin/python` |

A venv in another directory takes that directory in place of `.venv`. With the
launchers written back, the older release reads the store a newer one wrote: `doctor`,
`coverage` and `next-item` work as before. A downgrade across an analysis version
stops `verify` at exit 3, because the committed marks carry the newer stamp; the
refusal names both stamps. Measure with the older release, then re-seed:

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

## What analysis version 13 moves, section by section

[Analysis version 13](#analysis-version-13) re-seeds every marks file once. These
sections say which scores and names move, and why, one reader at a time.

### Score arithmetic

CRAP cubes `1 - cov` with two products where it called `** 3`. `pow()` differs between
C libraries, so Windows and Linux gave some scores different last bits, and a few
scores move at the 4 dp a mark is stored at. No function changes its name for this.

Over ccn 1 to 60 and every coverage fraction up to 240ths, measured on Windows:

- 21 scores print a different 4 dp value. 12 fall by 0.0001: CRAP(36, 53/120) is
  exactly 261.57225 and now prints 261.5722, not 261.5723. 9 rise by 0.0001:
  CRAP(20, 3/200) now prints 402.2687, not 402.2686.
- One score prints a different 2 dp value: CRAP(25, 19/50) is exactly 173.955 and now
  prints 173.96, not 173.95.

Seed tightens a mark whose score fell. It never raises one, so a marked function whose
score rose keeps its old mark, and `crapkit verify` reports a ratchet regression on a
function nobody edited, such as `402.2686 -> 402.2687`. Raise that mark by hand in
`crapkit-ratchet.tsv` to the value verify prints and commit it; see [a mark never rises
through verify](ratchet.md#overrides-and-the-audit-trail).

A CRAP exactly at its ceiling now reads at it. CRAP(18, 2/3) is exactly 30, but its
double is 30.000000000000004, so at `target = 30` its remedy said `add-tests`, the run
totals counted it over target and seed marked it. It now reads `ok`, seed leaves it
unmarked, and a mark it already has leaves the marks file at the next `verify` that
passes.

Two numbers that are not stored move too. `est_uncovered_paths` rounds
`(1 - cov) * ccn` half to even on the exact product, so (1 - 5/12) * 6 = 3.5 reads 4
where it read 3. `crap_load` adds the scores exactly and rounds once, so a load at a
2 dp tie can move by 0.01. The first `trend` or `report` after upgrading sums every
stored run again, once; on a store of about a million scored rows that takes a few
seconds.

### Coverage lands on the function that owns it

Scores move on functions nobody edited:

- Under an istanbul lane a counter is placed by line and column. A statement counts
  from a function's body on and a branch from its declaration on. The statement
  istanbul writes for `const f = (x) => ...` runs at import and starts ahead of the
  arrow's body, so it now counts for the code around the arrow: an arrow no test calls
  reads 0 where it read 0.5. A ternary or `&&` that opens ahead of a callback on its
  line moves from the callback to the function around it. See [what the istanbul parser
  reads](lanes.md#what-the-istanbul-parser-reads).
- A function coverage.py or istanbul was told to leave out (`# pragma: no cover`,
  `istanbul ignore next`, `v8 ignore next`, and from coverage.py 7.10.1 a stub whose
  body is `...`) reads the new flag `excluded` at `crap = ccn`, where it read cov 0 at
  `ccn^2 + ccn`. Its remedy turns from `add-tests` to `ok` or `decompose`. A client that
  checks `flag` against the four older values, or reads the coverage summary's four
  counts, should accept `excluded` too. See
  [flags](../README.md#flags-why-a-coverage-number-is-missing).

### Rust rows

Rust's numbers move:

- A signature decides nothing. A `where` clause, a `?Sized` bound and a `for<'a>`
  binder no longer add to ccn.
- A `for<'a>` binder and the `for` of `impl Trait for Type` are no loop in ccn or
  cognitive inside a function's body too, and a `?Sized` bound adds no ccn anywhere. A
  test function that implements a trait for its stub falls by 1 or more.
- A `||` or `&&` with no operand before it is no operator. `move || n`, `f(|| 0)` and
  `|&&x|` cost nothing in ccn, cognitive or nesting.
- A let-else counts one decision in ccn, like the `if let` it replaces.
- `?` costs nothing in cognitive or nesting and keeps its 1 in ccn.
- `loop` is a loop in cognitive and nesting.
- `catch`, `switch`, `foreach`, `case` and `def` are names, not structures.
- A trait's required method, a foreign function and a `fn` pointer type have no row,
  and a function one of them swallowed gets its own. Prune drops the marks of the
  rows that are gone, and seed marks the new ones.
- `params` counts a parameter of tuple or generic type, or one written as a tuple,
  array or struct pattern, once.
- A parameter typed `&&T` reads `& &` in the function's long name, so that function
  takes a new ratchet key. Prune drops the old one.
- In Python and shell, a name spelled `switch` no longer adds to ccn_mod. The gated
  ccn is unchanged.

### Go, Zig and shell readers, and `//` comments

crapkit reads Go and Zig signatures to where the language ends them and ends a `//`
comment at its line in every language but C, C++ and Objective-C. Here is what moves:

- Rows appear. A function that had no row is listed: one after a package-level function
  type or a Zig `extern fn` prototype, a Zig function named `@"..."`, a Go method after
  a `}` that closes nothing, such as the one a Go type switch left, and a function whose
  signature sits on the line after a `//` comment that ends in a backslash (`// C:\dir\`)
  in Go, Zig, Java, JavaScript, TypeScript, TSX, Vue, Swift or Rust.
- Rows go away. A function type such as `var cb func(int) error` no longer opens a
  function, and a composite literal of functions, `[]func(){f, g}`, is no longer an
  anonymous row.
- Keys change for two kinds of literal. A function literal inside a package-level
  composite literal reads `(i int)` where it read ` i int`, and a package-level literal
  whose result is a function type reads `(a int)` where it read `(a int)func b int`. No
  named function's long name changes.
- `ccn` rises. The function around a function type gets back the block the type took.
  A function with a Go type switch reads its whole body, where it ended at the switch's
  `}`, and so does one whose result type holds braces, one with a `//` comment ending in
  a backslash in any of the languages above, and a Zig function whose multiline string
  holds a `}`. A Go `select`
  with a case reads one decision where it read none. A shell `a ? b : c` inside `(( ))`
  or `$(( ))` counts one. Any of these can be over its ceiling and fails the gate the
  next time its file changes.
- `ccn` falls. A Zig switch reads as one decision in `ccn`, the smaller of the two
  columns, where it read one per prong and one more for `else =>`. An `if`, `and` or
  `or` in the text of a Zig multiline string no longer counts. A shell `for ((;;))`
  reads one lower.
- Reporting columns move. `params` moves for parameters of function type or with a
  braced type, and for a package-level literal's parameters, which read 0. A Zig `try`,
  an optional's `?` and an error-set `||` stop adding to `cognitive`, and `try` and `?`
  stop adding to `nesting`. A shell glob's `?` stops adding to `cognitive`.

### Line ends

Some shell numbers, the line numbers of functions that sit below certain comments, and
some JavaScript and TypeScript coverage move:

- The shell reader ends a heredoc line at LF only, as bash does. It used to end one at
  a vertical tab, form feed, `\x1c`-`\x1e`, NEL, U+2028 and U+2029 too. A body line
  such as `note<FF>EOF` closed the body early and the lines up to the real `EOF`
  counted as code; code after one of those characters on the line that opens a heredoc
  read as body. `ccn`, cognitive, nesting and NLOC move for the function that holds
  such a line, up where body text had counted and down where code had been blanked. A
  function the change puts over its ceiling fails the gate the next time its file
  changes.
- lizard counted a comment's lines with Python's `str.splitlines`, which also ends a
  line at a vertical tab, a form feed, `\x1c`, `\x1d`, `\x1e`, U+0085, U+2028 and
  U+2029. A comment holding one of them moved every function below it down one line
  per character. A comment now counts one line per LF, as git, Python's compiler,
  coverage.py, c8 and `@vitest/coverage-v8` count it. Every such function's `start` and
  `end` move up to the lines it sits on, and a function the move had pushed onto its
  neighbour's lines now reads its own coverage and its own CRAP.
- An istanbul lane's line numbers land on crapkit's own lines. `@vitest/coverage-v8`
  ends a JavaScript line at LF only; Babel (jest, nyc, `@vitest/coverage-istanbul`) and
  TypeScript source maps also end one at U+2028 and U+2029; crapkit ends one at LF,
  CRLF and a lone CR. Below a lone CR, or a U+2028 in a string or a comment, the
  coverage of one function went to a neighbor, so `cov`, CRAP and the uncovered lines
  `verify` checks a diff against move for JavaScript and TypeScript functions in such
  files. A function that reads less covered now can go over its ceiling.

### C, C++, Objective-C and Java rows

- `params` counts each declaration in a C, C++, Objective-C or Java parameter list,
  named or not: `f(int*, char)` reads 2, `f(const int arr[4])` and Java's
  `main(String args[])` read 1, and an Objective-C method counts its arguments.
  `params` is reported and never gated.
- Functions that had no row get one: those after a `<` comparison in a default
  template argument or a member initializer, which lizard read as a template bracket,
  a function returning a function pointer after a return type that ends in `*` or
  `&`, `char *(*get(void))(void)`, a C++20 function with a trailing requires-clause,
  `void f(T t) requires C<T>`, the functions of a namespace whose head holds a macro
  or an attribute, `namespace std _GLIBCXX_VISIBILITY(default)` in every libstdc++
  header, a constructor with a function-try-block, `S::S(int a) try : x(a) {`, the
  function after a constructor whose member initializer list ends in a pack
  expansion, `S(B... b) : B(b)... {`, and the member functions of a class defined
  inside a function. The function around such
  a class no longer pays for its members' decisions, so its `ccn` and `cognitive`
  fall. In Java, methods get rows after an
  annotated local variable, an enum constant with a body or an annotation element
  with a default, and inside a constant's body, an interface field's anonymous class,
  or a record or interface declared in a method, whose `ccn` falls the same way. A
  method whose anonymous or local class ended on a field or an abstract method gets
  its row back under its own name, with the class's field lines in its `nloc`. A Java
  text block is one string, so a `{` or an `&&` between two quotes in its text is no
  longer code that hides the next method or adds to `ccn`. A newly listed function
  over its ceiling fails the gate the next time its file changes, and `ratchet seed`
  marks it.
- Rows that were not functions go: a declaration whose trailing return type holds
  braces, an Objective-C instance-variable block, a C++20 concept's requires-expression
  (`requires( T a)`), a row named after the first statement of a function with a
  requires-clause (`if( t)`), a namespace read as one function named after its
  head's macro (`_GLIBCXX_VISIBILITY( default)`), each handler of a function-try-block
  (`catch( ...)`), a constructor's first member initializer read as the constructor
  (`x( a)`), a Java enum constant, an annotation element with a braced default, a Java
  field's anonymous class, and a Java record declared first in a class or interface
  body, which read as a method named after it.
- Rows named after an attribute take the function's name: `__attribute__((noinline))`,
  `API_AVAILABLE( ios(10))`, `LOCKS_EXCLUDED( mu)` after a constructor or destructor,
  or `)` for an Objective-C method, and Java rows named
  after an annotation with arguments, `InlineMe( replacement = ...)`. A member of a
  class declared with an export macro or an attribute, `class Q_CORE_EXPORT QString`
  or `class __declspec(dllexport) Foo`, reads `QString::size`, where it read
  `Q_CORE_EXPORTQString::size`, or had no class in its name, and a member of `namespace
  ns ABI_TAG` or `namespace a::inline b` reads `ns::f` or `a::b::f`, where it read
  `nsABI_TAG::f` or `a::inlineb::f`. A C-family function whose
  declarator sits in parentheses takes its own name: `int (*get(int k))(int)` reads
  `get( int k)`, where it read `int( * get(int k))( int)`, `static constexpr T
  (max)()` reads `max()`, where it read `T( max)()`, and a name a macro builds,
  `STRINGLIB(find)(...)`, reads `find(...)`. A Java method
  inside a method's anonymous or local class reads `A::go.run()`, where it read
  `A::A::go.run()` or took the name of a class declared before the method. A Java
  method inside an enum, an interface or a record carries its name, `A::F::g()` where
  it read `A::g()`, and a sealed class's methods read `Shape::area()`, where they read
  `ShapepermitsCircle::area()`. The name is a new ratchet key, and so is the long name of a
  function after one whose default argument holds a parenthesized `<`:
  `g( int a , int c)` where it read `g(int a,int c)`.
  `ratchet prune` drops the old mark and `ratchet seed` marks the function under its
  new name if it is over its ceiling.
- A C++ `&&` that declares a reference costs nothing: `for (auto&& x : r)`,
  `static_cast<T&&>(v)`, `auto&& w = f();` and a lambda taking `auto&&` lose the `ccn`,
  `cognitive` or `nesting` point it cost, so marks can tighten on the next seed. A
  logical `&&` followed by an assignment in the same condition, `while (n > 0 && (p =
  next(p)) != 0)`, counts in `ccn` again, which can put a function over its ceiling
  and fail the gate the next time its file changes. A function whose parameter list
  held a `&&` can read one `nesting` level deeper, the depth the same body reads
  without it; `nesting` is never gated.
- A function with a function-try-block, `int main() try { ... } catch (...) { ... }`,
  counts its handlers: each `catch` adds 1 to `ccn` and 1 to `cognitive`, as it does
  in a try statement, which can put the function over its ceiling and fail the gate
  the next time its file changes.
- A constructor whose member initializer list ends in a pack expansion, `S(B... b) :
  B(b)... {`, counts its body's decisions, where it read `ccn` 1, which can put it
  over its ceiling and fail the gate the next time its file changes.

### The Swift and Rust readers

Swift:

- A function that `super.init(...)`, `.init(...)`, `r.get()`, `case .get`,
  `Socket(protocol: p)`, `return type`, a `#fileID` default, `if #available(...) {` or a
  closure after a comma hid is listed, and the function that held it reads only its own
  lines. A failable `init?` or `init!` is listed as `init`. A function named by a raw
  identifier, ``func `keeps onboarding if offline`()``, is listed under that name,
  backticks included; a Swift Testing suite gains a row per such test. A newly listed
  function can be over its ceiling and fails the gate the next time its file changes.
- A function listed before keeps its long name, so its mark keeps its key. A row that
  named no function is gone: `init id : id` for a `super.init(id: id)` call, `get` for
  `r.get()`. `ratchet prune` drops its mark.
- ccn falls where the `case` of `if case`, a keyword argument label (`for name:`) or an
  optional mark (`(any Error)?`) counted. It rises by 1 for each `??` and for each `?`
  of an optional chain after a name (`a?.b`, `self?.done()`), which counted nothing.
  `Empty?.none` and `Int?.some(1)` name a member of the optional type and chain
  nothing. A chain after `)` or `]` (`f()?.g`) keeps its 1 and loses the nesting level
  it opened. `params` and `nesting` fall where a comma inside one parameter or a `try`
  counted; neither is in the score.
- A function whose string interpolation holds a string with a brace in it,
  `"\(f("{"))"`, is listed; it had no row. A `&&`, `||`, `??` or `?:` inside `\( )`
  now counts in ccn and cognitive, and an `if` or `for` in the text of a multi-line
  string no longer does.

Rust:

- A function with an attribute on the same line as its `fn`, `#[inline] fn f() {`, is
  listed; it had no row. A decision or a brace after a raw string (`r#"..."#`), a raw
  identifier (`r#type`) or an attribute on the same line now counts.

### Shell and PowerShell rows

Prune matters here: some rows are renamed and some phantom rows go, and their marks
are left under names the run no longer has.

- A shell function's `nesting` reads how deep its blocks go. lizard's ND column closed
  a level only on a `}` or at a `;`, and shell closes `if`, loops and `case` with `fi`,
  `done` and `esac`, so every block leaked a level: seven ifs side by side read 6, four
  nested read 3, and a `case` read 0. They read 1, 4 and 1 now, and `&&` or `||` opens
  no level. `nesting` is reported and never gated, so no verdict moves with it.
- A command inside a quoted substitution counts: `x="$(cmd || true)"` reads ccn 2
  where it read 1, as `x=$(cmd || true)` always did. Backticks inside quotes, `$(( ))`
  and a substitution inside `${v:-...}` count the same way. A heredoc opened inside a
  quoted substitution, as in `v="$(node - "$f" <<'JS'`, is a body, so its program adds
  no ccn and no NLOC, and so is one opened on the line that closes a multi-line
  quoted substitution. On a large consumer repo 146 of 1,613 shell functions rose by 1
  to 20, with this and the `#` change below, and 2 fell, by 1 and 23. A function the
  rise puts over its ceiling fails the gate the next time its file changes.
- A case statement inside a quoted substitution counts its arms, and a function whose
  body is a subshell, `f() ( case ... esac )`, ends at its own `)` rather than at the
  first pattern's. Such a function can gain lines and ccn: one on a large consumer repo
  went from 68 lines and ccn 26 to 140 lines and ccn 46.
- A shell reserved word counts only where shell reads one, so `echo done`,
  `git for-each-ref`, `done=1` and a `--exit-if-exists)` pattern no longer add a
  decision or open or close a block, and a `;;` in `for ((;;))` is no case arm. These
  only lower numbers: on a large consumer repo 3 functions fell, by up to 1 in ccn and
  2 to 24 in cognitive.
- A `#` inside a word, as in `(( 8#$mode ))` or a regex's `[#/]`, is part of the word
  and opens no comment. The rest of such a line had counted nothing, so a function
  with an `&&` after it rises in ccn (5 on a large consumer repo, by 1 to 5), and a
  `))` or `then` hidden there had left the function open to the end of its file. A
  file like that gains every function after it: one script went from 16 rows to 101,
  and a new row can be over its ceiling.
- A shell `?` outside arithmetic, as in `ls a?b` or a `=~` regex, no longer costs a
  ternary's cognitive +1 and its nesting, nor do the words of `echo break 2`, an array
  literal's words on later lines, or `goto`. These only lower numbers (on a large
  consumer repo 14 functions fell in cognitive, by 1 to 4), except that a `?:` inside
  `$(( ))` or `(( ))` now adds 1 to ccn, as a C ternary does.
- A PowerShell expression inside a `$( )` subexpression in a double-quoted string
  counts: `"$($a -and $b)"` reads ccn 2 where it read 1, up to eight levels of
  parens deep. Its cognitive score rises by the same decisions, and an `if` or loop
  there opens a `nesting` level. On a large consumer repo 6 of 339 PowerShell
  functions rose by 1 in ccn and 1 or 2 in cognitive, and one by a nesting level.
  Quotes inside the subexpression pair among themselves, so a function that held
  `"$(Get-Item "x{")"` and had no row now has one, and can be over its ceiling.
- PowerShell keywords count in any case where a statement starts: a capitalized `IF`,
  `-Or` or `ForEach` now costs what its lower-case spelling costs, and a `Default` arm
  is free. A keyword word that is a hashtable key, a command, an argument or a member
  costs nothing in any case (`@{ if = 1 }`, `$xs | foreach { }`, `git switch main`),
  and neither does the `?` in `$?`. `-and`, `-or` and `-xor` in a command's arguments
  are its parameters and cost nothing: `if (Test-Path $a -or $b)` reads 2 where it read 3.
  PowerShell 7's `&&`, `||`, `??`, `?.` and `?[` count once each. Gated `ccn` can rise
  or fall. `-and` and `-or` stop adding a nesting level.
- A PowerShell switch arm costs one point whatever its pattern or subject holds, and
  `ccn_mod` no longer reads one above `ccn_std` for each switch. The arms still cost
  their points in both columns, so the gate reads a twelve-arm switch as 13, as before.
- PowerShell rows appear for `Function Name`, `function script:Name`, `function
  Get.Name` and a function that followed a stray `configuration` or `filter` word, and
  the phantom rows those words opened go. A function whose header list writes a
  parameter in braces changes its key: `function A(${x})` reads `A ${x}` where it read
  `A $ { x }`. So does one whose header list holds `$?` (`A $x = $?` where it read
  `A $x = $ ?`) or a keyword or operator in capitals (`-AND`, `-Or`, `IF` and `ELSE`
  read in lower case). A `param(...)` block moves no key. A class method's decisions leave the function that declares the class.
  An unquoted URL's `//`, a glob's `/*` and a `#` inside a word (`a#b`, `C#`) no
  longer hide the code after them, so a function on the same line or between `a/*`
  and a later `*/` can gain decisions or a row, and `function Get-A#B` gets a row
  under that name. A function that gains a row, or keeps one and gains decisions, can
  be over its ceiling and fails the gate the next time its file changes.
- A shell function whose name holds `-`, `.` or `:` keeps the whole name: `do-thing`
  read `thing`, and `function log::info` had no row. A keyword inside a longer word,
  such as `select` in `xcode-select`, counts nothing, so some cognitive scores fall
  sharply.

See [the per-language gotchas](configuration.md#per-language-gotchas) for what each
reader counts.

### Cognitive complexity per language

`cognitive` reads each language's own rules. `ccn`, coverage and every function's name
stay as they were, so this moves no mark and no gate verdict.

Expect the `cognitive` column to change in `next-item --json`, `brief`, exports and
the MCP tools on the first run after upgrading, most often by 1:

- It drops where the pass charged recursion that was not there, the common case in
  Python: a method that calls another object's method of the same name, as an
  `__init__` calls `super().__init__()`, a method that wraps the module function it
  is named after, a local variable or import named like its function, or a C++, Java
  or Swift overload that forwards to another overload of its name.
  It rises in Go, shell, PowerShell, Java and C++ functions that call themselves,
  which cost nothing before, and in a Rust, Swift or Zig function that calls itself
  through its type's name (`R::spin(n - 1)`).
- A sequence of logical operators continued on the next line, or split by a comma in a
  call's arguments, costs +1 once where it cost 2. A negated group such as
  `a && !(b && c)` costs its own +1, and `??` costs nothing. Each operand of a
  conditional expression holds a sequence of its own, so `x && y ? a && b : c && d`
  costs 4 where it cost 2, and so does a group compared: `a && (b && c) == d && e`
  costs 2 where it cost 1.
- Swift's `repeat` and `guard`, Rust's `loop`, Go's `select`, PowerShell's `trap` and
  a Python `match` statement cost what a loop, an `if` or a `switch` costs; they cost
  nothing before. Words that are a keyword in another language, such as Python's
  `c.do(1)` or JavaScript's `p.then(g).catch(h)`, cost nothing.
- A structure inside a braceless body (`for (...) if (x) visit(x);`) costs one more in
  C, C++, Objective-C, Java, JavaScript, TypeScript and Zig. A structure after a block
  that holds a JavaScript or TypeScript arrow with a block body costs one less.

In Python the same pass measures `nesting`, which moves too: a comprehension's level
closes with its bracket, so `[p for p in a] + [q for q in b]` reads 1 where it read 2,
and a `match` statement opens a level. The other languages keep lizard's `nesting`.
Over 12,432 functions in 20 open-source projects, 1,082 move `cognitive`, 851 of
them by 1 and 1,014 by 3 or less, and 69 Python rows move `nesting`. The
[changelog](../CHANGELOG.md) lists every rule with an example.

### Nesting in every language

`nesting` moves outside Python, and `cognitive` in a few functions in every language.

- A function's `nesting` in C, C++, Objective-C, Java, JavaScript, TypeScript, Go,
  Rust, Swift, Zig, PowerShell and shell reads the depth crapkit's cognitive pass
  measures, the way Python has since 0.5.0. lizard's ND column, which those rows read
  before, opened a level for `&&`, `||`, `case` and `try` (PowerShell's `-and` and
  `-or` too) and lost one at a `}` or a `;`, so most rows that move go down: a switch
  reads 1 whatever its case count, a condition's operators add nothing, and a shell
  line such as `[ "$a" ] && [ "$b" ] || echo no` opens no level. Rows go up where ND
  lost a level, as with nested loops, an `if` inside an `else`, a Rust `match`, or a
  shell or PowerShell `case`/`switch`, which ND read as no level.
- `cognitive` drops in a function where a guard without braces (`if (a) return;`)
  comes before a block that is not a structure's, such as a bare `{`,
  `synchronized`, `@autoreleasepool` or a lambda's body: the structures inside that
  block no longer pay a level of nesting for the guard. 9 of 21,099 functions in the
  measuring corpus move.
- Both columns go up where a structure's header holds a `{` of its own, a literal or
  a lambda's: Go's `for _, x := range []string{"a", "b"} {` and table-driven tests,
  C++ initializer lists, Java array initializers, destructuring in a `for`. The body
  after that header now counts as the structure's, so the structures inside it read
  one level deeper and cost one more. In Go code expect about 1 function in 100 to
  cost more; 264 of 24,540 did in the Go standard library and actionlint. Both
  columns go down in a Swift function with an argument label spelled `for`, whose
  body no longer reads as a level. In a PowerShell function with a `[switch]`
  parameter only `cognitive` goes down: lizard's ND never read the word, so its
  `nesting` stays.
- `cognitive` goes down where a word spelled like a structure keyword is a name: a
  `do` in Go and Zig, which have no do-while (`do(n)`, a method or closure named
  `do`), a `do` after a `.` in any language but Python (`obs.do(fn)`), and an `if`,
  loop, `switch` or `catch` followed by a `:`, such as an object's key `{if: 1}` or a
  Swift argument label `g(for: x)`. The word no longer costs +1, and in Go the
  structures inside a method named `do` no longer pay a level of nesting for it. 6
  of the 24,540 Go functions moved, one of them from 45 to 9. A keyword key inside a
  function also stops opening a `nesting` level per key. A Go variable named `while`
  stops costing +1 at each use, since Go has no `while` loop.
- Both columns go up, in every language, where a `while` loop comes right after a
  `}` that closed anything but a do-while's block, such as an `if` block, an object
  literal or a Python dict. The loop read as the tail of a do-while, cost nothing and
  opened no level. 16 of the 21,099 functions in the measuring corpus move
  `cognitive`, 3 of them in Python, and 6 move `nesting`.
- In Zig, `nesting` goes down in a function whose switch has an `else =>` prong with
  a block, or where an else with a payload and no braces (`else |err| return err;`)
  comes before another block. `cognitive` goes down where `else |err| if` links an
  else-if chain, which now costs +1 as `else if` does. 37 of 3,475 functions in the
  Zig standard library lose a level and 12 cost less; 8 gain a level, where a
  `switch` follows `else |err|` and now sits in the else's body as it does after a
  plain `else`.
- In Zig both columns go down in a function whose return type holds an `if`
  (`fn f(x: anytype) if (A) u8 else u16 {`), whose whole body read one level deep,
  as it did in 0.8.0. 3 of the 3,487 Zig standard library functions lose a level
  and cost less.
- `nesting` and `cognitive` are reported and never gated, so no verdict moves with
  them. Expect both columns to change in `next-item --json`, exports and `brief` on
  the first run after upgrading; the [`nesting` row](agent-json.md#item-fields) says
  what opens a level.

## Upgrades from older releases

These sections say what the analysis versions before 13 moved. A repo that upgrades
across several of them reads each section it crosses.

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

### Analysis version 8

0.4.5 moved the reader from analysis version 7 to 8. Shell cognitive complexity nests
since then: `fi`, `done` and `esac` close the level `if`, a loop or `case` opened. The
first `verify` after that upgrade refused the marks, quoted here as crapkit prints it
today:

```
$ crapkit verify
crapkit: ratchet marks were recorded under [crapkit-analysis=7 lizard=1.24.0] but this run measures [crapkit-analysis=8 lizard=1.24.0] - CRAP scores are not comparable across metric versions; run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline with `crapkit ratchet seed`
```

That transition changed cognitive complexity, not `ccn` or the CRAP formula. Later
analysis versions also move function names, so a repo on a release that old follows the
steps at the top of this page, which re-seed once under the current reader.

## Release evidence

Each release passes two suites before it ships. [The accuracy suite](accuracy.md) checks
every number crapkit computes against outside tools and hand tables, and [the deploy
suite](../tools/deploy/README.md) installs crapkit through each channel and into each
agent, fresh and as an upgrade from the releases before it. Hosted CI runs the source
suite on Python 3.11, 3.12, 3.13 and 3.14 on Ubuntu and Windows, and on Python 3.13 on
macOS for the letter-case rows.
