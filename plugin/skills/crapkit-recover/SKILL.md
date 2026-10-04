---
name: crapkit-recover
description: "Recover a crapkit run that refused, and tell a real refusal from a line that only looks like one: which exit code means what, the seven causes behind a lane that wrote no artifact, the tainted-baseline escape, and why a crapkit-ratchet.tsv conflict goes to `crapkit ratchet merge` and never to hand-resolution. Use when a crapkit command exits 3/4/5/6/7/8/9, a lane reports \"produced no artifact\" or \"wrote no artifact this run\", doctor says a shell \"cannot run\" a lane's first word or that a lane \"declares no results_artifact\", a run \"cannot serve as a baseline\", marks \"were recorded under\" another metric version, a ratchet regression names a function you never touched, verify reports a tainted baseline, seed or prune refuses an \"ambiguous legacy function identity\", git conflicts crapkit-ratchet.tsv, a command names a file git holds \"in bytes that are not UTF-8\", the \"measurement owner stopped\", `crapkit claude-hook` exits 2 with an advisory, or `crapkit doctor --plugin-root` reports drift."
---

# Recovering a refused run

Route by the string the command printed. Every row names the one command to run before you
decide anything. The links point at the crapkit repo on GitHub, because the repo this
session works in does not hold those pages.

## Lines that are not failures

Start here. Each of these reads like a refusal and none of them stopped anything.

