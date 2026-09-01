# Adoption

The quickstarts in [README.md](../README.md) carry the mechanics: sniff the repo, check the
config, score it, seed the ratchet, install the gate. This page carries the decisions they do
not make for you, in the order you hit them. Read it before you run `crapkit init` in a repo
you care about.

## Cut fewer, broader scopes first

A scope is a ceiling plus a language set, not a package. One scope per language per
top-level tree is the right opening move, even in a repo with twenty packages.

Every scope you add owes a lane or a `coverage_optional`, because a lane-less scope is a
`doctor` FAIL. It also wants a `scoped_tests` template of its own. Ten scopes on day one is
ten lanes and ten templates before the first score lands, and the usual outcome is a config
that half exists.

Splitting later is cheap. Ratchet marks are keyed by path and function name with no scope
in the key, so re-cutting scopes leaves every recorded mark exactly where it was. Start
coarse, and split a scope the day you actually want a different ceiling or a different test
command for part of it.

When you do split, a nested scope wins over the scope that contains it. Declare `src` and
then `src/web`, and every file under `src/web` belongs to `src/web`: for scoring, for lane
reuse, for `test-scoped` routing, and for the ceiling `crapkit brief` hands an agent. Since
0.4.5 that is one rule with one answer. 0.4.4 answered it three ways, so a repo that already
nests scopes may see files change scope on its next scan, and the per-scope rollups and
ceilings move with them. A config whose scopes do not nest sees no change.

## Exclude, lane, or coverage_optional

Three ways to stop a file reading as untested debt, and they are not interchangeable.

| Choice | For | What it costs |
|---|---|---|
| `[exclude] globs` | generated, vendored, minified, build output | the code leaves the corpus completely and never scores again |
| a new `[[lane]]` | code you would test if a runner were wired to it | one command and one artifact path to keep true |
| `coverage_optional = true` | code no test can reach (entry points, deploy scripts, generated shims), and any scope whose language has no coverage parser | the scope still scores on complexity, and `remedy` narrows to `ok` or `decompose` |

The wrong pick in either direction is expensive. Excluding real code hides its debt
permanently and silently, and nothing ever reports what you excluded. Setting
`coverage_optional` on testable code turns every `add-tests` verdict in that scope into `ok`.
Prefer the lane whenever a runner could plausibly reach the code.

