# Command, setup, and delivery review

This historical review records the command, setup and delivery findings and their repairs. The four original findings, strict boolean admission and plugin diagnosis repair are integrated. Later findings and their final dispositions are recorded in [fresh-findings.json](fresh-findings.json); the current whole-project test and verification status is in [validation.json](validation.json). Earlier focused counts below describe those earlier runs, not the final suite.

The review used the `crapkit` checkout, the domain model, and ADRs 0001 and 0002. Original diagnostic files remain in the retained authoring archive. The public [evidence index](evidence/index.json) identifies the copies included with this report. Paths in the table below refer to the integration snapshot inspected during this review; maintained test files provide the current replay.

## Finding dispositions

| Priority | Finding and demonstrated behavior | Integration evidence recorded during review | Disposition |
|---|---|---|---|
| P1 | Two scopes named `app` merged their ceiling and coverage exemption. A strict scope at target 1 received target 100 and an exemption from the second scope. Actual `coverage --json` returned exit 0, two `cc_only` functions, no missing lane, and no breach. | `src/crapkit/config.py:686` admits each scope name once before building name-keyed maps. | Fixed and integrated. Duplicate scopes now raise `ConfigError`. |
| P2 | `target="six"` escaped as `ValueError`, exit 1, no JSON. Other raw integer conversions accepted strings and booleans. Scope target accepted `true` as 1, and tightening ratio accepted non-finite numbers. | `src/crapkit/config.py:666`, `:785`, `:789`, `:824`. | Fixed and integrated. Existing integer guards now cover the five raw conversions; finite ratio admission rejects NaN and infinity. |
| P1 | `coverage_optional="false"` became true. `full_suite` and `container_ok` also used truthiness instead of TOML boolean values. | `src/crapkit/config.py:810`, called by scope and lane parsing. | Fixed as an extension of the config finding. Quoted `false`, quoted `true`, numbers, and lists fail before scoring or lane execution. |
| P2 | Action changed-file output quoted Unicode paths. Git printed `"src/caf\\303\\251.py"`; the comment builder matched no worklist row for `src/café.py`. | `action.yml:208`; consumer exact match at `tools/action/comment.py:254`. | Fixed and integrated. The Action sets `core.quotePath=false` on its Git call. |
| P2 | `release.verify(target, version)` read the caller's Git tag and GitHub repository. A target tagged `v9.9.0` reported a different current-directory tag, `v1.0.0`. | `tools/release/release.py:208`, `:212`, `:261`. | Fixed and integrated. Default Git and GitHub readers use the explicit root; injected callback signatures stay unchanged. |
| P2 | `doctor --plugin-root PATH` crashed on valid JSON `{"hooks": []}`. The diagnosis command exited 1 with `AttributeError`, rather than naming the malformed hook file. | `src/crapkit/cli/admin.py:1393` now handles malformed nested shapes; the argument reader checks for a list of strings. | Fixed by the parent. Six red regressions, then 43 doctor-plugin tests passed. |

The positive ranges preserve the published contract. `docs/configuration.md:39`, `:40`, `:41`, and `:47` require values at least 1. `crapkit.schema.json:14`, `:15`, `:16`, and `:22` agree. Existing worklist tests explicitly reject a top value of zero. No documented zero-valued mode was removed.

## Shape of the repairs

| Seam | Before | After | Deletion test and retained behavior |
|---|---|---|---|
| Config to scope and lane models | TOML values passed through `int()` and `bool()`, then name-keyed consumers inferred meaning. | Config parsing rejects duplicate names and wrong numeric or boolean types once. | Remove coercion from admission. Keep the current `Config`, `Scope`, and `Lane` interfaces and downstream scoring rules. No new validation framework. |
| Action Git output to comment rows | Git chose filename quoting; the comment module expected unquoted paths. | The producing Git command emits the representation its consumer already accepts. | Remove dependence on host Git configuration. Keep the comment module's exact path match and existing JSON shape. |
| Release verifier to repository commands | Explicit root selected files, while process cwd selected Git and GitHub data. | The same root selects every repository-bound reader. | Remove ambient cwd from the decision. Keep the release table, callbacks, stage guards, and read-only verifier. |
| Plugin JSON to doctor | JSON syntax validation admitted nested lists and scalars into dictionary traversal. | The hook reader contains malformed nested structures and doctor names the unreadable `hooks/hooks.json`. | Keep shape handling beside plugin JSON reading. The handshake keeps its current missing, unreadable, and valid-file meanings. |

