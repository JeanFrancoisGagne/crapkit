# Crapkit architecture improvements

All 18 improvements are implemented. Normal configured verification and the
final clean-wheel comparison passed at `802257c`. Each candidate run passed
4,403 tests with three skips; both actual verify ledgers record zero findings.

The user authorized the improvements on 2026-09-06. The review and clean-wheel
comparison use starting commit `47629ea14ddea38e55ce2332d08297c31888acb9`.
Candidate evidence remains in `../2026-09-06-post-implementation/`. External
release publication is outside this implementation task.

Repository source:
`C:\Users\jfgag\crapkit\docs\architecture\2026-09-06-improvements\implementation.md`.

## Integration record

| Candidate | Implemented behavior | Integrated commit |
| --- | --- | --- |
| AN1 | Workers hash and parse the same bytes; changed or missing input refuses | `d3a2137` |
| AN2 | Valid UTF-8 Git paths preserve whitespace, control characters and Unicode separators | `d3a2137` |
| AN3 | Incomplete coverage members frame new chunks before another decode | `d3a2137` |
| AN4 | Cold identical inputs share one parse per reader identity | `d3a2137` |
| EX1 | Automatic reuse requires the same clean Git worktree, including inputs above a nested configuration; settings, environment and artifact bytes must match. Line display keeps its scoped source-freshness rule | `401ebfb`, `6ab8c12`, `ac95ae7` |
| EX2 | Init uses the same scope ownership rule as scoring | `f771c46` |
| EX3 | Measurement ownership lasts through command cleanup, parsing and stamp publication; default direct test runs retain separate output | `39f610c`, `6e01dde` |
| ST1 | Pruning and override audit insertion serialize on the same write transaction | `e064046` |
| ST2 | Packets and reports replay one mark-age history rule | `e064046` |
| ST3 | Optional ratchet reads defer whole-run rows until identity admission needs them | `e064046` |
| ST4 | Ratchet writers compare captured bytes before atomic replacement | `e064046` |
| ST5 | Trend metadata and totals come from one read snapshot | `e064046` |
| ST6 | GitHub annotation properties have their own escaping rule | `e064046` |
| RT1 | Runtime admission, doctor and editor schema share configuration facts | `5511b47`, `6ea749e` |
| RT2 | CI compares separately installed base/candidate wheels and the complete verdict; baseline failures remain visible and candidate tests must pass | `5511b47`, `425669b`, `3c80921` |
| RT3 | Build/check fixed artifacts before publication; resume through readbacks tied to the same destinations | `f9c2efd`, `f614ac9` |
| RT4 | Generated version/command facts and corrected operational guidance | `5511b47`, `79f47ca`, `7c12da0` |
| RT5 | One serial-unit and parallel-CLI schedule combines JUnit and coverage; nested pytest data and configuration stay separate, and configured contexts survive | `5511b47`, `425669b`, `32a5f6e`, `3c80921`, `96036fc` |

The final release destination repair passed 87 focused tests with no failures
or skips. Its Git operations used disposable local bare repositories; publication
commands and service responses were simulated. The new destination guard has
complete line and branch coverage. These checks do not claim a live publication.
See `evidence-release-rerun/destinations-handoff.md`.

## Measured costs and gains

These are paired local measurements, not projections for other repositories.
Analysis benchmarks used synthetic inputs while other tests ran on the same
machine. Allocation measurements use tracemalloc, not whole-process RSS.
The analysis timings are medians of five warm samples after the first sample.
The 133-case schedule comparison is one serial run and one eight-worker run;
the owner-cost probe used 20 interleaved pairs.

| Workload | Before | After | Evidence |
| --- | ---: | ---: | --- |
| Same 133 CLI tests, baseline commit | 92.21 s serial | 39.40 s with eight workers | `schedule-benchmark.json`, equal test identity digests and zero failures |
| 12 identical files, 100 functions each | 12 parses, 0.150 s | 1 parse, 0.017 s | `evidence-analysis-rerun/paired-final.json`, equal full results |
| 12 unique files, 100 functions each | 0.126 s | 0.145 s | Digest verification adds work; parser calls remain 12 |
| 16,777,460-byte coverage artifact | 0.492 s | 0.372 s | Equal output digest; traced peak stays about 43.3 MB |
| 5,000 small coverage members | 0.082 s | 0.078 s | Equal output digest; traced peak stays about 4.66 MB |
| No-op command on Windows/Python 3.11 | 55.9 ms | 110.0 ms within an owner | Launch barrier adds about 54 ms per command |
| Measurement owner startup | None | 59.8 ms per batch | `evidence-execution-rerun/ownership-cost.json` |

The full CI verdict adds base and candidate builds and measurements. The normal
platform matrix and composite-action check remain separate coverage of those
installation paths. No full-suite speedup is inferred from the 133-case sample.
The unique-input digest check adds about 19 ms in its sample. The small-member
4 ms difference is not a general speed claim; the large-reader memory cost stays.