Sometimes none can. crapkit reads two coverage formats, coverage.py's JSON and istanbul's
`coverage-final.json`, so a Go, Rust or Swift scope has no lane to prefer. There
`coverage_optional` is the honest answer, not a shortcut, and `crapkit init` already wrote
it: any scope whose languages all lack a parser comes out of `init` carrying the key. What
is left for you is the first reason — code a test could reach but never will. See
[lanes.md](lanes.md#which-languages-a-lane-can-measure).

## What to do about `no_lane_over_target`

`reasons.no_lane_over_target` above zero in `crapkit next-item` blocks the stop condition:
rows the queue cannot hand out are over their ceiling anyway. Three real answers, in order:

1. **Declare the `[[lane]]`.** The default. The rows are measurable and nobody wired them.
2. **Set `coverage_optional`** on the scope, when no test can reach that code by design.
3. **Exclude it**, only when the files should not be scored at all.

`crapkit next-item --exclude FRAG` is not on that list. It is a session flag, so the count
goes to zero for you and the next session sees the rows again.

## `scoped_tests` belongs between doctor and coverage

The quickstarts run `crapkit init`, then `crapkit doctor`, then `crapkit coverage`. Fill in
the `[crapkit.scoped_tests]` table between the last two. `init` already wrote a line per
scope, live where a detected lane proves the runner and commented everywhere else, so
uncommenting is usually the whole job.

Every python line it wrote names one launcher, the lockfile's where the repo has one:
`uv run python -m pytest ...` on a `uv.lock` repo, in the lane command, in the entries
here, and in the commented `[[lane]]` template. Step 3 measuring one environment while
step 4 tests another is the bug that rule exists to prevent, so keep the prefix on any
line you write by hand.

Skip it and nothing breaks loudly, which is the hazard. A repo with lanes and no templates
scores fine, doctor reports the gap as a warning, and every packet `crapkit brief` builds
hands back `commands.scoped_tests: null`. Step 4 of the burn-down loop then quietly does not
exist for any agent that ever works this repo.

## The two-templated-scopes trap

Uncommenting every template is what springs it. A test file that lives outside every scope's
`paths` routes to the single scope that declares a template. With two templated scopes there
is no single owner, and `crapkit test-scoped tests/test_stats.py` exits 3. Naming a source
file instead routes fine and then hands the runner a source path to collect tests from: no
tests ran, runner exit 5, crapkit exit 1.

The way out is the whole-suite form. A template with no `{files}` placeholder runs exactly
as written, so the scope runs its own suite whichever of its files you name:

```toml
[crapkit.scoped_tests]
calc = "uv run python -m pytest tests/test_grade.py -q -p no:cacheprovider"
util = "uv run python -m pytest tests/test_stats.py -q -p no:cacheprovider"
```

`uv run` there is the launcher, not decoration: this repo carries a `uv.lock`, so that
is the prefix `init` wrote on its lane command and on every line of this table. A repo
with no lockfile names no launcher, and the same two lines start at `python`.

Keep `{files}` only for a scope whose tests live under its own `paths`, where narrowing to
the files you named is what you want.

## The first verify taints the baseline

`crapkit verify` on a repo that has never passed one runs against a tree carrying all its
pre-existing debt. It fails, and a failed run then blocks every later baseline: each
subsequent run prints `run N is not the baseline: verify run M FAILED ...` and measures
against something older.

Prevent it by seeding first. `crapkit ratchet seed` records today's over-target functions as
accepted debt, so the first verify judges your edit rather than the repo's history.

Once it has happened, two escapes, both legitimate:

- Fix the findings the older baseline still shows, then run `crapkit verify` again.
- Accept the newer run by name: `crapkit verify --baseline N`, a visible act somebody can
  audit later.

`crapkit runs list` prints `verdict=-` on runs that rendered no verdict. See
[The trusted baseline](../README.md#the-trusted-baseline).

Since 0.4.5 the same failure also blocks `crapkit ratchet seed` and `crapkit ratchet prune`,
which used to take the newest trusted run and would happily sign marks off a `coverage` run
made after the failure. They now walk back with `verify` and say what they stepped over
(`skipped failed verify run 2`). If the failure stands in front of every trusted run there
is, both refuse and say a fresh `coverage` would be refused the same way. That is the case
you hit by running `verify` before seeding, so seed first
([ratchet.md](ratchet.md#seed-and-prune-pick-the-run-verify-picks)).

## Planning a campaign

Once the repo is seeded and green, somebody has to decide how many sessions run at once and
what that costs. The facts a plan needs, in the order it meets them. The timings come from
the 31,459-file repo the 0.4.5 performance work was refereed against.

**Two fan-outs, and the two N count different things.** `crapkit brief --batch N` emits one
packet per queue item, top N, and the orchestrator hands one packet to one session: its N is
a packet count. `crapkit worklist --batches N` cuts the active list into at most N groups
that share no file, and each group is one agent's territory: its N is an agent count.
Disjoint file sets are what make the resulting diffs merge, so the batches are the shape to
reach for once a session does more than one function.

The packets do not deal themselves one per batch, because the two commands rank
differently: `worklist` ranks by risk, which is ccn times recency-weighted churn, and the
queue behind `brief --batch` ranks by CRAP. On a six-file fixture repo:

```
$ crapkit worklist --batches 3
batch 1: 6 items in 3 files: calc/charlie.py, calc/delta.py, calc/foxtrot.py
batch 2: 2 items in 1 files: calc/alpha.py
batch 3: 4 items in 2 files: calc/bravo.py, calc/echo.py
```

`crapkit brief --batch 3` on that same run returned packets for `calc/alpha.py`,
`calc/echo.py` and `calc/bravo.py`: batch 2 once, batch 3 twice, batch 1 never. So an agent
holding a batch briefs its own rows. Every `batches[].entries[]` row carries `path` and
`function`, which are the two arguments `brief` takes, so the session runs one
`crapkit brief PATH "FUNCTION"` per item it picks up.

**One `brief --batch N` per wave, never N `brief` calls**, when it is the orchestrator
dealing packets. The batch call reads the store, the churn log and the ratchet file once,
and since 0.4.5 it shingles the repo once for the whole batch instead of once per packet. A
batch of 5 went from 11.8 s to 5.2 s, output byte-identical. That saving is the
orchestrator's; a session briefing the handful of items in its own batch pays a cold start
either way.

**The coupling cache is per checkout, and the first run in each pays for it.** Ranked
co-change pairs live in `.crapkit/coupling-cache-v1.json`, which `init` already gitignores
along with the rest of `.crapkit/`. Warm, `coupling` costs 0.11 s instead of 1.05 s and
`worklist --batches` 62% less. A fleet of ten worktrees is ten cold runs, once each. Passing
`--min-support` or `--min-confidence` off their defaults bypasses the cache every time, so
keep the fleet on the defaults unless somebody is investigating.

**`mutate` with `mutation_workers > 1` leaves worktrees on disk.** The workers now keep their
worktrees under `.crapkit/mutate-pool/w0..wN` and re-prepare them per run, which took a run's
setup from 30.6 s to 0.46 s. What is left behind is one full checkout of the repo per worker,
and **the pool is not size-bounded**: budget for it on a big tree, and reclaim it with
`crapkit mutate --drop-pool`. Single-worker runs mutate the working tree as they always did
and leave nothing. A second `mutate` in the same repo finds the pool locked and falls back to
a throwaway base, so it is slower rather than wrong.

**`trend` and `report` write.** Both fill a per-run rollup table on first read, which is what
takes `trend` from 4.58 s to 0.04 s. A session holding a checkout it must not write to should
not be the one running them, though the write is best effort and neither command fails when
it cannot land.

Nothing skips `verify`, so budget one full run per session that finishes an item. Caching a
verdict on HEAD plus the dirty file names was measured for 0.4.5 and rejected: that key
cannot see a second edit to a file that was already dirty, and a gate that misses one edit is
worse than a slow gate.

Upgrading crapkit mid-campaign costs one commit: 0.4.5 measures at analysis version 8 where
0.4.4 measured at 7, so every existing mark is refused until somebody runs `crapkit ratchet
seed` and commits the restamped file. Do it once, on one branch, before the fleet fans out
([ratchet.md](ratchet.md#upgrading-to-045-analysis-version-8)).

## Put repo traps in `notes`

Every repo teaches its agents something that no config key expresses: a module that must
stay importable without side effects, a generated file that looks hand-written, a helper
everything already imports. Put it in `[crapkit] notes` for the repo, or in a `[[scope]]`'s
own `notes` when it binds one tree only.

`crapkit brief` carries both into every packet, so the trap arrives with the work instead of
living in a commit message nobody reads. Keys are documented in
[configuration.md](configuration.md).

## Install the agent surface last

Wire the harness once the repo scores and `crapkit doctor` is clean. Do it earlier and the
`crapkit-onboard` skill points at a config that does not exist yet, while the `crapkit` skill
points at a store with no run in it.

**Claude Code users install the plugin.** One artifact, versioned against the CLI:

```
claude plugin marketplace add JeanFrancoisGagne/crapkit
claude plugin install crapkit@crapkit
```

It carries three skills, the read-side MCP server, and one advisory PostToolUse hook that
names functions an edit pushed over their ceiling. The hook never blocks; the commit gate
stays the only enforcement point. After a CLI upgrade, `crapkit doctor --plugin-root PATH`
compares the two and prints nothing when they agree.

The plugin registers that hook on `Edit|Write`. A session that writes source through a
shell heredoc gets no advisory until the consumer adds a second entry of their own with
matcher `Bash`, which costs one `git rev-parse` plus one `git status` per shell call and
is therefore opt-in. The snippet is in the `crapkit-onboard` skill. `doctor --plugin-root`
does not cover that entry: it reads the plugin's own `hooks/hooks.json` and nothing else, so
a protocol bump shows up for the shipped matcher and stays silent for the one you wrote.
Re-check it by hand after a CLI upgrade.

Every other harness takes one of the two surfaces under it. The table is the whole list; no
adapter beyond it exists yet.

| Harness | What it gets |
|---|---|
| Claude Code | the plugin: three skills, the MCP server, the advisory hook |
| any MCP client (Codex, Cursor, Zed, Continue) | `crapkit mcp` as a stdio server: nine read-only tools, no skills, no hook |
| anything else | the pre-commit hook and CI, which are git and shell and need no harness at all |

A runtime with a skills directory but no marketplace can copy `plugin/skills/*` into it and
get the skills alone. MCP wiring is in [agent-json.md](agent-json.md#mcp-server).
