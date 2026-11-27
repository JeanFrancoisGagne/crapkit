# Cobertura fixtures

Recorded Cobertura XML reports the `cobertura` reader reads in the unit and
e2e tests. Each was written by the producer named below, at the version named,
with the command shown, and committed as it came out except for the
`timestamp` attribute, set to `0`.

- `gcovr-7.2-stanza.xml`: gcovr 7.2, g++ 14.2.0 and CMake 3.31.6 from Debian
  trixie (`debian:trixie-slim`), over `cmake-repo/`. The command is the gcovr
  stanza `crapkit init` writes for a repo with a root `CMakeLists.txt`, run
  from the repo root after `mkdir -p .crapkit/cov` (a lane creates its
  artifact's directory before the attempt; gcovr 7.2 does not, and exits 2
  naming the output file).
- Stanza `cpp`: `cmake -S . -B build/crapkit-cov -DCMAKE_C_FLAGS=--coverage -DCMAKE_CXX_FLAGS=--coverage && cmake --build build/crapkit-cov && ctest --test-dir build/crapkit-cov && gcovr --root . --cobertura .crapkit/cov/cpp.xml`

gcovr writes `<source>.</source>` and each filename relative to `--root`, so
the reader keys `src/shapes.cpp` with no path_prefix. It also measures what
the build compiled outside the sources: the report holds CMake's
`CMakeCXXCompilerId.cpp` under `build/crapkit-cov/` and the test file, which
no scope claims.

`cmake-repo/` is the C++ fixture repo: `clamp` runs on two of its three paths
under its one test, and `grade` (ccn 7) never runs.

The probe recordings under `tests/accuracy/coverage_oracles/recorded/gcovr-7.2/`
came from the same container: `g++ --coverage -O0 -o drive cpp/drive.cpp
cpp/shapes.cpp`, `./drive <scenario>`, then `gcovr --root . --filter
cpp/shapes.cpp --cobertura <scenario>.xml --lcov <scenario>.lcov` from the
probe root. `regenerate.py`'s `gcovr-7.2` producer runs the same commands, and
its `check` and `record` in a fresh container give the committed bytes.

- `nyc-18.0.0-call.xml` and `nyc-18.0.0-idle.xml`: istanbul's cobertura
  reporter through nyc 18.0.0 on Node 20.19.2, in the same image, over the JS
  probe (`tests/accuracy/coverage_oracles/probes/js` and `probes/harness`)
  copied to `/home/user/repo`: `node node_modules/nyc/bin/nyc.js
  --reporter=cobertura --report-dir=<out> --temp-dir=<tmp> --include=js/shapes.js
  node harness/run-node.cjs <scenario>`. Its one `<source>` is that absolute
  project root, and each function's first line sits under `<methods>` as well
  as under the class's `<lines>`; the tests put the checkout at that root.

`WALKTHROUGH.md` takes `cmake-repo/` from `crapkit init` to a measured scope,
before and after crapkit read Cobertura.

Producers crapkit reads through `parser = "cobertura"` with no recorded
fixture here: kcov (shell) and cargo-tarpaulin.
