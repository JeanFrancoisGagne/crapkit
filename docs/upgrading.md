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

Check `crapkit --version` in the environment your shell, hook and MCP client use.
For a source checkout, follow [Development](../README.md#development). Stop a live
MCP server before upgrading on Windows; see [launcher locks](#windows-launcher-locks).

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
its running server uses the new code. A server that outlived the upgrade answers every
tool call with that instruction instead of running it:

    crapkit was upgraded from 0.8.0 to 0.8.1 while this MCP server ran, and the server still runs 0.8.0's code, which cannot load the new files. Restart the crapkit MCP server (reconnect it in your client, or start a new session), then call list_runs again.

A server from 0.8.0 or earlier does not check, and its first call after the upgrade can
fail with a JSON-RPC `-32603` error such as `TypeError: _operation() takes 2 positional
arguments but 3 were given` or `ToolError: measurement owner stopped before confirming
ownership`. The restart fixes that too. A running `crapkit watch` stops at its next
rescore after the upgrade, exits 1 and says to restart it.

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

## Windows launcher locks

A running `crapkit.exe mcp` holds its console launcher open, and each installer meets
that lock its own way. Measured on Windows 11 with pip 26.2.1, pipx 1.17.6 and uv
0.12.18, upgrading 0.7.6 to 0.8.0 while a server from the same install ran:

| Command | Exit | What it printed and left behind |
|---|---|---|
| `python -m pip install --upgrade crapkit` | 0 | `Successfully installed crapkit-0.8.0`, then `WARNING: Failed to remove contents in a temporary directory`. pip moved the busy `crapkit.exe` into that directory: `crapkit --version` says 0.8.0, and the running server still answers as 0.7.6 |
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
whichever crapkit release uv has cached or can download, and stops every commit on
`No module named crapkit` on a machine without uv. The PowerShell hook README's Route 1
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
| another MCP client | delete the `crapkit` server entry from its config ([stdio setup](agent-json.md#mcp-server)) |

Remove the plugins with the package. Both plugins start the bare `crapkit` command, so
with the package gone and the Claude Code plugin still installed, `claude mcp list`
shows `plugin:crapkit:crapkit: crapkit mcp - ✘ Failed to connect`.

## Release evidence

The [implementation report](architecture/2026-09-07-implementation/REPORT.md)
records complete Windows source and Linux installed-wheel verification, independent
reviews and repeatable performance probes. Its benchmark tables distinguish
synthetic duplication input, fixture setup and a fixed unit subset from complete
suite runs. They do not claim a whole-suite speedup. Hosted CI dispatch and macOS
runtime execution were outside that local verification.
