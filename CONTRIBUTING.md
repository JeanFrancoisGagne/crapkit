# Contributing

## Setup

```
git clone https://github.com/JeanFrancoisGagne/crapkit
cd crapkit
pip install -e ".[dev]"
pip install pytest-xdist
git config core.hooksPath git-hooks
python -m pytest
```

Quote `".[dev]"`: zsh globs the bare form and the install fails before pip sees it.

`pytest-xdist` is not optional. `tests/fixtures/mini_repo` declares a lane that shells out
to `pytest ... -n 2`, and without xdist that subprocess dies on an unrecognized `-n`,
failing the e2e tests that assert the lane exited 0. The dev extra ships it, so the
`pip install pytest-xdist` line is a no-op after the extra and the fix for every other
install route.

`core.hooksPath` arms the complexity gate on your own commits. Without it your commits
pass locally and get rejected in review.

## Tests

```
python -m pytest                   # both suites, serially
python -m pytest tests/unit -n 8   # 2,280 tests, 19 s (1m15 serially)
python -m pytest tests/e2e -n 8    # 595 tests, about 1m30 (2m on Windows)
```

`[tool.pytest.ini_options]` in pyproject.toml sets `testpaths = ["tests"]` and
`addopts = "-q --tb=short -p no:cacheprovider"`. Nothing else, so a bare run is serial and
`-n 8` is yours to add. Add it while you work and drop it (`-n 0`) to isolate a failure:
xdist reorders, which hides which test left the state behind. With pytest-randomly
installed globally, add `-p no:randomly` to pin the order too.

Both halves parallelize because every test owns its own tmp dir. The e2e half spawns
`python -m crapkit` against a real git repo per test, which is wall clock nobody's CPU is
using.

`tests/unit` covers pure seams, including `cli/verifying.py` and `cli/scoring.py`, which it
drives in process rather than through a subprocess. `tests/e2e` drives `python -m crapkit`
against real git repos in tmp dirs and asserts through the CLI only.

### One red run that is not your diff

pytest-randomly is not in the dev extra, so CI gets collection order, which is the order
that passes. Installed globally it shuffles every run, and two files then disagree:
`test_init_probe.py` puts fake interpreters on PATH, and doctor's runner probe memoizes
its answer per word (`_start_probe` in `src/crapkit/cli/admin.py`, an `lru_cache` keyed on
the word alone). Once a shim has answered for `python`, every later probe in that process
reads the shim's exit 9009, and doctor FAILs a lane it would have passed:

```
$ python -m pytest tests/unit/test_init_probe.py tests/unit/test_doctor_results_artifact.py -p no:randomly -n 0
FAILED tests/unit/test_doctor_results_artifact.py::test_a_pytest_lane_without_a_results_file_warns_and_names_what_is_off
FAILED tests/unit/test_doctor_results_artifact.py::test_a_lane_that_declares_one_is_left_alone
FAILED tests/unit/test_doctor_results_artifact.py::test_one_line_per_lane_missing_it
3 failed, 60 passed in 12.36s
```

That file passes whole on its own, and `python -m pytest -q -n 0 -p no:randomly tests/unit`
exits 0 in about 1m15, because collection order puts the probe file after it. A red run
naming `doctor` or the probe, on lanes your diff never touched, is this and not you.
Rerun with `-p no:randomly` before you go hunting. The fix is a `cache_clear()` in the
probe file's own teardown, which one test in it already calls.

### The e2e CLI runner

`tests/e2e/conftest.py` holds the one way e2e spawns the CLI. Before it, 42 copies of the
same four-line `subprocess.run` lived in the test files, 23 of them different, and nothing
said which differences were deliberate. A file binds its own contract once at the top:

```python
run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})
```

The defaults are the plainest child: 120 s, platform decoding, the inherited environment.
A test that needs otherwise says so in that call. Two things are not negotiable. The child
inherits `PYTHONPATH`, which is what makes the suite test the working tree instead of an
installed crapkit, and each command injects its own git identity, so no global git config
is required.

## The rules the repo holds itself to

- **The complexity gate is real.** Every function you add or touch must sit at ccn 6 or
  lower, the `target = 6` in this repo's own `crapkit.toml`. Comprehension `for`/`if`
  clauses, ternaries, and `and`/`or` all count. `git-hooks/pre-commit` runs
  `python -m crapkit hook-precommit` over your staged blobs, which exits 6 on a breach and
  turns into a git exit 1. Decompose; never widen the gate. A refusal is design feedback.
- **Tests first.** A behavior change starts with the failing test that proves it: unit
  tests in `tests/unit/` for the pure core, e2e tests in `tests/e2e/` that drive
  `python -m crapkit` against a throwaway git repo in `tmp_path`.
- **Determinism is the product.** Identical inputs produce byte-identical outputs:
  sorted-keys JSON, no wall-clock values in scored data, no network at analysis time.
- **The docs are pinned to the code.** Rename a subcommand or reword a message and you
  update the page in the same commit. Eight tests hold that line, all in `tests/unit`:

