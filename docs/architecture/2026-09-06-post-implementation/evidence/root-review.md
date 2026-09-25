# Fresh review of current implementation

Reviewed source: `499d9db4f9ff4d212fb94ea3975c7477b6b1c968` in `C:\Users\jfgag\crapkit`.

This pass starts from the current source. Earlier architecture candidates and their repaired follow-up findings are historical implementation evidence, not this review's checklist. No production, test or configuration changes were made in this pass.

## Scope and coverage

The mechanical inventory includes 647 tracked files: 64 production Python modules, 305 Python test/support files, 27 tooling files including demo fixtures, and delivery/configuration/documentation surfaces. The 167 files under the previous dated architecture report are retained history. Production contains 23,194 lines and 1,599 functions; tests/support contain 52,272 lines and 4,471 functions. Those counts describe size, not quality.

| Area | Review disposition |
| --- | --- |
| Production module graph and depth | All 64 modules assigned to the three independent source walks; findings require a concrete caller or state invariant, not a file-size split. |
| Configuration and schema | RT1: five schema/runtime admission mismatches reproduced; string paths can produce an empty inventory. |
| CLI and agent ergonomics | Preserve the public lazy main entry point, per-family imports, structured errors and stable handles. Dedicated execution review covers command orchestration, init, doctor, watch and MCP. |
| Analysis, language support and coverage | Dedicated analytical review covers all readers, cache identity, format projection, complexity semantics, source joins and scaling. |
| State, audit, ratchet and reports | Dedicated state review covers store schema, reads/writes, retention, claims, reports, history interpretation and serialization. |
| Execution and recovery | Dedicated execution review covers lane reuse, parallel command ownership, cancellation and mutation snapshots. |
| CI and self-verification | RT2: full hosted CRAP comparison absent; RT5: self-verification uses a different suite schedule. |
| Packaging and installation | Wheel build and import/version smoke pass from a disposable Git archive. Existing interpreter dependencies are reused; this is not a clean dependency-resolution test. CI exercises editable installations only. |
| Release lifecycle | RT3: in-memory execution adapters show whole-stage replay; source proof is present, artifact digests and per-surface completion are absent. No release command ran. |
| Documentation and policy | RT4: reference drift includes mutation ownership, supported versions and unit-test scheduling. Preserve editorial explanations and stable page anchors. |
| Test architecture | Inspected representative public CLI, private helper, schema and documentation contracts. Sixteen focused contract tests pass despite the reproduced admission mismatches. 186 imports reference 132 private production names; counts alone do not justify rewriting them. |
| Performance | Measured analytical decoding and state-read probes are supplied by the independent walks. Suite aggregation and release changes remain design proposals without measured speedup. |
| Portability | Windows disposable CLI/process/Git probes; static review of POSIX process and shell paths. No fresh Linux execution in this pass. |
| Security and output handling | Reviewed trusted repository-command assumption, local state ownership, Docker mount access and output encoders. This is not a full adversarial audit or secret scan. |
| Plugin, marketplace, Action and MCP | Dedicated execution review plus root installation/CI review. Keep ADR 0001's invalid-tool-argument result behavior. |
| Demo and reproduction tools | Read build/capture/render ownership and deterministic fixture/font design. Keep these existing modules; their interfaces own distinct work. No image regeneration was needed. |
| Dependencies and extensibility | Keep the small Python package and real format adapters. No new framework or dependency proposed merely to reduce visible function counts. |
| Architecture history | Preserve previous report and evidence bytes. The new review uses a separate directory and source SHA. |

## Fresh self-use

`python -m crapkit worklist --json --top 10` reads run 72 at the reviewed SHA with `stale=false`. `python -m crapkit duplication --json --top 30` finds one pair at the default minimum eight lines and similarity 0.8: `_scored_store` and `_rescore_baseline`, nine lines each, similarity 0.8333. Their refusal messages serve distinct commands. This pair does not justify a new shared module on its own.

These were fresh read commands over the existing verified snapshot. This review did not run another full coverage/verify lane. The historical 4,170-test verification belongs to the implementation report; it is not presented as a fresh test run here.

## Root evidence

| Check | Result |
| --- | --- |
| Five malformed TOML values | Published schema rejects all five; runtime accepts all five. |
| Scope paths as a string | Independent execution review reproduces public inventory exit 0 with zero functions. |
| Schema/CLI-doc/subprocess-coverage contracts | 16 tests pass; command: `python -m pytest tests/unit/test_schema_contract.py tests/unit/test_cli_docs_contract.py tests/unit/test_subprocess_coverage_config.py -q -p no:randomly`. |
| Wheel from current Git archive | Build exit 0; extracted-wheel import and version exit 0; source resolved inside disposable wheel contents. |
| Release retry | In-memory adapters record push, PyPI, GitHub release, plugin and Pages twice. No remote execution. |
| Independent state replay | Confirmed audit pruning, packet age disagreement and eager empty-mark query. |
| Independent interleaving replay | Confirmed wrong analysis cache hit, ratchet 50 to 10 to 20, and mixed trend totals. |

## Decisions

Retain the two accepted ADRs. The init ownership finding restores ADR 0002's intended guard. Treat races and false verdicts as first-priority work; keep faster decoding, reduced scans, generated references and suite scheduling separately reviewable. Do not combine all state into one global lock or propose a repository-wide framework rewrite. Fix each invariant at the module that owns it.

Reproduction fixtures use small synthetic repositories or controlled interleavings. They prove the cited failure mode; they do not estimate its frequency in user repositories. Instrumented timings include their stated measurement overhead. No source-code line reduction or production speedup is claimed before implementation.