The implemented changes are small and use existing interfaces. Their main compatibility effect is deliberate refusal of values the schema already forbids. The plugin diagnosis tests cover malformed nested structures alongside the existing valid and missing cases.

## Coverage map

| Area | Files inspected | Checks and retained seams |
|---|---|---|
| Public entry and lazy dispatch | `src/crapkit/__main__.py`, `cli/__init__.py`, `cli/parser.py`, `invocation.py`, `pyproject.toml` | Public `crapkit.cli:main` dispatches through the parser. The retired private export facade stays deleted. Family imports remain lazy; help and version avoid loading scoring. Checked parser command-to-handler mappings and representative argument paths. |
| Shared command work | `cli/_shared.py`, `rootfind.py`, `errors.py` | Traced implicit and explicit roots, path normalization, config loading, JSON errors, store opening, and output destinations. Nearest config wins, traversal stops at a Git root, and explicit `--repo` remains exact. These follow ADR 0002. |
| Init and setup | `cli/admin.py`, `scaffold.py` | Reviewed interpreter probes, local environments, marker and lockfile detection, package scripts, scope discovery, lane generation, test-path routing, scoped commands, config writes, and gitignore updates. Helper groups were surveyed with full command flows traced; this was not a line-by-line review of every generated TOML literal. |
| Config and doctor | `config.py`, `doctor.py`, `cli/admin.py` | Reviewed TOML admission, command restrictions, scope/lane identity, shared pytest configuration precedence, diagnostic findings, tuning, plugin discovery, and handshake. The config defects and malformed plugin structure above arose here and are fixed. |
| Scoring and verification command families | `cli/scoring.py`, `cli/verifying.py` | Traced coverage and inventory construction, lane selection, refusal attribution, result shape, rescore, gate, test-scoped routing, and explicit-base staged checks. Safe template preparation remains the execution owner. Detailed state/ranking changes went to another reviewer. |
| Queue, reports, ratchet, and analyses families | `cli/queue.py`, `cli/reports.py`, `cli/ratchet_cmds.py`, `cli/analyses.py` | Checked cross-family imports and command assembly, packet display routing, report inputs, selected-context reader call, baseline trust entry points, and analysis command routing. Mutation internals were excluded while their separate fixes were in flight. |
| Advisory hook | `cli/claude_hook.py`, plugin hook declarations | Reviewed protocol handling and entry flow. Keep standard-library-only module imports, silence for irrelevant events, and no snapshot-store access per edit. The parent corrected the stale store explanation at `cli/claude_hook.py:30`. |
| MCP | `mcp_server.py`, MCP command entry, plugin MCP declaration, `server.json` | Surveyed all 12 tool definitions and read the runtime argument, argv, subprocess, root, response, and session functions. Tool argument failures remain tool results under ADR 0001. JSON-RPC exceptions stay contained to one request. Parent corrected the stale count at `server.json:5`. |
| Action and comment delivery | `action.yml`, `tools/action/comment.py` | Traced per-invocation state, base worktree scoring, coverage and verify results, base-failure refusal, worklist filtering, comment assembly, posting conditions, and final gate. Retain unique invocation directories and the refusal when an attempted base run produced no baseline. Unicode path production was the remaining reproduced mismatch. |
| CI and package installation | `.github/workflows/ci.yml`, `pyproject.toml`, `.pre-commit-hooks.yaml`, `Dockerfile` | Reviewed the six OS/Python jobs, console entry check, unit/e2e split, event-base gate, plugin validation, Action dogfood, dependency floors, source packaging, and container entry. Dogfood deliberately leaves delta off because its editable install would measure HEAD inside the base worktree. |
| Release | `tools/release/release.py`, release contract tests, package/plugin/registry version files | Read version checks, bump, notes, verification, plans, stage execution, and new guards. Retain clean-main checks, exact HEAD/tag ownership, contract receipt, passing full-verification ledger requirement, path-safe artifact cleanup, and bounded tag rollback. Default verifier readers now obey explicit root. |
| Delivery declarations | `server.json`, `.claude-plugin/marketplace.json`, plugin manifest and hooks, `.github` templates | Checked commands, version ownership, tool count, and workflow relationships. Demo generation scripts were outside this command/setup/delivery assignment. |

