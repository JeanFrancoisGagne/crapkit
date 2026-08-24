# Adoption

The quickstarts in [README.md](../README.md) carry the mechanics: sniff the repo, check the
config, score it, seed the ratchet, install the gate. This page carries the decisions those
steps do not make for you, in the order you hit them. Read it before running `crapkit init`
in a repo you care about.

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

## Exclude, lane, or coverage_optional

Three ways to stop a file reading as untested debt, and they are not interchangeable.

| Choice | For | What it costs |
|---|---|---|
| `[exclude] globs` | generated, vendored, minified, build output | the code leaves the corpus completely and never scores again |
| a new `[[lane]]` | code you would test if a runner were wired to it | one command and one artifact path to keep true |
| `coverage_optional = true` | code no test can reach: entry points, deploy scripts, generated shims | the scope still scores on complexity, and `remedy` narrows to `ok` or `decompose` |

The wrong pick in either direction is expensive. Excluding real code hides its debt
permanently and silently, and nothing ever reports what you excluded. Setting
`coverage_optional` on testable code turns every `add-tests` verdict in that scope into
`ok`. Prefer the lane whenever a runner could plausibly reach the code.

## When `no_lane_over_target` is a lane and when it is an exclude

`reasons.no_lane_over_target` above zero in `crapkit next-item` blocks the stop condition:
rows the queue cannot hand out are over their ceiling anyway. Three real answers, in order:

1. **Declare the `[[lane]]`.** The default. The rows are measurable and nobody wired them.
2. **Set `coverage_optional`** on the scope, when no test can reach that code by design.
3. **Exclude it**, only when the files should not be scored at all.

`crapkit next-item --exclude FRAG` is not on that list. It is a session flag, so the count
goes to zero for you and the next session sees the rows again.

## `scoped_tests` belongs between doctor and coverage

The quickstarts run `crapkit init`, then `crapkit doctor`, then `crapkit coverage`. Fill in
the `[crapkit.scoped_tests]` table between the last two. `init` already wrote it commented
out, one line per scope, so uncommenting is usually the whole job.

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
calc = "python -m pytest tests/test_grade.py -q -p no:cacheprovider"
util = "python -m pytest tests/test_stats.py -q -p no:cacheprovider"
```

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

## `notes` is the channel for repo traps

Every repo teaches its agents something that no config key expresses: a module that must
stay importable without side effects, a generated file that looks hand-written, a helper
everything already imports. Put it in `[crapkit] notes` for the repo, or in a `[[scope]]`'s
own `notes` when it binds one tree only.

`crapkit brief` carries both into every packet, so the trap arrives with the work instead of
living in a commit message nobody reads. Keys are documented in
[configuration.md](configuration.md).

## Install the skills last

Once the repo scores and `crapkit doctor` is clean, copy `skills/*` into `~/.claude/skills`,
or your agent runtime's equivalent. Installed earlier, `crapkit-onboard` points at a config
that does not exist yet and `crapkit` points at a store with no run in it.
