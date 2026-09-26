# Calculation accuracy

crapkit's scores are only worth acting on if they are right. The accuracy suite
checks every number, label, ranking and pass/fail crapkit computes against an
expected value that does not come from crapkit's own code, and it keeps any of
them from moving without a declared change.

It lives in `tests/accuracy` and `tools/accuracy`, apart from the unit and e2e
suites in `tests/unit` and `tests/e2e`, which pin crapkit's behavior. This page
is for contributors: how to run it, what a failure means, and how to add a check.

## What it checks

`tests/accuracy/*/calcs.tsv` lists 93 calculations, from the CRAP score and each
complexity reader to the worklist order, the verify exit code and the printed
next-step commands. Each row names the calculation's modules and functions and
one independent test, whose imports reach no crapkit code.

Each calculation is checked by at least three methods whose expected value does
not come from crapkit:

| Method | Where the expected value comes from |
|---|---|
| Hand table | Worked rows, each citing an outside source: a paper, a spec section, a doc line |
| Outside oracle | Another tool on the same input: radon, mccabe, complexipy, ESLint, sonarjs, the TypeScript compiler, PowerShell's parser, gocyclo, PMD, coverage.py, istanbul and the rest in `tools/accuracy/pins.toml` |
| Clean-room model | A `model_*.py` written from cited doc lines alone; the contract fails when a cited line changes |
| Metamorphic relation | An edit with a known effect: a comment moves no score, reversed lanes move none |
| Property | A rule over Hypothesis inputs: CRAP lies between ccn and ccn^2 + ccn |

On top of those, goldens pin crapkit's output on a small and a full corpus,
cross-surface checks compare every surface that prints one number, receipts
compare Linux, Windows and macOS, mutation testing measures whether the suite
notices a changed line, and crapkit itself stops with exit 5 when a number it is
about to store breaks its documented bound (`src/crapkit/invariants.py`).

The work is split into packets, one directory each under `tests/accuracy`:

| Packet | Checks |
|---|---|
| `kit` | What the others share: exact arithmetic, the tier markers, dated repos, the CLI driver, the goldens lock, the contract |
| `score_model` | CRAP, coverage ratio, remedy, grade, budget, ceilings, queue and worklist order |
| `verdict_model` | Ratchet keys and marks, the gates, verify's verdict and exit, baselines, lane reuse, claims |
| `analysis_oracles` | The file universe, decoding, every language reader: functions, ccn, cognitive, nesting, nloc, params |
| `coverage_oracles` | coverage.py and istanbul attribution, the coverage join, flags, dark lines, diff coverage |
| `history_oracles` | Churn, coupling, changed ranges, renames, burn-down |
| `corpus_goldens` | The corpora and goldens, the wheel diff, the cross-platform exports, every output surface |
| `change_control` | The rules that stop an undeclared golden or metric move |
| `definitions` | Each field's definition, the same in the README, CONTEXT.md, agent-json.md, MCP and SARIF |
| `runtime_guards` | The runtime bounds: no stop on real repos, and what they cost |
| `suite_strength` | Past bugs replayed, mutation floors, the release gate |

## Tiers

| Tier | Where it runs | What it adds |
|---|---|---|
| `push` | Every push and pull request: `accuracy-push` in ci.yml, on Ubuntu and on Windows (the `os_sensitive` checks) | The fast checks, at most 504 declared serial seconds on Ubuntu (about 2 min at `-n 4`) |
| `nightly` | accuracy.yml at 03:17 UTC, in the accuracy image, plus native Windows and macOS cells | Every outside oracle, the full corpus, 20,000 Hypothesis examples, Python 3.11 to 3.14, mutation of the functions changed since the weekly run, a slice of the past-bug replays |
| `weekly` | accuracy.yml on Saturdays | Full mutation testing in 8 shards, a no-cache image rebuild that checks every pin |
| `release` | The release tool's `accuracy` stage | The push tier, both wheel diffs against the previous release, the store upgrade, the consumer replay, the retro replays, the mutation coverage |

A pull request runs the nightly tier as well while it carries the `accuracy` label.

## Run it

Install the pinned tools once, then run a tier from the repository root:

```
pip install -e ".[dev,accuracy-push]"
npm ci --prefix tools/accuracy/node/push
python tools/accuracy/run.py --tier push -n 4
```

`--shard analysis|coverage|history|verdict-score|corpus` runs one shard,
`--os-sensitive` runs only the checks whose answer can change with the OS. The
nightly tier needs the `accuracy` extra, `npm ci --prefix tools/accuracy/node/nightly`
and the full corpus (`python tools/accuracy/corpus.py fetch`); the outside tools
that are not Python or Node packages come with the accuracy image.

Exit codes: 0 every check passed, 1 a check failed, 3 only infra misses (an
oracle not installed, a fetch that failed) after one retry. Each run writes a
receipt to `.crapkit/accuracy/<tier>-<shard>-<os>-<python>.json` and prints each
check's declared and measured seconds.

The Linux cells run in the accuracy image, which holds every pinned oracle:

```
docker build -f tools/accuracy/image/Dockerfile -t crapkit-accuracy:$(python tools/accuracy/run.py image-tag) tools/accuracy
docker run --rm --network none -v "$PWD:/src" crapkit-accuracy:<tag> python tools/accuracy/run.py --tier nightly --shard corpus -n 4
```