| What printed | What it means | What to do |
|---|---|---|
| "crapkit advisory: N function(s) over ceiling C in PATH (the edit landed; nothing was blocked)", exit 2 from `crapkit claude-hook` (added context on exit 0 in Cursor, Copilot CLI and VS Code) | The PostToolUse hook judged a function the edit changed. PostToolUse runs after the write and cannot block | Decompose that function now. The commit gate refuses it later, with more work stacked behind it |
| "crapkit advisory: PATH could not be read, so no function in it was judged (the edit landed; nothing was blocked)", exit 2 from `crapkit claude-hook` | The edit left a file no reader can read, so none of its functions was judged. The commit gate refuses that file once staged, with the same `UNREAD` line | Change what the reason names (for a TypeScript arrow, wrap its body in parentheses or a block), or list the file under `[exclude]` in `crapkit.toml`: [README: the gate](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#the-gate) |
| "crapkit gate: N staged function(s) carry a ratchet mark and were not gated - `crapkit verify` fails a mark that rises" | The commit gate pardoned debt the ratchet already signed for. The commit went through | Nothing. Only `crapkit verify` judges whether a mark rose |
| "crapkit doctor: the plugin at PATH is version X, and the crapkit its hooks spawn (CLI_PATH) is Y", exit 1 | The plugin and the crapkit on PATH ship as separate artifacts and drifted apart; the line names which executable answered | Update whichever is behind: the CLI, or the plugin with your agent's refresh lines, in Claude Code `claude plugin marketplace update crapkit` then `claude plugin update crapkit@crapkit --scope user`, in Codex `codex plugin marketplace remove crapkit`, then the README's `codex plugin marketplace add` line at the CLI's release tag, then `codex plugin add crapkit@crapkit`. A second `claude plugin install` only answers that the plugin is already installed |
| "crapkit doctor: checking PATH", then nothing | You ran `--plugin-root` with no PATH, or named a directory above the plugin root, and doctor found the install (one line per install it checks). The line says which tree the verdict is about | Nothing. Exit 0 means the plugin and the CLI agree |
| "WARN lane 'py' declares no results_artifact: the crashed-worker check and the no-new-failures check (exit 8) cannot run for it", from `crapkit doctor` | The lane measures coverage exactly as before. What it cannot feed are the two checks that read a test-results file | Add the junit flag and `results_artifact` the WARN prints. Until then exit 8 can never fire for that lane's scopes: [AGENTS: when a lane will not start](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#when-a-lane-will-not-start) |
| "note lane 'js': runner unknown (npm run cov names none crapkit knows); runner-specific hints and refusals are off for it", from `crapkit doctor` | Not a failure, and doctor's exit code does not change. crapkit read no runner from the lane's command, the package.json script it runs, or devDependencies, so the hints and refusals that key on one runner skip this lane. The lane measures as before | Nothing, or name the runner in the command to turn those checks back on: `npx vitest run --coverage` in place of `npm run cov`: [docs: how crapkit reads a lane's runner](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#how-crapkit-reads-a-lanes-runner) |
| "warning: crapkit-ratchet.tsv carries no metric stamp (written before stamping)", from `crapkit verify` | The marks file predates stamping, so nothing can be compared against it | Run `crapkit coverage`, then `crapkit ratchet prune`, then `crapkit ratchet seed`: seed stamps the metric of the run it reads: [docs: the metric stamp](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-metric-stamp) |
| "crapkit: lane 'py': coverage.py report carries no branch data, so the coverage term is statement-based for this artifact - add --cov-branch to the lane command to measure branches", from `crapkit coverage` | The lane scored on statements instead of branches, so CRAP is understated on branchy functions. A report carrying neither branches nor statements is still exit 5 | Add `--cov-branch` to the lane command, then rerun `crapkit coverage`: [docs: pytest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#pytest) |
| "crapkit: lane 'py': coverage.py report has no function regions for 1 of 40 file(s) (tpl/page.html) - those files are skipped and the rest of the report is scored", from `crapkit coverage` | A plugin reporter, django or jinja templates, declares no code regions for those files. Every other file in the report scored. A report where no file carries regions is still exit 5 | Nothing, unless you expected those files measured: [docs: a file the report carries no regions for](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#a-file-the-report-carries-no-regions-for) |
| "warning: churn counts read only the commits this clone holds; this shallow clone does not hold every commit: set fetch-depth: 0 on the checkout or run git fetch --unshallow", from `crapkit worklist` or `crapkit next-item` (`brief` and `ratchet report` name mark ages too) | The checkout is a shallow clone, so churn, mark ages and repayments count only the commits it holds: one per file at depth 1. The command answered, and its JSON says `shallow: true` | Nothing blocks. Before trusting the ranking or a mark's age, set `fetch-depth: 0` on the checkout or run `git fetch --unshallow`: [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) |

Six more lines come out of `crapkit doctor --plugin-root`, same exit 1.
"crapkit doctor: the plugin at PATH asks for hook protocol N" means the plugin is ahead of
the CLI, so the advisory hook exits 0 in silence on every edit.
"crapkit doctor: the plugin at PATH has no .claude-plugin/plugin.json, so it is no plugin
root" means the path holds no crapkit install at or below it, and the line ends with what to
do instead: "name the plugin root or a directory above it, or run `crapkit doctor
--plugin-root` with no PATH to check the installs Claude Code and Codex recorded". "has a
.claude-plugin/plugin.json that is not a JSON object" and "has a .claude-plugin/plugin.json
with no version string" mean the file is there but damaged, and each line ends with the
reinstall, once per scope that holds the install. In Claude Code that is "reinstall it with
`claude plugin uninstall crapkit@crapkit --scope user`, then `claude plugin install
crapkit@crapkit --scope user`, and restart Claude Code's sessions", in Codex "reinstall it
with `codex plugin remove crapkit@crapkit`, then `codex plugin add crapkit@crapkit`, and
start a new Codex task". A `claude plugin install` alone only answers that the plugin is
already installed. For a plugin Claude Code loads in place from a local directory, the line
names the `git checkout` or the copy that puts the file back instead.
"crapkit doctor: no installed crapkit plugin under DIR or CODEX_DIR" means the
bare flag found nothing in Claude Code's plugin directory or in Codex's: install the plugin
with the commands the line names, Claude Code's or Codex's.
"crapkit doctor: FAIL no `crapkit` on PATH" means the plugin is installed but the bare name
its hooks and `.mcp.json` spawn resolves nowhere, so every PostToolUse edit fires a command
that cannot start and the MCP server never comes up. A `pip install` into a project `.venv`
or `pip install --user` is the usual way to land there: `pipx install crapkit`, or point the
plugin at the environment holding it. When the line ends `This crapkit's launcher is in
DIR`, add DIR to PATH and restart the agent. `crapkit claude-hook` and
`crapkit doctor --plugin-root` are both specified in
[README: subcommands](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#subcommands).

Exit 2 from any other crapkit command is argparse: the subcommand or the flag does not exist
in this version.

## By exit code

| Exit | What refused | Owner | First command |
|---|---|---|---|
| 3 | config: `crapkit.toml` unparseable, a lane command the guard refuses, a metric-stamp mismatch, a `test-scoped` file under no templated scope, a scoped file whose name is not UTF-8 or a path argument naming a file whose name is not UTF-8 (rename it with `git mv`), a root `package.json` that `init` cannot read (`init wrote no file`; save it as one UTF-8 JSON object), a root on a Windows network share (map it with the `net use` line it prints) | [docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | `crapkit doctor` |
| 4 | git: not a repository, a repository with no commit yet or one git refuses to open (each refusal names its fix), a baseline commit rewritten out of the history or made on a branch HEAD does not contain, a baseline commit or fork point a shallow clone does not hold, or `crapkit ratchet report --enforce` with a debt key set in any shallow clone (mark ages and repayments need the whole history). The shallow ones end `set fetch-depth: 0 on the checkout or run git fetch --unshallow` | [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) | `crapkit runs list`, or `git fetch --unshallow` when the line names a shallow clone |
| 5 | a lane produced no artifact, produced one measuring a different tree or spelling this one absolutely, wrote a report crapkit refuses to read, timed out past its retries, or refused a container; the measurement owner stopped; or `crapkit verify --reuse-artifacts` found a lane's declared `results_artifact` missing or unreadable, stored no run, and ended `run verify without --reuse-artifacts so the lane writes it again`: run `crapkit verify` without the flag | [docs: what a failed lane does to scoring](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#what-a-failed-lane-does-to-scoring) | `crapkit coverage --lane NAME` |
| 5, kind `internal` | "stopped: an internal check failed": a number crapkit calculated broke its documented bound, and crapkit stopped before storing or printing it. A crapkit bug, not the repo's: a rerun stops at the same check, and no crapkit.toml setting moves it | [docs: errors](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md#errors) | `crapkit --version`, then report it with the whole message at https://github.com/JeanFrancoisGagne/crapkit/issues |
| 6 | gate: a function the diff touched is over its ceiling and above any ratchet mark it carries; or an `UNREAD` line names a changed file no reader could read (change what its reason names, or list it under `[exclude]`), and an override grants nothing until it is gone | [AGENTS: gate the edit](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#3-gate-the-edit) | `crapkit rescore FILE --gate` |
| 7 | ratchet: a marked function scores worse than its recorded mark | [docs: how verify uses the ratchet](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#how-verify-uses-the-ratchet) | `crapkit explain PATH NAME` |
| 8 | a test that passed in the baseline fails now | [README: exit codes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-codes) | `crapkit test-scoped FILE` |
| 9 | more uncovered changed lines than `diff_uncovered_max` | [docs: configuration](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | `crapkit verify --json` |

`verify` reports the first of 6, 7, 8, 9 that fires, so a fixed 6 can uncover a 7 underneath
it. An exit 8 from `verify --baseline-tsv FILE` that comes with "warning: the baseline file
FILE holds no test results" can be a failure the default branch already had: crapkit 0.8.0 or
older wrote that file, and it forgives no failure. Re-emit it on the default branch with
`crapkit verify --emit-baseline FILE` and commit it:
[docs: the portable baseline's stamp line](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/portable-records.md#the-portable-baselines-stamp-line). Exit 1 is three unrelated things at once:
[README: exit 1 means one of three things](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#exit-1-means-one-of-three-things)
splits them by command.

`measurement owner stopped before confirming ownership; its error is at the end of
/repo/.crapkit/owner.log` (or `during command registration`, or `before publication`), exit 5,
means the helper that holds the lane locks and stops each command's process tree ended early.
Read the last dated entry in the file the line names: `.crapkit/owner.log` for `coverage`,
`verify` and `mutate`, `~/.cache/crapkit/owner.log` for `test-scoped` and the MCP server.
`it wrote nothing to` in place of `its error is at the end of` means a signal ended it, often
the OOM killer: rerun.
[docs: when the measurement owner stops](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#when-the-measurement-owner-stops).

## Three exit-3 signatures worth naming

Exit 3 fires before any lane runs, so nothing was measured and nothing was written.

`ratchet marks were recorded under [crapkit-analysis=11 lizard=1.24.0] but this run
measures [crapkit-analysis=13 lizard=1.24.0]` is the 0.8.1 upgrade, not a break. Version 13
moves numbers in every language but Python, and some of Python's cognitive and nesting, so
CRAP scores from the two versions are not comparable:
[docs: analysis version 13](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#analysis-version-13).
Run `crapkit coverage`, then `crapkit ratchet prune`, then `crapkit ratchet seed`: prune
drops a mark whose key the new analysis moved, and seed stamps the metric of the run it
reads, so a seed from a run the older crapkit measured keeps the old stamp and verify keeps
refusing. When a failed verify pins the baseline, plain prune and plain seed both read the
pinned run. Name the newer one to each: `crapkit ratchet prune --baseline N`, then
`crapkit ratchet seed --baseline N`. On such a store a plain `crapkit verify` prints N in the
taint warning above its refusal, and the refusal ends by saying a failed verify pins the plain
seed; `crapkit verify --baseline N` ends with that seed itself. When the first bracket is the
newer one, a newer crapkit or lizard wrote the marks (`the marks come from a newer crapkit
than this install`): upgrade this install, and never seed, since seed and prune refuse those
marks and a seed would restamp the team's marks backwards:
[docs: the metric stamp](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-metric-stamp).

`lane 'py': positional argument 'slow'' narrows a full-suite coverage run ... (cmd.exe
does not treat ' as a quote: write the value in double quotes)` is the lane guard reading
the command the way the shell will. On Windows a single-quoted value reaches the runner
one word per space, so the guard sees a positional that would narrow the run. Rewrite the
value in double quotes:
[AGENTS: when a lane will not start](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#when-a-lane-will-not-start).

`src/caf\xe9.py is in scope 'src', but git names it in bytes that are not UTF-8 ... rename
it (git mv) to a UTF-8 name` means a scope takes a file whose name git holds in another
encoding, a Latin-1 name made on Linux, so no row can be keyed on it and no gate may pass it
unread. Rename it and commit: on Linux `git mv $'src/caf\xe9.py' src/café.py`; on Windows,
where Git for Windows checked the file out as `src/café.py`, `git add -A` stages that rename.
`crapkit claude-hook` says the same about a file an agent just wrote under such a name, as an
advisory at exit 2 (`crapkit advisory: src/caf\xe9.py is in scope 'src', but git names it in
bytes that are not UTF-8 ...`): the edit landed, no function in it was judged, and the rename is
the fix. The line names the first such file and counts the rest; under `--json` the error
object lists every one in `unread_files`, each `{path, reason, dirty}` as a gate verdict
lists an unread file. The `check_gate` tool answers such a file with `gate.ok` false and the file in
`gate.unread_files`, a failed gate and not a broken tool. `left out docs/r\xe9sum\xe9.txt: git names it in
bytes that are not UTF-8` is a warning for a tracked name no scope takes, and the command's
own exit stands; `--json` lists the same names in `unreadable_names`:
[docs: file paths](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#file-paths-and-root-discovery).

## An exit-5 line about the report itself

`lane 'py' FAILED: unparseable coverage.py report PATH: pkg/mod.py: outer: no start_line;
coverage.py writes it on every function from 7.13.1, so install coverage>=7.13.1 and rerun
the lane` means the lane ran and wrote its report with coverage.py 7.6 to 7.13.0, which
writes no `start_line`. crapkit reads each function's span from that line and refuses the
whole report rather than guess it. Install coverage.py 7.13.1 or newer where the lane runs,
`pip install "coverage>=7.13.1"`, or `pip install "crapkit[py]"` when crapkit shares the
suite's venv, then rerun `crapkit coverage`. A nested function can then score above its mark
once, which `verify` reports at exit 7:
[docs: upgrading](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#081-on-coverage-76-to-7130).

## a lane that wrote no artifact: seven causes

The lane log names which one. It sits at `.crapkit/lane-<name>.log`; the failure line quotes
its tail and names that path in full, so read the log before guessing: the tail is 500
characters of a file that holds the whole run.

On a lane with `retries` set, every attempt appends to that one file and the cause the
message quotes is read from the last attempt alone: the text after the final
`--- attempt N ---` banner line. A retry that died of something else than attempt 1 is
what you are being shown, and the earlier attempts are in the log above that banner, which
is why the path is worth opening. Attempt 1 writes no banner, so a log holding none is a
single attempt and its whole output is in scope.

| Cause | Signature in the log | Owner |
|---|---|---|
| No coverage provider installed, vitest | `MISSING DEPENDENCY '@vitest/coverage-v8'` | [docs: getting an artifact out of vitest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#getting-an-artifact-out-of-vitest) |
| No coverage provider installed, pytest | `unrecognized arguments: --cov`, so pytest-cov is missing from the environment the suite runs in, which a pipx or uv-tool install of crapkit never shares | [docs: pytest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#pytest) |
| Tests failed, so the runner wrote no report | a red suite and no file, vitest with `reportOnFailure` unset | [docs: reportOnFailure](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#reportonfailure) |
| The report landed somewhere the lane does not name | the suite passed and `artifact` still points at nothing | [docs: where artifacts live](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#where-artifacts-live) |
| Killed or refused before it could write | `timed out after Ns (attempt N)`, `wrote no output for Ns (attempt N), so crapkit killed it` (the `no_progress_seconds` watch), or `host-only (container runs OOM)` | [docs: a suite that stops making progress](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#a-suite-that-stops-making-progress), [docs: timeouts and retries](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#timeouts-and-retries) |
| pytest died during collection, so the coverage plugin wrote nothing | "Interrupted: N error during collection" in the log, with the junit on disk and the coverage JSON missing | Add `--continue-on-collection-errors` to the lane command, which `crapkit init` now writes: [docs: --continue-on-collection-errors](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#--continue-on-collection-errors) |
| The lane reran and rewrote nothing | "wrote no artifact this run - the PATH on disk predates it and is the previous run's", or with a results file left too "the PATH and PATH on disk predate it and are the previous run's". A command that only touches the old report counts as writing nothing: crapkit moves the file aside before the attempt. Under `--reuse-artifacts` the leftover stays refused while it holds the same bytes, so a touch or a copy keeps it refused, and new bytes (a real run, or a salvage combined by hand) lift it | [docs: the artifact has to be the one this run wrote](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#the-artifact-has-to-be-the-one-this-run-wrote) |

Before triaging any of the seven, check that the command ran at all. `crapkit doctor` reads
each lane with the shell that will run it and FAILs one whose first word will not start:
`lane 'py': cmd.exe cannot run 'python' (exit 9009)`. On Windows that is usually the Store
`python.exe` alias a stock PATH carries with no Store app behind it, which resolves and
then refuses to run, so nothing that only reads PATH sees it. Point the lane at a python
that runs: [AGENTS: when a lane will not start](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#when-a-lane-will-not-start).

doctor reads the lane command and nothing behind it. It checks that the first word of every
segment (`&&`, `||`, `&`, `|`) resolves on PATH, and it starts the line's own first word
once, which is the only word it starts. So a lane written as
`npm run test -- --coverage ...` is checked as far as `npm`, and the runner the package
script names is invisible to it. A package that lists `vitest` in `devDependencies` with no
`node_modules` on disk therefore passes doctor and then fails the lane:

    $ crapkit doctor
    ...
    doctor: no problems found
    $ crapkit coverage
    crapkit: lane 'js' FAILED: lane 'js' produced no artifact at .crapkit/cov/js/coverage-final.json (command exit 1); last output: ...
    'vitest' is not recognized as an internal or external command,
    operable program or batch file.

That signature is none of the seven. It is `not recognized` on Windows and `not found` from
sh on POSIX, and it means the suite's own dependencies are not installed. Install them,
then rerun `crapkit coverage`.

This is tooling, not your code. What it costs depends on whether any lane survived. A lane
that fails beside a lane that worked still writes a run: the failed lane's scopes fall back
to `no-lane`, the run is typed `partial`, and `verify` refuses to conclude at all. When
every declared lane fails, `coverage` prints a closing count, `crapkit: every lane failed
(N of N); the errors are above`, exits 5 and writes no run at all, so `crapkit runs` has
nothing to show and there is no partial run for `verify` to refuse against. Read the lane
lines above that count; it repeats none of them.

## "measured N file(s), none of them under the paths its scopes declare"

The lane wrote a real artifact, and none of the paths in it reach the scopes the lane
claims, so the join finds nothing and every function in those scopes would score
`untested`. **Read the paths first**: this message comes in three verdicts, two of them exit 5 and
only one of them lets the run score on. The measured paths decide which, and the message quotes a few of
them:

| The paths it reports | Verdict | Cause and fix |
|---|---|---|
| absolute or drive-lettered (`C:/…`) and resolving **outside** this checkout, or climbing out of it (`../…`) | the lane FAILS, **exit 5**; its scopes fall back to `no-lane` | the run measured a different tree: a stale artifact, or the wrong environment. A `python -m pytest` lane binds to whatever venv the shell has active, which in a second worktree is the other checkout's: run the suite through the project's own manager (`uv run python -m pytest …`). On an istanbul lane the reader rebases every path under this checkout's root, so an escaped path means the artifact was written elsewhere: rerun the suite here rather than reusing one copied in or restored from a CI cache. A `../` climb lands here whatever it points at: it is relative to a working directory the artifact never recorded |
| absolute or drive-lettered and resolving **under** this checkout | the lane FAILS, **exit 5**; its scopes fall back to `no-lane` | right tree, wrong spelling: the runner reported absolute paths and crapkit joins on root-relative ones, so the join finds nothing. Nothing about the environment is wrong. Turn the spelling off at the runner, then rerun the lane: coverage.py takes `relative_files = true` under `[tool.coverage.run]` in pyproject.toml (or `[run] relative_files = true` in .coveragerc), and an istanbul reporter takes its own `cwd`/`root` option |
| repo-relative but rooted one level down (`faro/core.py` where the scope is `src`), or no paths at all | a **warning** on stderr and **exit 0**: the run scores on, with every function in those scopes `untested` | the runner reports relative to a subdirectory: set `path_prefix` on the lane (coveragepy only; the istanbul reader never reads that key). Or it is the greenfield shape, a suite that imports none of the scoped source yet, where `untested` is the right answer and there is nothing to fix |

So a green `crapkit coverage` can still be carrying this: `lane 'py' measured N file(s) …
so every function in those scopes will score untested`. Nothing in either exit-5 row applies
to it. And `path_prefix` only ever prepends, so it cannot rescue an absolute path in the
other direction.

An artifact holding both shapes at once takes the first row. A path from somewhere else
can only have come from somewhere else, and the count in that message names the outside
paths alone, so a refusal reporting fewer paths than the artifact holds is not a miscount.

Owner: [docs: an artifact that measured a different tree](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#an-artifact-that-measured-a-different-tree).

## Exit 7 on a function you never touched

Test rot, not code rot. A ratchet regression fires whether or not the diff touched the
function, which is the whole point: deleting the coverage behind an untouched function is
enough to raise its CRAP. Start at `crapkit explain PATH NAME`, look for the test that
stopped exercising it, and restore the coverage rather than the code.

## Tainted baseline

`run N is not the baseline: verify run M FAILED with K finding(s)` means the newest run never
cleared its findings, so an older one is being measured against. Two escapes, both
legitimate:

- Fix the findings the older baseline still shows, then rerun `crapkit verify`.
- Accept the newer run by name: `crapkit verify --baseline N`, a visible act somebody can audit later.

The marks take the same name. `crapkit ratchet seed` and `crapkit ratchet prune` read the
run verify would pick, so after a failed verify they read the run before it, and their line
names the newer run they passed over and the `--baseline` that reads it.
`crapkit ratchet seed --baseline N` reads run N instead, refused for the same four reasons
as `crapkit verify --baseline N`. It is the way out when seed refuses the pinned run:
`ambiguous legacy function identity in PATH: NAME in run M; seed reads run M because verify run K FAILED after it`
means run M was stored before crapkit recorded where same-line functions sit, and no
coverage run changes which run seed reads. The line ends with the `--baseline` to pass:
[docs: naming the run to seed from](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#naming-the-run-to-seed-from).
The named seed can then refuse at exit 3 with `N mark(s) name functions run M does not hold`
and `same-line twins in K group(s) this seed would mark`: the marks file has no
`# crapkit-keys=1` line, and those marks hold it at the old key format. Run the
`crapkit ratchet prune --baseline M` it names, then the seed again. The twin groups it lists
are marks the seed would add, not saved marks to reconcile.

Owner: [README: the trusted baseline](https://github.com/JeanFrancoisGagne/crapkit/blob/main/README.md#the-trusted-baseline).
`crapkit runs list` prints `verdict=-` on runs that rendered no verdict.

The second escape can itself be refused, at exit 1, and the refusal carries the answer:

    crapkit: run 1 is an inventory run (no coverage was measured) and cannot serve as a baseline; trusted runs: 2; pass `--baseline 2` for the newest

Read the middle clause. It names why that run cannot serve, and the four reasons are a
failed verify, a hook run, a partial run (a lane subset, or a lane that failed) and an
inventory run. Then take an id from `trusted runs`, or the one the line hands you. A run
id that is not in the store at all gets a different line naming `crapkit runs`.

## A conflicted crapkit-ratchet.tsv

The `resolving-merge-conflicts` skill's always-resolve rule does not apply to this file. Do
not resolve it by hand and do not take one side: hand-resolution is exactly where a mark gets
raised, which the ratchet exists to forbid. `crapkit ratchet merge` is the resolver, and per
key it takes the side that changed, or the lower value when both did.

A conflict here means the merge driver is not installed in this clone. Install it, then redo
the merge:

    git config merge.crapkit-ratchet.driver "crapkit ratchet merge %O %A %B"

When git printed `crapkit: not found` above the conflict, the driver is installed and git's
PATH has no `crapkit`, which is where a clone that runs crapkit through uvx lands. The file
then holds your side with no conflict markers: do not stage it. Run `git merge --abort`, set
the driver to the uvx form, and merge again:

    git config merge.crapkit-ratchet.driver "uvx crapkit ratchet merge %O %A %B"

An add/add conflict (`git status` shows `AA crapkit-ratchet.tsv`, as when two branches each
ran a first `crapkit ratchet seed`) with the driver installed means a crapkit older than
0.8.1 ran it. That driver read git's empty base as a legacy marks file and refused with
`ratchet key identity versions differ; reconcile the legacy function mapping before merging`,
which names no problem in either file. Run `git merge --abort`, upgrade crapkit, and merge
again: 0.8.1 merges the two files as a union, each mark both sides hold at the lower value.

Owner: [docs: the git merge driver](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-git-merge-driver).
When the driver itself refuses (`marks from different metric versions cannot merge`),
run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline one side with
`crapkit ratchet seed`, and merge again.