| Test | What it pins |
|---|---|
| `test_cli_docs_contract.py` | README's `## Subcommands` table against the argparse parser, both directions, plus the flags the packet rows promise |
| `test_docs_claims_contract.py` | quoted transcripts against the strings the code emits, the lane examples against the config loader, the setup steps AGENTS.md calls mandatory against CONTRIBUTING |
| `test_fresh_user_docs_contract.py` | what a first-time reader copies: the doctor transcript, the `next-item` payload shape, the pinned `rev`, the vitest provider install |
| `test_handle_docs_contract.py` | the `handle` form, the promoted packet fields and the refresh contract across the three agent pages |
| `test_skills_contract.py` | the plugin's skills against the parser and against the modules that print the refusals they quote |
| `test_ci_install_contract.py` | the `dev` extra against the pytest plugins the fixture lanes spell |
| `test_precommit_contract.py` | `.pre-commit-hooks.yaml` at the repo root against the console script it names |
| `test_schema_contract.py` | `crapkit.schema.json` against doctor's known-key sets, one vocabulary in two views |

  A contract that pins a sentence you have to change is not a wall: change the test in the
  same commit and say in the message why the old sentence stopped being true.
- **crapkit scores itself.** `crapkit.toml` and `crapkit-ratchet.tsv` at the repo root are
  live, and `crapkit verify` must stay green on your branch.

## Running crapkit on crapkit

```
python -m crapkit coverage
python -m crapkit worklist
python -m crapkit verify
```

## How a change gets reviewed

Two gates, and the first one is yours.

**Before you push.** `git-hooks/pre-commit` refuses the commit on a staged function over
ccn 6. Then `python -m crapkit verify` on the branch: it reruns the lane, gates the
functions your diff touched, and checks that no ratchet mark rose and no test that passed
in the baseline fails now. The hook runs again in CI. The verify does not, so this is the
only place its verdict stops anything.

**In CI** (`.github/workflows/ci.yml`), three jobs:

| Job | Runs | Blocks the PR |
|---|---|---|
| `test` | `pip install -e ".[dev]"`, `crapkit --version`, `python -m pytest tests/unit`, `python -m pytest -n 8 tests/e2e`, then `python -m crapkit hook-precommit`, on six matrix legs (Python 3.11, 3.12, 3.13 on ubuntu and windows) | yes, the gate's exit code stands |
| `plugin` | two calls over two files: `claude plugin validate plugin --strict`, which schema-checks `plugin/.claude-plugin/plugin.json`, its `hooks.json` and the skill frontmatter no Python test can reach, then `claude plugin validate .`, which reads the repo-root `.claude-plugin/marketplace.json` a `claude plugin marketplace add` fetches and nothing else checks | yes |
| `dogfood` | `coverage`, `verify --json`, `worklist --top 5` on crapkit itself | no: the `verify` line ends in `\|\| true`, so a rising mark or a dark diff line is reported for a human to read, not enforced |

That last row is why the verify above matters. Nothing downstream fails the PR for you.

## Adding a language

Two cases, and the first one is most of them.

**lizard already reads it.** Nothing new to write. Admit the label in four places, then
prove it:

| File | Change |
|---|---|
| `src/crapkit/config.py` | add the label to `SUPPORTED_LANGUAGES` |
| `src/crapkit/universe.py` | add its suffixes to `LANGUAGE_EXTENSIONS` |
| `src/crapkit/_pygdefer.py` | name it in the module docstring's list, which a test pins to the language set |
| `crapkit.schema.json` | add it to the `languages` enum |

Then regenerate `plugin/hooks/hooks.json` from `LANGUAGE_EXTENSIONS` (a test rebuilds it
and diffs), and name the language in the README intro and the handbook standfirst, both
pinned to the same set. Bump `ANALYSIS_VERSION` in `analyze.py` so existing stores
re-analyze. Coverage joins only where a parser exists, so a new language's scopes declare
`coverage_optional = true` until one does.

`mutate.py` needs nothing unless the language spells its operators differently. Anything
unnamed there falls through to the C-family table, which is right for Swift, Go, Vue, Zig,
C, C++, Objective-C and Java. Add an entry only for a real difference, and add a
`UNMUTABLE` reason when the operators mean something else entirely, as `<` and `>` do in
shell and PowerShell.

**lizard reads it wrong, or not at all.** Write the reader. Three exist as the pattern:

| Module | Why it exists |
|---|---|
| `lizardrust.py` | lizard's Rust reader counts a `match` block once no matter how many arms it has (lizard #494). This one counts each non-wildcard arm, and retires itself the day upstream fixes it. |
| `lizardshell.py` | lizard ships no shell reader, and answers `.sh` with `CLikeReader` rather than a failure, so the numbers were plausible and wrong. |
| `lizardpowershell.py` | same for `.ps1` and `.psm1`, plus a cp1252 decode fallback. |

Register it inside the `deferred_pygments()` block at the top of `analyze.py`, beside the
other three. That module scope is what a `ProcessPoolExecutor` child imports; register
anywhere else and spawned workers measure with the readers lizard shipped and report
plausible wrong numbers.

Every reader lands with a hand-counted probe battery: real files, a human-counted expected
ccn per function, and one test per parsing hazard the language has (heredocs, here-strings,
nested quotes, comment forms). Language docstrings state the ccn convention explicitly,
because a convention nobody wrote down is a number nobody can check.