On Docker Desktop for Windows, a bind mount makes every file stat slow; copying
the checkout into the container first runs the nightly shards several times
faster. Under Git Bash, set `MSYS_NO_PATHCONV=1` before `-e VAR=/path`.

## When a check fails

A failing check means crapkit and its expected value disagree. Find out which
one is wrong before changing either:

- crapkit is wrong: fix it at the root, with a unit or e2e test for the fix.
  Until the fix lands, the check stays red, or it becomes a strict xfail through
  a `defect` row in its packet's `rulings.tsv` that names the bug.
- crapkit means something else on purpose: add a `definition` row to the
  packet's `rulings.tsv` that cites where the docs say so, and fix the docs if
  they do not.
- The oracle is wrong: show why in the check, and keep that oracle out of it.

A strict xfail that starts passing fails the run: its bug is fixed. Change its
rulings row to `fixed` with one value on both sides.

## Past bugs

`tests/accuracy/suite_strength/retro/bugs.tsv` lists every past calculation bug:
its fix commits, the commit before them, and the check that must catch it.
`ledger.tsv` beside it records the last replay of each row. `tools/accuracy/retro.py`
replays a row: it checks out the commit before the fix and the fix, installs each
commit's crapkit in a venv of its own, and runs the check from this tree against it.

```
python tools/accuracy/retro.py run R57 --record
```

A replay counts only when the check fails on an AssertionError at the commit
before the fix and passes at the fix. A check that passes before the fix catches
nothing, and one that fails at the fix proves nothing; both are refused, the row
stays `pending`, and its ledger note says why. Each night accuracy.yml replays the
rows whose check changed and a seventh of the rest: the `retro` job in the Linux
image, and the Windows cell the rows whose `platform` is `windows`
(`retro.py nightly --platform-only`).

When the check cannot ask its question of the old commit (it reads a field the fix
added, or a later bug fails it too), write a probe: a script in `retro/probes/`
that asks only this bug's question through the CLI or API both commits have. It
exits 0 when the value holds and raises AssertionError when it does not, and its
`# source:` line names where the expected value comes from, never crapkit's output
at the fix. Name it in the row's `probe` cell.

The replayed check runs with `CRAPKIT_ACCURACY_PYTHON` set to the commit's venv,
that commit's crapkit (and nothing else from its venv) first on PYTHONPATH, and
`CRAPKIT_ACCURACY_CHECKOUT` naming the commit's checkout, where a check finds the
files a wheel does not carry, such as `action.yml`. A row's `env` cell may set
`CRAPKIT_ACCURACY_LANGUAGES` and `CRAPKIT_ACCURACY_ROOT_PATHS` for a commit that
read fewer languages or refused a root scope of `.`. `CRAPKIT_RETRO_WORK` moves
the worktrees and venvs (default `.crapkit/accuracy/retro`), and rows R01 to R12
need `CRAPKIT_RETRO_BUNDLE`, the history bundle their commits live in.

## Change control

Goldens, hand tables, rulings, probes and oracle adapters are locked
(`tests/accuracy/change_control/goldens.lock`). A diff that moves one, or that
touches a module a `calcs.tsv` row names, needs a declared change:

```
python tools/accuracy/change_control.py declare C12 --kind fix --calcs "CRAP score" --reason "..."
```

The kind is `fix`, `definition`, `feature` or `none`. `declare` regenerates the
goldens, judges every moved value against its outside oracle, and stops on a
move the oracle does not support ("crapkit now says 9, radon says 7"). It
records the change in `CHANGES.tsv` and prints the CHANGELOG line the commit
needs. A fix also needs a `bugs.tsv` row and a replayable check.

The rules run in three places: `git-hooks/pre-push` against `origin/main`, CI's
verdict job against `refs/accuracy/green` (the newest main commit whose verdict
passed, so a push that skipped CI is still judged), and the release against the
previous tag.

## Add a check

1. Put the test in its packet's directory. Its expected values come from the
   methods above, never from crapkit's output.
2. Name its file in a row of `tools/accuracy/checks/<packet>.py`, with its
   serial seconds on Ubuntu, and `os_sensitive: True` when the answer can change
   with the OS. The contract fails on a test module no check names, and on a push
   tier whose declared seconds pass 504, naming the five slowest checks.
3. A new calculation gets a `calcs.tsv` row naming its modules, its functions and
   its independent test.
4. Take Hypothesis settings from `accuracy.kit.settings` (`pure` or `process`),
   dated repos from `make_repo`, and the measured small corpus from the
   `small_corpus` fixture.

`tests/accuracy/kit/test_kit_contract.py` holds the rules every packet follows:
no skip or xfail outside a rulings row, no crapkit import in an independent
test's closure, every hand table citing its source, every model citing doc lines
that still hash to their pins.

## Releases

`python tools/release/release.py run accuracy VERSION` runs after the verify
stage: the release tier here, then accuracy.yml's release mode on the tag commit.
Stage 2b publishes only when the local receipt passed at this HEAD and GitHub
holds a successful release run at the tag commit. See
[tools/release/README.md](../tools/release/README.md#the-accuracy-stage).
