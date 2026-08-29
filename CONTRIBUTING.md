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
failing the e2e tests that assert the lane exited 0. The dev extra ships `pytest` and
`pytest-cov` only.

`core.hooksPath` arms the complexity gate on your own commits. Without it your commits
pass locally and get rejected in review.

## Tests

```
python -m pytest                # both suites
python -m pytest tests/unit     # 1,719 tests, ~30s
python -m pytest tests/e2e      # 481 tests, ~6m
```

`[tool.pytest.ini_options]` in pyproject.toml sets `testpaths = ["tests"]` and
`addopts = "-q --tb=short -p no:cacheprovider"`. Nothing else. The suite runs serially, so
no `-n 0` is needed to isolate a failure. With pytest-randomly installed globally, add
`-p no:randomly` to pin the order.

`tests/unit` covers pure seams. `tests/e2e` drives `python -m crapkit` against real git
repos in tmp dirs and asserts through the CLI only. Each e2e command injects its own git
identity, so no global git config is required.

## The rules the repo holds itself to

- **The complexity gate is real.** Every function you add or touch must sit at ccn 6 or
  lower. Comprehension `for`/`if` clauses, ternaries, and `and`/`or` all count. The
  pre-commit hook runs `python -m crapkit hook-precommit` over your staged blobs and exits
  6 on a breach. Decompose; never widen the gate. A refusal is design feedback.
- **Tests first.** A behavior change starts with the failing test that proves it: unit
  tests in `tests/unit/` for the pure core, e2e tests in `tests/e2e/` that drive
  `python -m crapkit` against a throwaway git repo in `tmp_path`.
- **Determinism is the product.** Identical inputs produce byte-identical outputs:
  sorted-keys JSON, no wall-clock values in scored data, no network at analysis time.
- **The docs are pinned to the code.** `tests/unit/test_cli_docs_contract.py` diffs
  README's `## Subcommands` table against the argparse parser in both directions, and
  `test_docs_claims_contract.py` compares quoted transcripts against the strings the code
  emits. Rename a subcommand or reword a message and you update the page in the same
  commit.
- **crapkit scores itself.** `crapkit.toml` and `crapkit-ratchet.tsv` at the repo root are
  live, and `crapkit verify` must stay green on your branch.

## Running crapkit on crapkit

```
python -m crapkit coverage
python -m crapkit worklist
python -m crapkit verify
```

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
