# Walkthrough: a C++ CMake repo, before and after the gcovr lane

This walkthrough takes a C++ repo built with CMake from `crapkit init` to a measured scope
twice: once with crapkit before it read Cobertura, once after. `tests/e2e/test_cobertura_lane_e2e.py`
runs the measured half with the committed `gcovr-7.2-stanza.xml` in place of the build.

The repo is this folder's `cmake-repo/` plus a `.gitignore` of `build/`, committed once. The
output below ran in a fresh clone under `/home/user/repo`, in `debian:trixie-slim` with g++
14.2.0, CMake 3.31.6, gcovr 7.2 and Python 3.13.5. `grade` sits at ccn 7, over the default
ceiling of 6, so the cc-only seed gives it a mark. Run ids and shas differ on another machine.

## Before: no stanza, no note

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): src
every scope is cc-only (coverage_optional = true) and needs no lane - next: run `crapkit coverage`
added to .gitignore: .crapkit/
```

The config holds the scope and no lane, commented or not:

```toml
[[scope]]
name = "src"
paths = ["src"]
languages = ["cpp"]
coverage_optional = true
```

```
$ crapkit doctor
...
ok   scope 'src': 2 files
ok   every tracked source file belongs to a scope
ok   no [[lane]] declared: every scope is cc-only, so none is needed
ok   lizard 1.24.0
doctor: no problems found
$ crapkit coverage
run 1 @ 2e3fff7250f: 2 functions scored: 2 cc-only, 1 over ceiling 6, CRAP load 10.0, grade F
-> next: crapkit worklist
$ crapkit worklist
worklist @ 2e3fff7250f (run 1, floor ccn>=5, churn 12mo) - 2 of 2 active (worklist_top 50), 0 dormant
  risk      7.0  ccn   7  crap     7.0  cov    -    1c/1a  src/shapes.cpp:13  grade( int score)
  risk      3.0  ccn   3  crap     3.0  cov    -    1c/1a  src/shapes.cpp:3  clamp( int value , int low , int high)  ok
no crapkit-ratchet.tsv yet: seed marks each function over its ceiling at today's score, and from then on a mark may only fall
-> next: crapkit ratchet seed
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (2e3fff7250f)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ 4cd2dbfb70f vs baseline 2e3fff7250f (1 changed files)
  changed files: crapkit-ratchet.tsv
```

Nothing in init's output, the config or doctor says crapkit can read gcovr's report, so the
scope stays cc-only.

## After: the stanza, the note and the flip

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): src
every scope is cc-only (coverage_optional = true) and needs no lane; init left a commented lane for cpp (gcovr): uncomment it to measure, see docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one - next: run `crapkit coverage`
added to .gitignore: .crapkit/
```

The scope keeps `coverage_optional = true`, and the root `CMakeLists.txt` gives it a commented
gcovr lane:

```toml
# Lanes for the toolchains this repo uses: uncomment one and delete coverage_optional from its scopes to measure them (docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one).
# [[lane]]
# name = "cpp"
# command = "cmake -S . -B build/crapkit-cov -DCMAKE_C_FLAGS=--coverage -DCMAKE_CXX_FLAGS=--coverage && cmake --build build/crapkit-cov && ctest --test-dir build/crapkit-cov && gcovr --root . --cobertura .crapkit/cov/cpp.xml"
# artifact = ".crapkit/cov/cpp.xml"
# parser = "cobertura"
# scopes = ["src"]
```

doctor adds one note. (The machine that ran this held both crapkit versions on PATH, and
doctor's line about the two launchers is cut here.)

```
$ crapkit doctor
...
ok   no [[lane]] declared: every scope is cc-only, so none is needed
note scope 'src' is cc-only, and a reader can measure cpp through parser = "cobertura" from gcovr; adding that lane and deleting coverage_optional measures it (docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one)
ok   lizard 1.24.0
doctor: no problems found
$ crapkit coverage
run 1 @ 2edfece5716: 2 functions scored: 2 cc-only, 1 over ceiling 6, CRAP load 10.0, grade F
-> next: crapkit worklist
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (2edfece5716)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ a0c1385476e vs baseline 2edfece5716 (1 changed files)
  changed files: crapkit-ratchet.tsv
```

The note's step: delete the `# ` in front of the six stanza lines and delete
`coverage_optional = true`, then commit. The lane configures a coverage build, runs ctest and
writes the report; crapkit creates `.crapkit/cov/` before the attempt, since gcovr 7.2 does not.

```
$ crapkit coverage
run 3 @ bfe905151db: 2 functions scored: 2 measured, 1 over ceiling 6, CRAP load 59.14, grade F
-> next: crapkit worklist
$ crapkit worklist
worklist @ bfe905151db (run 3, floor ccn>=5, churn 12mo) - 1 of 1 active (worklist_top 50), 0 dormant
  risk      7.0  ccn   7  crap    56.0  cov   0%    1c/1a  src/shapes.cpp:13  grade( int score)
-> next: crapkit next-item
$ crapkit brief src/shapes.cpp clamp
src/shapes.cpp:3  clamp( int value , int low , int high)
  ccn 3 (cognitive 2)  cov 75%  crap 3.1 vs ceiling 6  -> ok
  mark none  churn 1 commits / 1 authors
  uncovered lines: 8
  file: 2 function(s), 1 over ceiling, crap load 59.14
  gate ceiling 6  lane cpp  ->  crapkit rescore src/shapes.cpp --gate
$ crapkit verify
crapkit: ratchet marks were recorded under [crapkit-stamp=2 lizard=1.24.0] in crapkit-ratchet.tsv, and the rules have moved since in 1 scope(s): src (coverage cc-only to cobertura=1) - CRAP scores are not comparable across metric versions; run `crapkit coverage`, then `crapkit ratchet prune`, then re-baseline with `crapkit ratchet seed --scope src`; src was cc-only and is now measured by its cobertura lane(s), so its marks hold no coverage yet: follow docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one
[exit 3]
$ crapkit ratchet seed --scope src
crapkit-ratchet.tsv: scope src: added 0, tightened 0, raised 1 (first src/shapes.cpp grade( int score) 7.0000 -> 56.0000) - 1 mark(s) vs run 3 (bfe905151db)
-> next: commit crapkit-ratchet.tsv, then run `crapkit verify`
$ crapkit verify
verify OK @ 19bb6cdd69b vs baseline bfe905151db (1 changed files)
  changed files: crapkit-ratchet.tsv
```

`grade` never runs under the one test, so its crap is ccn² + ccn = 56 and the scoped seed
raises its mark from the cc-only 7.0 to 56.0. `clamp` runs two of its three paths: gcov counts
its conditions, so its cov is branch coverage, and line 8 is the one line no test ran. verify
names the flip once for the scope, and run 2 is the run the first `verify` wrote.
