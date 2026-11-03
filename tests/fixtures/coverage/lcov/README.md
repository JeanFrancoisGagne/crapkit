# lcov fixture

`tests/e2e/test_lcov_lane_e2e.py` reads these files. They are copies of accuracy check 1's
Rust probe and its recording, so the e2e test runs from a tracefile a real producer wrote.

| File | What it is |
|---|---|
| `rust/Cargo.toml`, `rust/drive.rs`, `rust/shapes.rs` | the probe crate, copied from `tests/accuracy/coverage_oracles/probes/rust/` |
| `cargo-llvm-cov-0.9.1.lcov` | the tracefile for the `call` scenario, copied from `tests/accuracy/coverage_oracles/recorded/cargo-llvm-cov-0.9.1/call.lcov` |

## Producer

- Producer: cargo-llvm-cov 0.9.1 on stable rustc 1.99.1, in `debian:trixie-slim` with Debian's
  llvm 19.1.7.
- Command, run from the probe root: `cargo llvm-cov run --manifest-path rust/Cargo.toml --lcov
  --output-path <out>/artifact.lcov -- call`.
- Stable rustc writes `DA` lines and no `BRDA` arms: branch coverage needs nightly and
  `--branch`.
- cargo-llvm-cov keys each `SF:` record by its absolute path. The recording keeps them relative
  to the probe root (`SF:rust/shapes.rs`), and the e2e test respells them as the case needs:
  as recorded, absolute under the checkout, under `path_prefix`, or from another checkout.

`python tests/accuracy/coverage_oracles/regenerate.py record --producer cargo-llvm-cov-0.9.1`
records the accuracy copy again; copy its `call.lcov` here when it changes.

[WALKTHROUGH.md](WALKTHROUGH.md) takes the same crate from `crapkit init` through
`crapkit worklist`, cc-only first and then with an lcov lane.

## The stanzas init writes commented

`crapkit init` writes one commented `[[lane]]` per toolchain it detects at the repo root
(`toolchain.Stanza`). Each command below is the stanza's command as written, run from the root
of a fresh clone with no `.crapkit/cov/`, and `tests/unit/test_toolchain_table.py` checks that
each stanza command equals the one recorded here.

- Stanza `bun`: `bun test --coverage --coverage-reporter=lcov --coverage-dir=.crapkit/cov/bun`
- Stanza `deno`: `deno test --coverage=.crapkit/cov/deno-raw && deno coverage --lcov --output=.crapkit/cov/deno.lcov .crapkit/cov/deno-raw`
- Stanza `rust`: `cargo llvm-cov --lcov --output-path .crapkit/cov/rust.lcov`

| File | Tool | What it is |
|---|---|---|
| `bun-1.4.4.lcov` | bun 1.4.4 on Windows | the bun stanza over `probes/js/shapes.js` and `probes/ts/shapes.ts`, driven by `probes/harness/drive.mjs` in the call scenario |
| `deno-2.9.8.lcov` | deno 2.9.8 on Windows | the deno stanza over the same probes |
| `cargo-llvm-cov-0.9.1-stanza.lcov` | cargo-llvm-cov 0.9.1, rustc 1.99.1, in `debian:trixie-slim` | the rust stanza over the crate in `rust/`, run through a `crapkit coverage` lane |

The clone held the two probes, `drive.mjs`, and one test file each runner finds: for bun
`harness/probe.test.ts`, which imports both probes and calls `call()` on each inside one
`bun:test` test; for deno `harness/probe_test.ts`, the same in one `Deno.test`. The js probe is
CommonJS, so the deno clone also held `js/package.json` (`{ "type": "commonjs" }`) and a
`deno.json` of `{ "unstable": ["detect-cjs"] }`.

- bun writes each `SF:` key relative to the root, with `\` between directories on Windows. The
  lcov reader reads them as `/`.
- deno writes each `SF:` key as an absolute path. The recording keeps them relative to the clone
  root, the way the cargo-llvm-cov recording does.
- deno 2.9.8's `deno coverage` skipped `ts/shapes.ts` with "Missing transpiled source code", so
  the deno recording holds `drive.mjs` and the js probe only. No deno reading of the ts probe
  exists here, and
  [CROSS-PRODUCER.md](CROSS-PRODUCER.md) compares the ts probe through bun alone.

The rust clone held the crate in `rust/` without its own `[workspace]` line, a root
`Cargo.toml` of `[workspace]` and `members = ["rust"]`, and `rust/tests/call.rs`, one test that
runs the built `drive` binary with `call`.

- In that fresh clone the bare command exits 1 after the tests pass: `failed to create file
  .crapkit/cov/rust.lcov: No such file or directory`. cargo-llvm-cov 0.9.1 does not create the
  output path's directory. The same command run as the uncommented lane exits 0 and writes the
  tracefile, because crapkit creates each declared artifact's directory before the attempt.
- cargo-llvm-cov keys each `SF:` record by its absolute path. The recording keeps them relative
  to the clone root.
- The `rust/shapes.rs` record's `DA` lines equal those of `cargo-llvm-cov-0.9.1.lcov`, which
  `cargo llvm-cov run` wrote for the same `call` scenario.

[WALKTHROUGH-rust-stanza.md](WALKTHROUGH-rust-stanza.md) and [WALKTHROUGH-bun.md](WALKTHROUGH-bun.md)
take a Rust repo and a Bun repo through init, the commented lane and the first measured run.
