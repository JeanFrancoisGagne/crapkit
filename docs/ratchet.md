# The ratchet

The ratchet is a committed TSV of per-function high-water CRAP marks. It answers one
question: *has this function got worse than the day we agreed to live with it?*

**A mark only ever falls.** An improvement lowers it or drops it. A regression never raises
it; it fails the build instead. New debt enters only through `ratchet seed` or an audited
override, both of which are visible in a diff.

**Changed in 0.4.0: a commit that touches a marked function is no longer refused.** The
pre-commit gate now treats a mark as an exemption, and `crapkit verify` is what fails a mark
that rises. [The rule, and why it moved](#the-commit-gate-skips-marked-functions).

Default file: `crapkit-ratchet.tsv` at the repo root, settable with `[crapkit] ratchet_file`.
Commit it.

---

## What a mark is

```
# crapkit-analysis=3 lizard=1.24.0
path	long_name	crap
calc/grade.py	classify( score , attempts , late , bonus )	66.0714
calc/report.py	render( rows , wide , totals , header )	56.0000
```

A comment line carrying the metric stamp, a header, then one tab-separated row per mark:
path, long name, CRAP to four decimals. Rows are sorted by `(path, long_name)`, so the file
is diffable and merge conflicts are local.

Identity is `(path, long_name)`, never the line number. Spans drift on every edit; names
survive. When one long name appears at two spans (anonymous twins), the **worst** twin
represents the key, so a regression cannot hide behind a clean sibling.

---

## The commit gate skips marked functions

New in 0.4.0, and it decides which commits get refused. Read it before you seed a repo.

`hook-precommit` judges staged blobs. A blob carries no coverage, so a staged violation has
a ccn and no CRAP, and there is nothing to compare a mark against. The hook asks the one
question it can answer: does a `(path, long_name)` mark exist? If it does, that function is
not gated, whatever the edit did to it. One stderr line reports the count, never a list:

```
crapkit gate: 1 staged function(s) carry a ratchet mark and were not gated — `crapkit verify` fails a mark that rises
```

The three gates read the file differently:

| Gate | What it holds | What a mark does there |
|---|---|---|
| `hook-precommit` | a staged blob: ccn, no coverage | skips the function on the mark's **existence** |
| `rescore --gate` | a scored row: ccn and CRAP | skips the function **at or under** the recorded value |
| `verify` | a full run | **exit 7** when the fresh score is above the mark |

Nothing about `verify` changed. It still compares the numbers, and it is still where a real
regression is caught.

The looseness is deliberate. Before it, a comment added inside a marked function refused the
commit. On a repo carrying 40,303 marks that meant a seeded tree could not be touched, while
`rescore --gate` on the same tree passed. The advisory hook `crapkit claude-hook` exempts on
existence too, so a session of green advisories no longer ends at a red commit.

---

## Seeding

Seeding is how a legacy repo gets a ratchet. It records today's over-target functions as
accepted debt, so from then on the gate judges your edit instead of the repo's history.

```
$ crapkit coverage
run 1 @ 549e0ccdcdf: 3 functions scored — 2 measured / 1 untested / 0 no-lane / 0 cc-only, 2 over target 6, CRAP load 124.07, grade F

$ crapkit ratchet seed
crapkit-ratchet.tsv: added 2, tightened 0 — 2 mark(s) vs run 1 (549e0ccdcdf)
```

`seed` marks every function over its scope ceiling from the latest full run, at its current
score. It is idempotent, and it can only lower: rerunning after an improvement reports
`tightened`, never `added`.

**Seed once, early.** Skipping it means a legacy repo's existing debt carries no marks, so
the ratchet check has nothing to compare and coverage rot on untouched code goes unnoticed.
`verify` still gates the diff, but the standing debt is unprotected.

`ratchet seed` needs a `coverage` or `verify` run in the store:

```
$ crapkit ratchet seed            # no store at all
crapkit: no snapshot in /repo — run `crapkit coverage` first
EXIT=1

$ crapkit ratchet seed            # inventory ran, but no lanes ever did
crapkit: no full coverage run to work from — run `crapkit coverage` first
EXIT=1
```

---

## The metric stamp

Scores measured under different rules are not comparable. The file's first line records the
analysis version and the lizard behind the numbers, so crapkit can refuse instead of
comparing them silently.

Three cases:

| Recorded stamp | Behavior |
|---|---|
| Matches the running metric | Compare normally. |
| Differs | **Refused**, exit 3. |
| Absent (a file written before stamping) | Accepted with a warning. There is nothing to disagree with. |

```
$ crapkit verify
crapkit: ratchet marks were recorded under [crapkit-analysis=2 lizard=1.17.10] but this run measures [crapkit-analysis=3 lizard=1.24.0] — CRAP scores are not comparable across metric versions; re-baseline with `crapkit ratchet seed`
EXIT=3
```

```
$ crapkit verify
warning: crapkit-ratchet.tsv carries no metric stamp (written before stamping) — re-baseline with `crapkit ratchet seed` to stamp it
verify OK @ 525a3276065 vs baseline 525a3276065 (1 changed files)
EXIT=0
```

`ratchet seed` and `prune` always rewrite the stamp to the running metric. The merge driver
is the exception: it writes whatever stamp both sides already shared, so two legacy sides
stay legacy.

This is why `crapkit` pins its lizard dependency by lower bound and why upgrading lizard is
a deliberate act. A new lizard changes the stamp, and every consumer's next run refuses its
own marks until somebody re-seeds.

---

## Pruning, and renames

`prune` drops marks whose function is absent from the latest run, and nothing else drops
them. Absence alone is not proof the code left: an exclude glob or a lane outage removes rows
too, and the per-verify update keeps stale entries rather than erasing an audited override's
only diff-visible record. Running `prune` is you confirming.

```
$ crapkit ratchet prune
crapkit-ratchet.tsv: pruned 0, followed 2 rename(s) — 2 mark(s) vs run 11 (7d09097ea8a)
```

**A rename follows instead of dropping.** Before pruning, crapkit asks git for renames since
the store's first run and re-paths any mark that meets all three conditions:

1. The function is gone from its recorded path.
2. git calls that path renamed.
3. The same `long_name` exists at the destination.

A *copy* fails condition 1 (the source survives), so a copy never moves a mark. Before and
after `git mv calc/grade.py calc/grading.py`:

```
calc/grade.py	audit( rows , strict , cap , floor , verbose )	132.0000
calc/grading.py	audit( rows , strict , cap , floor , verbose )	132.0000
```

### Moving marks by hand

When git cannot see the rename (a vendored tree, a rewritten history), state it:

```
$ crapkit ratchet move calc/grade.py calc/grading.py
crapkit-ratchet.tsv: moved 2 mark(s) from calc/grade.py to calc/grading.py
```

A trailing `/` on the old path moves a whole directory:

```
$ crapkit ratchet move calc/ scoring/
crapkit-ratchet.tsv: moved 2 mark(s) from calc/ to scoring/
```

Values never change. A move that matches nothing is a config error rather than a silent
no-op:

```
$ crapkit ratchet move nope/x.py y.py
crapkit: ratchet move: no mark under nope/x.py in crapkit-ratchet.tsv (a directory must end in '/')
EXIT=3
```

`move` needs no run and no store; it reads the file and rewrites it.

---

## The git merge driver

Two branches that both burn down debt produce two different ratchet files, and git's default
text merge will conflict on adjacent lines. Hand-resolving a conflict is exactly where
somebody accidentally raises a mark, which the whole design forbids.

Install the driver. Two steps, both required.

**1. `.gitattributes`, committed:**

```
crapkit-ratchet.tsv merge=crapkit-ratchet
```

**2. `git config`, run once per clone** (git will not take a driver command from a committed
file, by design, so this cannot be automated away):

```
git config merge.crapkit-ratchet.driver "python -m crapkit ratchet merge %O %A %B"
git config merge.crapkit-ratchet.name "crapkit ratchet 3-way merge"
```

The `.driver` line is the one that matters; `.name` is only a description git shows. Put
both in your CONTRIBUTING setup steps.

`%O %A %B` are base, ours, theirs. The driver writes the merged result **in place over
`%A`** and exits 0, which is what git requires of a merge driver.

Live, merging a branch that added a mark into a branch that tightened another:

```
$ git merge feature -m "merge feature"
ratchet merge: 2 mark(s)
Auto-merging crapkit-ratchet.tsv
Merge made by the 'ort' strategy.
```

```
# crapkit-analysis=3 lizard=1.24.0
path	long_name	crap
app/a.py	foo( x )	31.5000
app/b.py	bar( y )	22.0000
```

`foo` kept main's tightened 31.5 and `bar` arrived from the feature branch. Per key, the
side that changed wins over the side that did not; when both changed the **lower** value
wins, because a mark can only fall and `prune` is re-runnable.

The driver refuses to merge across metric versions, and git falls back to a normal text
conflict for you to resolve after re-seeding one side:

```
$ git merge feature
crapkit: ratchet merge refused: ours is [unstamped] and theirs is [crapkit-analysis=3 lizard=1.24.0] — marks from different metric versions cannot merge; re-baseline one side with `crapkit ratchet seed`
Auto-merging crapkit-ratchet.tsv
CONFLICT (content): Merge conflict in crapkit-ratchet.tsv
Automatic merge failed; fix conflicts and then commit the result.
```

`ratchet merge` runs with no `crapkit.toml` in sight, because git invokes it from a temp
directory. It is the one ratchet subcommand that needs no config.

---

## Reporting the burn-down

`ratchet report` answers two questions: how much debt is still open, and how fast it is being
repaid.

```
$ crapkit ratchet report
ratchet burn-down: 2 open mark(s), 0 repaid (0 in the last 30d, 0 in 90d)
      0d  calc/grade.py  classify( score , attempts , late , bonus )
      0d  calc/report.py  render( rows , wide , totals , header )
```

Ages and repayment velocity come from the ratchet file's **own git history**, replayed patch
by patch. No timestamp lives in the TSV, so a fixed history reports the same numbers forever.
Everything anchors on the newest commit in that history, never the wall clock.

Which marks are *open* is a question about now, so that reads the working tree. A seed you
have not committed yet is still debt somebody owes, and the report says so:

```
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 — 1 mark(s) vs run 2 (d9cdcfdcb1a)

$ crapkit ratchet report
ratchet burn-down: 1 open mark(s), 0 repaid (0 in the last 30d, 0 in 90d)
  1 uncommitted mark(s) in crapkit-ratchet.tsv: open reads the working tree, ages and repayment read committed history
      0d  calc/grade.py  classify( score , attempts , late , bonus )
```

A mark with no commit behind it reports `0d`. The burn-down clock starts when you commit the
file.

---

## The debt policy

Two optional `[crapkit]` keys turn the report into a gate:

| Key | Flags |
|---|---|
| `debt_max_age_months` | Open marks older than this, counted at 30 days per month. |
| `repayment_min_per_30d` | A burn-down that repaid fewer marks than this in the last 30 days, while debt is open. |

`--enforce` evaluates them and exits 1 on a violation:

```
$ crapkit ratchet report --enforce
ratchet burn-down: 2 open mark(s), 1 repaid (1 in the last 30d, 1 in 90d)
  POLICY repayment stalled: 1 mark(s) repaid in 30d (policy wants 5)
      0d  calc/grade.py  audit( rows , strict , cap , floor , verbose )
      0d  calc/grade.py  classify( score , attempts , late , bonus )
EXIT=1
```

With neither key set, `--enforce` can never fire. In `--json`, `policy_violations`
distinguishes the three states honestly:

| `policy_violations` | Means |
|---|---|
| `null` | No policy was evaluated: `--enforce` was absent, or no debt knobs are configured. |
| `[]` | The policy ran and found nothing. |
| `["..."]` | Violations, and the exit code is 1. |

Do not read `[]` off a call without `--enforce`. There is none to read.

---

## How `verify` uses the ratchet

A clean pass tightens it automatically. After a green `verify`:

- A marked function now at or under its ceiling loses its mark entirely.
- A marked function still over its ceiling keeps the **lower** of its mark and its fresh
  score.
- A marked function absent from the run keeps its mark untouched. Absence is not proof the
  code is gone; that is what `prune` is for.

A run that passed **because of an `--override`** does not tighten anything. The override
already wrote the debt it granted, and letting the same run also rewrite every other mark
would mix a granted exemption into a routine tightening.

A function scoring worse than its mark is a ratchet regression, whether or not the diff
touched it. That is the point: coverage rot regresses functions nobody edited.

```
$ crapkit verify
verify FAILED @ 8c780bb18da vs baseline 8c780bb18da (1 changed files)
  RATCHET  app/m.py  pick( a , b , c ): 10.75 -> 20.0
  findings: 1 committed / 0 dirty (uncommitted tracked edits)
EXIT=7
```

That run changed only a test file. The source function was untouched; deleting its coverage
was enough.

Comparison happens at the precision the mark is stored at (four decimals). `cov` is a
division, so long decimals are routine and an unrounded compare would wedge an unchanged
tree against its own mark.

The commit gates read the same file and ask it a looser question. See
[The commit gate skips marked functions](#the-commit-gate-skips-marked-functions).

---

## Overrides and the audit trail

An override is a human granting audited debt, not a bypass. **Three records or nothing**:

1. An alert line through `[crapkit] alert_command` on stdin. It fires first, because it is
   the step most likely to fail.
2. A row in the snapshot store's override log.
3. An entry in the committed ratchet, staged into the pending commit, so the debt is
   diff-visible.

A failure between step 2 and step 3 leaves an audit trail with no grant, never a grant with
no trail. Without `alert_command` the override is refused outright, before anything happens:

```
$ crapkit verify --override "shipping the hotfix, ticket 412"
crapkit: no alert_command configured — the override requires a visible alert line; set [crapkit] alert_command in crapkit.toml
EXIT=3
```

With it configured:

```
$ crapkit verify --override "shipping the hotfix, ticket 412"
verify OK @ 8c780bb18da vs baseline 8c780bb18da (2 changed files)
  OVERRIDDEN  app/m.py:9  route( a , b , c , d )
EXIT=0
```

```
$ crapkit overrides
run  10 @ 8c780bb18da 2026-08-23T01:36:42Z  crap 56.0  app/m.py  route( a , b , c , d )  (shipping the hotfix, ticket 412)
```

The pre-commit hook takes the same path through `CRAPKIT_OVERRIDE_REASON`:

```
$ CRAPKIT_OVERRIDE_REASON="hotfix 412, decompose in the follow-up" git commit -m "add route"
crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:
  ccn   7  app/m.py:9  route( a , b , c , d )
crapkit: override granted with full audit (hotfix 412, decompose in the follow-up).
crapkit: unset CRAPKIT_OVERRIDE_REASON now — while set it grants again on every commit.
```

The hook path never raises an existing mark. It has no coverage data, so it synthesizes a
worst-case score, and letting that overwrite a real measurement would blind the ratchet to a
later coverage collapse. A prior tighter mark stays, and the next `verify` still demands
repayment.

An empty reason is refused. Runs an override names are pinned in the store: `runs prune`
never deletes them.

---

## Adoption checklist

```
crapkit init                # scopes, a lane, .gitignore
crapkit doctor              # the config still describes the repo
crapkit coverage            # a scored run exists
crapkit ratchet seed        # existing debt gets marks
git add crapkit.toml crapkit-ratchet.tsv .gitignore
git commit -m "adopt crapkit"
crapkit verify              # should be green on the tree you just committed
```

Then install the gate ([README](../README.md#installing-the-gate)) and the merge driver
above.
