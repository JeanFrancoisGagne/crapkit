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

<!-- generated:test-schedule -->
```sh
python tools/testing/run.py
python -m pytest tests/unit -p no:randomly -n 4
python -m pytest tests/e2e -n 8 -p no:randomly
```
<!-- /generated:test-schedule -->

`[tool.pytest.ini_options]` in pyproject.toml sets `testpaths = ["tests"]` and
`addopts = "-q --tb=short -p no:cacheprovider"`. The shared runner runs `tests/unit`
with four workers and `tests/e2e` with eight workers. Use `--unit-workers 1` on the
shared runner to reproduce a unit failure serially. Each worker has its own Python
process and test directories. Both suites disable a globally installed
pytest-randomly plugin.

Add `--coverage` to the shared runner to combine branch coverage, subprocess
measurements, configured test contexts and JUnit results. Every direct run retains its
evidence in a unique `.crapkit/test-runs/run-*` directory and prints the absolute path
before starting. Either suite failing makes the runner fail. Remove old run directories
when their evidence is no longer needed.

`--output DIR` replaces evidence in a caller-managed directory inside `--repo`; relative
paths resolve from that repository. Crapkit's own lane supplies `--output .crapkit/cov`
while it owns those measurement artifacts, and the CI verdict driver uses that location
in its private checkout. A direct run should keep the default destination so it cannot
change an active lane's evidence.

`python tools/docs/generate.py` updates the marked version and command facts and
the editor schema. CI checks these generated sections through the unit suite.

`tests/unit` covers pure seams, including `cli/verifying.py` and `cli/scoring.py`, which it
drives in process rather than through a subprocess. `tests/e2e` drives `python -m crapkit`
against real git repos in tmp dirs and asserts through the CLI only.

### Test isolation

`test_init_probe.py` and `test_doctor_results_artifact.py` clear the memoized runner
probe before and after each test. Fake interpreters on PATH therefore do not leave
cached answers for later tests. Investigate doctor failures under a changed test order;
the former cache leak is fixed.

### The e2e CLI runner

`tests/e2e/conftest.py` holds the one way e2e spawns the CLI. Before it, 42 copies of the
same four-line `subprocess.run` lived in the test files, 23 of them different, and nothing
said which differences were deliberate. A file binds its own contract once at the top:

```python
run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})
```

The defaults are the plainest child: 120 s, platform decoding, the inherited environment.
A test that needs otherwise says so in that call. The child inherits the parent's
package selection: `PYTHONPATH` can select a development checkout, while isolated CI
uses its environment's verified wheel. Each Git command injects its test identity,
so no global Git configuration is required.

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
in the baseline fails now. CI also runs the event-base hook and a complete verdict
against separate base and candidate wheel installations.

**In CI** (`.github/workflows/ci.yml`), four jobs:

| Job | Runs | What fails the job |
|---|---|---|
| `test` | Editable dev install, console-script check and `python tools/testing/run.py` on Python 3.11, 3.12 and 3.13 on Ubuntu and Windows. Ubuntu/Python 3.12 also runs `hook-precommit --base "$BASE_REF"`. | A test failure or event-base complexity breach. |
| `verdict` | `python tools/testing/ci.py --base "$BASE_REF"` builds and verifies separate base/candidate wheels, measures both suites, transfers the complete baseline ledger and runs `verify --no-tighten`. | A candidate suite failure, incomplete evidence from either revision, a refused measurement or a failing CRAP verdict. |
| `plugin` | `claude plugin validate plugin --strict` and `claude plugin validate .` check the plugin, hooks, skills and marketplace manifests. | A validation error. |
| `dogfood` | The repository's composite action runs `coverage`, `verify --json` and `worklist --top 5` on Crapkit. | Action execution errors. Its `gate: false` setting leaves score enforcement to `verdict`. |

The verdict job checks installed source bytes before mapping coverage paths and
compares its JSON verdict with the actual run ledger. Its evidence is uploaded
from `.crapkit/ci-verdict`. Repository branch protection controls which checks
are required for merging.

An older baseline can have existing test failures. CI keeps its exit code,
failed test IDs and counts, and requires complete JUnit evidence from both
revisions before scoring. The candidate must pass its suites and the real CRAP
verdict. Missing, empty or unfinished baseline evidence refuses the comparison;
an existing baseline failure is never rewritten as a passing test.

## Adding a language

Two cases, and the first one is most of them.

**lizard already reads it.** Admit the label in three places, then
prove it:

| File | Change |
|---|---|
| `src/crapkit/config_contract.py` | add the label to the scope languages enum; `SUPPORTED_LANGUAGES` derives from it |
| `src/crapkit/universe.py` | add its suffixes to `LANGUAGE_EXTENSIONS` |
| `src/crapkit/_pygdefer.py` | name it in the module docstring's list, which a test pins to the language set |

Run `python tools/docs/generate.py` to update the editor schema. Then regenerate
`plugin/hooks/hooks.json` from `LANGUAGE_EXTENSIONS` (a test rebuilds it
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
