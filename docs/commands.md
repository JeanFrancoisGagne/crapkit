# Command details

README's [Subcommands](../README.md#subcommands) table gives each command, every flag
and what the command is for, in one row. Three commands check, print or refuse more than
a row can say. This page holds the rest for `doctor`, `mutate` and `claude-hook`.

## doctor

`crapkit doctor` checks that the config still describes the repo. It names unknown keys
(with the accepted spellings), zero-file scopes, tracked source no scope claims, scopes no
lane covers, lane cwds and commands that no longer resolve, whether lizard imports, and
oversized files. `--show-files` lists every file each scope matched. The JSON form is in
[docs/agent-json.md](agent-json.md#doctor---json).

### Lanes and their runners

`doctor` reads each lane command with the shell that will run it, so a quoted interpreter
path is one word and a runner after `&&` is checked too. It FAILs a lane whose runner does
not resolve or that the shell cannot start, naming the word to change. A bare name is
looked for on PATH, and a runner spelled as a path is looked for under the directory the
lane runs in, so `.venv/bin/python` answers the same from any directory you run `doctor`
in. Each distinct runner is probed once per directory and environment it starts in, not
once per lane.

A probed `pytest --cov` lane prints the interpreter it resolves to with its pytest,
pytest-cov and coverage.py versions. `doctor` FAILs the lane when coverage.py is older
than 7.13.1, which writes no function start lines and gets the lane's report refused with
exit 5.

`--json` gives each lane a `refusal`: the sentence `--reuse-artifacts` would refuse its
artifact with, or `null`.

### What it warns about

`doctor` WARNs on:

- a lane writing its artifact at the repo root;
- a `coveragepy` or `istanbul` lane with no `results_artifact`: the crashed-worker and
  no-new-failures checks are off for it, whichever runner the lane spells;
- a committed hook under `core.hooksPath` that is not executable in the index;
- a `coveragepy` lane with no `container_ok` inside a container, which the lane runner
  refuses with exit 5;
- a hook that runs crapkit's gate while git runs another: Route 1 under a global
  `core.hooksPath`, Route 2 in a clone that skipped its `git config` line, or either one
  after husky took `core.hooksPath` back;
- a `.pre-commit-config.yaml` naming `crapkit-gate` before `pre-commit install`;
- a marks file whose merge attribute names a driver this clone never defined;
- a directory whose functions are all `untested` while its tests exist;
- a scope a lane measures with no `[crapkit.scoped_tests]` template behind it, which is
  the loop's step 4 with nothing to run;
- a lane whose artifact on disk is the leftover of a failed attempt that
  `--reuse-artifacts` refuses;
- a `.crapkit/artifacts.json` it cannot read.

### The marks file

`doctor` WARNs on a marks file `verify` refuses because an older metric stamped it, and
prints verify's own refusal and remedy. After an upgrade that moves the analysis version
the remedy is `crapkit coverage`, `crapkit ratchet prune`, then `crapkit ratchet seed`.
It FAILs a marks file a newer crapkit or lizard stamped, which only an upgrade of this
install clears, and a marks file it cannot read.

### Launchers on PATH

Two or more `crapkit` launchers on PATH are named, each with its version. They get a WARN
when they answer different versions, since the shell, a hook, the plugin and an MCP client
each run the first their own PATH lists, and a `note` while they agree.

### --tune

`--tune` prints suggested `[crapkit]` parallelism knobs for this machine, from the CPU
count and recorded lane durations, and writes nothing.

### --plugin-root

`--plugin-root PATH` reads no repo at all. It checks an installed
[plugin](https://github.com/JeanFrancoisGagne/crapkit/tree/main/plugin) against the
`crapkit` on PATH (the bare name its hooks and MCP server spawn) on both version and hook
`--protocol`. It FAILs when PATH carries no `crapkit` at all or one that answers no
version. It prints one line per disagreement, each naming the command that closes it, and
exits 0 with no such line when they agree.

A version gap names the side that is behind and the commands that move it:

- Claude Code's update lines, once per scope that holds the install, with the project
  directory to run a project or local one in;
- `git -C <dir> pull` for a plugin Claude Code loads in place from a local directory
  marketplace;
- Codex's refresh for a plugin under `~/.codex` or `CODEX_HOME`;
- the upgrade for the installer that owns the launcher: `uv tool upgrade`,
  `pipx upgrade`, or pip for that launcher's python.

A pre-release or local build names both sides. A hook asking for an older or newer
protocol names the same repair for the side behind, and a hooks file or manifest it
cannot read names the reinstall for each scope (`git -C <root> checkout -- <file>` for a
checkout Claude Code loads in place). It also names an install whose files differ from
its marketplace's copy at one version, which `claude plugin update` leaves in place, with
the reinstall lines for each scope, and a Claude Code below 2.1.139 when the plugin's
hooks pass `args`.

Run under uvx, `uv run --with` or `pipx run`, it looks past every environment in uv's or
pipx's cache, which the plugin never inherits, and the launcher count above leaves them
out too.

PATH is the plugin root or any directory above it, `~/.claude` included. Only manifests
named `crapkit` count, and the newest install wins. With no PATH it checks every install
Claude Code recorded (a user install and a project install made at another version are
two), falls back to the newest in Claude Code's plugin cache, then Codex's, and checks a
marketplace added from a local directory in that directory, where Claude Code loads it. A
root it found rather than one you typed is named first, as `crapkit doctor: checking
PATH`.

## mutate

`crapkit mutate` is diff-scoped mutation testing: comparison flips, boundary shifts,
boolean connectives and boolean literals on changed lines. It runs `mutation_command`
once per mutant and lists the survivors. The README row has the file
selection, the counts and the summary lines. This section is which operators make a
mutant in each language.

### Files it refuses

Shell and PowerShell files are refused by name on stderr rather than mutated: `<` and
`>` are redirections there, not comparisons.

### Operators that make no mutant

- Zig's connectives are `and` and `or`, and its error-set merge `A || B` never mutates.
- Swift's unspaced `a<b` makes no mutant, because `Foo<Bar>` is spelled the same way;
  `a < b` does.
- An operator the language reads as one token makes no mutant, so a piece of it is never
  flipped as a comparison: Go's `<-`, the `>>>` and `>>>=` of JavaScript, TypeScript and
  Java, C++'s `<=>` and a Swift operator such as `|>`.
- A `&&` or `||` that joins no two operands makes none either, such as Rust's closure
  `|| 0` or borrow `&&x` and C++'s references `auto&& x` and `Foo&& x`. Nor does the
  name in `bool operator<(...)` or the type in `static_cast<T&&>(x)`.
- `mutate` cannot tell a type name from any other name, so in C, C++ and Objective-C
  files the layout decides after a name: a `&&` hugged to one side, `Foo&& x` or
  `ok&& ready`, makes no mutant, and one spaced on both sides, `Foo && x` or
  `ok && ready`, does.
- The C family's `--` is one token too, so `n-->0` reads as `n-- > 0` and its `>`
  mutates.

## claude-hook

`crapkit claude-hook` reads one PostToolUse payload from stdin, as Claude Code, Copilot
CLI, Cursor or VS Code sends it, and judges each file it edited: ccn against the
scope ceiling, on the functions the edit changed, minus the functions a ratchet mark
already covers. It is advisory only: the edit has landed, and `hook-precommit` stays the
enforcement point.

### What it prints

The advisory is the only thing it ever says. It prints one block per judged file: a head
line, one line per breaching function and a closing line. For a file a scope takes whose
name git gives in bytes that are not UTF-8, the block is two lines naming it and the
rename. For a changed file it could not judge, the block names the file and the reason:

- no reader could parse it: `crapkit advisory: PATH could not be read, so no function in
  it was judged`, then the reader's reason and the fix, because the commit gate refuses
  that file once staged;
- git could not report what the edit changed.

The advisory goes on stderr with exit 2 for Claude Code, and as one JSON object on stdout
with exit 0 for Copilot CLI, Cursor and VS Code, which read exit 2 otherwise.

### When it stays silent

A file type crapkit does not measure, no `crapkit.toml` above the edited file, an
unscoped file, a rebase or merge in progress, a `--protocol` other than 1, a file that
holds no function, and any internal failure all exit 0 in silence.

### Which functions count as changed

Before a repo's first commit there is no HEAD to diff against, so every function in the
file counts as changed. The root is the first `crapkit.toml` above the edited file; the
walk stops at a `.git` entry, so a worktree never borrows its parent's config.

### A Bash event

A `Bash` event names no file, so the hook judges the working tree instead: the dirty or
untracked `*.py` files touched in the last 12 seconds, 25 at most, each through the same
ladder, minus any file whose bytes this session already judged. A clean tree or a cwd
outside any repo gets silence. That half fires only where you register a `Bash` matcher
([README: the Claude Code plugin](../README.md#the-claude-code-plugin)).

The hook opens no snapshot, and the one thing it writes is that session's record of
judged bytes, under the git directory.
