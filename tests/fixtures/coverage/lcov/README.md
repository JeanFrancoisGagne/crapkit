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
