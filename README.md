# crapkit

<!-- mcp-name: io.github.JeanFrancoisGagne/crapkit -->

[![ci](https://github.com/JeanFrancoisGagne/crapkit/actions/workflows/ci.yml/badge.svg)](https://github.com/JeanFrancoisGagne/crapkit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/crapkit)](https://pypi.org/project/crapkit/)
[![Python](https://img.shields.io/pypi/pyversions/crapkit)](https://pypi.org/project/crapkit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/JeanFrancoisGagne/crapkit/blob/main/LICENSE)
[![crapkit MCP server](https://glama.ai/mcp/servers/JeanFrancoisGagne/crapkit/badges/score.svg)](https://glama.ai/mcp/servers/JeanFrancoisGagne/crapkit)

![crapkit init, coverage and worklist --top 5 on a small Python repo, then a shell heredoc adding a function at ccn 7: the per-edit advisory reports it and exits 2, and the commit gate refuses the staged file with exit 6](https://raw.githubusercontent.com/JeanFrancoisGagne/crapkit/main/docs/demo.gif)

crapkit scores every function in your repo on complexity times uncovered risk, ranks the
worst ones by how often the file changes, and blocks commits that add more. It reads
Python, TypeScript, TSX, JavaScript, Swift, Go, Rust, shell, PowerShell, C and C++,
Objective-C, Vue, Java and Zig through [lizard](https://github.com/terryyin/lizard), and
joins per-function branch coverage from the istanbul or coverage.py artifact your own test
command already writes. JSON commands use sorted keys and a versioned schema for
scripts, coding agents and the optional MCP server.

```
CRAP = ccn^2 * (1 - cov)^3 + ccn
```

The name is not ours: C.R.A.P. (Change Risk Anti-Patterns) was coined for crap4j by
Alberto Savoia and Bob Evans in 2007.

`ccn` is the smaller of standard and modified cyclomatic complexity, both read off one
lizard pass. `cov` is branch coverage inside the function's span; with no branches it
falls back to statement coverage, and with no statements to invoked-or-not, so a
half-executed straight-line function never reads as fully covered.

**Above the ceiling, coverage cannot save you. Decompose.** At the default ceiling of 6, a
function at ccn 7 with 100% coverage still scores 7 and still fails the gate. The only
move that clears it is splitting the function.

**Why 6 and not 30.** crap4j's conventional threshold of 30 is a CRAP score: it lets an
untested `ccn 5` through (25 + 5 = 30) and a fully covered `ccn 30` too. crapkit's default
is a complexity ceiling, because coverage can at best collapse CRAP to `ccn`, and a
function you cannot cover past `ccn 6` is one you decompose. Set `target = 30` in
`crapkit.toml` if you want the crap4j number. A repo with existing debt does not need to:
`ratchet seed` marks today's over-ceiling functions at today's score, the gate then judges
only the functions a change touches, and marks may only fall, so adoption never starts with
a wall of red. Next to crap4py, radon, xenon, wily and SonarQube:
[docs/comparison.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/comparison.md).

crapkit scores **git-tracked files only**. Source you have not `git add`ed is invisible to
it; `init` and `verify` name the first three such files they find.

| Start with | When |
|---|---|
| [Install](#install) and [the 60-second start](#the-60-second-start) | You want the first score in an existing Git repository. |
| [Python](#quickstart-python) or [TypeScript](#quickstart-typescript) quickstart | You want a worked example from setup through a passing verify. |
| [Adoption](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adoption.md) | You need to choose scopes, wire tests or introduce a ratchet to existing debt. |
| [Upgrading](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md) | You already have saved runs, ratchet marks or an installed plugin. |
| [Subcommands](#subcommands) and [JSON/MCP reference](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md) | You are scripting commands or connecting a coding agent. |

---

## The 60-second start

```
pip install crapkit
cd your-repo
crapkit init        # write crapkit.toml and ignore measurement output
crapkit doctor      # check scopes, test commands and coverage dependencies
crapkit coverage    # runs the lane, joins coverage, stores a scored run
crapkit worklist    # the ranked risk map
crapkit ratchet seed
git add crapkit.toml crapkit-ratchet.tsv .gitignore
git commit -m "adopt crapkit"
crapkit verify      # the first passing verdict
```

Not a Python repo? `uvx crapkit init` runs the same commands and adds nothing to your
manifest: see [A repo that is not Python](#a-repo-that-is-not-python). A Python repo
skips uvx and installs crapkit as above, or with `uv tool install` or `pipx install`; that
section says why. pip stops with `externally-managed-environment`? See
[When pip refuses](#when-pip-refuses).

`coverage` measures a commit, so a fresh `git init` repo needs its first commit before
it. Until then it exits 4 and says so:

```
$ crapkit coverage
crapkit: the git repository at /repo has no commit yet: crapkit measures a commit, so make the first one (git add, then git commit) and run it again
```

`init` detects pytest, Vitest and Jest from the repository's own files. Review the
generated config before running its commands. When detection leaves a commented
lane, fill it in using the [lane recipes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md).
Commit the adoption files, then run `crapkit verify` to establish a passing verdict.
Install the [commit gate](#the-gate) when the config and ratchet are ready.
Each step prints the next one: `init` names `coverage`, `coverage` names `worklist`,
`worklist` names `ratchet seed`, and `ratchet seed` names the commit and `verify`.

`coverage` scores, `worklist` ranks:

```
$ crapkit coverage
run 1 @ fae4db93108: 2 functions scored: 2 measured, 1 over ceiling 6, CRAP load 41.0, grade F
-> next: crapkit worklist

$ crapkit worklist
worklist @ fae4db93108 (run 1, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk     14.0  ccn  14  crap    38.5  cov  50%    1c/1a  calc/grade.py:7  classify( score , attempts , late , bonus )
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
```

`risk 14.0` is ccn times a churn weight of one: a one-commit repo has no spread of commits
to weight, so each commit counts once and the ranking is complexity order until the
history grows ([Risk](#risk-what-ranks-the-worklist)). `crap 38.5` and `cov 50%` are the
score and the coverage behind it.

`ratchet seed` signs today's debt at today's score. From then on marks only ever fall, so
the repo can get better and never worse while you burn it down.

**One thing stops most first runs: the coverage plugin.** `init` writes a lane that shells
out to your own test runner, and the runner needs its coverage package installed:
`pytest-cov` for pytest, `@vitest/coverage-v8` (pinned to your vitest major) for vitest.
Without it the lane produces no artifact and `coverage` exits 5 quoting the runner's own
error. A pytest lane also needs coverage.py 7.13.1 or newer, which writes the function
start lines crapkit reads; an older one fails the lane at exit 5. For pytest, `init` probes the python its lane will run and prints the install
command when `pytest_cov` is missing; `pip install "crapkit[py]"` pulls the plugin
alongside crapkit when the two share a venv. On a Windows PATH holding only the `py`
launcher it writes `py`, not a `python3` the lane could never run, and when cmd.exe cannot
start the interpreter at all (exit 9009, the Store alias) it names that instead of guessing
at pytest-cov. A repo that pins no lockfile and carries its own `.venv` gets that venv's
interpreter in the lane, when that interpreter can import pytest, rather than whichever
python the shell answers with. The two quickstarts below walk a real repo end to end.

**On Windows a lane command is read by cmd.exe**, the shell that will run it, not by sh.
Double quotes are the portable quoting. A single-quoted value is refused at config load
with exit 3, because cmd.exe would hand pytest five words and the lane would write no
artifact:

```
# the lane in crapkit.toml
command = "python -m pytest -m 'not live and not perf' --cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json"

$ crapkit doctor
crapkit: lane 'py': positional argument 'live' narrows a full-suite coverage run; drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), or set full_suite = false deliberately (cmd.exe does not treat ' as a quote: write the value in double quotes); a suite whose testpaths cannot be collected in one process needs one lane per testpath, each with full_suite = false and its own artifact
```

Write it `-m "not live and not perf"`. Carets, `&&` and `|` segments, redirections and
empty quoted arguments all read the way the shell reads them, so a chained lane
(`cd tests && python -m pytest --cov ...`) is checked one segment at a time. `doctor` reads
a lane the same way, and FAILs one whose runner will not start.

## Install

```
pip install crapkit
```

That is the release on [PyPI](https://pypi.org/project/crapkit/). For the unreleased tip
of `main`, or from a local clone (run at the clone root):

```
pip install git+https://github.com/JeanFrancoisGagne/crapkit.git
pip install .
```

Commits on `main` between two releases carry the same version string, and pip keeps an
installed crapkit whose version matches, so running the git line again after a new
commit leaves the old code in place. This line replaces it with the tip:

```
pip install --force-reinstall --no-deps git+https://github.com/JeanFrancoisGagne/crapkit.git
```

`pip install .` in a clone rebuilds on every run and needs no flag.

### When pip refuses

Debian 12, Ubuntu 23.04 and later, Homebrew and uv mark the Python they install as
theirs (PEP 668), and pip refuses to write into it with
`error: externally-managed-environment`:

```
$ pip install crapkit
error: externally-managed-environment
```

Give crapkit an environment of its own. Either line installs it into one and puts a
`crapkit` command in `~/.local/bin` (`%USERPROFILE%\.local\bin` on Windows), the
command the agent plugins start:

```
pipx install crapkit
uv tool install crapkit
```

When that directory is not on PATH yet, the installer says so and names the fix:
`pipx ensurepath`, or `uv tool update-shell`. Open a new shell after either. The
`python` on PATH does not hold this install, and the [commit hook](#the-gate) runs the
`crapkit` command before it tries `python`, so it reaches either install as printed.

A Python repo can carry crapkit in its own venv instead, beside the test dependencies
its lane runs:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install "crapkit[py]"
```

The same from cmd.exe on Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install "crapkit[py]"
```

`--break-system-packages` makes pip write into the system Python anyway, where the next
OS package update can break crapkit or the OS's own tools. None of the routes above
needs it.

### A repo that is not Python

crapkit is a command-line tool, never a dependency of the code it scores. A TypeScript,
Go or Rust repo adds nothing to its own manifest. With [uv](https://docs.astral.sh/uv/)
on the machine, `uvx` fetches crapkit into a cache of its own and runs it:

```
$ uvx crapkit init
wrote crapkit.toml with 1 scope(s): src
detected 1 lane(s) from this repo's own files: js - next: run `uvx crapkit coverage`
added to .gitignore: .crapkit/

$ uvx crapkit coverage
$ uvx crapkit worklist
```

uvx puts no `crapkit` on PATH, so every next step crapkit prints under uvx starts with
`uvx crapkit`. The lane still runs your own test runner, so Vitest or Jest and its coverage
package come from the repo's `node_modules` as they do today. `uv tool install crapkit` or
`pipx install crapkit` puts a `crapkit` command on PATH once, which is what the
[commit gate](#the-gate) and the Claude Code plugin call. The ratchet's merge driver takes
either form; [the git merge driver](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#the-git-merge-driver)
gives the uvx line. uv brings its own Python when the machine has none.

A Python repo installs crapkit instead: in the environment its suite runs in
(`pip install "crapkit[py]"`), or with `uv tool install crapkit` or `pipx install crapkit`.
uvx puts its own environment first on the PATH the lane inherits, so the lane's `python`
starts uv's cached interpreter, which holds neither the suite's packages nor pytest-cov.
`init` then says that interpreter cannot import pytest_cov, and installing pytest-cov into
uv's cache fixes nothing the suite needs.

Requires Python 3.11 or newer and Git on PATH. On an older Python pip stops with
`No matching distribution found for crapkit`, and `uvx crapkit` runs crapkit on a Python
3.11 or newer that uv finds or downloads. The CLI has one runtime dependency,
`lizard>=1.24.0,<1.25`; a package mirror needs both distributions. Install into the environment
you intend to use, then check `crapkit --version`. The `pip install -e ".[dev,accuracy-push]"` under
[Development](#development) is a different thing: it adds the test extras, for people
changing crapkit.

`pip install --user crapkit` puts the `crapkit` command in your user scripts directory:
`~/.local/bin` on Linux, `~/Library/Python/3.12/bin` for a python.org Python 3.12 on
macOS, `%APPDATA%\Python\Python312\Scripts` for Python 3.12 on Windows. When that
directory is not on PATH, pip ends its install with `WARNING: The script crapkit is
installed in '...' which is not on PATH`. Add the directory it names to PATH and open a
new shell. Until then the shell answers `crapkit` with `command not found` (`is not
recognized` on Windows), and the Claude Code plugin lists its server as
`Failed to connect`.

Python projects can install `pip install "crapkit[py]"` in their test environment to
include pytest-cov and subprocess-capable coverage.py. A separate tool installation
still needs the coverage plugin in the environment that runs the suite.

Analysis and scoring run locally and send no telemetry. Configured lane, mutation
and alert commands run with your permissions and can contact services or change
files. Review those commands before running crapkit in a repository you do not trust
([SECURITY.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/SECURITY.md)).

```
$ crapkit --version
crapkit 0.8.1
```

`python -m crapkit` works identically to the console script and is what to use from a
source checkout. A next step crapkit prints names `crapkit` when PATH finds this
installation's console script, and otherwise the interpreter running it, spelled with
forward slashes (`C:/venv/Scripts/python.exe -m crapkit coverage`) so Git Bash, cmd.exe
and PowerShell all run it as printed. A segment that holds a space is quoted on its own
(`C:/"Program Files"/Python312/python.exe -m crapkit coverage`), so the line never opens
with a quote and runs in PowerShell too. One case loses cmd.exe: a venv's `python.exe` in
a spaced directory with no 8.3 short name, where you put the whole path in double quotes
by hand ([docs/adr/0003](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adr/0003-a-pasted-command-never-opens-with-a-quote.md)).
Every subcommand accepts `--repo PATH` (default: the nearest `crapkit.toml`
at or above the current directory, so a monorepo workspace finds the root's), and with it
you never have to `cd` into the repo you are scoring; [Subcommands](#subcommands) shows
where the flag goes. A leading `~` in PATH is your home directory, also in cmd.exe and in
an MCP client's `args`, where no shell expands it.

## Upgrading

Keep the CLI and plugin versions aligned, measure fresh coverage after upgrading,
and review any ratchet identity refusal before reseeding. The current reader is
analysis version 13. It reads a coverage.py function's span from the `start_line`
coverage.py 7.13.1 and newer write, and refuses a report without one. It scores a source
saved as UTF-16 and keys an identifier that holds one of the five bytes cp1252 leaves
undefined by its name. It moves numbers in every language but Python and some of
Python's `cognitive` and `nesting`, puts JavaScript and TypeScript coverage on the function
that owns it, and computes CRAP alike on every platform. So every marks file re-seeds once. Version 11, in 0.8.0, renamed once each Python def with a PEP 695
type parameter list and each def nested three or more deep, and listed a def whose body
sits on its colon line; `crapkit ratchet prune` drops the marks left under the old names.
Older JavaScript and TypeScript callback marks can require a reviewed mapping.
Follow the [upgrade guide](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md)
for the upgrade line each installer takes (pip, pip --user, pipx, uv tool, uvx and a git
install), saved state, portable records and Windows launcher locks.
[CHANGELOG.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/CHANGELOG.md) says
what each release changed.

### Upgrading from 0.8.0 to 0.8.1

1. Install coverage.py 7.13.1 or newer where each Python lane runs:
   `pip install -U "coverage>=7.13.1"`, or `pip install -U "crapkit[py]"` when crapkit
   shares the suite's venv.
2. Upgrade crapkit in every clone, then re-seed each repo once: `crapkit coverage`,
   `crapkit ratchet prune`, `crapkit ratchet seed`. When a failed verify pins the baseline,
   pass `--baseline N` to prune and to seed.
3. Commit the re-seed together with every pin that runs crapkit: the CI install pin, the
   Action's `uses:` pin and the pre-commit `rev`. 0.8.1's `verify` refuses marks stamped
   with analysis version 11, and 0.8.0's refuses marks stamped 13.
4. Keep `{python}` and `{python:DIR}` out of a committed `crapkit.toml` until every clone
   runs 0.8.1: 0.8.0 hands the token to the shell as written, and the lane fails.

Until the re-seed, `crapkit verify` refuses the marks 0.8.0 stamped, at exit 3:

    crapkit: ratchet marks were recorded under [crapkit-analysis=11 lizard=1.24.0] but this run measures [crapkit-analysis=13 lizard=1.24.0] - CRAP scores are not comparable across metric versions; run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline with `crapkit ratchet seed`

The upgrade guide lists [every step in
order](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#080-to-081-in-order),
and [analysis version
13](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#analysis-version-13)
says what moves. A metric-stamp refusal from an older upgrade is quoted under [analysis
version 8](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#analysis-version-8).

### The exe lock on Windows

A running `crapkit.exe mcp` holds its launcher open, and each installer meets that lock
its own way. From pip 23.3 on, pip succeeds: it moves the busy `crapkit.exe` aside and
installs the new release, and the running server keeps serving the old code until you
restart its client. pipx does the same when it installs through pip. An older pip, such
as the 22.3.1 a Python 3.11.2 venv ships, fails with `ERROR: Could not install packages
due to an OSError: [WinError 5] Access is denied` and puts the old release back: run
`python -m pip install --upgrade pip` first, or stop the server as for uv. `uv tool
upgrade crapkit` fails with `os error 32`. `uv tool install crapkit@latest`, and `pipx
upgrade crapkit` when pipx installs through uv, fail with `Access is denied. (os error
5)`; after that `uv tool install` the `crapkit` command fails with `ModuleNotFoundError:
No module named 'crapkit'` until the install runs again. After any of those failures,
stop the server or its agent session, rerun the same command, then restart the client. The
[Windows upgrade procedure](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#windows-launcher-locks)
lists what each installer printed.

### Removing crapkit

Take the commit hook and the merge driver out before the package: both call crapkit.
After `pip uninstall crapkit` alone, the sh hook Route 1 and Route 2 write keeps judging
every commit through `uvx crapkit` on a machine with uv, with whichever crapkit release uv
has cached or can download, and on a machine without uv stops every commit on
`No module named crapkit`, or on `exec: python: not found` where there is no `python` at
all, as on a Debian, Ubuntu or macOS that has only `python3`. The PowerShell hook Route 1 and the handbook write names the launcher
that `pip uninstall crapkit` deletes, so it stops every commit on
`No such file or directory`, with uv or without it.
Every merge that touches `crapkit-ratchet.tsv` conflicts after the driver's
`crapkit: not found`. The upgrade guide's
[Removing crapkit](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#removing-crapkit)
lists each piece, in a repo and on the machine.

## The Claude Code plugin

```
claude plugin marketplace add JeanFrancoisGagne/crapkit --sparse .claude-plugin plugin
claude plugin install crapkit@crapkit
```

Two commands, installed once per user, and every repo on the machine gets it. The plugin
ships three skills, the read-side MCP server, and one advisory PostToolUse hook that names
any function an edit pushed over its ceiling. Claude reaches two of the skills by itself,
`crapkit` and `crapkit-recover`; the third you type, as `/crapkit:crapkit-onboard`, because
wiring a repo up happens once and its description has no business in every turn's window.
It adds no files to your repo, and it needs the crapkit CLI on PATH.

The hook is one shell command, `crapkit claude-hook --protocol 1`, so it runs as written in
any Claude Code version with plugin support, and in the other agents that load Claude Code
plugins: Cursor (which imports them), GitHub Copilot CLI and VS Code. Claude Code gets the
advisory on stderr with exit 2 and wakes the model with it. Those three read exit 2 as a
deny, a message for the user alone, or a blocking error, so there the hook exits 0 and
hands the model the same lines as added context
([other harnesses](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md#other-harnesses)).

`--sparse .claude-plugin plugin` checks out the two small directories the plugin ships
from. Without it Claude Code clones the whole repository under a 120-second clone
timeout, which one measured add ran out of. A marketplace added without `--sparse`
keeps that full clone; `claude plugin marketplace remove crapkit` drops it and uninstalls
the plugin, and the two lines above put both back.

A repo with no `crapkit.toml` costs a silent no-op per edit: 68 ms on Windows through the
`crapkit.exe` launcher, where a bare `python -c pass` took 32 ms on the same machine. Every
edit starts it, whatever the file type; an edit to a type crapkit does not measure stops at
a suffix check before any config is read. On Windows, Claude Code starts the hook through
Git Bash, whose own startup comes on top, and runs it async, so no edit waits for it. After
upgrading the CLI, refresh the marketplace before updating the installed plugin:

```
claude plugin marketplace update crapkit
claude plugin update crapkit@crapkit --scope user
crapkit doctor --plugin-root
```

The installed plugin moves only at a release. `claude plugin update` compares version
strings, and main carries the last release's version until the next one, so between
releases it reports the plugin up to date. Restart existing Claude Code sessions to apply
the plugin update. The check above compares installed files with the CLI on PATH; it does
not reload a running session. When they disagree it prints the commands for the side that
is behind, and when `claude plugin update` answered "already at the latest version" over
files main has moved past, it prints the uninstall and install lines that replace them. The update above is for a user-scope
install. For one made with `--scope project` or `--scope local`, doctor names that scope and
the project directory to run it in; for a plugin installed from a marketplace you added as a
local directory, which Claude Code loads in place, it names `git -C <that directory> pull`.

A headless `claude -p` has no one to grant a tool call, so it refuses every crapkit call
and the model reads `Claude requested permissions to use ..., but you haven't granted it
yet.` Pass `--allowedTools mcp__plugin_crapkit_crapkit` for the plugin's server, or
`--allowedTools mcp__crapkit` for one you added yourself.

The hook registers on `Edit|Write`, which is every write that names a file. An agent that
writes its source through a shell heredoc names none, so a `Bash` event is judged off the
working tree instead. That half is yours to register, because it costs two
git spawns per shell call. Add a second PostToolUse entry to your own settings, same
command, matcher `Bash`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "crapkit claude-hook --protocol 1", "timeout": 20 }
        ]
      }
    ]
  }
}
```

The cost is one `git rev-parse --show-toplevel` and one `git status --porcelain -z -uall`
per shell call in any git repo, whether or not crapkit measures it: about 50 ms together
on crapkit's own checkout on Windows, and more on a bigger tree. What comes back is the dirty or
untracked `*.py` files written in the last 12 seconds, 25 at most, each judged the way an
edit is. Python only, so a TypeScript or Go repo pays the two spawns and hears nothing.

The hook remembers, per Claude Code session, the bytes each judgement read: one small
record per file under `.git/crapkit/claude-hook/<session_id>/`, and a session idle for 7
days is pruned when another one starts. A recent file whose bytes match its record is
skipped, so a `touch`, a rewrite of the same bytes, or a test run right after an `Edit`
does not repeat an advisory the session already heard. Content that arrives with an old
mtime is never judged by the `Bash` half: a file moved or copied in (`mv`, `cp -p`, an
unpacked archive), or one a long command wrote well before it returned. The commit gate
still judges those.

### Codex

Codex 0.131.0 or newer installs the same marketplace's plugin through its own manager;
0.130.0 has no `codex plugin add`:

```
codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref v0.8.1 --sparse .claude-plugin --sparse plugin
codex plugin add crapkit@crapkit
```

Use the three skills and MCP server in Codex. Codex loads no crapkit hook: the plugin's
Codex manifest leaves hooks out, because Codex reports an edit as `apply_patch` patch text,
which the advisory does not read. Codex offers `crapkit` and `crapkit-recover` to the model
by itself; `crapkit-onboard` stays out of the model's list until you type
`$crapkit:crapkit-onboard`, as Claude Code waits for `/crapkit:crapkit-onboard`. A plugin
from 0.8.0 or earlier ships no Codex manifest: Codex 0.156.1 lists its hooks as untrusted
PostToolUse hooks that run a bare `crapkit`, and they should stay untrusted.

`--ref` pins the marketplace to this release's tag, and crapkit's release step rewrites
it to the tag it cuts. Codex 0.156.1 checks every Git marketplace each time it starts
and reinstalls the marketplace's plugins when it moved. Added without a ref, the marketplace
follows main, and a push to main moves the plugin past the CLI you installed from PyPI
with no command from you. `--sparse` fetches the two small directories the plugin ships
from rather than the whole repository.

A marketplace added at a tag stays there: `codex plugin marketplace upgrade` keeps it
at that tag. After upgrading the CLI, remove the marketplace, add it at the new tag,
and install the plugin again. The listing's `--json` needs Codex 0.137.0; drop it on an
older Codex.

```
codex plugin marketplace remove crapkit
codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git --ref v0.8.1 --sparse .claude-plugin --sparse plugin
codex plugin add crapkit@crapkit
codex plugin list --marketplace crapkit --json
```

The installed plugin moves only at a release. Removing the marketplace keeps it, and it
stays at the old version until `codex plugin add` installs the new one. The same four
lines move a marketplace added without `--ref` onto the tag.

Check the installed Codex plugin with `crapkit doctor --plugin-root`: with no PATH it
reads Codex's plugin cache when Claude Code has no install, and PATH names one install
under `~/.codex/plugins/cache/crapkit/crapkit/`. A gap there names these refresh lines,
never a `claude` command.
See [plugin upgrades](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#plugin-and-mcp-clients)
for choosing that path and starting a fresh MCP session. A runtime with a skills
directory but no compatible marketplace can copy `plugin/skills/*` into it instead:
`~/.claude/skills` for Claude Code, `$CODEX_HOME/skills` (`~/.codex/skills` by default)
for Codex, `~/.gemini/skills` for Gemini CLI. Gemini CLI lists `crapkit-onboard` to its
model with the other two; `gemini skills disable crapkit-onboard --scope user` takes it out
once the repo is adopted.

### Other agents

Every other agent starts `crapkit mcp` from its own config file, and each one reads its own
key and fields. The `mcpServers` block Cursor takes starts nothing in OpenCode, Amp or VS
Code's `.vscode/mcp.json`, and none of them says so; in Gemini CLI it connects, but a
headless `gemini -p` offers the model none of the tools without `"trust": true`.
[Wiring crapkit into your agent](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/harnesses.md)
gives the block for each of 27 agents, Gemini CLI, Goose, Zed and Continue among them, with
the file it goes in and where the agent starts the server.

## Languages

14 languages, two coverage parsers. Coverage joins where a parser exists; everything else
scores on complexity alone.

| Language | Files | Coverage |
|---|---|---|
| Python | `.py` | coverage.py |
| TypeScript | `.ts` | istanbul |
| TSX | `.tsx` | istanbul |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | istanbul |
| Vue | `.vue` | istanbul, when your vitest run reports on `.vue` files |
| Swift | `.swift` | none: cc-only |
| Go | `.go` | none: cc-only |
| Rust | `.rs` | none: cc-only |
| shell | `.sh` `.bash` | none: cc-only |
| PowerShell | `.ps1` `.psm1` | none: cc-only |
| C and C++ | `.c` `.cc` `.cpp` `.cxx` `.h` `.hpp` | none: cc-only |
| Objective-C | `.m` `.mm` | none: cc-only |
| Java | `.java` | none: cc-only |
| Zig | `.zig` | none: cc-only |

A cc-only scope declares `coverage_optional = true`, scores `crap = ccn`, and needs no
lane. Nothing about it is provisional: the ceiling still binds and the gate still refuses
a function over it. Add a coverage lane the day a parser exists and the same scope starts
joining coverage.

`crapkit init` writes that key itself, on every scope whose languages all lack a parser,
and leaves it off any scope a lane could still measure. So the 60-second start above runs
unchanged on a Go, Rust or shell repo: `crapkit coverage` scores it with no lane at all,
and that run is the baseline `worklist`, `next-item`, `ratchet seed` and `verify` read.

Shell, PowerShell and Rust run on crapkit's own readers. lizard ships none for shell or
PowerShell, so crapkit counts their functions itself, and reads the command inside a
quoted `"$(...)"` as code: its `&&`, `||`, `-and` and `-or` count as they do written
bare. lizard's Rust reader scores a 7-arm `match` as ccn 2 (filed as lizard #494), so crapkit counts each non-wildcard arm like a C
`case`. It also reads a Rust signature, a closure's empty `||`, a let-else and a `for`
that is no loop the way Rust means them (see
[per-language
gotchas](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#per-language-gotchas)),
and retires each correction the day upstream fixes it. It lists `#[inline] fn f() {`
written on one line, which lizard took for a C preprocessor line. The cognitive column charges a
`match` once, the way Sonar charges a `switch`. The Rust, shell and PowerShell readers count each `match`, `case` or `switch` arm in the modified column too, so both columns agree and the arms are gated: a seven-arm `match` gates at `ccn` 8, where lizard's modified count, and so the gated `ccn`, of a C `switch` with seven cases is 2.

Go and Zig read through crapkit's subclasses of lizard's readers, which end a signature
where the language does. A function type such as `var cb func(int) error` opens no
function, a result type's braces are not the body, and a parameter of function type counts
once.

Python and Swift read through crapkit's subclasses of lizard's readers too. lizard's Python
reader ends a def at the first `)` of a signature that runs past it, so crapkit reads the
signature to the body's colon. lizard's Swift reader takes `super.init(...)`, `r.get()` and
`Socket(protocol: p)` for declarations and `#fileID` for the start of a preprocessor line,
which hid the functions after them, and lists no function named by a raw identifier with a
space in it (``func `keeps onboarding if offline`()``), so crapkit reads each one as the
name or literal it is. It also reads the expression in a string's `\( )` as code, which
lizard read as part of the string.

C, C++, Objective-C and Java run on lizard's readers with crapkit's fixes on top. lizard
hid every function after some constructs, named rows after an attribute or a macro, and
left unnamed and array parameters out of `params`; the
[per-language notes](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#per-language-gotchas)
list what crapkit reads differently.

Expression arrows in arrays and argument lists are measured separately. In TypeScript,
wrap an arrow body in parentheses when it contains `<` before a comma, such as
`x => (pair<T,U>(x))` or `x => (x < 0)`. Without that delimiter, analysis refuses
the file because this reader cannot distinguish type arguments from an expression
separator. A run scores the refused file as zero functions and names it, and every gate
refuses it once a change touches it ([The gate](#the-gate)). Generic arrow parameter
declarations remain supported.

Functions on the same line have separate occurrence identifiers. Existing ratchet
marks with ambiguous old identities require a reviewed mapping; see
[same-line function identity](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#same-line-function-identity).

## The gate

Use the advisory while editing, the gate when committing, and `verify` for the
full verdict. The preview and hooks differ in what their available evidence can prove:

| Surface | Fires | Power |
|---|---|---|
| `crapkit claude-hook` | after an agent's edit lands | **advisory.** Names the breach to the agent: on stderr in Claude Code, as added context in Cursor, Copilot CLI and VS Code. Blocks nothing, because PostToolUse runs after the write |
| `crapkit rescore FILE --gate` | when you ask, after the first coverage run | **preview.** A stricter preview of the commit gate, sub-second, before you stage: a ratchet mark pardons a function only while its CRAP is at or under the mark. With no run behind it, exit 1 and `no snapshot` |
| `crapkit hook-precommit` | `git commit`, or by hand | **blocks.** The hook exits 6; git reports 1. Inside a commit it judges the staged blobs only, so it costs the size of the commit and needs no coverage. Run outside a commit with nothing staged, as `pre-commit run --all-files` does, it judges every tracked file and fails on committed debt no ratchet mark covers; `crapkit coverage`, then `crapkit ratchet seed`, records that debt |
| `crapkit verify` | before you push, and in CI | **the verdict.** Gate, ratchet, new test failures, diff coverage, against the trusted baseline |

Both hooks pardon a function that the marks file in the working tree carries a mark for,
whether or not that mark is staged or committed, so touching signed debt never refuses a
commit. In CI the file on disk is the committed one, so `verify` there fails a function
whose mark never reached a commit. `verify` is also what fails a mark that rises. Since 0.4.5
its gate pardons a touched function whose fresh CRAP sits **at or under** its mark, the
rule `rescore --gate` already applied; push it past the mark and the gate fires again. The
pre-commit hook still pardons on the mark's existence alone, on purpose: a staged blob has
no coverage, so there is no fresh CRAP to compare against. It reports how many it pardoned
on stderr (`staged function(s) carry a ratchet mark and were not gated`), and says the same
about a staged file no `[[scope]]` claims, so a new top-level directory cannot go ungated
in silence.

Every gate refuses a changed file no reader could read, exit 6, because it judged none of
the functions in it: `crapkit gate: 1 staged file(s) could not be read, so no function in
them was judged:`, then `UNREAD  src/a.ts: ...` with the reader's reason, then what to
do. A TypeScript expression-arrow body with `<` before a comma is the common case; wrap the
body in parentheses or a block. `claude-hook` names the same file after the edit, and an
override grants nothing while one is in the change. `crapkit coverage` scores such a file
as zero functions and names it on stderr, and `crapkit doctor` WARNs about each one the
newest run could not read, so you meet the file before the commit gate refuses it. A file
the change never touched still passes, so an old unanalyzable file blocks nothing until
someone edits it.

**The crapkit root can sit below the Git top.** A config in `packages/api` gates
that package's staged files as project-relative paths such as `app/m.py`. A CI step
starts at the top, where no `crapkit.toml` is, so Route 4 and
[the GitHub Action](#the-github-action) take `working-directory: packages/api`. Any
command other than the hook that runs at the top exits 3, and the refusal names the root
below it:

```
crapkit: no crapkit.toml at /repo - nothing to analyze; crapkit.toml sits below it in packages/api: pass --repo packages/api
```

[Path and root rules](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#file-paths-and-root-discovery)
also cover absolute arguments, literal filenames and Git diff settings. Routes 1 to 3
below need no `--repo` in such a monorepo when you run them at the git top. Git runs the
hook there, and with no `crapkit.toml` at the top the gate runs in each crapkit root below
that owns a staged file and names paths from the top (`packages/api/app/m.py`). A commit
that stages nothing under any `crapkit.toml`, a docs-only commit or any commit in a repo
armed before `crapkit init`, passes with one note on stderr. To pin the gate to one root
instead, end each `hook-precommit` line of the hook below with `--repo packages/api`,
as in `exec python -m crapkit hook-precommit --repo packages/api`, and Route 3 adds
`args: [--repo, packages/api]` under `id: crapkit-gate`. Route 4's `crapkit verify` takes
`--repo packages/api`.

Route 1 and Route 2 write this hook body. It runs the first of three that the hook's
PATH offers: the `crapkit` command a pipx, uv tool or venv install puts there, then
`uvx crapkit`, then `python -m crapkit`:

    command -v crapkit >/dev/null 2>&1 && exec crapkit hook-precommit
    command -v uvx >/dev/null 2>&1 && exec uvx crapkit hook-precommit
    exec python -m crapkit hook-precommit

The uvx line is what gates a machine that runs crapkit only through `uvx`. It runs the
release uv fetched first, or the newest one when uv has none, which need not be the
release the rest of your team runs. It also keeps the gate running after
`pip uninstall crapkit` on any machine with uv, so take the hook out before the package
([Removing crapkit](#removing-crapkit)).

Route 1's PowerShell form writes the launcher's own path instead, as that route explains.
Route 3's framework writes a hook of its own that runs `crapkit hook-precommit` from the
environment it installs crapkit into, and Route 4 gates in CI with no hook.

Git hands the hook the PATH of whatever ran `git commit`. A terminal with your venv
activated passes the venv on; an IDE or GUI client you did not start from that terminal
does not. When that PATH reaches no `crapkit` command, no `uvx` and no `python` that
imports crapkit, git refuses every commit. With no `python` at all, as on a Debian,
Ubuntu or macOS that has only `python3`, the hook exits 127 with
`exec: python: not found`. With a `python` that does not import crapkit, such as a system
python, it exits 1 with `No module named crapkit`. Spell the path out for that client:
`exec /path/to/venv/bin/crapkit hook-precommit`, or `Scripts/crapkit.exe` on Windows.

**Run `git config core.hooksPath` before you pick a route.** When it prints a directory,
set globally or by husky, lefthook or another hook manager, git runs hooks from there
and never reads `.git/hooks`: Route 1 arms nothing, and husky sets the path back over
Route 2's on the next `npm install`. Add the gate to the pre-commit file that directory
already runs. Under husky, that is `crapkit hook-precommit` as a line of its own in
`.husky/pre-commit`, with no `exec`: husky runs the file under `sh -e`, so the gate's
exit 6 stops the commit, and the lines after it still run when it passes.

### Route 1: `.git/hooks/pre-commit` (local, not committed)

```sh
hook="$(git rev-parse --git-common-dir)/hooks/pre-commit"
cat > "$hook" <<'EOF'
#!/bin/sh
command -v crapkit >/dev/null 2>&1 && exec crapkit hook-precommit
command -v uvx >/dev/null 2>&1 && exec uvx crapkit hook-precommit
exec python -m crapkit hook-precommit
EOF
chmod +x "$hook"
```

`git rev-parse --git-common-dir` finds the repository's `.git` directory from any
subdirectory and from a linked worktree, where `.git` is a file and `cat >
.git/hooks/pre-commit` fails with `Directory nonexistent`. Every worktree runs the hook
kept there. It ignores `core.hooksPath` on purpose: `--git-path hooks` follows a global
hooks path, and would write this repo's gate into every repo on the machine.

The same hook from PowerShell. Windows PowerShell 5.1 puts a byte-order mark in front of
the shebang, UTF-16 from `>` and `Out-File` and UTF-8 from `Out-File -Encoding utf8`,
and git then refuses every commit with `error: cannot spawn .git/hooks/pre-commit: No
such file or directory` (measured on git 2.43 for Windows) without ever running the
gate. PowerShell 7 writes no mark, and `Set-Content -Encoding ascii` writes none in
either. Git runs the hook with its own `sh`, so the launcher's path is spelled with
forward slashes and quoted, and no `chmod` is needed on Windows:

```powershell
$crapkit = (Get-Command crapkit -ErrorAction Stop).Source -replace '\\', '/'
$hook = "$(git rev-parse --git-common-dir)/hooks/pre-commit"
Set-Content -Path $hook -Encoding ascii -NoNewline -Value "#!/bin/sh`nexec '$crapkit' hook-precommit`n"
```

This hook names the `crapkit` your shell finds today, so a GUI client that never saw your
venv runs the same one. It has no uvx line: while that path is gone, as after you move the
install or `pip uninstall crapkit`, every commit stops on
`.git/hooks/pre-commit: line 2: .../crapkit.exe: No such file or directory`. Paste the
block again after you move or rebuild that install.

`Get-Command` stops the block when no `crapkit` is on PATH (a uvx-only machine): install
one with `uv tool install crapkit` or `pipx install crapkit`, or write the `sh` form above
from Git Bash.

`crapkit doctor` warns when the hook file git would spawn starts with a byte-order mark,
and when a `core.hooksPath` set elsewhere (a global one, or husky's `.husky/_`) sends git
away from `.git/hooks`, where git then runs no hook at all.

PowerShell 5.1's `>` writes UTF-16 behind the same mark wherever it saves a file, and crapkit
reads each such file as far as the program that owns it can:

- A source file saved that way scores its functions as its UTF-8 twin does. git diffs it as
  binary, so `crapkit doctor` names it in a note.
- A `.gitignore` in UTF-16 is one git cannot read. `init` names it on stderr with the fix and
  leaves it as it was.
- A root `package.json` in UTF-16 is one `init` will not guess a lane from: it exits 3 before
  it writes any file, naming the file and the fix.
- The marks file (`crapkit-ratchet.tsv`) saved that way reads as its rows, and a write puts
  it back in UTF-16 behind its mark and in its own line endings. A mark whose name
  holds a cp1252 byte reads with that byte as U+FFFD, and `ratchet seed`, `prune`, `move`,
  `merge`, `verify`'s tighten and both overrides refuse at exit 3, naming the byte, rather
  than save U+FFFD in place of the name:
  [how the marks file is read](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#how-the-file-is-read).

`Set-Content -Encoding utf8` saves any of them as UTF-8.

### Route 2: a committed hooks directory

The whole route, from a repo that has no `githooks/` yet:

```sh
mkdir -p githooks
cat > githooks/pre-commit <<'EOF'
#!/bin/sh
command -v crapkit >/dev/null 2>&1 && exec crapkit hook-precommit
command -v uvx >/dev/null 2>&1 && exec uvx crapkit hook-precommit
exec python -m crapkit hook-precommit
EOF
chmod +x githooks/pre-commit
printf 'githooks/pre-commit text eol=lf\n' >> .gitattributes
git add .gitattributes githooks/pre-commit
git update-index --chmod=+x githooks/pre-commit
git commit -m "add crapkit gate hook"
git config core.hooksPath githooks
```

PowerShell stops that block at its heredoc, before any hook is written, and the next
commit goes through ungated. From PowerShell, the same route:

```powershell
New-Item -ItemType Directory -Force githooks | Out-Null
Set-Content -Path githooks/pre-commit -Encoding ascii -NoNewline -Value "#!/bin/sh`ncommand -v crapkit >/dev/null 2>&1 && exec crapkit hook-precommit`ncommand -v uvx >/dev/null 2>&1 && exec uvx crapkit hook-precommit`nexec python -m crapkit hook-precommit`n"
Add-Content -Path .gitattributes -Encoding ascii -Value 'githooks/pre-commit text eol=lf'
git add .gitattributes githooks/pre-commit
git update-index --chmod=+x githooks/pre-commit
git commit -m "add crapkit gate hook"
git config core.hooksPath githooks
```

The committed hook keeps the PATH lookup rather than one machine's launcher path, since
every clone runs it.

**The `--chmod` goes between the `add` and the `commit`.** It writes the executable bit to
the index, so a commit that already happened does not carry it: run it after and `git
ls-tree HEAD` still says `100644`, which is a hook Unix checkouts silently skip. The
`.gitattributes` line is the harder half of the same failure: under Windows' default
`core.autocrlf` the hook checks out CRLF and `#!/bin/sh\r` dies on Linux and macOS with a
bad-interpreter error. `crapkit doctor` warns when a file under `core.hooksPath` is not
`100755` in the index and prints the `update-index` line for it.

Git will not read a hooks path out of a committed file, so that `git config` line belongs
in your CONTRIBUTING setup steps. Every clone arms the gate with it. `crapkit doctor` names
a clone that skipped it:

```
WARN githooks/pre-commit runs crapkit's gate, but core.hooksPath is unset and git runs .git/hooks/pre-commit, so every commit here skips the gate without a word; run `git config core.hooksPath githooks` in this repo, or call crapkit hook-precommit from .git/hooks/pre-commit
```

After husky's `npm install` takes `core.hooksPath` back to `.husky/_`, the same WARN names
`.husky/pre-commit` as the file to call crapkit from.

### Route 3: the pre-commit framework

crapkit ships a `.pre-commit-hooks.yaml` declaring `id: crapkit-gate`. In your
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/JeanFrancoisGagne/crapkit
    # crapkit's release step rewrites this line to the tag it just cut
    rev: v0.8.1
    hooks:
      - id: crapkit-gate
```

That file arms nothing on its own. The framework writes `.git/hooks/pre-commit` when you
tell it to, and until then `git commit` runs no gate and says nothing:

```sh
pip install pre-commit
pre-commit install
```

`pre-commit install` is the line every clone needs, the way Route 2 needs its
`git config core.hooksPath` line. [prek](https://github.com/j178/prek) reads the same
`.pre-commit-config.yaml`: `prek install` arms the hook in its place, and the gate
refuses and accepts the same commits. `crapkit doctor` WARNs on a config naming `crapkit-gate`
while the hook git runs is not the one the framework writes.

`pre-commit run --all-files`, the form pre-commit.ci and pre-commit/action run, starts no
commit and stages nothing. Outside a commit, with nothing staged, the hook judges every
tracked file instead of a staged diff that is empty: a function over its ceiling fails
unless the marks file in the working tree marks it, which in CI is the committed file. So
a breach committed from a clone that never ran `pre-commit install` fails the CI job.
Inside a commit the hook judges only what is staged, as before. It tells the two apart by
`GIT_INDEX_FILE`, which git sets for the hooks a commit runs. For a CI gate that also
weighs coverage and the marks, use Route 4.

`rev` is a git ref pre-commit resolves against that remote. Pin a release tag, not a
branch: `pre-commit autoupdate` only moves between tags, and a moving `main` would change
your gate under you.

### Route 4: CI

A CI job runs on a fresh clone, which has no `.crapkit/` store, so bare `crapkit verify`
exits 1. Running `coverage` first would make the PR's own tree the baseline, a gate that
can never fail. The portable baseline is the mechanism:

```
# on the default branch, after a passing verify: commit this file
crapkit verify --emit-baseline crapkit-baseline.tsv

# in the PR job, against the committed baseline
crapkit verify --baseline-tsv crapkit-baseline.tsv --github
```

`--github` emits `::error file=...` annotations that land on the PR diff; `--sarif PATH`
writes SARIF 2.1.0 for code-scanning upload. Refresh the committed baseline whenever the
default branch's verify passes.

The file carries each lane's test count and failure list on its first line, so the PR job
forgives a test that already failed on the default branch and warns when a lane runs fewer
tests. A file written by crapkit 0.8.0 or older carries neither: against it every failing
test is a new failure, exit 8, and verify says so and names the line that rewrites it:

```
warning: the baseline file crapkit-baseline.tsv holds no test results, so every test failure counts as new and no suite size is compared; write it again with `crapkit verify --emit-baseline crapkit-baseline.tsv` to carry them
```

Two things the job has to do before those lines run. **Install crapkit**, `pip install
crapkit`, and pin the version the way Route 3 pins `rev`: an unpinned install moves your
gate on whatever day a release lands. **Fetch the whole history.** `actions/checkout`
clones one commit by default, `verify` reads the diff against the baseline's commit out of
git, and a shallow clone does not have that commit:

```
$ crapkit verify --baseline-tsv crapkit-baseline.tsv
crapkit: baseline commit a74260f321f is not an ancestor of HEAD in this shallow clone, which does not hold it; set fetch-depth: 0 on the checkout or run git fetch --unshallow
```

That is exit 4 on a `git clone --depth 1` of a repo whose baseline verifies at full depth.
On a full clone that holds the commit, the same exit names the branch that holds it, or,
when no branch does, blames a rebase or an amend that rewrote history, and asks for a
fresh baseline instead. A clone that does not hold it at all, because `.crapkit/` came
from another clone or from a CI cache keyed on a branch, says so and names the fetch:

```
$ crapkit verify
crapkit: baseline commit a74260f321f is not in this clone, so git cannot say whether it is behind HEAD; fetch it with `git fetch origin a74260f321f4e0b9d2c61a8f3e57d0c1b2a9e8f7`, or run `crapkit coverage` here for a baseline this clone holds
```

`verify --base` and `hook-precommit --base` look up the fork point with `git merge-base`,
and in the shallow clone above they refuse with exit 4 and the same fix:

```
$ crapkit hook-precommit --base c47a37b1df69c434ba42eec5979ddad03d2bf1e4
crapkit: git merge-base c47a37b1df69c434ba42eec5979ddad03d2bf1e4 HEAD failed in /home/runner/work/app/app: fatal: Not a valid commit name c47a37b1df69c434ba42eec5979ddad03d2bf1e4; this shallow clone does not hold every commit: set fetch-depth: 0 on the checkout or run git fetch --unshallow
```

When the clone holds both commits but not the one they fork from, the line reads
`no merge base between REF and HEAD in ROOT`, followed by the same fix.
`ratchet report --enforce` with `debt_max_age_months` or `repayment_min_per_30d` set
refuses in a shallow clone, exit 4 with the same fix: every mark reads 0 days old there and
no repayment shows, so an age limit a full clone fails would pass and a repayment quota a
full clone passes would fail.
Set `fetch-depth: 0` on the checkout step, which is what crapkit's own
[.github/workflows/ci.yml](https://github.com/JeanFrancoisGagne/crapkit/blob/main/.github/workflows/ci.yml) does.

The whole PR job, on GitHub Actions:

```yaml
on: pull_request
jobs:
  crapkit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # verify needs the baseline's commit
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "crapkit==0.8.1"
      - run: pip install -e ".[dev]"   # your own test dependencies
      - run: crapkit verify --baseline-tsv crapkit-baseline.tsv --github
```

The second install is the one people leave out. `verify` reruns your lanes, so the job
needs whatever your test command needs: the coverage plugin, `npm ci`, a database, all of
it. Without them the lane writes no artifact and `verify` exits 5 quoting the runner's own
error, which is a broken job and not a verdict.

### What a refusal looks like

```
$ git commit -m "add route"
crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:
  ccn   7  app/m.py:9  route( a , b , c , d )
decompose before committing (coverage cannot save a function above the target).
```

That commit exited **1**, not 6. Git collapses any failed hook to 1, so 6 is a code you
only ever see by running the hook yourself: `crapkit hook-precommit` exits 6 on a
violation and 0 otherwise. The stderr block above is the same either way.

`CRAPKIT_OVERRIDE_REASON` is not a bypass. Setting it routes the commit through the full
three-record audit: an alert line through `alert_command`, a ratchet entry staged into the
commit, and a row in the override log. All three land or nothing does, and an unset
`alert_command` refuses the override outright. See
[docs/ratchet.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#overrides-and-the-audit-trail).

## The GitHub Action

[action.yml](https://github.com/JeanFrancoisGagne/crapkit/blob/main/action.yml) at this repository's root is a composite action, so a reviewer
sees crapkit's numbers on the pull request without installing anything. Four lines add it
to a workflow, and every input has a default:

```yaml
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: JeanFrancoisGagne/crapkit@v0.8.1
```

The whole job those four lines sit in:

```yaml
on: pull_request
jobs:
  crapkit:
    runs-on: ubuntu-latest
    permissions:
      contents: read                   # actions/checkout clones the repository
      pull-requests: write             # the comment
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0               # the diff, and verify's baseline commit
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"       # the interpreter the install below lands in
      - run: pip install -e ".[dev]"   # whatever your lanes need to run
      - uses: JeanFrancoisGagne/crapkit@v0.8.1
        with:
          gate: "false"
```

That `pip install` step is the one people leave out, and it is the same one Route 4 above
names: the action installs crapkit and nothing else, so your lanes still need whatever
your test command needs. Without it the lane writes no artifact and the comment says so.

`fetch-depth: 0` is the other one. `actions/checkout` clones a single commit; the action
reads the pull request's changed files out of git and `verify` reads the diff against the
baseline's commit. A shallow clone lacks the base commit, so git refuses the diff: the step
logs git's error, and the comment ranks the whole repository and says why under its
heading, quoting git's first line and naming `fetch-depth: 0`. A shallow clone also
flattens every number counted from history: churn sees one commit per file, so the
worklist ranks on ccn alone, and a
ratchet mark's age reads as 0 days. `worklist`, `next-item`, `brief` and `ratchet report`
print one line on stderr and carry `shallow: true` in their JSON (`false` in a full clone),
and the comment repeats the worklist's line above its table:

```
warning: churn counts read only the commits this clone holds; this shallow clone does not hold every commit: set fetch-depth: 0 on the checkout or run git fetch --unshallow
```

`brief` names churn counts and mark ages in that line, and `ratchet report` mark ages and
repayments.

The action installs crapkit from `$GITHUB_ACTION_PATH`, which is its own checkout of the
ref you pinned in `uses:`. So a pin left at last month's tag scores your tree with last
month's crapkit rather than with whatever released since, and pinning a tag is the whole
version policy; the snippets above name the current release. Move the pin in the same commit
that re-seeds the marks under a new analysis version: an Action one release behind the marks
refuses them with exit 3 on every pull request
([a team's upgrade](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md#a-team-upgrades-every-reader-before-the-re-seed-lands)).

### What the comment looks like

One comment per pull request, edited in place on every push. A hidden
`<!-- crapkit-action -->` line opens it, and the next run edits the first comment that
starts with that line, so a fifteen-push branch carries one comment and not fifteen. A
reviewer's reply that quotes the comment does not start with it and is left alone. When
GitHub answers a page of the comment list with an error, the step edits the crapkit
comment it found before the error. When it found none, it lists the comments once more
before it posts fresh; if that listing fails too, it posts a fresh comment, so the pull
request still gets this push's verdict, and the job log says the listing failed twice.
On a `push` event there is no pull request to carry it, and the same text goes to the job
log instead.

Rendered from three saved payloads: a pull request that adds an untested `route()` (ccn 8)
beside a ratchet-marked `legacy_router()`, in a repository whose `diff_uncovered_max` is 3.
The payloads are under `tests/fixtures/action_comment/`, and the unit suite pins this block
to their render:

```markdown
<!-- crapkit-action -->

## crapkit

4 functions in 2 files, 2 over ceiling 6, CRAP load 149.59, grade F.

**verify failed, exit 6: complexity gate.**

- gate: `app/calc.py:34` `route( a , b , c , d )` ccn 8, cov 0%, crap 72.0 -> decompose
- uncovered lines in `app/calc.py`: 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45

Run 3 against baseline 1, 1 changed file (`app/calc.py`): 1 gate violation, 0 ratchet regressions, 0 new test failures, 11 uncovered changed lines.

### Worklist: 1 changed file

| File | Function | ccn | risk | remedy |
|---|---|---:|---:|---|
| `app/calc.py:34` | `route( a , b , c , d )` | 8 | 4.0 | decompose |
| `app/calc.py:19` | `legacy_router( a , b , c , d , e )` | 8 | 4.0 | decompose (accepted debt) |
```

The first line is the run `crapkit coverage` wrote: functions and files, how many sit over
the ceiling (`over ceiling 6`, or `over their ceilings (6; reports 12, util 4)` when scopes
set their own), CRAP load and grade, then ``; scope `src` scored no function: it claims no
file`` for each scope that scored nothing, and a failed lane's first error line appended as
`; lane 'js' failed: ...`. When `coverage --json` died before a summary, the line quotes the
error object it printed instead: `` `crapkit coverage` exited 5: lane 'py' cannot import
pytest-cov; pip install pytest-cov. ``

The verdict opens with the exit code and the rule it stands for (`unreadable name`, `complexity gate`,
`ratchet regressions`, `new test failures`, `diff-coverage ceiling N`), then one bullet per
finding: each file a scope takes whose name is not UTF-8, as `` - unreadable name: `src/caf\xe9.py`: `` and the sentence naming its scope and the `git mv` rename to a UTF-8 name (verify stops on it before any lane runs, so the counts line opens `Nothing was measured against baseline 1` and counts `1 unreadable name`); each gate violation with its function, ccn, coverage, CRAP and remedy; each
changed file no reader could read, as `` - unread: `src/a.ts`, so the gate judged none of
its functions: `` and the reader's reason; each ratchet regression as recorded -> fresh;
each new test failure by id; the first twenty uncovered changed lines, one bullet per
file, with a count of the rest; and each function an `--override` passed, as `- overridden:` with the fields of its gate bullet, under a failing verdict or after the pass line. Each kind prints fifty bullets at most, then `- and 1450
more new test failures; `crapkit verify` lists them all`. The counts line closes it,
naming up to three of the files verify judged and counting the rest:
``1 changed file (`app/calc.py`)``. An unread file fails the gate as a function over the ceiling does, so the counts line counts
it among the gate violations and says how many were files:
`1 gate violation (1 unread file)`. A verify that passed is one line: `**verify passed.**
Run 2 against baseline 1, 7 changed files (`a.py`, `b.py`, `c.py` and 4 more).` A verify
older than 0.8.1 lists no `changed_paths`, and its line gives the count alone. A lane that recorded no test results (it declares no `results_artifact`)
adds ``New test failures went unchecked in lane `py`: it recorded no test results.`` to
that line, and a new failure in a lane whose baseline recorded no failure list gets a
bullet of its own, ``- lane `py`: the baseline recorded no failure list, so its new failures
may predate this change``: the fork point's lane declared no `results_artifact`, so nothing
can tell the pull request's failures from older ones, and verify still counts them.

GitHub refuses a comment over 65,536 characters, and a pull request then gets no comment
at all. A body that still runs over (long names under a large `top`) is cut at the last
line that fits, the marker line still first, and ends with `the comment stopped at
GitHub's 65,536-character limit; the job log above holds the whole text.` The step prints
the whole comment to the job log before it posts.

The rows are the ranked worklist for the files the pull request changed, worst first,
`top` of them, with the rows a finding names listed first. `risk` is ccn times churn
weight, the number `crapkit worklist` ranks on, and `remedy` is the run's own verdict for
that function: `decompose`, `split-lines`, `add-tests` or `ok`. `(accepted debt)` marks a function the
committed ratchet carries a mark for, so an untouched `legacy_router` does not read like
the pull request's own new function. A pull request that touches no ranked function gets
the heading and `No ranked function in these files.` When `crapkit worklist` wrote no
ranking, the table's place says why rather than reading as an empty one: the error it
printed (`` `crapkit worklist` exited 1: no snapshot in ...: run `crapkit coverage` first. ``),
or, when it printed nothing, `` `crapkit worklist` printed no ranking; its error is in the
job log. ``

The two file counts describe the same diff, counted twice. `39 changed files` is
`git diff --name-only --relative base.sha...HEAD` run in `working-directory`, the
branch's own commits under it, and it is what the table is filtered to; the step's log
names up to three of them. The count on the verdict line is what `verify` measured from
the same fork point. With `delta: "false"` the second one is 0, because there is nothing
behind the checkout to measure from.

### The inputs

| Input | Default | What it does |
|---|---|---|
| `gate` | `"false"` | `"true"` exits with `crapkit verify`'s own code, so a finding fails the check. On a pull request with `delta` on it also exits 1 when the base run was not made, because a verdict with no base run judged no changed function. Anything else exits 0 and the comment is the whole output |
| `delta` | `"true"` | scores the pull request's base commit first, so the verdict covers the commits the pull request adds. Costs a second lane run; `"false"` scores the checkout alone, and the verdict then judges no changed function |
| `top` | `"5"` | worklist rows rendered in the table. An empty value renders 5. A value that is not a whole number (`"ten"`, `"5.0"`, `"-1"`) renders 5 and puts a warning naming the input on the run's summary page |
| `python-version` | `"3.12"` | the interpreter `actions/setup-python` installs crapkit into. Match it to the version your own setup-python step named, or the lanes run on an interpreter your dependencies never reached |
| `working-directory` | `"."` | the directory that holds `crapkit.toml`, relative to the checkout. Every step that runs crapkit or lists the changed files runs there, and the base run scores the same directory at the fork point. Set it when `crapkit.toml` sits below the repository top: at the top, `crapkit coverage` finds no `crapkit.toml` and exits 3 |

On a monorepo whose `crapkit.toml` sits in `packages/api`, the crapkit step takes
`working-directory: packages/api` under `with:`, and the job's own `pip install -e
".[dev]"` step takes the same key at step level, because `.[dev]` names that package's
`pyproject.toml` and the top has none. The changed files are then listed from
`packages/api`, the way the worklist names them, and a change elsewhere in the
repository is not part of the table. Point it at the directory that holds
`crapkit.toml` itself, not at one below it. The input is new in 0.8.1: an action pinned
to an older tag warns `Unexpected input(s) 'working-directory'` and scores the top.

`gate: "false"` is the default on purpose. A team adopts the action before it has decided
which findings should stop a merge, and a check that fails on day one gets turned off on
day two.

### What the verdict line covers

On a pull request, the commits the pull request adds. The action scores the fork point
first, then the checkout, then runs `crapkit verify --base <fork>`, which measures the
diff from there and takes the fork point's run as its baseline. So the gate judges the
functions in the diff a reviewer is reading, and a repository that was already over its
ceiling before the branch started does not fail every pull request that touches it.

The fork point is `git merge-base` of `base.sha` and HEAD, not `base.sha` itself.
`base.sha` is the base branch's tip when the event fired, so a base branch that moved
after the branch forked carries commits HEAD never saw, and a run there would be neither
the baseline verify wants nor a diff anyone is reviewing.

The base run happens in a detached worktree under `RUNNER_TEMP`, in the same
`working-directory` inside it, and its store is copied over the checkout's so both runs
sit in one place. The cost is **two lane runs on a pull request**: your suite runs once
at the fork point and once on the checkout. Set `delta: "false"` to skip the base run,
and the verdict falls back to the checkout against its own run, which reports the tree's
own health and judges no changed function. The comment says so in place of
`verify passed`:

```markdown
**verify judged no changed function:** the base run was not made (no base commit). Run 2 against baseline 2, 0 changed files.
```

Three things leave the base run unmade: a shallow clone that does not hold the fork
point, a fork point older than your `crapkit.toml`, and a lane that will not run against
that tree. The step logs `crapkit base scoring exited N` and writes the reason to
`crapkit-base.reason` in the words the comment then quotes, `shallow clone does not hold
the fork point of <sha>; set fetch-depth: 0 on the checkout`, `no usable crapkit.toml at
the fork point <sha>: ...`, or `lane failed at the fork point <sha>: ...` with the first
`lane '<name>' FAILED:` line crapkit printed, so a warning from a lane that passed never
stands in for the lane that failed. A crapkit that died printing nothing reads `crapkit
coverage exited <code> and printed nothing`. The verdict falls back the same way `delta: "false"` does, and the
ratchet still runs, so exit 7 there is a finding. What differs is the job's status. With
`gate: "true"` on a pull request whose base run was attempted and not made, the exit step
exits 1 and prints the reason, because `actions/checkout`'s default depth-1 clone would
otherwise turn every pull request into a green check that judged nothing. A `push` event
and `delta: "false"` never attempt the base run, so they keep verify's own code; a `push`
has no base commit and no pull request to comment on.

One requirement the base run adds: the lane has to measure the tree it runs in. A lane
that reaches an installed copy of your package instead of the checkout will measure the
pull request's code while standing on the base commit, and the two runs then describe the
same tree. `crapkit verify` refuses a run whose artifact names files outside the tree
(exit 5), which catches the loud version of this; a lane pinned to a path outside the
worktree is the quiet one. Point the lane at the tree, or set `delta: "false"`.

`--reuse-artifacts` is what keeps each of those runs to one pass of your suite. `coverage`
ran the lanes moments earlier on that tree, and verify parses those artifacts rather than
running the whole suite a second time for the same numbers.

Which is why `crapkit coverage` has to exit 0 for there to be a verdict. When it exits
anything else (a lane that failed, an artifact it refused), the action does not run
`verify`: on a runner that keeps its workspace between jobs (`clean: false`), a
`verify --reuse-artifacts` over a failed measurement read the artifact the dead lane had
left from an earlier run, passed over it, and made that run the trusted baseline. The
comment then carries coverage's failure in place of the verdict:

```markdown
**no verdict: `crapkit coverage` exited 5 (lane 'py' failed: lane 'py' wrote no artifact on its last attempt; the .crapkit/cov/py.json on disk predates it); verify did not run.**
```

The parenthesis is the first line of the lane failure the summary carries. When every
lane failed, `coverage` prints an error object instead of a summary and the lane errors
are only in the job log, and the line says so: `(every lane failed (1 of 1); the lane
errors are in the job log)`. When `coverage` printed nothing at all, it crashed or was
killed before it could: `(it printed no run summary, so it crashed or was killed before
scoring; its error is in the job log)`. When `coverage` refused files a scope takes whose
names are not UTF-8, the parenthesis names the first and counts the rest, and one bullet
under the line names each file the error object lists in `unread_files`:
``- refused: `src/caf\xe9.py`: its name is not UTF-8, ...``. When the directory coverage
ran in holds no `crapkit.toml`, the parenthesis names that directory and ends ``set the
action's `working-directory` input to the directory that holds crapkit.toml``. With
`gate: "true"` the job exits with coverage's code.

The other gate that judges a delta is the portable baseline in [Route 4](#route-4-ci):
commit `crapkit-baseline.tsv` on the default branch and run `crapkit verify --baseline-tsv
crapkit-baseline.tsv` in a step of your own. It needs no second lane run, and it needs
someone to keep that file current.

The comment is posted with `gh api` and the job's own `GITHUB_TOKEN`, which needs
`pull-requests: write`, and `contents: read` for the checkout once the job names any
permission: naming one sets every other to none. Two things it cannot do: a pull request
from a fork gets a read-only token, so the POST is a 403 there, and a self-hosted runner
without the `gh` CLI on PATH posts nothing. In both cases the step stays green, logs gh's
exit code, and leaves the rendered text in the job log.

## Subcommands

`crapkit clean --dry-run --json` previews abandoned temporary mutation recovery. See
[resource policies](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/resources.md)
for shared analysis workers, process lifetime and bounded logs.

Every subcommand takes `--repo PATH`, and the flag goes **after** the subcommand. Without
it the root is the nearest `crapkit.toml` at or above the current directory
([ADR 0002](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adr/0002-configuration-is-found-upward-nearest-wins.md)): from a
monorepo workspace `crapkit worklist` reads the root configuration that claims the
workspace, says `crapkit: using crapkit.toml at /repo` on stderr, and reads a relative path
argument from where you stand. `claude-hook` is the one exception: it has no `--repo`,
because it takes its root from the file named in the hook payload it reads.

```
$ crapkit worklist --repo /path/to/repo --scope util --top 1
worklist @ a7c5c85ac37 (run 1, floor ccn>=5, churn 12mo) - 1 of 3 active (--top 1), 0 dormant
  risk      5.4  ccn   5  crap    30.0  cov   0%    5c/1a  util/stats.py:1  bucket( value , low , high )
-> next: crapkit next-item
```

Before it, argparse reads the path as the subcommand name and exits 2 without ever
mentioning `--repo`:

```
$ crapkit --repo /path/to/repo worklist --top 1
crapkit: error: argument command: invalid choice: '/path/to/repo' (choose from 'inventory', 'coverage', ...)
```

`--json` prints one sorted-keys JSON object on stdout, always carrying a `schema` field.

| Command | What it does |
|---|---|
| `clean [--dry-run] [--json]` | Recovers abandoned temporary mutation worktrees. Preserves active runs and intentional mutation pools. `--dry-run` reports planned recoveries. It leaves test evidence alone: crapkit's own development runner, `tools/testing/run.py`, prunes `.crapkit/test-runs/` with `--retention-days` and `--retention-count`. `--json` keeps `test_runs` with every array empty. |
| `init` | Sniffs tracked source into per-directory scopes, writes a self-validated starter `crapkit.toml` whose lanes report into `.crapkit/cov/`, and appends `.crapkit/` plus each runner's own droppings to `.gitignore`. Writes a live `[[lane]]` when it can detect the test runner, otherwise a commented template. Never rewrites an existing config: over one, it adds only the `.gitignore` entries an earlier run left out. |
| `doctor [--show-files] [--json] [--tune] [--plugin-root [PATH]]` | Checks the config still describes the repo: unknown keys (with the accepted spellings), zero-file scopes, tracked source no scope claims, scopes no lane covers, lane cwds and commands that no longer resolve, lizard importable, oversized files. It FAILs a lane whose runner does not resolve or that the shell cannot start, a `pytest --cov` lane whose coverage.py is older than 7.13.1, and a marks file a newer crapkit or lizard stamped. It WARNs on lane, hook and marks-file settings a later command refuses or skips, such as a scope a lane measures with no `[crapkit.scoped_tests]` template behind it, and names each `crapkit` launcher on PATH with its version when there are two or more. `--show-files` lists every file each scope matched. `--json` gives each lane a `refusal`, the sentence `--reuse-artifacts` would refuse its artifact with, or `null`. `--tune` prints suggested parallelism knobs and writes nothing. `--plugin-root PATH` reads no repo at all: it checks an installed [plugin](https://github.com/JeanFrancoisGagne/crapkit/tree/main/plugin) against the `crapkit` on PATH on both version and hook `--protocol`, one line per disagreement, each naming the command that closes it. Every check and warning: [docs/commands.md: doctor](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/commands.md#doctor). The JSON form: [docs/agent-json.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md#doctor---json). |
| `inventory [--db PATH] [--export PATH] [--json]` | One lizard pass over every in-scope file into a SQLite snapshot run, cached by content hash. `--db` is the only way to point crapkit at a store outside `.crapkit/`, and only this command accepts it. |
| `coverage [--lane NAME] [--reuse-artifacts] [--reuse-unchanged] [--export PATH] [--sarif PATH] [--github] [--json]` | Runs the lanes, joins branch coverage onto a fresh inventory, writes a scored run. A failed lane is recorded, not fatal: its scopes fall back to `no-lane` and the run is typed `partial`, so it can never serve as a baseline. `--reuse-unchanged` reuses a lane whose proof still holds and says what that proof leaves out; `--reuse-artifacts` reads the saved files and warns, naming up to three files whose bytes moved since the lane measured them. A failed attempt's leftover stays refused while it holds the same bytes: a `touch` does not lift the refusal, new bytes do. See [docs/lanes.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md). |
| `verify [--baseline ID \| --base REF \| --baseline-tsv PATH] [--emit-baseline PATH] [--override REASON] [--reuse-artifacts] [--reuse-unchanged] [--no-tighten] [--sarif PATH] [--github] [--json]` | The full verdict against the trusted baseline: gate on touched functions, ratchet, no new test failures, optional diff-coverage ceiling. The three baseline selectors are mutually exclusive; `--baseline ID` also bypasses the taint rule ([The trusted baseline](#the-trusted-baseline)), and `--baseline-tsv` reads a commit-stamped file so a fresh clone verifies with no store. `--no-tighten` passes the verdict without rewriting the ratchet. Findings a dirty tree produced are tagged `dirty` and counted apart. A line under the verdict names the first three changed files, `--json` lists them all as `changed_paths`, and an untracked source file inside a scope is named on stderr as not judged. A changed file no reader could read fails the gate, exit 6, as an `UNREAD` finding (`unread_files` in `--json`), and `--override` grants nothing while one is present. It reads each istanbul artifact once for coverage, dead lines and its digest, and skips the artifact walk on an empty diff; skipping the whole run on an unchanged tree was measured and rejected, because a key made of HEAD plus the dirty names cannot see a second edit to a file that was already dirty. |
| `worklist [--top N] [--scope NAME] [--batches N] [--json]` | The risk map: every admitted function ranked by `ccn * churn weight`, floored by `worklist_floor`, with hot simple code and anything over its ceiling admitted past that floor. It ranks finished rows and `no-lane` rows too, marked `ok` and `no-lane`, so it never empties; `next-item` carries the stop condition. Every row carries the function's `crap` and `cov` off the ranked run and its `ratchet_mark` when the marks file in the working tree signs for it, and the header counts the active rows the cap hid: `50 of 3980 active (worklist_top 50)`. `--scope NAME` (repeatable) is exact, not a substring; a name no `[[scope]]` declares is a configuration error, exit 3, naming the declared scopes. `--batches N` **adds** a `batches[]` view cutting the active list into at most N file-disjoint batches with co-changing files kept together, off the same cached pairs `coupling` reads; the normal keys stay. Files go out largest summed `risk` first, each to the batch with the least `risk` so far (LPT scheduling). The text ends with the command to run next: `coverage` when the ranked run cannot serve as a baseline, `ratchet seed` while the repo has no marks file, `next-item` after that. |
| `next-item [--top N] [--exclude FRAG] [--scope NAME] [--claim]` | The actionable queue as JSON, with churn, budget estimates and uncovered lines. Same run and same admission floor as `worklist`, a different view of it: `no-lane` rows are skipped and counted in `skipped_no_lane`, and what is left is ranked by `crap` descending rather than by risk, so the item it hands out is often not the worklist's first row. `--exclude FRAG` (repeatable) skips items whose path or function name contains FRAG, and reads a path fragment the way a [file argument](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#file-paths-and-root-discovery) is read, so `./pkg/legacy` is `pkg/legacy`, while a function name keeps its case. `--scope NAME` (repeatable) is exact, not a substring, and a name no `[[scope]]` declares is a configuration error, exit 3, naming the declared scopes. `--claim` holds what it hands out so a second session skips it. Every item carries a `handle`, the name form that survives the edit the item asks for. [docs/agent-json.md: next-item](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md#next-item) gives the tie order, `stale`, `scored_changes` and every field. |
| `claims [list \| release PATH NAME \| release --all] [--json]` | The open claims, and the way to hand one back without waiting for a verify. `release` takes the bare identifier, the whole long name, or the `handle` the claim was taken under, which is the only one that picks out a single `(anonymous)` claim. A claim taken before analysis version 11 on a nested Python def also answers to the name that version gives the def. |
| `brief FILE NAME [--batch N] [--json]` | The start-editing packet for one function: its own `source` text, every function in the file, the scored row and the scope ceiling, the ratchet mark and what the gate will bind on, uncovered lines, duplication twins, file churn, coupling partners, the config's notes, and the literal commands for the rest of the loop. Plus `handle`, `remedy` and the same `est_splits` / `est_uncovered_paths` the queue prints, and a `commands.refresh` that writes a run (`refresh_writes_run`) rather than re-reading the stale one. `NAME` takes the bare identifier, the long name `next-item` printed, the function's start line, `(anonymous)#N` for a function printed `(anonymous)` counting the file's anonymous functions from the top, or `NAME#2` for the second of several functions a file gives one name to. `--batch N` drops the positionals and emits `packets[]` instead: the top N of the queue, built from one read of the store and one duplication pass over the snapshot for the whole batch (batch of 5: 11.8 s to 5.2 s, output byte-identical to five separate calls). |
| `explain FILE NAME [--history] [--tests] [--json]` | A function's score across runs plus its mark. `NAME` resolves exact first: a function whose bare identifier or long name is exactly `NAME` wins, and only when nothing matches exactly does it fall back to a prefix match, so `route` explains `route` rather than every `route_*` beside it. It also takes the function's start line, the form `brief` takes, which is how you open one printed `(anonymous)`. `--history` adds the commits that touched it (`git log -L` over the span the run measured, carried through your uncommitted edits onto HEAD's lines), each carrying its message `body` as committed, and says so when the span holds only uncommitted lines or git cannot answer; `--tests` adds the tests that covered it, which needs coverage.py contexts turned on ([recipe](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#test-attribution-for-explain---tests)), and withholds them with the same note whenever the file's dark lines are withheld. `--json` emits the same content as one `schema` 1 object. |
| `rescore FILE ... [--gate] [--json]` | Fresh complexity for named files over the latest run's stale coverage, joined by name. A function on a line span another one shares, and a Python def whose body starts on the line its signature ends, scores untested, as the coverage run scores it. A function the run holds no row for (added or renamed since) or one in a scope no lane measures prints `-` for its cov and ends `(coverage not measured)`; `--json` marks it `unmeasured: true` and keeps its `cov`, `crap` and `remedy`. Advisory: it writes no run. `--gate` applies the pre-commit hook's policy to the same selection the hook uses (functions the tree changed since HEAD), minus functions whose CRAP sits at or under their ratchet mark, and exits 6. It also exits 6 on a changed file no reader could read, listed under `gate.unread_files` in `--json`. A marked function past its mark is gated; the pre-commit hook pardons on the mark's existence instead, because a staged blob has no coverage to score. |
| `ratchet seed \| prune \| merge \| move \| report [--baseline ID] [--enforce] [--json]` | The mark lifecycle: seed new debt, prune gone code (a mark whose file git renamed follows it, and the prune line names up to three renames it followed; when this clone lacks the commit the renames start from and a marked file left before the oldest commit it holds, prune exits 4 and writes nothing), merge as a git driver, move re-paths marks, report reads burn-down from the file's own git history. `seed` ends by naming the commit and `verify` that follow it. `seed` and `prune` take `--baseline ID` to read a named run instead of verify's pick, refused for the reasons `verify --baseline` refuses one. See [docs/ratchet.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md). |
| `runs [list \| prune [--keep N]] [--json]` | Run history, and retention. `list` marks the run `verify` compares against today `baseline`, and prints `verdict=-` for a run that produces no verdict rather than one that failed. See [The trusted baseline](#the-trusted-baseline). `--keep` (default 5) is a floor on the newest trusted runs, not a cap: the digest pair, every passing verify baseline, every run an override names, and the newest non-hook run are kept too. `prune` VACUUMs afterwards. |
| `overrides [--json]` | The override audit trail: who granted what, when, and why. |
| `trend [--json]` | Totals per trusted run: functions, over-target count, CRAP load, average, per-scope rollup. It reads a per-run rollup table rather than rescanning every scored row, and fills that table for any run missing one, so it writes to the store (best effort: a read-only `.crapkit/` costs the speed, not the command). |
| `digest [--alert]` | The delta between the two newest runs with identical lane sets. Silent when nothing changed. Past the totals line it names up to five functions of each kind: those whose CRAP rose by more than 0.01, largest rise first; new functions over their ceiling, highest CRAP first; and functions that were over their ceiling and dropped by more than 0.01, largest drop first. Moves and scores equal to 4 decimal places list by path. An over-ceiling function the older run holds no row for reads `new over ceiling` when that run scored its scope, and `newly scored over ceiling in scope NAME` when it scored nothing there: a scope added to `crapkit.toml` between the two runs brings old code in, and that is not new debt. `--alert` pipes the body to `alert_command` on stdin. Plain lines, never JSON. |
| `report [--out PATH]` | One self-contained HTML page written to `.crapkit/report.html` (or `--out PATH`, repo-relative, or an absolute path you name), with the path printed on stdout. It renders what `worklist --json` and `trend --json` already answer at their defaults: the ranked worklist capped at `worklist_top`, the per-scope grades off the newest run, the trend series, and a banner naming every stale lane and, from `scored_changes`, how many files the run scored changed since. It measures nothing and opens no network connection. Every row carries the function's CRAP and coverage, and prints the `crapkit explain` call for the rest: dark lines, history, the mark. It reads the same per-run rollups `trend` does, and writes them on the same terms. |
| `duplication [--min-lines N] [--similarity F] [--top N] [--json]` | Near-duplicate functions by normalized line shingles with containment scoring. Defaults: `--min-lines 8`, `--similarity 0.8`, `--top 50`. Ties have a stable order across hash seeds. A positive `--top` bounds retained candidates and output; dense inputs still require pair comparisons. Each function is shingled from its own lines: the lines of a function nested in it, past that function's first line, are the nested function's. A function and its nested closure never pair. Blank lines and comment lines stay out, and so does a Python line starting with three quotes. Code after a block comment's closer is code (`/* tag */ acc += 1;`, `*/ x = a`). `s = "http://x"; /* note` opens a block comment and `x = 1; // see /* here` does not. The reader knows plain strings and character literals only, so it misreads `r"C:\"`, `r#"a"b"#`, `R"(a"b)"`, `/"/`, `re = /a /* b/;` and `echo a#b <# note`: [docs/commands.md: duplication](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/commands.md#duplication) gives the comment reader's rules and what each of these lines does. |
| `coupling [--min-support N] [--min-confidence F] [--top N] [--json]` | File pairs that keep landing in the same commits. Defaults: `--min-support 5` shared commits, `--min-confidence 0.5` max-direction ratio, `--top 50`. Bulk commits never couple pairs, and a young repo returns nothing at the default support. Pairs rank by support times confidence, highest first, and pairs that tie rank by their paths. The ranked pairs are cached in `.crapkit/coupling-cache-v2.json`, keyed on HEAD, the churn window, today's UTC date, the path format, the clone's history depth and a digest of the tracked set, and shared with `brief` and `worklist --batches` (warm: 1.05 s to 0.11 s on a 72k-commit repo). The date is part of that key, so the first run after midnight UTC rebuilds the pairs on an unchanged HEAD, and gets the same pairs: the window ends at HEAD's commit date, not today's. The depth is part of it too, so `git fetch --unshallow` rebuilds them the same day. `--top` reads the cache, because it truncates that same order; `--min-support` or `--min-confidence` off their defaults ask a wider question than the file answers, so they bypass it and recompute. |
| `mutate [--files F ...] [--max-mutants N] [--drop-pool] [--json]` | Diff-scoped mutation testing: flips comparisons, boundary shifts, boolean connectives and boolean literals on changed lines, runs `mutation_command` per mutant, lists survivors. `--files` replaces diff scope with the whole file. Both lists pass through the scored corpus first, the same predicate `coverage` uses: a file outside it is named on stderr and never mutated, `--json` lists it under `outside_corpus`, and when nothing is left stdout says `nothing to mutate` at exit 0. `--max-mutants` (default 100) caps the run, and `mutants` in `--json` is the capped count. A mutant whose suite timed out, or exited 5 and ran no test, counts as killed, and `--json` also counts it under `timed_out` or `no_verdict`, each a count inside `killed`. The summary counts the no-verdict ones on a line of its own, `no verdict: N of the K killed ran no test (exit 5), so no test caught them`. Shell and PowerShell files are refused by name on stderr, and some operators make no mutant in some languages: [docs/commands.md: mutate](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/commands.md#mutate). Every worker uses a kept worktree, including one; see [mutation worktrees](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#mutation-worktrees). `--drop-pool` removes them and exits. |
| `test-scoped FILE ...` | Runs each owning scope's `[crapkit.scoped_tests]` template on the files (quoted, longest-prefix scope wins). A file outside every scope runs only from a `test`, `tests` or `__tests__` directory, under the one scope that declares a template. A template with no `{files}` runs as written, which is how a scope whose tests live outside its own paths runs its whole suite. Exit code only; a nonzero runner exits 1. |
| `hook-precommit [--base REF]` | The cc-only gate on staged blobs. No coverage, no snapshot, no repo-wide cache. Exit 6 on a violation, and on a staged file no reader could read, named on an `UNREAD` line with the reader's reason; `CRAPKIT_OVERRIDE_REASON` grants nothing while such a file is staged. `--base REF` compares the index with the merge base of REF and HEAD, the form a CI checkout runs. Outside a commit with nothing staged, as under `pre-commit run --all-files`, it judges every tracked file. With no `crapkit.toml` at or above where it runs, it gates in each root below that owns a staged file. |
| `claude-hook [--protocol N]` | Reads one PostToolUse payload from stdin, as Claude Code, Copilot CLI, Cursor or VS Code sends it, and judges each file it edited: ccn against the scope ceiling, on functions the edit changed, minus functions a ratchet mark already covers. Advisory only: the edit has landed, and `hook-precommit` stays the enforcement point. The advisory goes on stderr with exit 2 for Claude Code, and as one JSON object on stdout with exit 0 for Copilot CLI, Cursor and VS Code, which read exit 2 otherwise. An unscoped file, or one with no `crapkit.toml` above it, exits 0 in silence. A `Bash` event judges the working tree instead, where you register a `Bash` matcher ([The Claude Code plugin](#the-claude-code-plugin)). It opens no snapshot, and the one thing it writes is that session's record of judged bytes, under the git directory. What it prints and when it stays silent: [docs/commands.md: claude-hook](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/commands.md#claude-hook). |
| `watch [--interval SECONDS] [--cycles N]` | Rescores the files your scopes claim when their content changes (default every 2s, subprocess-isolated so a half-saved syntax error never kills the watcher). Each poll lists the tracked and untracked files under the scope paths again, so a file created while it runs is rescored too. A file whose mtime moved is read and rescored only when its bytes differ, so a touch or an editor saving the same bytes rescores nothing; an edit written under the file's old mtime (`cp -p`, `touch -r`) is not seen, the limit the analysis cache shares. `--cycles N` polls exactly N times and exits 0; without it the loop runs until ctrl-c. After a `pip install -U` under it, the next rescore exits 1 and says to restart it. |
| `help [TOPIC]` | The help git, npm and docker answer to. With no TOPIC it prints the command list; with one it prints that subcommand's own help, the same page as `crapkit TOPIC --help`. A TOPIC that names no subcommand exits 3. |
| `mcp` | A stdio MCP server with no extra dependency, exposing twelve read-side tools named `verb_noun`, each with a title and output schema. Tools call the CLI to inspect current scores, source and edited-file gates. They take no claims and run no verification; calls can write caches or store metadata. See [the MCP contract](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md#mcp-server), and [Wiring crapkit into your agent](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/harnesses.md) for the block each agent's config file takes. |

## Reading the output

Every line crapkit writes from its own words is ASCII, so `$x = crapkit doctor` or
`$x = crapkit worklist 2>&1` in Windows PowerShell 5.1 captures it intact under any
console code page. A path or function name crapkit quotes keeps its own characters and
goes out as UTF-8; to capture one of those, set
`[Console]::OutputEncoding = [Text.Encoding]::UTF8` first.

### Flags: why a coverage number is missing

| Flag | Meaning | Scored |
|---|---|---|
| `measured` | A lane artifact spoke about this function. | Real `cov`. |
| `untested` | A lane covers the scope, but its artifact is silent on this function, which normally means no test imports the file. | `cov = 0`. A testing gap, and `uncovered_lines` comes back `null` because no artifact can name lines it never saw. |
| `excluded` | A lane's artifact measured the file and was told to leave this function out: `# pragma: no cover`, an `exclude_lines` pattern that takes every statement in it, or from coverage.py 7.10.1 a stub whose body is `...`, under coverage.py; `/* istanbul ignore next */` or `/* v8 ignore next */` under istanbul. istanbul drops such a function from the file's `fnMap`, so a function missing from an instrumented file's `fnMap` (one written beside its `statementMap`) that lists others reads `excluded` too. | `crap = ccn`, and `remedy` can only be `ok` or `decompose`: no test can move its number. `uncovered_lines` comes back `[]`. To have it measured, remove the exclusion. |
| `no-lane` | No lane's `scopes` list names this function's scope. | `cov = 0`. A tooling gap, not a testing gap. `next-item` never hands one out and counts them in `skipped_no_lane`; `worklist` ranks them and marks the row `no-lane`, because a wiring gap is a risk you have to see. |
| `cc-only` | The scope sets `coverage_optional = true`, so no coverage number can exist. | `crap = ccn`, and `remedy` can only be `ok` or `decompose`. `uncovered_lines` comes back `null` with a note naming that setting. |

The coverage summary counts all five as `measured` / `untested` / `excluded` / `no_lane` /
`cc_only`.

### Remedy: what to do about it

| Remedy | Condition | Action |
|---|---|---|
| `decompose` | `ccn > ceiling` | Split it. No amount of coverage clears this. |
| `split-lines` | `ccn <= ceiling`, `crap > ceiling`, and another function shares its source lines, or a Python def's body starts on the line its signature ends | Put each definition on its own lines, then measure again. Coverage cannot tell functions on one line apart, so the score stays at uncovered whatever the tests do. A one-line Python def shares its line with its `def` statement, which runs at import, so coverage.py cannot show a call. So does a body on the last line of a signature that spans several lines, or one that goes on from the colon's line inside brackets or after a backslash: move the body to its own line after the signature. |
| `add-tests` | `ccn <= ceiling` and `crap > ceiling` | Cover the branches. |
| `ok` | `crap <= ceiling` | Nothing. |

The comparison is made on the exact CRAP, not on the float that computes it. CRAP(18, 2/3)
is exactly 30, and its float is 30.000000000000004; at `target = 30` it reads `ok`. The
over-target count, the grade, `ratchet seed` and `verify`'s gate judge it the same way.

### Grade and CRAP load

The grade is the share of functions over their ceiling: `A+` at exactly zero, `A` under
2%, `B` under 5%, `C` under 10%, `D` under 20%, `F` at 20% or more. `crap_load` beside it
is the sum of every function's CRAP score, so it moves when a function gets better even if
the letter does not. It is added exactly (`math.fsum`) and rounded once to 2 dp, so the
order the rows come in and the Python or SQLite version never move it. Both are taken over the scopes the run measured: a scope
whose lane failed or was left out by `--lane` scores at the cov-0 stand-in and reports
its load under `by_scope` only. `trend`, `coverage`, the digest
and `brief`'s file totals print the same load for the same rows.

### Risk: what ranks the worklist

`risk = ccn * churn weight`. The weight is a time-weighted sum over the file's commits in
the churn window: each commit contributes a logistic weight rising to 0.5 for the newest
commit in the log and falling to near zero for the oldest, so five edits last month
outrank fifty from two years ago. The window reaches `churn_window_months` back from
HEAD's commit date, never from the wall clock, so a fixed tree ranks identically forever.

Age is not the input, position in the log is. A log whose commits all share one timestamp
has no range to weight against, so each commit counts once: a one-commit repo weighs every
file 1.0, ranks by ccn, and promotes nothing under the floor, because a top 10% of equal
weights would be every file. Commits minutes apart already rank. This repo was eight
commits old, all made the same day:

```
$ crapkit worklist --scope util
worklist @ a7c5c85ac37 (run 1, floor ccn>=5, churn 12mo) - 3 of 3 active (worklist_top 50), 0 dormant
  risk      5.4  ccn   5  crap    30.0  cov   0%    5c/1a  util/stats.py:1  bucket( value , low , high )
  risk      4.5  ccn   9  crap    90.0  cov   0%    1c/1a  util/curve.py:1  curve( scores , mode , floor , ceiling , skip_none )
  risk      4.3  ccn   4  crap     4.2  cov  75%    5c/1a  util/stats.py:13  spread( values , cap )  ok
-> next: crapkit next-item
```

`bucket` at ccn 5 outranks `curve` at ccn 9 because five commits touched it and one
touched `curve`. That is the whole point of weighting by churn. `spread` carries the `ok`
marker: already at or under its ceiling, listed anyway, and `next-item` would not hand it
out.

The list splits in two: **active** (files with commits in the window) and **dormant**
(zero churn, kept out of the queue but counted). Two rules reach under the
`worklist_floor`. A file whose churn weight sits in the top 10% is promoted down to ccn 3,
which is why `spread` appears above at ccn 4. And a function over its ceiling is admitted
whatever its ccn, so the floor can never hold back debt.

### The trusted baseline

Every `verify` measures the working tree against one earlier run, the **trusted
baseline**. `crapkit runs list` marks which one that is today.

**Which runs qualify.** A `coverage` run, or a `verify` that passed. A failed `verify`
never qualifies, and neither does a `partial` run (a lane failed, so some scope fell back
to `no-lane`) nor a `hook` override record, which carries no scored rows at all. In `runs
list`, `verdict=-` marks a run that produces no verdict rather than one that failed: only
`verify` renders a verdict. Four readers ask this one question and get this one answer: the
baseline pick here, `ratchet seed`, `prune`, and the tighten damping that compares a mark
against the same commit's previous run. A mark can no longer be signed off a run `verify`
refused.

**What advances it.** Any qualifying run whose commit is at or behind HEAD. `coverage`
writes one wherever HEAD is, so a dashboard cron advances the baseline exactly as CI does.
A passing `verify` advances it and tightens the ratchet on the way.

**A run on another branch never serves.** The store keeps every branch's runs, and after
`git checkout main` the newest qualifying run can be a feature branch's. `verify`, `ratchet
seed`, `ratchet prune` and `runs list` all skip it and read the newest qualifying run in
main's own history. When no qualifying run sits behind HEAD, `verify` exits 4 and says why
the newest one does not: it was made on a branch HEAD does not contain (run `coverage` on
this branch), or a rebase or an amend rewrote its commit.

**The taint rule.** A failed `verify` recorded findings against a tree. Until some
`verify` passes, runs made after that failure do not become the baseline: choosing one
would move the comparison point past the findings, the flagged function would stop
counting as touched, and nothing would look at it again. `verify` says which run it
refused and falls back to the newest run in front of the failure.

```
$ crapkit runs list
run   1 @ 88012a148f6 2026-08-23T09:27:46Z coverage  verdict=-      lanes=py  baseline
run   2 @ 803bdde8556 2026-08-23T09:27:53Z verify    verdict=FAILED lanes=py
run   3 @ 803bdde8556 2026-08-23T09:28:02Z coverage  verdict=-      lanes=py

$ crapkit verify
warning: run 3 is not the baseline: verify run 2 FAILED with 1 finding(s) and no passing verify has cleared it since - measuring against run 1 @ 88012a148f6 instead, so those findings stay visible. Fix them, or pass `--baseline 3` to accept the newer run deliberately.
verify FAILED @ d89068de7f3 vs baseline 88012a148f6 (2 changed files)
  changed files: calc/legacy.py, tests/test_legacy.py
  GATE  crap     72.0  ccn   8 cov 0%  calc/legacy.py:7  legacy_router( a , b , c , d , e )  -> decompose
  findings: 1 committed / 0 dirty (uncommitted edits and untracked files)
```

Run 3 is a `coverage` run somebody took on the tree run 2 refused, and it scores the same
ccn-8 function. Without the rule it would have become the baseline, `legacy_router` would
have stopped being a touched function, and that gate line would never print again.

**A baseline with no test results for a lane.** A trusted run can hold a lane with no
test count and no failure list: the lane declared no `results_artifact` then,
`coverage --reuse-artifacts` found its junit gone or unreadable, or a crapkit older than
0.8.1 stored a `verify --reuse-artifacts` run over such a junit. verify does not read that as a
suite of zero tests that failed nothing. It compares that lane with the newest trusted run
at or behind the baseline's commit that recorded it, and the line names that run:

```
warning: lane 'py': baseline run 2 recorded no test results, so its failures are compared with run 1's
warning: lane 'py' runs 8 fewer tests than run 1 (baseline run 2 recorded no test count for it)
```

When no run recorded a failure list for the lane, each of its failures still counts as
new, exit 8, and a line says it may predate the change; `verify --json` lists the lane
under `lanes_without_baseline_results`.

**A junit this verify cannot read.** `verify --reuse-artifacts` over a lane that declares
a `results_artifact` it finds missing or unreadable exits 5 and stores no run, the exit a
real run of that lane gets for the same file. The refusal names the lane and the file and
ends `run verify without --reuse-artifacts so the lane writes it again`. Before 0.8.1 that
verify passed at exit 0 with no test checked and stored a passing run, which became the
next baseline. The Action never reaches this case: its `coverage` step runs the lanes and
refuses the same junit first. `coverage --reuse-artifacts` over that junit still warns and
scores on. A lane that declares no `results_artifact` passes with a stderr line, and
`verify --json` lists it under `lanes_without_results`.

**The escape, twice.** Fix the findings and let a `verify` pass, which clears the taint
for good. Or accept the newer run on purpose with `verify --baseline 3`: an explicit id
bypasses the rule, and the run history records which run the verdict used. Nothing here
touches a repo that has never run `verify`: with no failure to protect, `coverage` alone
always advances the baseline.

`ratchet seed --baseline ID` and `ratchet prune --baseline ID` take the same name as
`verify --baseline ID`, admitted by the same rule. When a failed verify pins seed to a run
it cannot read or sign, that is the way out
([docs/ratchet.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md#naming-the-run-to-seed-from)).

**When the id you pass cannot serve.** A `--baseline ID` naming a real run that is not a
candidate says which run it is, why, and which ones can:

```
$ crapkit verify --baseline 3
crapkit: run 3 is an inventory run (no coverage was measured) and cannot serve as a baseline; trusted runs: 1, 2; pass `--baseline 2` for the newest
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. For `verify` and `hook-precommit`: the gate passed. |
| 1 | **Overloaded.** Three unrelated things, listed below the table. |
| 2 | Usage error from argparse: unknown flag, missing positional. Raised before crapkit's own error handling. |
| 3 | Config error: `crapkit.toml` missing or unparseable, an unknown language or parser, a lane command the shell that runs it reads as a narrowed suite, a ratchet metric-stamp mismatch ([Upgrading](#upgrading)), a `test-scoped` file under no scope or under a scope with no template, a scoped file whose name is not UTF-8 or a path argument naming a file whose name is not UTF-8 (both end `rename it (git mv) to a UTF-8 name`), a root `package.json` that `init` cannot read as one UTF-8 JSON object (`init wrote no file: ...`, before it writes any file), a root on a Windows network share (the line gives the `net use` command that maps it to a drive letter). |
| 4 | Git error: not a repository, a repository with no commit yet, one git refuses to open (the refusal quotes git's own fix, such as a `safe.directory` exception), a baseline commit rewritten out of the history, made on a branch HEAD does not contain or missing from this clone, a baseline commit or fork point a shallow clone does not hold, `ratchet report --enforce` with a debt key set in a shallow clone (mark ages and repayments need the whole history), a `ratchet prune` that cannot tell whether a marked file was renamed because this clone lacks the commit its renames start from. The shallow refusals end with `set fetch-depth: 0 on the checkout or run git fetch --unshallow`. |
| 5 | Tool error: lizard not importable, a lane that produced no artifact, one that measured a different tree, one that measured this tree and reported it in absolute paths (the join is root-relative, so those match nothing either; the refusal names the runner's own switch, `relative_files = true` under `[tool.coverage.run]` for a coveragepy lane, the reporter's `cwd`/`root` option for an istanbul one), a lane that timed out past its retries, `verify --reuse-artifacts` over a lane whose declared `results_artifact` is missing or unreadable (it stores no run), an override alert command that failed, a process with no home directory (`USERPROFILE` on Windows or `HOME` on POSIX unset, and the operating system names none; the message names the variable to set). A `timeout_seconds` kills the whole process tree, so no orphan suite keeps running behind the failure. |
| 6 | Gate violation. A function the diff touched is over its ceiling and past any ratchet mark it carries: an edit that leaves a marked function at or under its mark is the debt the repo signed for and is pardoned. Also `rescore --gate`, which applies the same rule, and `hook-precommit`, which pardons on the mark's existence instead. All three also refuse a changed file no reader could read (`UNREAD` lines), since they judged none of its functions. |
| 7 | Ratchet regression the diff never touched. A marked function scores worse than its recorded high-water mark; a touched one past its mark reports 6. |
| 8 | New test failures against the baseline run. Failures the baseline already had do not count, against a `--baseline-tsv` file too: its stamp line lists each lane's failures ([portable records](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/portable-records.md#the-portable-baselines-stamp-line)). |
| 9 | Diff-coverage ceiling breached: `diff_uncovered_max` is set and more changed lines than that never ran. A changed file no lane artifact mentions counts every line of its functions. |

### Exit 1 means one of three things

CI cannot tell a crash from a clean policy verdict on the code alone. Which one you got
depends on the command:

| Command | What exit 1 means |
|---|---|
| `doctor` | A **`FAIL` finding**. This is a verdict, not a crash. A `WARN` (an unmeasured directory, or a lane writing its artifact at the repo root) and a `note` (a file over `max_file_bytes`, or no lanes declared) both exit 0. |
| `ratchet report --enforce` | The **debt policy was breached**. Also a verdict. In a shallow clone, with a debt key set, it judges nothing and exits 4. |
| anything else | An unexpected error: "no snapshot yet, run `crapkit coverage` first", a `brief` name that matches no function, a `test-scoped` runner that exited non-zero. |

`verify` reports the **first** of 6, 7, 8, 9 that fires, in that order. A gate violation
and a ratchet regression together report 6. A run that takes any of them fails, so it
neither advances the baseline nor tightens the ratchet, exit 9 included.

## Quickstart: Python

A repo with `calc/grade.py`, `tests/test_grade.py`, and a `pyproject.toml`. Commit first;
crapkit reads `git ls-files`. Install the coverage plugin first, because the lane `init`
writes runs `pytest --cov` and those flags come from `pytest-cov`:

```
pip install pytest-cov "coverage>=7.13.1"
```

Quote the coverage requirement, or the shell reads `>` as a redirect. Without it, pip
keeps an older coverage.py the venv already holds, since pytest-cov accepts it, and the
lane fails at exit 5 on its report.

(`pip install "crapkit[py]"` pulls both at once when crapkit shares the suite's venv.)

If your suite drives its own CLI through `subprocess.run`, add `[tool.coverage.run]
patch = ["subprocess"]` to `pyproject.toml` and keep `coverage>=7.13.1`: pytest-cov 7.0.0
dropped subprocess measurement, so without that key every entry point scores 0% and nothing
warns. 7.13.1 is also the oldest coverage whose report crapkit reads; an older one fails the
lane at exit 5. [docs/lanes.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md) has the whole rule.

### 1. Scaffold the config

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): calc
detected 1 lane(s) from this repo's own files: py - next: run `crapkit coverage`
added to .gitignore: .crapkit/, .coverage, __pycache__/
```

`init` sniffs tracked source into one scope per top-level source directory, and detects a
coverage lane from what the repo already has: a pytest marker file (`pyproject.toml`,
`pytest.ini`, `setup.cfg`) writes a live `[[lane]]`, and so does a `test` script or
`vitest`/`jest` in `package.json`. A lockfile beside them names the environment: `uv.lock`,
`poetry.lock`, `pdm.lock` or `Pipfile.lock` makes the lane `uv run python -m pytest …` (and
the matching `run` for the rest), because a bare `python` binds to whichever venv the shell
has active rather than the one the repo pins; see
[The interpreter a lane binds to](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#the-interpreter-a-lane-binds-to). Whatever
it detects, it also leaves commented templates for the runners it did not find, and those
carry the same launcher, so uncommenting one cannot hand the bare `python` back. Every lane
it writes reports into `.crapkit/cov/`, which is why the `.gitignore` list is so short: see
[Where artifacts live](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#where-artifacts-live).
With no lockfile the lane names its interpreter with a launcher token, so one committed
line runs on every OS: `{python}` here, which crapkit reads as `python` on Windows and
`python3` on Linux and macOS, or `{python:.venv}` when a `.venv` in the repo holds pytest.
[The launcher token](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#the-launcher-token)
lists each form.

`init` reads each `package.json` past a UTF-8 byte-order mark, as npm does. A root
`package.json` that is not one UTF-8 JSON object stops `init` at exit 3 before it writes any
file, naming the file and the fix, since a lane read off such a file would be a guess: one in
UTF-16 (what PowerShell 5.1's `Out-File` writes), one holding a byte that is not UTF-8, one
that does not parse and one that holds an array or another value, as in
`init wrote no file: package.json is not UTF-8 (byte e9 at offset 36); save it as UTF-8`. A
nested one, a test fixture say, is skipped with one warning line naming it.

`init` writes `.gitignore` before `crapkit.toml`, and appends in that file's own line ending
without touching a byte already there. It reads the lines past a UTF-8 byte-order mark, as git
does, so an entry already there is not added twice. A UTF-16 `.gitignore`, which git cannot
read either, is named on stderr with the fix and left as it was. Run `init` again over an existing
`crapkit.toml` and it adds the `.gitignore` entries its lanes need, says what it finished,
exits 0 and leaves `crapkit.toml` byte for byte. Over a UTF-16 `.gitignore` it exits 3 with
the line that names that file and the entries to add; with nothing missing it refuses with
`already exists`.

```toml
[crapkit]
target = 6

[[scope]]
name = "calc"
paths = ["calc"]
languages = ["python"]

[exclude]
# A leading **/ matches zero or more directories, so each glob below reaches the
# repo root and every nested copy. Test directories leave the corpus on their own.
globs = [
  "**/node_modules/**",
  "**/dist/**",
  "**/build/**",
  "**/vendor/**",
  "**/generated/**",
  "**/__generated__/**",
  "**/*.generated.*",
  "**/*.test.*",
  "**/*.spec.*",
  "**/test_*.py",
  "**/*_test.py",
  "**/conftest.py",
  "**/*_test.go",
  "**/*.config.ts",
  "**/*.config.js",
  "**/*.config.mts",
]

[[lane]]
name = "py"
command = "{python} -m pytest --cov --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml --continue-on-collection-errors"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/junit-py.xml"
parser = "coveragepy"
scopes = ["calc"]

# Declare one [[lane]] per coverage command, then run `crapkit coverage`.
# [[lane]]
# name = "js"
# command = "npx vitest run --coverage --coverage.reportsDirectory=.crapkit/cov/js --coverage.reportOnFailure --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml"
# artifact = ".crapkit/cov/js/coverage-final.json"
# results_artifact = ".crapkit/cov/js/junit.xml"
# parser = "istanbul"
# scopes = ["<your-scope>"]

# `crapkit test-scoped FILES` runs one command per scope, with {files}
# replaced by that scope's files, each quoted; a template with no {files}
# runs as written, which is how a scope whose tests live elsewhere runs them.
[crapkit.scoped_tests]
# calc: no test file under calc/, so the whole suite runs, from tests/
calc = "{python} -m pytest tests -q -p no:cacheprovider"

```

The last block is the one an agent loop needs. `crapkit test-scoped` exits 3 for a file
whose scope declares no template, and [AGENTS.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#4-run-the-owning-scopes-tests)
makes it step 4 of the burn-down loop. Every key is in
[docs/configuration.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md).

### 2. Check the config against the repo

```
$ crapkit doctor
resources: up to 8 analysis worker(s) per pool, 8 shared slot(s); lane log limit 16777216 bytes per file
ok   config keys all recognized
ok   scope 'calc': 1 file
ok   every tracked source file belongs to a scope
ok   1 lane(s) declared
ok   lane 'py': python3 -> /home/you/.venvs/ledger/bin/python3 (pytest 8.3.3, pytest-cov 7.1.0, coverage 7.13.1)
ok   lane 'py': runs pytest (named in its command)
ok   lizard 1.24.0
doctor: no problems found
```

`doctor` prints one line per check and exits 1 only on a `FAIL`. `WARN` and `note` report
and exit 0, and the closing line counts the WARNs above it (`doctor: no problems found, 1
warning above`).

### 3. Score the repo, and read the queue

```
$ crapkit coverage
run 1 @ fae4db93108: 2 functions scored: 2 measured, 1 over ceiling 6, CRAP load 41.0, grade F
-> next: crapkit worklist

$ crapkit worklist
worklist @ fae4db93108 (run 1, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk     14.0  ccn  14  crap    38.5  cov  50%    1c/1a  calc/grade.py:7  classify( score , attempts , late , bonus )
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
```

Columns: `risk`, `ccn`, the function's `crap` and `cov` off the ranked run (both `-` on an
inventory-only run, and `cov` `-` on a `no-lane` or `cc-only` row, which no lane
measured), `<commits>c/<authors>a` in the churn window, `path:line`, the function's long
name, then a marker on rows the burn-down queue will not hand out (`ok`, `no-lane`). The
header counts the active rows against their total, so `50 of 3980 active (worklist_top 50)`
says what the cap hid, and reads `(--top N)` when the flag set the cap.
`--json` also carries `ccn_std`, `weight` and `ratchet_mark`.

The last line names the step after this one. It is `ratchet seed` while the repo has no
marks file, `next-item` once it has one, and `coverage` when the ranked run cannot serve
as a baseline: an inventory run, a partial run or a failed verify. This walk looks at the
top item first, in step 4, and seeds in step 5.

**`worklist` is the risk map, not a to-do list.** It ranks finished rows too, so it does
not empty when the burn-down does. `next-item` is the other view of that run: it drops the
`no-lane` rows, ranks by `crap`, and its `empty: true` is the stop condition.

### 4. Take the top item

```
$ crapkit next-item
{"commands": {"refresh": "crapkit coverage --reuse-unchanged"}, "commit": "fae4db93108b4841a00959f9117430679e7250ca", "empty": false, "item": {"authors": 1, "ccn": 14, "ccn_std": 14, "cognitive": 13, "commits": 1, "cov": 0.5, "crap": 38.5, "end": 28, "est_splits": 3, "est_uncovered_paths": 7, "flag": "measured", "function": "classify( score , attempts , late , bonus )", "handle": "classify", "nesting": 3, "nloc": 22, "occurrence": 1, "path": "calc/grade.py", "remedy": "decompose", "scope": "calc", "start": 7, "target": 6, "uncovered_lines": [9, 11, 15, 17, 19, 24, 25, 26, 27, 28], "unmeasured": false}, "run_id": 1, "schema": 1, "scored_changes": 0, "shallow": false, "skipped_no_lane": 0, "stale": false}
```

`remedy: "decompose"`, `est_splits: 3` (this needs roughly three pieces to fit under 6),
and `uncovered_lines` naming the ten lines no test walks. `handle` is the name form to
pass back. `scored_changes: 0` says no file the run scored holds other content now, so
these numbers describe the files on disk; anything but `0`, `null` included, means run
`commands.refresh` before acting on them. `stale: false` says HEAD is still the run's
commit, a question about the commit and not the files. Every field is in
[docs/agent-json.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md).

### 5. Seed the ratchet

Arm the debt gate before fixing anything. `ratchet seed` records every over-target
function at its current score, and from then on nothing may get worse.

```
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (fae4db93108)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`

$ git add crapkit.toml crapkit-ratchet.tsv .gitignore && git commit -m "adopt crapkit"
```

A `verify` here would pass and give the repo its first passing verdict. This walk runs it
in step 6, after the fix.

### 6. Fix it and verify

Extract until every piece sits at or under the ceiling. Here `classify` became
`_validate`, `_adjusted`, `_band` and a `classify` that only sequences them, with the
table of cases pushed into parametrized tests. Commit the fix, then:

```
$ crapkit verify
verify OK @ 8d10c13303d vs baseline fae4db93108 (5 changed files) ratchet: 1 dropped, 0 tightened -> git add crapkit-ratchet.tsv
  changed files: calc/grade.py, .gitignore, crapkit-ratchet.tsv and 2 more

$ crapkit coverage
run 3 @ 8d10c13303d: 5 functions scored: 5 measured, 0 over ceiling 6, CRAP load 19.0, grade A+
-> next: crapkit worklist
```

CRAP load 41.0 to 19.0, grade F to A+. `verify` reruns the lanes and checks three things
against the trusted baseline: every function the diff touched sits at or under its
ceiling, no marked function got worse, and no test that passed in the baseline fails now.
Exit 0 advances the baseline and tightens `crapkit-ratchet.tsv` in place, so the repaid
mark leaves the file: follow up with `git commit -am "ratchet: classify repaid"`. The full
mark lifecycle is in [docs/ratchet.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md).

`crapkit next-item` now comes back `empty: true` with a `reasons` object saying which
ending you got. That is most of the stop condition, not all of it:
[AGENTS.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md#the-termination-rule) states the whole rule and reads the rest of
`reasons`.

## Quickstart: TypeScript

A vitest repo with `src/grade.ts` and `test/grade.test.ts`.

### 1. Scaffold the config

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): src
detected 1 lane(s) from this repo's own files: js - next: run `crapkit coverage`
added to .gitignore: .crapkit/
```

The lane `init` wrote is
`npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js --coverage.reportOnFailure --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml`.
It reads vitest's `json` reporter from `.crapkit/cov/js/coverage-final.json`; the
`reportsDirectory` flag is what keeps that report out of your root. The junit half is the
lane's `results_artifact`, which the crashed-worker and no-new-failures checks read; both
reporters are named because `--reporter=junit` alone would replace the console output you
watch the suite through. Anything that produces
an istanbul `coverage-final.json` works; see [docs/lanes.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md) for the
[jest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#jest) and [pytest](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#pytest) recipes, a package
[one directory down](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#running-from-a-subdirectory), and a
[crapkit root below the repo top](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#a-crapkit-root-below-the-repo-top).

`init` writes the vitest line under `[crapkit.scoped_tests]` commented out, because it
cannot see which vitest config a file-scoped run needs. Until you uncomment that line or
write your own, `crapkit doctor` WARNs `scope 'src' has a lane but no
[crapkit.scoped_tests] template`. The WARN leaves doctor's exit at 0; it means `crapkit
test-scoped` exits 3 on files under `src/`.

### 2. Install a coverage provider

**This is the step that stops most TypeScript users.** vitest ships no coverage provider
by default. Without one, `init` and `doctor` are both happy and `coverage` dies with
exit 5:

```
$ crapkit coverage
crapkit: lane 'js' FAILED: lane 'js' produced no artifact at .crapkit/cov/js/coverage-final.json (command exit 1); lane log: /repo/.crapkit/lane-js.log; last output: $ npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js --coverage.reportOnFailure --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml

> app@1.0.0 test
> vitest run --coverage --coverage.reportsDirectory=.crapkit/cov/js --coverage.reportOnFailure --reporter=default --reporter=junit --outputFile=.crapkit/cov/js/junit.xml

 MISSING DEPENDENCY  Cannot find dependency '@vitest/coverage-v8'

(exit 1)
crapkit: every lane failed (1 of 1); the errors are above
```

The two `>` lines are npm's banner: `> <name>@<version> test` from your package.json (`> test`
when it has no name or version), then the command the `test` script runs.

That failure **writes no run**. Every lane failed, so `coverage` exits before it opens a
store: there is no `.crapkit/crap.sqlite` yet and the run ids below still start at 1.

Install the provider, and pin the major yourself. Unpinned, npm resolves the newest
provider against your older vitest and refuses the tree:

```
npm i -D "@vitest/coverage-v8@<your vitest major>"
```

| Question | Answer |
|---|---|
| Which provider? | Either works. `@vitest/coverage-v8` is vitest's default and needs no config. `@vitest/coverage-istanbul` also works and needs `coverage.provider = "istanbul"` in your vitest config. |
| Which crapkit parser? | Both feed `parser = "istanbul"`. The provider name and the parser name are unrelated: v8 output is remapped to the istanbul JSON schema before it is written. |
| Which version? | The provider's major has to match vitest's. Read your vitest major with `npm ls vitest`, then install the provider at that major: on vitest 5, `npm i -D "@vitest/coverage-v8@5"`. Drop the pin and npm answers `ERESOLVE unable to resolve dependency tree`, naming the peer it could not satisfy. |

The artifact crapkit wants is `coverage-final.json`, written by vitest's `json` coverage
reporter, which is on by default. If your vitest config sets `coverage.reporter`
explicitly, keep `"json"` in the list.

vitest writes **no coverage report at all when the run fails**. The lane `init` wrote
already carries `--coverage.reportOnFailure`, so a red test still produces the artifact.
If you write the lane by hand, or you would rather keep the switch beside your other
coverage settings, `coverage.reportOnFailure = true` in the vitest config does the same
job; either one is enough. The full block is in
[docs/lanes.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md#reportonfailure).

### 3. Score the repo

```
$ crapkit coverage
run 1 @ 8bfbe613fcd: 2 functions scored: 2 measured, 1 over ceiling 6, CRAP load 56.68, grade F
-> next: crapkit worklist

$ crapkit worklist
worklist @ 8bfbe613fcd (run 1, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk     15.0  ccn  15  crap    52.4  cov  45%    1c/1a  src/grade.ts:8  classify ( row Row )
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
```

`classify` is ccn 15 against a ceiling of 6: one function holding the late-and-retry
penalty, the letter bands, the demotion rule and the null case.

### 4. Seed the ratchet and commit

`ratchet seed` records every over-target function at the score it has today, so nothing
can get worse while you burn this one down.

```
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (8bfbe613fcd)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`

$ git add crapkit.toml crapkit-ratchet.tsv .gitignore && git commit -m "adopt crapkit"
```

### 5. Fix it

Above the ceiling, coverage cannot help, so `classify` gets split rather than tested.
`penalty`, `band` and `demote` come out as their own exported functions, and `classify`
keeps the null case and the bonus:

```ts
export function classify(row: Row): string {
  if (row.score === null) {
    return "N/A";
  }
  let score = row.score - penalty(row.attempts, row.late);
  if (row.bonus && score < 90) {
    score += 3;
  }
  return demote(band(score), row);
}
```

`rescore --gate` judges that edit on complexity alone, before the slow step:

```
$ crapkit rescore src/grade.ts --gate
rescore vs run 1 @ 8bfbe613fcd (coverage STALE, complexity fresh)
   ccn   cov     crap  remedy      function
     5     -     30.0  add-tests   src/grade.ts:22  band ( score )  (coverage not measured)
     5     -     30.0  add-tests   src/grade.ts:38  demote ( letter , row Row )  (coverage not measured)
     4     -     20.0  add-tests   src/grade.ts:8  penalty ( attempts , late )  (coverage not measured)
     4   45%      6.7  add-tests   src/grade.ts:48  classify ( row Row )
     4   75%      4.2  ok          src/grade.ts:59  average ( scores Array )
gate: 4 changed function(s) judged, 0 over ceiling 6
```

Exit 0 and the `gate:` line: every changed piece is at or under 6. The `crap` column is loud because its coverage half
is still run 1's, from before three of those functions existed. Run 1 has no row for
`band`, `demote` or `penalty`, so each prints `-` for `cov`, ends `(coverage not
measured)` and scores as if no test ran it. `add-tests` is the literal instruction for
step 6.

### 6. Cover the new pieces

`rescore --gate` passed on complexity, not on coverage. `penalty`, `band` and `demote` are
three functions no test has ever called, so each gets a table test:

```ts
describe("band", () => {
  it.each([[95, "A"], [85, "B"], [75, "C"], [65, "D"], [10, "F"]])(
    "scores %i as %s", (score, expected) => expect(band(score)).toBe(expected));
});
```

Run the suite once before the slow step:

```
$ npx vitest run
 Test Files  1 passed (1)
      Tests  21 passed (21)
```

Skip this step and step 7 fails rather than passes. Run on a copy of this repo with steps
1 to 5 as written, step 5 left uncommitted and step 6 left out, `verify` reruns the lanes
against the real tree and three functions the old suite never called come back over the
ceiling. Each GATE line ends `[dirty]` because the edit is not committed, and the last line
counts the findings that way. The block leaves out the two warnings verify prints above its
verdict, one for changed lines no test covers and one for debt no mark signs:

```
$ crapkit verify
verify FAILED @ 0296156ff21 vs baseline 8bfbe613fcd (6 changed files)
  changed files: src/grade.ts, .gitignore, crapkit-ratchet.tsv and 3 more
  GATE  crap     17.8  ccn   5 cov 20%  src/grade.ts:38  demote ( letter , row Row )  -> add-tests  [dirty]
  GATE  crap     12.4  ccn   5 cov 33%  src/grade.ts:22  band ( score )  -> add-tests  [dirty]
  GATE  crap     10.8  ccn   4 cov 25%  src/grade.ts:8  penalty ( attempts , late )  -> add-tests  [dirty]
  findings: 0 committed / 3 dirty (uncommitted edits and untracked files)
```

### 7. Verify

```
$ crapkit verify
verify OK @ 2af3433d979 vs baseline 8bfbe613fcd (7 changed files) ratchet: 1 dropped, 0 tightened -> git add crapkit-ratchet.tsv
  changed files: src/grade.ts, .gitignore, crapkit-ratchet.tsv and 4 more

$ crapkit coverage
run 3 @ 2af3433d979: 5 functions scored: 5 measured, 0 over ceiling 6, CRAP load 22.0, grade A+
-> next: crapkit worklist
```

CRAP load 56.68 to 22.0, grade F to A+, and the mark seeded in step 4 is gone: `verify`
dropped it once `classify` scored under the ceiling, rewriting the tracked
`crapkit-ratchet.tsv` in place. Commit it with your change. Marks only ever fall.

The seven changed files are everything since run 1's commit: the three files step 4
committed, the `package.json` and `package-lock.json` that step 2's install changed, and
the two files steps 5 and 6 edited.

A verify may also print `warning: N changed line(s) have no coverage` above its verdict;
that block is advisory unless `diff_uncovered_max` is set
([docs/configuration.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md)).
It prints `warning: N function(s) over the ceiling carry no ratchet mark` and names the
first three when the tree holds debt `ratchet seed` never signed: the gate judges touched functions only and the
ratchet check compares marks only, so coverage loss on such a function would pass unseen.
The count is `unmarked_over_target` in `--json`, fires no exit code, and is zero on a repo
with no debt.

## Documentation

| Page | Covers |
|---|---|
| [The handbook](https://www.jfgagne.com/crapkit/handbook.html) | **Start here for anything deeper.** The illustrated handbook: what crapkit is, how every piece works, and where each command earns its keep. The same page ships in the repository as `docs/handbook.html`, self-contained, so it opens straight from a clone. |
| [docs/adoption.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/adoption.md) | The judgment layer over the quickstarts: scope granularity, exclude vs lane, scoped_tests wiring, the first-verify taint hazard. |
| [docs/configuration.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md) | Every `crapkit.toml` key: type, default, and what it does. |
| [docs/lanes.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/lanes.md) | The lane model, vitest and jest and pytest recipes, artifact reuse, flake retest, containers. |
| [docs/resources.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/resources.md) | Worker budgets, command cleanup, log rotation and safe cleanup. |
| [docs/ratchet.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/ratchet.md) | Seeding, pruning, the git merge driver, metric stamps, debt policy, overrides. |
| [docs/upgrading.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/upgrading.md) | Existing installations: analysis and key versions, saved state, plugin alignment and Windows upgrades. |
| [docs/portable-records.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/portable-records.md) | Lossless exports, portable baselines and ratchets, including filenames with delimiters. |
| [docs/commands.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/commands.md) | What `doctor`, `mutate` and `claude-hook` check, print and refuse, and which lines `duplication` compares, past what a Subcommands row holds. |
| [docs/agent-json.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/agent-json.md) | The machine surface: `schema`, every payload field, real captured examples. |
| [docs/harnesses.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/harnesses.md) | Wiring crapkit into 27 agents: each one's MCP config, whether it runs the advisory hook, and how it restarts after an upgrade. |
| [docs/comparison.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/comparison.md) | Where crapkit sits next to radon, xenon, wily, coverage.py and SonarQube, and how they run together. |
| [docs/accuracy.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/accuracy.md) | How crapkit checks its own numbers: each calculation against outside tools, hand tables and models, the tiers that run the checks, and every place crapkit reads a construct differently from an oracle on purpose. |
| [AGENTS.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/AGENTS.md) | The burn-down loop an agent runs, and the rules for changing crapkit itself. |
| [CHANGELOG.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/CHANGELOG.md) | What each release changed and how to upgrade to it. |
| [plugin/](https://github.com/JeanFrancoisGagne/crapkit/tree/main/plugin) | Three skills and the MCP server for Claude Code and Codex, and the advisory PostToolUse hook that Claude Code, Cursor, Copilot CLI and VS Code run. |

[crapkit.schema.json](https://github.com/JeanFrancoisGagne/crapkit/blob/main/crapkit.schema.json) is the authority on the config file shape.

## Development

```
pip install -e ".[dev,accuracy-push]"
git config core.hooksPath git-hooks
git config merge.crapkit-ratchet.driver "python -m crapkit ratchet merge %O %A %B"
python tools/testing/run.py
```

The dev extra includes pytest, pytest-cov, pytest-xdist and coverage.py; the
accuracy-push extra holds the pinned oracles of the
[calculation-accuracy suite](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/accuracy.md).
The shared runner owns the unit and E2E schedule; use `--unit-workers 1` for serial unit
reproduction or `--coverage` for combined branch coverage and JUnit. The
`core.hooksPath` line arms the complexity gate on commits and change control on pushes,
and the `merge.crapkit-ratchet.driver` line merges `crapkit-ratchet.tsv` through
`crapkit ratchet merge` instead of as text. See
[CONTRIBUTING.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/CONTRIBUTING.md)
for development, [docs/accuracy.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/accuracy.md)
for how crapkit checks its own numbers, and
[tools/deploy/README.md](https://github.com/JeanFrancoisGagne/crapkit/blob/main/tools/deploy/README.md)
for the install tests each release passes.

## Maintainer and project background

crapkit is created and maintained by [Jean-François Gagné](https://www.jfgagne.com/).
Read the [project background](https://www.jfgagne.com/projects/crapkit/) for the
problem it addresses and how it fits into his work on software and AI.

## License

MIT. See [LICENSE](https://github.com/JeanFrancoisGagne/crapkit/blob/main/LICENSE).
