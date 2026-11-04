# Walkthrough: a Rust crate, cc-only and then measured through lcov

This walkthrough takes the crate in `rust/` from `crapkit init` through `crapkit worklist`
twice: first cc-only, where crap equals ccn, and then with a hand-declared lcov lane, where
crap comes from coverage. The output below is what crapkit 0.9.0 printed for this fixture;
run ids, commit shas and timings differ on another machine.

The repo holds the crate under `rust/` and nothing else crapkit scores. The crate is small, so
this walkthrough lowers `target` from 6 to 5 to put one function over the ceiling.

## Before: cc-only

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): rust
every scope is cc-only (coverage_optional = true) and needs no lane - next: run `crapkit coverage`
```

init writes no lane for Rust. The scope it writes:

```toml
[crapkit]
target = 5

[[scope]]
name = "rust"
paths = ["rust"]
languages = ["rust"]
coverage_optional = true
```

```
$ crapkit coverage
run 1 @ cca3e57a99f: 9 functions scored: 9 cc-only, 0 over ceiling 5, CRAP load 16.0, grade A+
-> next: crapkit worklist
$ crapkit worklist
worklist @ cca3e57a99f (run 1, floor ccn>=5, churn 12mo) - 0 of 0 active (worklist_top 50), 0 dormant
```

With no coverage, each function's crap is its ccn. No function in the crate has a ccn over 5,
so the worklist is empty, whether a test runs `untouched` or not.

## After: an lcov lane

Declare a lane whose command writes an lcov tracefile, set `parser = "lcov"`, and drop
`coverage_optional = true` from the scope, since the scope is measured now:

```toml
[[lane]]
name = "rust"
command = "cargo llvm-cov --lcov --output-path target/lcov.info"
artifact = "target/lcov.info"
parser = "lcov"
scopes = ["rust"]
```

The output below came from the recorded tracefile in this folder, which cargo-llvm-cov 0.9.1
wrote for the probe's `call` run ([README.md](README.md) names the command): the lane here
copied that file to `target/lcov.info`.

```
$ crapkit coverage
run 2 @ cca3e57a99f: 9 functions scored: 9 measured, 1 over ceiling 5, CRAP load 20.42, grade D
-> next: crapkit worklist
$ crapkit worklist
worklist @ cca3e57a99f (run 2, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk      2.0  ccn   2  crap     6.0  cov   0%    1c/1a  rust/shapes.rs:32  untouched x : i32
$ crapkit brief rust/shapes.rs untouched
rust/shapes.rs:32  untouched x : i32
  ccn 2 (cognitive 1)  cov 0%  crap 6.0 vs ceiling 5  -> add-tests
  mark none  churn 1 commits / 1 authors
  uncovered lines: 32, 33, 34, 35, 36, 37
  file: 7 function(s), 1 over ceiling, crap load 16.41
  gate ceiling 5  lane rust  ->  crapkit rescore rust/shapes.rs --gate
```

`untouched` never runs in the `call` run, so its cov is 0% and its crap rises from 2 to 6,
over the ceiling: the remedy is `add-tests`, and brief lists the `DA` lines with count 0.

A scope whose marks were seeded while it was cc-only holds `crap = ccn` marks. Its first
`verify` after the lane lands names the flip once for the scope, and
`crapkit ratchet seed --scope rust` reseeds it.
