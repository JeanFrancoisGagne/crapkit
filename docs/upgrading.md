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

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

`pip --user` puts the launcher in the user scripts directory: `~/.local/bin` on Linux,
`%APPDATA%\Python\Python3XX\Scripts` on Windows. When that directory is not on PATH, pip
says so on install, `WARNING: The script crapkit is installed in '...' which is not on
PATH`, and the `crapkit` your shell finds is some other install, or none. Add the
directory it names to PATH before checking the version.

Once uvx has fetched crapkit, plain `uvx crapkit` keeps running that release after a
newer one ships, and so does an agent config that starts `uvx crapkit mcp`. The uvx row
asks for the newest release by name; from then on plain `uvx crapkit` runs that one.

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

A reader you missed exits 3 on the committed marks. From 0.8.1 an older release that meets
marks a newer one wrote says so, `the marks come from a newer crapkit than this install`,
asks for an upgrade, and its `ratchet seed` and `ratchet prune` refuse the file. 0.8.0 and
older releases instead name `crapkit ratchet seed`, and that seed restamps the team's marks
under the older analysis, after which every upgraded teammate's verify refuses them. Upgrade
that reader rather than follow the line.

## Measure before changing marks

0.8.0 moves the reader to analysis version 11, so a marks file stamped under 10
needs one re-seed; [analysis version 11](#analysis-version-11) says what moved.
The package upgrade rebuilds the versioned analysis cache automatically, and the
first `inventory` or `coverage` after it analyzes every file again. That run's
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

## Teammates' clones

Two settings live in each clone and never in a commit: the merge driver's
`git config merge.crapkit-ratchet.driver` line ([merge driver](ratchet.md#the-git-merge-driver))
and Route 2's `git config core.hooksPath` line. A clone without the driver merges
`crapkit-ratchet.tsv` with git's text merge, and a conflict there gets resolved by
hand, which is how a mark rises. Put both lines in your CONTRIBUTING setup steps. After
an upgrade that re-seeds the marks under a new analysis version, a teammate still on
the older release gets `verify`'s exit 3 on them until they upgrade too.

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
the listing. With no explicit path, doctor checks Claude Code's cache, and Codex's
when Claude Code has no install. A version gap on a Codex install names Codex's refresh
lines above, never a `claude` command.
Use the three skills and MCP server in Codex. Codex loads no crapkit hook: the
plugin's Codex manifest leaves hooks out. Start a new Codex task to load updated
plugin skills and tools.

Codex does the first two steps on its own. Each start upgrades the configured git
marketplaces and refreshes the installed plugin from them: with Codex 0.156.1, one
`codex app-server` start after a release replaced
`~/.codex/plugins/cache/crapkit/crapkit/0.8.0` with the new version's directory. So
upgrade the CLI before the next Codex start, or the plugin runs ahead of it, and run
`crapkit doctor --plugin-root PATH` after that start to confirm the two agree.

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