The strict opt-in startup check remains unmet on this host. Fifty paired runs
found a median candidate-minus-baseline difference of -0.0812 ms, with a paired
bootstrap 95% interval from -4.5083 to +6.0484 ms. Both snapshots exceeded the
40 ms budget above the interpreter floor: 53.5486 ms for baseline and 49.2894 ms
for candidate. All seven imported Crapkit modules and the entry file were
byte-identical. A separate 15-pair control mixed path and existing bytecode-cache
differences; its +11.1209 ms paired result is not a pure path estimate. The budget
stays unchanged; no production startup fix is supported by this evidence. See
`evidence-advisory-startup/conclusion.md`.

## Limits retained

Automatic reuse does not prove ignored inputs other than `crapkit.toml`, installed
dependencies, external files or services. Changes there require a fresh
measurement. Source-line display keeps its separate scoped freshness rule.

Ratchet replacement protects cooperating current writers. A manual editor or
older writer can still change a file after the final check; directory-entry
durability after power loss is outside this guarantee. Release recovery assumes
one active stage per checkout and retains refusal for unknown remote outcomes.

Windows checks cover leading whitespace, non-ASCII names and U+2028 in valid
UTF-8 Git paths. Filesystem cases containing tabs, newlines, carriage returns,
trailing spaces or literal backslashes are POSIX-only. The POSIX signal-return
check also remains unexecuted on this Windows host.

## Refused complete runs

The clean-wheel candidate at `9e08454` passed all 4,397 applicable tests:
3,638 unit tests and 759 CLI tests, with three skips. Its actual CI verification
still refused. Ledger run 2 records `verdict_ok=0` and `findings=2` because
`_ratchet_merge` and `run_bounded` each measured CCN 6, coverage 0.5 and CRAP 10.5.
The retained `state-evidence/review/wheel-contract/final-acceptance.md` documents
that refusal; its filename does not imply acceptance.

The normal configured measurement at the same source recorded run 73 with
4,396 passes, one failure and three skips:

| Suite | Passed | Failed | Skipped | Duration |
| --- | ---: | ---: | ---: | ---: |
| Unit | 3,637 | 1 | 1 | 1,370.67 s |
| CLI | 759 | 0 | 2 | 1,245.27 s |

The ownership-release test reacquired ownership, then its no-op command exceeded
an incidental five-second deadline. Root verify run 74 against baseline 72
refused three findings: the two coverage gates and that test failure.
`self-refused/refused-audit.json` proves the original 72 run records and all
737 tracked inputs stayed unchanged. Raw artifacts, environment records and
ledger backups remain in `self-refused/`.

The unit run used older global dependencies and unrelated pytest plugins. These
durations do not establish a code performance regression. The three skips were
the POSIX signal-return check and two opt-in strict timing checks.

Earlier `c205602` wheel-test and coverage-export failures remain in `final-ci/`.
Its summary preserves exact per-suite counts, failure names, skip reasons and
JUnit hashes. Later repairs addressed source-checkout assumptions, missing
YAML/Pillow dependencies, nested coverage contamination and Windows launcher
identity. The historical failed attempts remain failed.

## Test-only coverage repairs

Commits `a0d84fb` and `802257c` change tests only. Production code and all
configured gate thresholds remain unchanged from `9e08454`.

| Function | Added behavior checks | Focused measurement |
| --- | --- | --- |
| `_ratchet_merge` | Four invalid CLI argument-count cases require exit 3 and preserve inputs and state | Coverage 1.0, CCN 6, CRAP 6 |
| `run_bounded` | Two live parent/grandchild cancellation cases require process cleanup and reusable ownership | Coverage 1.0, CCN 6, CRAP 6 |

The ownership-release test still requires exit codes 7 and 0 across release and
reacquisition. It no longer asserts an unrelated five-second completion deadline.
The existing timeout and idle tests retain their budgets. Two such process timing
checks failed during host contention, then passed unchanged in the first quiet
rerun, two passes in 7.84 seconds. Both results remain in
`evidence-execution-rerun/final-cancellation/`, including `quiet.log`.

The cancellation tests inject an exception at the wait boundary while real
children remain alive; they do not claim Windows signal-delivery coverage.
The merge and cancellation notes retain the failed gate evidence and focused
measurements. These focused results do not change either frozen run's refusal.

## Completed root verification

The normal configured run passed at
`802257c8796070f6a6be72e99cf284f6afd13362`:

| Suite | Passed | Failed | Skipped | Duration |
| --- | ---: | ---: | ---: | ---: |
| Unit | 3,644 | 0 | 1 | 978.60 s |
| CLI | 759 | 0 | 2 | 620.03 s |

`self-final/independent-acceptance.json` confirms these counts from the raw JUnit
and reads the saved ledger. Actual verify run 76 records `verdict_ok=1` and
`findings=0` against baseline run 72 at `499d9db`, an ancestor of the review's
`47629ea` comparison base. Both `_ratchet_merge` and `run_bounded` now measure
coverage 1.0, CCN 6 and CRAP 6 in the full run.