This map records source and caller-flow review. It does not claim every listed integration ran in this subtask. Existing test families inspected include config, root finding, init and doctor, CLI dispatch, MCP, Action contracts, plugin manifests, and release guards. See [validation.json](validation.json) for full-suite and configured verification results.

## Validation evidence

The filenames and counts in this table identify historical authoring runs. A filename absent from the public evidence index belongs to the retained archive, not to an omitted runtime dependency.

| Evidence | Result |
|---|---|
| `command_probes.py`, `command-probes.json` | Four original failures through actual CLI or tool seams. All Git state changes occurred in temporary repositories. Release network readers were substituted; no publishing occurred. |
| `command-fixes-red.txt` | Before the initial fix, 38 regressions failed and 10 passed. |
| `command-fixes-green.txt` | Initial four-fix regression set: 48 passed. |
| `command-booleans-red.txt` | Before boolean admission, 16 regressions failed and 47 passed. |
| `command-booleans-green.txt` | Final new regression set: 66 passed. |
| `command-fixes-focused-final.txt` | Broader pre-boolean config/release checks: 178 passed, one stale worktree pytest-configuration expectation deselected. The current root had already fixed that expectation. |
| `command-fixes-final-focused.txt` | A later Git Bash run exposed six old worktree test failures because their Action fixture sets `RUNNER_TEMP` instead of the integrated Action's `CRAPKIT_STATE`. Compared with current root tests, which already use `CRAPKIT_STATE`. This is stale fixture evidence, not a production regression. |
| `command-fixes-final-complexity.txt` | All 63 config functions and 58 release functions have CCN at most 6. |
| `command-fixes-final-hook.txt` | `hook-precommit` passed. `git diff --check` passed. |
| `command-fixes.patch` | 14,773-byte incremental patch against byte-identical saved root files. `git apply --check` passed before root integration. Parent reports 95 integrated config/action/release/template regressions passed. |
| `extra_command_probes.py`, `extra_command_probes.json` | Preserved before-fix `doctor --plugin-root` traceback from temporary malformed hooks. The separate invalid-config MCP startup probe correctly exits 3 before serving; it is not an MCP defect. Parent's plugin repair passed 43 related tests after six red regressions. |

Every Python run explicitly set `PYTHONPATH` to the source tree under test. The worktree resolution probe printed that worktree's `src/crapkit/__init__.py`. No global editable install, commit, push, published artifact, or real-repository ref change occurred in this subtask.

## Current replay

Run the maintained regressions from the repository root with the development dependencies installed:

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
python -B -m pytest tests/unit/test_config_admission_regressions.py tests/unit/test_action_unicode_paths.py tests/unit/test_release_repo_root.py tests/unit/test_doctor_plugin_root.py tests/unit/test_release_guards.py
```

The original `command_probes.py` and `extra_command_probes.py` invocations were historical diagnostics against the pre-repair checkout. They are superseded by the maintained regressions above. This review makes no performance improvement claim; its measured results concern correctness and diagnostics.
