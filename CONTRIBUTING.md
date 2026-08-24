# Contributing

## Setup

```
git clone https://github.com/JeanFrancoisGagne/crapkit
cd crapkit
pip install -e ".[dev]"
pip install pytest-xdist
git config core.hooksPath git-hooks
python -m pytest -q
```

Quote `".[dev]"`: zsh globs the bare form and the install fails before pip sees it.

`pytest-xdist` is not optional. `tests/fixtures/mini_repo` declares a lane that shells out
to `pytest ... -n 2`, and without xdist that subprocess dies on an unrecognized `-n`,
failing the e2e tests that assert the lane exited 0. The dev extra ships `pytest` and
`pytest-cov` only.

`core.hooksPath` arms the complexity gate below. Without it your commits pass locally and
get rejected in review.

## The rules the repo holds itself to

- **The complexity gate is real.** Every function you add or touch must sit at
  cyclomatic complexity 6 or lower (comprehension `for`/`if` clauses, ternaries,
  and `and`/`or` all count). The pre-commit hook (`python -m crapkit
  hook-precommit`) blocks anything above it. Decompose; never widen the gate.
- **Tests first.** A behavior change starts with the failing test that proves
  it: unit tests in `tests/unit/` for the pure core, e2e tests in `tests/e2e/`
  that drive `python -m crapkit` against a throwaway git repo in `tmp_path`.
- **Determinism is the product.** Identical inputs must produce byte-identical
  outputs: sorted-keys JSON, no wall-clock values in scored data, no network at
  analysis time.
- crapkit scores itself: `crapkit.toml` and `crapkit-ratchet.tsv` at the repo
  root are live. `crapkit verify` must stay green on your branch.

## Running crapkit on crapkit

```
python -m crapkit coverage
python -m crapkit worklist
python -m crapkit verify
```