Coverage run 75 records 1,867 functions: 1,652 source functions measured with
coverage and 215 tools functions checked for complexity only. The grade is A+
with zero functions over target. Verify still records 23 uncovered changed
lines; `diff_uncovered_max=null` leaves that count without a configured ceiling.
The three skips remain the POSIX signal-return check and two opt-in strict
timing checks.

`self-final/configured-audit.json` confirms all 74 prior run records stayed
unchanged and all 739 tracked inputs match their before-run hashes. It also
binds the accepted ledger entry to the coverage and JUnit artifact hashes. Raw results are in
`self-final/coverage.json`, `self-final/verify.json` and `self-final/lane-py.log`.

The configured run used `configured-runtime/venv` with declared development
dependencies: pytest 9.1.1, pytest-cov 7.1.0, coverage 7.16.0 and xdist 3.8.0.
PYTHONPATH selects the current root source. The frozen clone, noneditable wheel
and installed package match across all 69 Python files. Root's two CRLF-only
differences remain unchanged and have separate raw hashes.

## Completed clean-wheel comparison

`final-ci-complete/verdict.json` records phase `complete`, candidate suite exit
0 and actual verify run 2 against baseline run 1 at `47629ea`. The verdict is
true with zero findings, no overrides and no ratchet changes. Both source
revisions were built and installed separately before their full measurements.

| Revision and suite | Passed | Failed | Skipped | Pytest console duration |
| --- | ---: | ---: | ---: | ---: |
| Baseline unit | 3,396 | 2 | 42 | 554.28 s |
| Baseline CLI | 716 | 1 | 2 | 419.75 s |
| Candidate unit | 3,644 | 0 | 1 | 999.99 s |
| Candidate CLI | 759 | 0 | 2 | 582.91 s |

Counts come from the raw JUnit summary in `final-ci-complete/summary.json`;
durations come from `final-ci-complete/attempt-yi62o7o2/driver.log`. These suites
contain different tests, so their durations do not establish a speed comparison.
The baseline retains failures in the Pester root-level glob and source-tree
version fallback tests. Its additional `no_toml` timing failure measured
521.9062 ms against the unchanged 500 ms budget. The baseline's 42 unit skips
include 41 missing-YAML cases and one collection skip. Candidate skips remain
the POSIX signal-return check and two opt-in strict timing checks.

The independent audit in
`state-evidence/review/wheel-contract/acceptance-802257c.json` reports no issues:
the ledger matches the verdict, the baseline ledger stays unchanged and the
independent installed-package hashes match. Both former coverage-gate functions
measure coverage 1.0, CCN 6 and CRAP 6. Baseline run 1 has no stored JUnit digest;
its retained JUnit counts and failed test IDs match the stored lane, and its
coverage digest matches exactly.

The exact base and candidate wheels are retained beside
`state-evidence/review/wheel-contract/acceptance-802257c-wheel-artifacts.json`.
That manifest records their SHA256 hashes, sizes and original filenames;
`acceptance-802257c-source-check.json` records zero source mismatches across
64 baseline and 69 candidate Python files. The completed comparison preserves
the earlier refused attempts and does not use their results as passing evidence.

## Evidence layout

`collect-evidence.py --work <workspace>/work/implementation` builds
`focused-evidence.zip`. Its manifest records each input's bytes and SHA256.
Paths beginning `work/implementation` in early decision rows refer to that
workspace; remove that prefix to find the same file inside the archive.
The archive includes before/after probes, focused logs, paired benchmark scripts
and raw measurements. It also retains the earlier root unit run's one stale
schema-import failure, which was followed by fresh passing schema checks.
The collector selects named evidence groups and retained CI files rather than
recursing through scratch checkouts, test repositories or virtual environments.
The startup source-A/source-B Python copies are included as the exact compared
inputs. CI coverage databases, including incomplete failed-attempt data, remain
beside their JUnit, ledger and proof records. `self-refused/` preserves the refused
`9e08454` root run; `self-final/` holds the passing configured run's input hashes,
prior ledger backup, raw results and completed audits. Top-level `configured-runtime/`
files record environment admission without copying its clone or venv.
`report-evidence/` holds report and collector checks.

`final-ci-complete/attempt-yi62o7o2/` retains the completed wheel comparison's
raw measurements and ledger. Its summary, verdict and independent audit identify
the accepted `802257c` run. Hosted GitHub jobs have not been dispatched by this
local implementation task.

The completed archive contains 613 files: 135,819,061 uncompressed bytes and
21,148,239 ZIP bytes. `report-evidence/check-archive.py` confirmed every manifest
hash and source byte, all 30 required acceptance inputs, and the exclusions.
Archive SHA256:
`e8be318b887bb846f1a6415d054e80edeb4d2adb8f903187134b085fa8a00267`.
