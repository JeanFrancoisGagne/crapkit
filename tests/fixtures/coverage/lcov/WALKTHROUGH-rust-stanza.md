# Walkthrough: a Rust repo, before and after init writes the commented lane

This walkthrough takes a Rust repo from `crapkit init` to a measured scope twice: once with
crapkit before init learned the commented lanes, once after.
`tests/e2e/test_cc_only_reader_note_e2e.py` runs the same steps with the recorded tracefile in
place of cargo.

The repo holds the crate in `rust/` (this folder's `rust/` files, less the crate's own
`[workspace]` line), a root `Cargo.toml` of `[workspace]` and `members = ["rust"]`, and
`rust/tests/call.rs`, one test that runs `drive call`. The output below ran in a fresh clone
under `/home/user/repo`, in `debian:trixie-slim` with rustc 1.99.1, cargo-llvm-cov 0.9.1 and
Python 3.13.5. The crate's functions sit at ccn 3 or less, so the walkthrough sets `target = 2`
after init, to give the cc-only seed a mark. Run ids and shas differ on another machine.

## Before: no stanza, no note

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): rust
no coverage parser reads this repo's languages, so every scope is cc-only (coverage_optional = true) and needs no lane - next: run `crapkit coverage`
added to .gitignore: .crapkit/
```

The config holds the scope and no lane, commented or not:

```toml
[[scope]]
name = "rust"
paths = ["rust"]
languages = ["rust"]
coverage_optional = true
```

```
$ crapkit doctor
...
ok   scope 'rust': 2 files
ok   every tracked source file belongs to a scope
ok   no [[lane]] declared: every scope is cc-only, so none is needed
ok   lizard 1.24.0
doctor: no problems found
$ crapkit coverage
run 1 @ fa0c44788b8: 9 functions scored: 9 cc-only, 1 over ceiling 2, CRAP load 16.0, grade D
-> next: crapkit worklist
$ crapkit worklist
worklist @ fa0c44788b8 (run 1, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk      3.0  ccn   3  crap     3.0  cov    -    1c/1a  rust/drive.rs:17  main
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (fa0c44788b8)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ 436eb55007c vs baseline fa0c44788b8 (3 changed files)
  changed files: .gitignore, crapkit-ratchet.tsv, crapkit.toml
```

Nothing in init's output, the config or doctor says a reader exists for Rust, so the scope
stays cc-only until someone finds the lcov recipe on their own.

## After: the stanza, the note and the flip

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): rust
every scope is cc-only (coverage_optional = true) and needs no lane; init left a commented lane for rust (cargo llvm-cov): uncomment it to measure, see docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one - next: run `crapkit coverage`
added to .gitignore: .crapkit/
```

The scope keeps `coverage_optional = true`, and the root `Cargo.toml` gives it a commented lane:

```toml
[[scope]]
name = "rust"
paths = ["rust"]
languages = ["rust"]
coverage_optional = true

# Lanes for the toolchains this repo uses: uncomment one and delete coverage_optional from its scopes to measure them (docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one).
# [[lane]]
# name = "rust"
# command = "cargo llvm-cov --lcov --output-path .crapkit/cov/rust.lcov"
# artifact = ".crapkit/cov/rust.lcov"
# parser = "lcov"
# scopes = ["rust"]
```

```
$ crapkit doctor
...
ok   no [[lane]] declared: every scope is cc-only, so none is needed
note scope 'rust' is cc-only, and a reader can measure rust through parser = "lcov" from cargo llvm-cov; adding that lane and deleting coverage_optional measures it (docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one)
ok   lizard 1.24.0
doctor: no problems found
$ crapkit coverage
run 1 @ 7e6532e91df: 9 functions scored: 9 cc-only, 1 over ceiling 2, CRAP load 16.0, grade D
-> next: crapkit worklist
$ crapkit worklist
worklist @ 7e6532e91df (run 1, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk      3.0  ccn   3  crap     3.0  cov    -    1c/1a  rust/drive.rs:17  main
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (7e6532e91df)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ dd6a61f5c2b vs baseline 7e6532e91df (3 changed files)
  changed files: .gitignore, crapkit-ratchet.tsv, crapkit.toml
```

The note's step: delete the `# ` in front of the six stanza lines and delete
`coverage_optional = true`. The lane runs in this clone, which has no `.crapkit/cov/`: crapkit
creates the artifact's directory before the attempt, since cargo-llvm-cov does not.

```
$ crapkit coverage
run 3 @ dd6a61f5c2b: 9 functions scored: 9 measured, 6 over ceiling 2, CRAP load 20.42, grade F
-> next: crapkit worklist
$ crapkit verify
crapkit: ratchet marks were recorded under [crapkit-stamp=2 lizard=1.24.0] in crapkit-ratchet.tsv, and the rules have moved since in 1 scope(s): rust (coverage cc-only to lcov=1) - CRAP scores are not comparable across metric versions; run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline with `crapkit ratchet seed --scope rust`; rust was cc-only and is now measured by its lcov lane(s), so its marks hold no coverage yet: follow docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one
[exit 3]
$ crapkit ratchet seed --scope rust
crapkit-ratchet.tsv: scope rust: added 5, tightened 0, raised 1 (first rust/drive.rs main 3.0000 -> 3.0123) - 6 mark(s) vs run 3 (dd6a61f5c2b)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ 75735dcb221 vs baseline dd6a61f5c2b (3 changed files)
  changed files: Cargo.lock, crapkit-ratchet.tsv, crapkit.toml
```

verify names the flip once for the scope, not once per mark. The scoped seed replaces the
scope's marks with the measured run's values and counts the one mark that rose: `main` held
`crap = ccn = 3` while cc-only, and line 23 of `drive.rs` never runs in the `call` test, so its
measured crap is 3.0123. Run 2 is the run the first `verify` wrote.
