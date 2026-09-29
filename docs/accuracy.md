# Calculation accuracy

crapkit's scores are only worth acting on if they are right. The accuracy suite
checks every number, label, ranking and pass/fail crapkit computes against an
expected value that does not come from crapkit's own code, and it keeps any of
them from moving without a declared change.

It lives in `tests/accuracy` and `tools/accuracy`, apart from the unit and e2e
suites in `tests/unit` and `tests/e2e`, which pin crapkit's behavior. This page
is for contributors: how to run it, what a failure means, and how to add a check.

## What it checks

`tests/accuracy/*/calcs.tsv` lists 93 calculations, from the CRAP score and each
complexity reader to the worklist order, the verify exit code and the printed
next-step commands. Each row names the calculation's modules and functions and
one independent test, whose imports reach no crapkit code.

Each calculation is checked by at least three methods whose expected value does
not come from crapkit:

| Method | Where the expected value comes from |
|---|---|
| Hand table | Worked rows, each citing an outside source: a paper, a spec section, a doc line |
| Outside oracle | Another tool on the same input: radon, mccabe, complexipy, ESLint, sonarjs, the TypeScript compiler, PowerShell's parser, gocyclo, PMD, coverage.py, istanbul and the rest in `tools/accuracy/pins.toml` |
| Clean-room model | A `model_*.py` written from cited doc lines alone; the contract fails when a cited line changes |
| Metamorphic relation | An edit with a known effect: a comment moves no score, reversed lanes move none |
| Property | A rule over Hypothesis inputs: CRAP lies between ccn and ccn^2 + ccn |

On top of those, goldens pin crapkit's output on a small and a full corpus,
cross-surface checks compare every surface that prints one number, receipts
compare Linux, Windows and macOS, mutation testing measures whether the suite
notices a changed line, and crapkit itself stops with exit 5 when a number it is
about to store breaks its documented bound (`src/crapkit/invariants.py`).

The work is split into packets, one directory each under `tests/accuracy`:

| Packet | Checks |
|---|---|
| `kit` | What the others share: exact arithmetic, the tier markers, dated repos, the CLI driver, the goldens lock, the contract |
| `score_model` | CRAP, coverage ratio, remedy, grade, budget, ceilings, queue and worklist order |
| `verdict_model` | Ratchet keys and marks, the gates, verify's verdict and exit, baselines, lane reuse, claims |
| `analysis_oracles` | The file universe, decoding, every language reader: functions, ccn, cognitive, nesting, nloc, params |
| `coverage_oracles` | coverage.py and istanbul attribution, the coverage join, flags, dark lines, diff coverage |
| `history_oracles` | Churn, coupling, changed ranges, renames, burn-down |
| `corpus_goldens` | The corpora and goldens, the wheel diff, the cross-platform exports, every output surface |
| `change_control` | The rules that stop an undeclared golden or metric move |
| `definitions` | Each field's definition, the same in the README, CONTEXT.md, agent-json.md, MCP and SARIF |
| `runtime_guards` | The runtime bounds: no stop on real repos, and what they cost |
| `suite_strength` | Past bugs replayed, mutation floors, the release gate |

### Per calculation

Each calculation, the packet that owns it, its independent test, and how many
rulings rows record a place where it and an oracle read a construct differently:
on purpose (a definition), wrongly (an open defect, a strict xfail until its fix)
or no longer (fixed). `python tools/docs/generate.py` writes this table and the
[rulings](#rulings) from the `calcs.tsv` and `rulings.tsv` tables; the unit suite
fails while either is out of date.

<!-- generated:calcs -->
| Calculation | Packet | Independent test | Definitions | Open defects | Fixed |
|---|---|---|---:|---:|---:|
| File universe and scope ownership | `analysis_oracles` | `test_universe_layouts.py::test_crapkit_assigns_the_hand_owners` | 0 | 1 | 0 |
| Source decoding and line normalization | `analysis_oracles` | `test_decode_matrix.py::test_every_variant_reads_the_hand_rows` | 0 | 1 | 2 |
| Function discovery and spans | `analysis_oracles` | `test_complexity_oracles.py::test_spans_match_the_treesitter_counters_on_the_probes` | 5 | 27 | 29 |
| Python reader: spans, names, inline_body, unread-def net | `analysis_oracles` | `test_python_ast.py::test_spans_match_ast` | 2 | 0 | 0 |
| JS/TS expression arrows and template literals | `analysis_oracles` | `test_ts_compiler.py::test_sibling_arrows_are_separate_functions` | 0 | 1 | 0 |
| Rust reader (match arms) | `analysis_oracles` | `test_equivalence.py::test_match_equals_if_chain` | 2 | 0 | 0 |
| Shell reader | `analysis_oracles` | `test_hand_probes.py::test_crapkit_matches_the_hand_value` | 2 | 0 | 1 |
| PowerShell reader | `analysis_oracles` | `test_powershell_oracles.py::test_powershell_matches_parser_ast` | 5 | 0 | 3 |
| ccn_std, ccn_mod and gated ccn | `analysis_oracles` | `test_complexity_oracles.py::test_python_ccn_matches_radon_and_mccabe` | 44 | 11 | 24 |
| Cognitive complexity | `analysis_oracles` | `test_cognitive_oracles.py::test_python_cognitive_matches_the_sonar_counter` | 57 | 2 | 51 |
| Nesting depth | `analysis_oracles` | `test_nesting_oracles.py::test_python_depth_matches_model` | 26 | 0 | 51 |
| nloc | `analysis_oracles` | `test_nloc_params.py::test_python_nloc_matches_tokenize` | 7 | 5 | 1 |
| Parameter list (params, packet.params) | `analysis_oracles` | `test_nloc_params.py::test_python_params_match_ast` | 5 | 12 | 10 |
| Unanalyzable files and twin-name notes | `analysis_oracles` | `test_notes.py::test_a_truncated_signature_refuses_its_file_and_nothing_else` | 0 | 0 | 1 |
| Analysis cache and stat index | `analysis_oracles` | `test_cache_identity.py::test_warm_equals_cold_after_every_step` | 0 | 0 | 0 |
| Near-duplicate functions | `analysis_oracles` | `test_duplicates.py::test_copies_with_comments_blanks_and_spacing_pair_at_seven_of_eight` | 6 | 0 | 3 |
| init scaffolding | `analysis_oracles` | `test_init_scaffold.py::test_scopes_follow_top_level_dirs_and_parsers` | 1 | 0 | 0 |
| Watch change detection | `analysis_oracles` | `test_watch.py::test_a_touch_names_the_file_once_and_moves_no_number` | 0 | 0 | 0 |
| Inventory rows and TSV exports | `analysis_oracles` | `test_inventory_rows.py::test_every_row_is_marked_exactly_when_the_document_says` | 0 | 0 | 0 |
| Change-control verdict | `change_control` | `test_change_control_rules.py::test_each_rule_fails_its_seeded_repo` | 0 | 1 | 0 |
| HTML report | `corpus_goldens` | `test_html_report.py::test_each_scope_s_grade_is_its_rows_added_up` | 0 | 0 | 0 |
| SARIF and GitHub annotations | `corpus_goldens` | `test_sarif_annotations.py::test_every_result_says_crap_7_over_ceiling_6` | 0 | 0 | 1 |
| PR comment | `corpus_goldens` | `test_action_scenarios.py::test_the_summary_line_adds_up_the_rows` | 0 | 0 | 0 |
| MCP tool results | `corpus_goldens` | `test_surfaces_agree.py::test_every_mcp_tool_prints_the_counts` | 0 | 0 | 0 |
| Store and cache upgrade | `corpus_goldens` | `test_store_upgrade.py::test_the_candidate_marks_the_baseline_the_readme_picks` | 0 | 0 | 1 |
| Printed commands | `corpus_goldens` | `test_printed_commands.py::test_every_printed_argv_exits_0_and_names_its_function` | 0 | 0 | 2 |
| Wheel diff moved-row map | `corpus_goldens` | `test_wheel_diff_tool.py::test_the_map_equals_a_csv_diff` | 0 | 0 | 0 |
| coverage.py per-function coverage | `coverage_oracles` | `test_ground_truth.py::test_crapkit_scores_what_the_ground_truth_says` | 1 | 0 | 2 |
| Istanbul attribution (vitest v8, nyc, Jest, c8) | `coverage_oracles` | `test_producers_agree.py::test_crapkit_attributes_every_recording_by_the_documented_line_rule` | 6 | 0 | 2 |
| Artifact streaming framing and digest | `coverage_oracles` | `test_adversarial_artifacts.py::test_split_window_hands_back_what_json_loads_reads` | 0 | 0 | 0 |
| Coverage join | `coverage_oracles` | `test_join.py::test_every_artifact_function_joins_its_own_row` | 1 | 1 | 0 |
| Shared span and def-line floor | `coverage_oracles` | `test_shared_span.py::test_signature_layouts_floor_by_body_line` | 1 | 0 | 0 |
| Coverage flag | `coverage_oracles` | `test_flags.py::test_every_scored_flag_follows_the_readme_table` | 0 | 0 | 0 |
| Dark lines and changed-line dead set | `coverage_oracles` | `test_dark_lines.py::test_dead_lines_are_the_statements_the_driver_never_ran` | 1 | 0 | 0 |
| Per-line test contexts | `coverage_oracles` | `test_contexts.py::test_each_line_the_driver_ran_names_the_test_that_ran_it` | 0 | 0 | 0 |
| Artifact tree admission | `coverage_oracles` | `test_adversarial_artifacts.py::test_the_admission_verdict_follows_the_documented_table` | 0 | 0 | 0 |
| Diff coverage | `coverage_oracles` | `test_diffcov_oracle.py::test_verify_lists_the_changed_lines_the_ground_truth_leaves_unrun` | 1 | 0 | 0 |
| Doctor findings (unmeasured directories, nearby test) | `coverage_oracles` | `test_doctor_unmeasured.py::test_rule_model_vs_production_path` | 0 | 0 | 0 |
| Field definitions across docs and schemas | `definitions` | `test_definition_models.py::test_sarif_over_target_messages_follow_the_definitions` | 0 | 1 | 1 |
| Churn counts and recency weight | `history_oracles` | `test_churn_oracle.py::test_every_path_matches_the_numstat_walk` | 8 | 0 | 1 |
| Change coupling | `history_oracles` | `test_coupling_oracle.py::test_pairs_match_the_numstat_walk` | 2 | 0 | 1 |
| Changed line ranges | `history_oracles` | `test_changed_ranges.py::test_touched_functions_match_unidiff` | 1 | 0 | 0 |
| Identity across renames and moves | `history_oracles` | `test_renames.py::test_marks_follow_the_documented_renames` | 0 | 0 | 0 |
| Burn-down, mark age and debt policy | `history_oracles` | `test_burn_down.py::test_report_matches_the_history_walk` | 0 | 1 | 0 |
| Runtime invariant verdict | `runtime_guards` | `test_invariant_verdict.py::test_crapkit_stops_exactly_where_the_documented_bounds_do` | 2 | 3 | 0 |
| CRAP score | `score_model` | `test_crap.py::test_reduced_grid_matches_the_exact_value` | 6 | 0 | 1 |
| Function coverage ratio | `score_model` | `test_counts_join.py::test_every_scored_row_matches_its_artifact_counts` | 1 | 2 | 0 |
| Remedy label | `score_model` | `test_remedy_grade_budget.py::test_remedy_matches_the_readme_table` | 0 | 0 | 1 |
| Grade letter | `score_model` | `test_remedy_grade_budget.py::test_grade_matches_the_band_table_for_every_count_to_400` | 0 | 0 | 0 |
| Work budget estimates | `score_model` | `test_remedy_grade_budget.py::test_uncovered_paths_match_away_from_exact_halves` | 1 | 0 | 1 |
| Ceiling per row | `score_model` | `test_ceiling.py::test_every_scope_s_ceiling_matches_the_docs` | 0 | 0 | 0 |
| Rescore overlay | `score_model` | `test_overlay.py::test_overlay_matches_the_join_by_name` | 1 | 0 | 0 |
| Queue admission and floors | `score_model` | `test_queue.py::test_the_lowest_debt_ccn_is_the_closed_form_for_every_ceiling_to_2000` | 1 | 0 | 0 |
| Worklist ranking and dormant list | `score_model` | `test_queue.py::test_the_worklist_ranks_what_the_docs_admit_in_the_docs_order` | 2 | 0 | 0 |
| next-item ranking and empty-queue reasons | `score_model` | `test_queue.py::test_next_item_ranks_by_crap_what_the_docs_let_it_take` | 2 | 1 | 0 |
| Batch split | `score_model` | `test_batch_split.py::test_batches_are_disjoint_cover_every_entry_and_keep_groups_whole` | 1 | 0 | 1 |
| Brief packet fields and regrowth | `score_model` | `test_rejudge.py::test_regrowth_label_matches_model` | 0 | 1 | 0 |
| Run totals and trend rollup | `score_model` | `test_rollups.py::test_totals_match_the_exact_sums` | 0 | 1 | 3 |
| Digest deltas | `score_model` | `test_rollups.py::test_only_identical_lane_sets_pair` | 1 | 1 | 0 |
| Coverage run summary | `score_model` | `test_rollups.py::test_the_coverage_summary_is_the_scored_rows_counted` | 0 | 0 | 0 |
| doctor --tune knobs and lane cost | `score_model` | `test_tune.py::test_the_cost_line_is_lpt_within_graham_s_bound` | 2 | 1 | 0 |
| Mutation results | `suite_strength` | `test_mutate_results.py::test_negation_dies_and_boundary_survives` | 0 | 0 | 4 |
| Retro replay verdict | `suite_strength` | `test_retro_tool.py::test_a_planted_bug_reads_red_before_and_passes_after` | 0 | 0 | 0 |
| Mutation survivor verdict | `suite_strength` | `test_mutation_tool.py::test_a_recorded_weekly_run_reads_the_verdict_worked_by_hand` | 0 | 0 | 1 |
| Release accuracy gate | `suite_strength` | `test_release_gate_model.py::test_the_gate_matches_the_plan_s_model` | 0 | 0 | 0 |
| Accuracy tier runner and receipts | `suite_strength` | `tests/accuracy/kit/test_run_tool.py::test_shards_merge_to_the_receipt_of_the_whole_tier` | 0 | 0 | 1 |
| Ratchet key and twin ordinals | `verdict_model` | `test_keys_handles.py::test_seed_marks_every_twin_under_its_own_key` | 0 | 0 | 0 |
| Handles and NAME resolution | `verdict_model` | `test_keys_handles.py::test_next_item_handles_follow_the_docs` | 0 | 1 | 0 |
| Identity admission of saved marks | `verdict_model` | `test_keys_handles.py::test_worklist_refuses_unordered_same_line_twins` | 0 | 0 | 0 |
| Pre-commit gate | `verdict_model` | `test_three_gates.py::test_the_hook_exempts_a_marked_function` | 0 | 0 | 0 |
| rescore --gate verdict | `verdict_model` | `test_three_gates.py::test_pardon_relation_at_the_mark` | 0 | 0 | 0 |
| Claude-hook advisory and file choice | `verdict_model` | `test_three_gates.py::test_a_bash_event_judges_the_fresh_dirty_python_files_git_status_names` | 0 | 0 | 0 |
| verify gate violations | `verdict_model` | `test_three_gates.py::test_renamed_file_faces_every_gate` | 0 | 0 | 0 |
| Ratchet regressions | `verdict_model` | `test_history_machine.py::test_seed_then_verify_same_run_passes` | 0 | 0 | 0 |
| Test failure classes | `verdict_model` | `test_failure_classes.py::test_failure_classes_follow_the_model` | 0 | 0 | 0 |
| Verdict exit code and dirty split | `verdict_model` | `test_exit_and_settle.py::test_exit_json_and_stored_run_name_one_verdict` | 0 | 0 | 0 |
| Standing unmarked debt | `verdict_model` | `test_exit_and_settle.py::test_the_hand_table_holds_all_16_subsets_and_the_model_reads_it` | 0 | 1 | 0 |
| Baseline and run trust selection | `verdict_model` | `test_history_machine.py::test_every_step_in_one_scripted_history` | 0 | 0 | 1 |
| Metric stamp guard | `verdict_model` | `test_history_machine.py::test_seed_stamps_the_metric_of_the_run_it_read` | 0 | 1 | 0 |
| Audited override grant | `verdict_model` | `test_history_machine.py::test_override_checks_the_stamp` | 0 | 1 | 0 |
| Ratchet tighten, damping and delta | `verdict_model` | `test_history_machine.py::test_bouncing_measurement_holds_marks` | 0 | 1 | 0 |
| Ratchet seed and prune | `verdict_model` | `test_history_machine.py::test_seed_reads_the_run_verify_reads` | 0 | 1 | 0 |
| Ratchet merge driver | `verdict_model` | `test_merge_driver.py::test_every_key_merges_as_git_merge_file_and_the_docs_resolve_it` | 0 | 0 | 0 |
| Ratchet file parse and dump | `verdict_model` | `test_ratchet_file.py::test_move_rewrites_every_other_row_as_the_docs_write_a_mark` | 4 | 0 | 0 |
| Claim ownership and closing | `verdict_model` | `test_claim_close.py::test_verify_releases_a_claim_once_the_function_sits_at_its_ceiling` | 0 | 0 | 0 |
| JUnit results parsing | `verdict_model` | `test_junit_parse.py::test_lane_failures_and_counts_match_junitparser` | 0 | 0 | 0 |
| Suite drop and shrink warnings | `verdict_model` | `test_lanes_reuse.py::test_coverage_warns_past_a_tenth_drop` | 0 | 0 | 1 |
| Lane reuse and artifact staleness | `verdict_model` | `test_lanes_reuse.py::test_the_first_failed_condition_names_the_rerun` | 0 | 0 | 4 |
| Explain history | `verdict_model` | `test_history_machine.py::test_explain_reads_the_run_brief_reads` | 0 | 0 | 3 |
| Lane command reading and the full-suite guard | `verdict_model` | `test_full_suite_guard.py::test_the_refused_word_is_one_the_shell_hands_the_runner` | 2 | 0 | 7 |
| Run retention keep set | `verdict_model` | `test_retention.py::test_each_keep_rule_keeps_its_run` | 1 | 0 | 0 |
| test-scoped routing | `verdict_model` | `test_scoped_routing.py::test_every_file_reaches_its_scope_as_one_word` | 0 | 0 | 1 |
| Doctor lane and plugin checks | `verdict_model` | `test_doctor_lanes.py::test_doctor_probes_with_the_lane_s_environment` | 0 | 0 | 0 |
<!-- /generated:calcs -->

## Tiers

| Tier | Where it runs | What it adds |
|---|---|---|
| `push` | Every push and pull request: `accuracy-push` in ci.yml, on Ubuntu and on Windows (the `os_sensitive` checks) | The fast checks, at most 504 declared serial seconds on Ubuntu (about 2 min at `-n 4`) |
| `nightly` | accuracy.yml at 03:17 UTC, in the accuracy image, plus native Windows and macOS cells | Every outside oracle, the full corpus, 20,000 Hypothesis examples, Python 3.11 to 3.14, mutation of the functions changed since the weekly run, a slice of the past-bug replays |
| `weekly` | accuracy.yml on Saturdays | Full mutation testing in 8 shards, a no-cache image rebuild that checks every pin |
| `release` | The release tool's `accuracy` stage | The push tier, both wheel diffs against the previous release, the store upgrade, the consumer replay, the retro replays, the mutation coverage |

A pull request runs the nightly tier as well while it carries the `accuracy` label.

## Run each tier locally

Run every command from the repository root. `run.py` exits 0 when every check
passed, 1 when a check failed, and 3 when the only problems were infra misses
(an oracle not installed, a fetch that failed) after one retry. Each run writes a
receipt to `.crapkit/accuracy/<tier>-<shard>-<os>-<python>.json` and prints each
check's declared and measured seconds. A check whose test file is not there
fails without running and names the file and its row in `tools/accuracy/checks/`.

### Push

```
pip install -e ".[dev,accuracy-push]"
npm ci --prefix tools/accuracy/node/push
python tools/accuracy/run.py --tier push -n 4
```

`--shard analysis|coverage|history|verdict-score|corpus` runs one shard, and
`--os-sensitive` runs only the checks whose answer can change with the OS, which
is what CI's Windows job runs. A bare `python -m pytest tests/accuracy/<packet>`
selects the push tier too.

On Windows the push tier pastes crapkit's printed commands into cmd.exe,
Windows PowerShell 5.1, PowerShell 7 and Git Bash, and reads the PowerShell
probes with the parsers of both PowerShells. Windows and Git for Windows bring
all of them but PowerShell 7: install it with `winget install Microsoft.PowerShell`
so `pwsh` is on PATH. On Linux and macOS the commands go to bash. A shell that is
not installed fails the check that needs it, and the failure says how to install it.

### Nightly

The nightly tier needs the `accuracy` extra, the nightly Node tools and the full
corpus. The outside tools that are not Python or Node packages come with the
accuracy image, so the Linux cells run there:

```
pip install -e ".[dev,accuracy]"
npm ci --prefix tools/accuracy/node/push
npm ci --prefix tools/accuracy/node/nightly
python tools/accuracy/corpus.py fetch
python tools/accuracy/run.py --tier nightly -n 4
```

```
docker build -f tools/accuracy/image/Dockerfile -t crapkit-accuracy:$(python tools/accuracy/run.py image-tag) tools/accuracy
docker run --rm -v "$PWD:/src" crapkit-accuracy:<tag> python tools/accuracy/run.py --tier nightly --shard corpus -n 4
```

Every check finds the fetched corpus on its own, looking at
`CRAPKIT_ACCURACY_CORPUS` first, then the image's `/corpus`, then the fetch
cache. A check that finds none ends as an infra miss naming each place it
looked. Some nightly checks fetch from PyPI (the wheel diff, the store upgrade,
the producer reruns), so give the container the network.

On Docker Desktop for Windows, a bind mount makes every file stat slow; copying
the checkout into the container first runs the nightly shards several times
faster. Under Git Bash, set `MSYS_NO_PATHCONV=1` before `-e VAR=/path`.

### Weekly

Mutation testing runs in the accuracy image, since mutmut forks and runs on Linux
only. One shard of the weekly run, and the run over the functions changed since
the last weekly:

```
docker run --rm --network none -v "$PWD:/src" -w /src crapkit-accuracy:<tag> python tools/accuracy/mutation.py weekly --shard 1 --of 8
docker run --rm --network none -v "$PWD:/src" -w /src crapkit-accuracy:<tag> python tools/accuracy/mutation.py diff --since-weekly --cap-minutes 30
```

A survivor on neither `suite_strength/mutation/survivors.tsv` nor
`equivalent.tsv` fails the run, and a capped `diff` run reports `incomplete`,
never `pass`.

Both commands check out HEAD under `.crapkit/accuracy/mutation/calc-stage` and
run `tests/unit` and the accuracy tests at the push tier against mutmut's copy
of the code in its `mutants/` folder. mutmut first runs that suite once to learn
which tests reach which function, and when one test fails there it judges no
mutant at all, so every test must pass inside the copy. The copy's `src/` is
rewritten with trampolines, so the checks that read crapkit's own source as
data read the stage's `src/crapkit` instead, which the tool names in
`CRAPKIT_ACCURACY_SOURCE`. A test that cannot pass inside the copy whatever
mutant is active (it reads a module as text, times a call, or starts an
interpreter on the copy) goes in `COPY_BOUND` in `tools/accuracy/mutation.py`
with its reason, and the stage leaves it out, as it leaves out the tests an open
defect ruling names. CI still runs it on the tree.

### Release

```
python tools/accuracy/run.py --tier release --receipt .crapkit/release-accuracy-VERSION.json
```

The release tool runs this in its `accuracy` stage (see [Releases](#releases)).

## When a check fails

A failing check means crapkit and its expected value disagree. Find out which
one is wrong before changing either:

- crapkit is wrong: fix it at the root, with a unit or e2e test for the fix.
  Until the fix lands, the check stays red, or it becomes a strict xfail through
  a `defect` row in its packet's `rulings.tsv` that names the bug.
- crapkit means something else on purpose: add a `definition` row to the
  packet's `rulings.tsv` that cites where the docs say so, and fix the docs if
  they do not.
- The oracle is wrong: show why in the check, and keep that oracle out of it.

A strict xfail that starts passing fails the run: its bug is fixed. Change its
rulings row to `fixed` with one value on both sides.

A pytest process that ends with exit 1 and no summary had a crapkit call, run
inside the process, stuck in C code past its bound
(`tests/e2e/cli_in_process.py`). It ends on purpose, after writing every
thread's stack to `in-process-hangs.log` under pytest's basetemp
(`pytest-of-<user>/pytest-<n>/`, or its `popen-gw<N>` folder under xdist).

## Rulings

Every place an oracle and crapkit read a construct differently has a row in its
packet's `rulings.tsv`: the calculation, the oracle, the construct, both values,
and the ruling. A test pins the row with `@rulings.applies(ID)` and
`pin_ruling(ID, crapkit=..., oracle=...)`, so a row whose values stop matching
what either side says fails, whichever side moved.

| Ruling | Means | The row needs |
|---|---|---|
| `definition` | crapkit means something else on purpose | Outside support: a URL, a paper section or a docs anchor with its `#` that states crapkit's value, or `convention_only`, which the maintainer answers before a merge |
| `defect` | crapkit is wrong | The bug it records; its test is a strict xfail until the fix |
| `fixed` | A former defect | One value on both sides |

<!-- generated:rulings -->
| Packet | Definitions | Open defects | Fixed |
|---|---:|---:|---:|
| `analysis_oracles` | 162 | 54 | 176 |
| `corpus_goldens` | 0 | 1 | 4 |
| `coverage_oracles` | 12 | 2 | 4 |
| `definitions` | 0 | 1 | 1 |
| `history_oracles` | 11 | 0 | 4 |
| `runtime_guards` | 2 | 4 | 1 |
| `score_model` | 17 | 1 | 7 |
| `suite_strength` | 0 | 17 | 6 |
| `verdict_model` | 7 | 0 | 14 |

<details><summary><code>analysis_oracles</code>: 392 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| AO-PY-CCN-WILDCARD | ccn_std, ccn_mod and gated ccn | Python `case _` counted as a decision (probe py get-words) | 5 | hand: NIST SP 500-235 sec. 4.1 | 4 | defect | paper: NIST SP 500-235 sec. 4.1 |
| AO-PY-FLOORDIV | ccn_std, ccn_mod and gated ccn | Python `//` then a conditional expression on one line (probe py floor-split) | 1 | hand: NIST SP 500-235 sec. 4.1 | 2 | defect | paper: NIST SP 500-235 sec. 4.1 |
| AO-PY-FLOORDIV-COG | Cognitive complexity | Python `//` then a conditional expression on one line (probe py floor-split) | 0 | hand: Sonar paper | 1 | defect | paper: Sonar Cognitive Complexity v1.7 App. B |
| AO-PY-COG-RECURSION | Cognitive complexity | self.inner.close() inside close (probe py close) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-PY-COG-RUNS-NOT | Cognitive complexity | if a and not (b and c) (probe py logical-not) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-PY-COG-RUNS-COMMA | Cognitive complexity | if a or pick(x, y) or b (probe py comma-run) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-PY-COG-LAYOUT | Cognitive complexity | an or sequence continued on the next line inside brackets (probe py run-over-lines) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B |
| AO-PY-COG-COMP-SCOPE | Cognitive complexity | two comprehensions in one return (probe py two-comprehensions) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B |
| AO-PY-NEST-COMP-SCOPE | Nesting depth | two comprehensions in one return (probe py two-comprehensions) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-COG-WHILE-AFTER-BRACE-PY | Cognitive complexity | a while right after a dict literal (probe py dict-then-while) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B |
| AO-NEST-WHILE-AFTER-BRACE-PY | Nesting depth | a while right after a dict literal (probe py dict-then-while) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-PY-COG-KEYWORD-NAMES | Cognitive complexity | cmd.do(1) (probe py keyword-named) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B |
| AO-PY-NEST-KEYWORD-NAMES | Nesting depth | cmd.do(1) (probe py keyword-named) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-PY-COG-NESTED-RECURSION | Cognitive complexity | a nested def that calls itself (probe py countdown) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-PY-PARAMS-DEFAULTS | Parameter list (params, packet.params) | def defaults(a, bases=(), skip=...) (probe py defaults) | 2 | hand: Python reference 8.7 | 3 | definition | docs/agent-json.md#what-the-session-reads |
| AO-PY-PARAMS-DEFAULTS-B | Parameter list (params, packet.params) | def bounded[T: (int, str), U: int](a, b=(), c=1) (probe py pep695-bounded) | 2 | hand: Python reference 8.7 | 3 | definition | docs/agent-json.md#what-the-session-reads |
| D4 | Cognitive complexity | Python match (probe py get-words, the paper's getWords) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 Switches |
| AO-PY-FINALLY | ccn_std, ccn_mod and gated ccn | a try's finally clause (probe py one-except) | 3 | hand: NIST SP 500-235 sec. 4.1 | 2 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_languages/python.py#L42 |
| AO-PY-NLOC-DOCSTRING | nloc | a docstring line (probe py documented) | 3 | hand: lizard README nloc | 4 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_languages/python.py#L202 |
| AO-PY-NLOC-NESTED-A | nloc | lines of a def nested in the function (probe py a-decorator) | 3 | hand: lizard README nloc | 6 | definition | convention_only |
| AO-PY-NLOC-NESTED-B | nloc | lines of a def nested in the function (probe py not-a-decorator) | 4 | hand: lizard README nloc | 7 | definition | convention_only |
| AO-PY-NESTED-ROW-A | Cognitive complexity | a nested def is its own row (probe py a-decorator, paper App. A total 1) | 0 | hand: Sonar paper | 1 | definition | docs/agent-json.md#item-fields |
| AO-PY-NESTED-ROW-B | Cognitive complexity | a nested def is its own row (probe py not-a-decorator, paper App. A total 2) | 0 | hand: Sonar paper | 2 | definition | docs/agent-json.md#item-fields |
| AO-PY-NESTED-ROW-C | Cognitive complexity | a nested def's own row starts at nesting 0 (probe py inner2) | 1 | hand: Sonar paper | 2 | definition | docs/agent-json.md#item-fields |
| AO-PY-COG-LAMBDA | Cognitive complexity | a lambda adds no nesting level (probe py my-method2, the paper's myMethod2) | 1 | hand: Sonar paper | 2 | definition | docs/agent-json.md#item-fields |
| AO-N-PY-MATCH | Nesting depth | match adds a nesting level, as the paper's switch does (probe py get-words) | 1 | hand: Sonar paper App. B2 | 1 | fixed | docs/agent-json.md#item-fields |
| AO-N-PY-LAMBDA | Nesting depth | a lambda and a conditional expression add no nesting level (probe py my-method2) | 0 | hand: Sonar paper App. B2 | 2 | definition | docs/agent-json.md#item-fields |
| AO-N-PY-TERNARY | Nesting depth | a conditional expression adds no nesting level (probe py ternary) | 0 | hand: Sonar paper App. B2 | 1 | definition | docs/agent-json.md#item-fields |
| AO-PY-COG-CONDITION | Cognitive complexity | a comprehension in an if's condition is charged the if's body depth (probe py condition-comprehension) | 3 | hand: Sonar paper | 2 | definition | convention_only |
| AO-N-PY-CONDITION | Nesting depth | a comprehension in an if's condition sits one level inside the if (probe py condition-comprehension) | 2 | hand: Sonar paper App. B2 | 1 | definition | convention_only |
| AO-PY-COG-ELEMENT | Cognitive complexity | a comprehension's element, written before its for, is read outside the loop (probe py element-ternary) | 2 | hand: Sonar paper | 3 | definition | convention_only |
| AO-PY-COG-FILTER | Cognitive complexity | a comprehension's if adds no nesting level for a later for (probe py filter-then-for) | 5 | hand: Sonar paper | 6 | definition | convention_only |
| AO-PY-COG-ITER | Cognitive complexity | a comprehension's iterable is read inside its own loop (probe py ternary-iterable) | 3 | hand: Sonar paper | 2 | definition | convention_only |
| AO-PY-COG-INDIRECT-A | Cognitive complexity | indirect recursion is not counted (probe py is-even) | 1 | hand: Sonar paper | 2 | definition | convention_only |
| AO-PY-COG-INDIRECT-B | Cognitive complexity | indirect recursion is not counted (probe py is-odd) | 1 | hand: Sonar paper | 2 | definition | convention_only |
| D1a | Function discovery and spans | export function identity&lt;T&gt;(x: T) (probe ts identity) | 0 | hand: TypeScript handbook, Generics | 1 | defect | https://www.typescriptlang.org/docs/handbook/2/generics.html#hello-world-of-generics |
| D1b | Parameter list (params, packet.params) | a function-typed parameter (probe ts apply) | 1 | hand: ECMA-262 sec. 15.1 | 2 | defect | https://tc39.es/ecma262/2025/#sec-parameter-lists |
| D1b-END | Function discovery and spans | a function-typed parameter (probe ts apply) | 1 | hand: ECMA-262 sec. 15.2 | 3 | defect | https://tc39.es/ecma262/2025/#sec-parameter-lists |
| D1c | Function discovery and spans | the function after a regular expression holding a backtick (probe ts after-regex) | 0 | hand: ECMA-262 sec. 12.9.5 | 1 | defect | https://tc39.es/ecma262/2025/#sec-literals-regular-expression-literals |
| D1c-END | Function discovery and spans | a regular expression holding a backtick (probe ts has-tick) | 7 | hand: ECMA-262 sec. 12.9.5 | 3 | defect | https://tc39.es/ecma262/2025/#sec-literals-regular-expression-literals |
| D2a | ccn_std, ccn_mod and gated ccn | `a ?? 0` (probe ts nullish-pick) | 3 | hand: NIST SP 500-235 sec. 4.1 | 2 | defect | paper: NIST SP 500-235 sec. 4.1 |
| D2b | Cognitive complexity | `a ?? 0` (probe ts nullish-pick) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Ignore shorthand |
| D2c | ccn_std, ccn_mod and gated ccn | `a ??= 1` (probe ts nullish-assign) | 3 | hand: NIST SP 500-235 sec. 4.1 | 2 | defect | paper: NIST SP 500-235 sec. 4.1 |
| D2d | Cognitive complexity | `a ??= 1` (probe ts nullish-assign) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Ignore shorthand |
| AO-TS-COG-RUNS-NOT | Cognitive complexity | if (a &amp;&amp; !(b &amp;&amp; c)) (probe ts logical-not) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-TS-END | Function discovery and spans | a function with a return type ends at its closing brace (probe ts straight) | 7 | hand: ECMA-262 sec. 15.2 | 5 | defect | https://tc39.es/ecma262/2025/#sec-function-definitions |
| AO-TS-NLOC-END | nloc | the next statement's line counts toward a function with a return type (probe ts straight) | 6 | hand: lizard README nloc | 5 | defect | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-VUE-LINES | Function discovery and spans | a method in a &lt;script&gt; block below a &lt;template&gt; (probe vue label) | 5 | hand: Vue 3 SFC spec | 9 | defect | https://vuejs.org/api/sfc-spec.html#language-blocks |
| AO-TS-CASE-LABELS | ccn_std, ccn_mod and gated ccn | two case labels on one statement (probe ts kind) | 4 | hand: NIST SP 500-235 sec. 4.1 | 3 | definition | paper: NIST SP 500-235 sec. 4.1 (a model where each case label adds one is consistent, not the typical usage) |
| AO-TS-PARAMS-DESTRUCTURE | Parameter list (params, packet.params) | ({ a, b }, [c]) counts the names it binds (probe ts destructured) | 3 | hand: ECMA-262 sec. 15.1 | 2 | definition | convention_only |
| AO-JS-COG-TERNARY | Cognitive complexity | a conditional expression in another's alternative adds no nesting (probe cjs sign) | 2 | hand: Sonar paper | 3 | definition | convention_only |
| AO-N-TS-LABELED | Nesting depth | for, for, if under a labeled loop (probe ts sum-of-primes) | 3 | hand: Sonar paper App. B2 | 3 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-TS-SWITCH | Nesting depth | a switch's case adds a level (probe ts get-words) | 1 | hand: Sonar paper App. B2 | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-TS-CONDITION | Nesting depth | a logical operator in a condition adds a level (probe ts logical-mix) | 1 | hand: Sonar paper App. B2 | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-TS-ELSE | Nesting depth | an if inside an else adds no level for the else (probe ts nested-in-else) | 2 | hand: Sonar paper App. B2 | 2 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-TS-CASE | Nesting depth | a switch's case adds a level (probe ts kind) | 1 | hand: Sonar paper App. B2 | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-PY-NAME-CLASS | Python reader: spans, names, inline_body, unread-def net | a method is named without its class (shape methods) | method | Python ast (PEP 3155 __qualname__) | Holder.method | definition | docs/upgrading.md#analysis-version-11 |
| AO-PY-NAME-LOCALS | Python reader: spans, names, inline_body, unread-def net | a nested def names each enclosing def once, without &lt;locals&gt; (shape three_deep) | outer.middle.inner | Python ast (PEP 3155 __qualname__) | outer.&lt;locals&gt;.middle.&lt;locals&gt;.inner | definition | docs/upgrading.md#analysis-version-11 |
| AO-TS-END-APPEND | Function discovery and spans | a function with a return type, last in its file, grows to the function appended after it (ts/generic.ts afterGeneric ends at 7, then 9) | 2 | metamorphic: an appended function moves no earlier row | 0 | defect | https://tc39.es/ecma262/2025/#sec-function-definitions |
| D1c-APPEND | Function discovery and spans | a function appended after a regular expression holding a backtick is swallowed by the function before it (ts/regex.ts hasTick ends at 7, then 14) | 7 | metamorphic: an appended function moves no earlier row | 0 | defect | https://tc39.es/ecma262/2025/#sec-literals-regular-expression-literals |
| AO-RADON-ASSERT | ccn_std, ccn_mod and gated ccn | radon adds 1 for an assert; lizard counts no assert keyword, only the and/or in its test (hand case AO-RADON-ASSERT) | 1 | radon 6.0.1 | 2 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_languages/python.py#L42 |
| AO-RADON-FINALLY | ccn_std, ccn_mod and gated ccn | a try's finally is a decision to lizard's Python reader, not to radon (hand case AO-RADON-FINALLY) | 3 | radon 6.0.1 | 2 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_languages/python.py#L42 |
| AO-RADON-ELSE | ccn_std, ccn_mod and gated ccn | radon adds 1 for a loop's or a try's else; lizard counts no else (hand case AO-RADON-ELSE) | 2 | radon 6.0.1 | 3 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_languages/python.py#L42 |
| AO-RADON-GUARD | ccn_std, ccn_mod and gated ccn | a case guard's if is a decision to lizard, not to radon (hand case AO-RADON-GUARD) | 4 | radon 6.0.1 | 3 | definition | paper: NIST SP 500-235 sec. 4.1 (each binary decision adds one) |
| AO-RADON-CLASS-BODY | ccn_std, ccn_mod and gated ccn | radon scores a class body nested in a def as the class's; crapkit charges its decisions to the def whose lines hold them (hand case AO-RADON-CLASS-BODY) | 2 | radon 6.0.1 | 1 | definition | docs/agent-json.md#item-fields |
| AO-MCCABE-TRY | ccn_std, ccn_mod and gated ccn | mccabe's graph adds 1 for a try besides one per handler; lizard counts each except (hand case AO-MCCABE-TRY) | 2 | mccabe 0.7.0 | 3 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_languages/python.py#L42 |
| AO-MCCABE-EXPRESSIONS | ccn_std, ccn_mod and gated ccn | mccabe graphs statements only; and/or, a conditional expression and a comprehension's for and if are decisions to crapkit (hand case AO-MCCABE-EXPRESSIONS) | 2 | mccabe 0.7.0 | 1 | definition | paper: NIST SP 500-235 sec. 4.1 (each binary decision adds one) |
| AO-MCCABE-SKIPPED | ccn_std, ccn_mod and gated ccn | mccabe never walks a match statement or a finally body (hand case AO-MCCABE-SKIPPED) | 4 | mccabe 0.7.0 | 1 | definition | paper: NIST SP 500-235 sec. 4.1 (a multiway decision adds one per case) |
| AO-MCCABE-NESTED | ccn_std, ccn_mod and gated ccn | mccabe folds a nested def into its parent's graph; crapkit lists it as its own function (hand case AO-MCCABE-NESTED) | 1 | mccabe 0.7.0 | 3 | definition | docs/agent-json.md#item-fields |
| AO-CXP-TERNARY | Cognitive complexity | a conditional expression in another's branch: complexipy nests it (the paper's B2), crapkit reads it flat (hand case AO-CXP-TERNARY) | 2 | complexipy 8.0.1 | 3 | definition | docs/agent-json.md#item-fields |
| AO-CXP-LAMBDA | Cognitive complexity | a conditional expression in a lambda: complexipy nests the lambda (the paper's B2), crapkit does not (hand case AO-CXP-LAMBDA) | 1 | complexipy 8.0.1 | 2 | definition | docs/agent-json.md#item-fields |
| AO-CXP-ELEMENT | Cognitive complexity | a conditional expression as a comprehension's element: complexipy reads it one level in, crapkit outside the loop (hand case AO-CXP-ELEMENT) | 2 | complexipy 8.0.1 | 3 | definition | convention_only |
| AO-CXP-ITER | Cognitive complexity | a conditional expression as a comprehension's iterable: crapkit reads it inside the loop, complexipy at the comprehension's level (hand case AO-CXP-ITER) | 3 | complexipy 8.0.1 | 2 | definition | convention_only |
| AO-CXP-CONDITION | Cognitive complexity | a comprehension in an if's condition: crapkit charges the if's body level, complexipy the if's own (hand case AO-CXP-CONDITION) | 3 | complexipy 8.0.1 | 2 | definition | convention_only |
| AO-CXP-COMP-FLAT | Cognitive complexity | a comprehension's second for: complexipy +1 flat, crapkit +2 as a loop in a loop (hand case AO-CXP-COMP-FLAT) | 3 | complexipy 8.0.1 | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 and B3 (a loop nested in a loop) |
| AO-CXP-COMP-FILTER | Cognitive complexity | a comprehension's if: complexipy +1 flat, crapkit +2 as an if inside the loop (hand case AO-CXP-COMP-FILTER) | 3 | complexipy 8.0.1 | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 and B3 (an if nested in a loop) |
| AO-CXP-LOOP-ELSE | Cognitive complexity | a loop's else: complexipy adds nothing, crapkit +1 (oracle side set aside from the comparison) (hand case AO-CXP-LOOP-ELSE) | 2 | complexipy 8.0.1 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (else is an increment) |
| AO-CXP-RECURSION-NAME | Cognitive complexity | a call through a parameter named like the def: complexipy +1 as recursion (it reads any call spelled like the def, a method's call to the module function of its name too), crapkit 0 since the call never reaches the def (hand case AO-CXP-RECURSION-NAME) | 0 | complexipy 8.0.1 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-CXP-GUARD | Cognitive complexity | a match case guard: complexipy adds nothing for it, as the paper names no guard; crapkit charges it as an if one level inside the match, +2 at the top of a function (hand case AO-CXP-GUARD) | 3 | complexipy 8.0.1 | 1 | definition | docs/configuration.md#per-language-gotchas |
| AO-CXP-SKIPS | Cognitive complexity | a conditional expression inside a binary operator: complexipy reads nothing there (an oracle bug; such defs are set aside) (hand case AO-CXP-SKIPS) | 1 | complexipy 8.0.1 | 0 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (the ternary operator is an increment) |
| AO-CXP-NESTED-DEF | Cognitive complexity | a nested def's if: complexipy charges it to the enclosing def, crapkit to the nested def's own row (such defs are set aside) (hand case AO-CXP-NESTED-DEF) | 0 | complexipy 8.0.1 | 1 | definition | docs/agent-json.md#item-fields |
| AO-N-PYLINT-TRY | Nesting depth | an if inside a try body: pylint opens a level for the try, crapkit does not (hand case AO-N-PYLINT-TRY) | 1 | pylint 4.0.9 R1702 | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 (try adds no nesting) |
| AO-N-PYLINT-EXCEPT | Nesting depth | an if inside an except body: crapkit opens a level for the except, pylint reads the handler beside the try (hand case AO-N-PYLINT-EXCEPT) | 2 | pylint 4.0.9 R1702 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 (catch adds nesting) |
| AO-N-PYLINT-COMPREHENSION | Nesting depth | a comprehension's for: crapkit opens a level, pylint counts no expression (hand case AO-N-PYLINT-COMPREHENSION) | 1 | pylint 4.0.9 R1702 | 0 | definition | docs/agent-json.md#item-fields |
| AO-VUE-ROWS | Function discovery and spans | a .vue method's row starts on its &lt;script&gt; block's line count, not the file's (vue/label.vue: a row on line 5, none on line 9) | 1 | TypeScript 6.0.2 compiler | 0 | defect | https://vuejs.org/api/sfc-spec.html#language-blocks |
| AO-JS-OBJECT-MEMBERS | Function discovery and spans | a method and a function expression in an object literal (js/object_methods.js: no row on line 2) | 0 | TypeScript 6.0.2 compiler | 1 | defect | https://tc39.es/ecma262/2025/#sec-method-definitions |
| AO-JS-TEMPLATE-ARROW | JS/TS expression arrows and template literals | an arrow inside a template literal's substitution (js/template_arrow.js: no row on line 2) | 0 | TypeScript 6.0.2 compiler | 1 | defect | https://tc39.es/ecma262/2025/#sec-template-literals |
| AO-TS-OVERLOADS | Function discovery and spans | an overload signature with no body is listed as a function (js/overloads.ts: a row on line 1) | 1 | TypeScript 6.0.2 compiler | 0 | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#function-overloads |
| AO-TS-END-SPAN | Function discovery and spans | a function with a return type ends on the next function's line, 2 lines past its brace (ts/flow.ts straight: 7 for 5) | 2 | TypeScript 6.0.2 compiler | 0 | defect | https://tc39.es/ecma262/2025/#sec-function-definitions |
| D1b-SPAN | Function discovery and spans | a function-typed parameter ends the function 2 lines early (ts/fntype.ts apply: 1 for 3) | -2 | TypeScript 6.0.2 compiler | 0 | defect | https://tc39.es/ecma262/2025/#sec-parameter-lists |
| D1c-SPAN | Function discovery and spans | a regular expression holding a backtick runs its function 4 lines past its brace (ts/regex.ts hasTick: 7 for 3) | 4 | TypeScript 6.0.2 compiler | 0 | defect | https://tc39.es/ecma262/2025/#sec-literals-regular-expression-literals |
| AO-JS-ARROW-PARAMS | Parameter list (params, packet.params) | an arrow function's parameters read 0 (js/callbacks.js: (x) =&gt; x * 2) | 0 | TypeScript 6.0.2 compiler | 1 | defect | https://tc39.es/ecma262/2025/#sec-arrow-function-definitions |
| AO-ESLINT-DEFAULTS | ccn_std, ccn_mod and gated ccn | a default parameter value: ESLint adds 1, crapkit adds nothing, as no Python tool does for a default (ts/shapes.ts withDefaults) | 1 | ESLint 10.11.0 complexity | 2 | definition | convention_only |
| AO-TS-OPTIONAL-CHAIN | ccn_std, ccn_mod and gated ccn | optional chaining `a?.b`: one short-circuit decision, which crapkit does not count (probe ts optional-read) | 1 | hand: NIST SP 500-235 sec. 4.1; ESLint complexity | 2 | defect | paper: NIST SP 500-235 sec. 4.1 (each binary decision adds one); ECMA-262 sec. 13.3.9 |
| AO-SONARJS-RECURSION | Cognitive complexity | direct recursion: the paper adds 1, sonarjs adds nothing (ts/sonar.ts factorial) | 2 | eslint-plugin-sonarjs 4.2.1 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-SONARJS-OR | Cognitive complexity | a sequence of \|\| : the paper adds 1, sonarjs 4.2.1 counts none (js/callbacks.js `(a) =&gt; a \|\| 0`); a function holding \|\| is set aside from the sonarjs comparison | 1 | eslint-plugin-sonarjs 4.2.1 | 0 | definition | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-N-ESLINT-ELSE | Nesting depth | an if inside an else: ESLint's max-depth counts the else's block, lizard's ND does not (ts/flow.ts nestedInElse) | 2 | ESLint 10.11.0 max-depth | 2 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-ESLINT-SWITCH | Nesting depth | a switch's cases: lizard's ND adds a level for the case, ESLint only for the switch (ts/flow.ts kind) | 1 | ESLint 10.11.0 max-depth | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-ESLINT-TERNARY | Nesting depth | a conditional expression: lizard's ND adds a level, ESLint counts no expression (ts/flow.ts pick) | 1 | ESLint 10.11.0 max-depth | 0 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-ESLINT-LABEL | Nesting depth | loops under a labeled statement: ESLint counts the labeled body's block, lizard's ND does not (ts/sonar.ts sumOfPrimes) | 3 | ESLint 10.11.0 max-depth | 3 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-ESLINT-TRY | Nesting depth | blocks inside a try: ESLint counts the try block, lizard's ND does not (ts/sonar.ts myMethod) | 3 | ESLint 10.11.0 max-depth | 4 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-ESLINT-LOGICAL | Nesting depth | a logical operator in a condition: lizard's ND adds a level, ESLint counts no expression (ts/sonar.ts logicalMix) | 1 | ESLint 10.11.0 max-depth | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-ND-LOOPS | Nesting depth | three loops, one inside another (probe &lt;lang&gt; eq-nested-loops: C, C++, Objective-C, Java, JS, TS, Vue, Go, Zig, shell) | 3 | hand: Sonar paper App. B2 | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SH-NESTING-DEEP | Nesting depth | four shell ifs, one inside another (probe sh eq-four-deep) | 4 | hand: Sonar paper App. B2 | 4 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SH-NESTING-FLAT | Nesting depth | seven shell ifs side by side (probe sh eq-flat-seven) | 1 | hand: Sonar paper App. B2 | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-RS-MATCH-MOD | Rust reader (match arms) | a Rust match's arms in ccn_mod (probe rs eq-seven-arms) | 8 | hand: lizard README option -m | 2 | definition | README.md#languages |
| AO-PS-SWITCH-MOD | PowerShell reader | a PowerShell switch in ccn_mod (probes ps1 ps-kind and ps-switch-index) | 3 | hand: lizard README option -m | 2 | definition | README.md#languages |
| D3 | PowerShell reader | a switch arm whose test is a script block (probe ps1 ps-size) | 3 | hand: NIST SP 500-235 sec. 4.1 | 3 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| D12 | Parameter list (params, packet.params) | a param() block (probe ps1 ps-param-block) | 2 | hand: PowerShell about_Functions_Advanced_Parameters | 2 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_functions_advanced_parameters |
| AO-PS-NEST-LOGICAL | Nesting depth | -and and -or in an if's condition (probe ps1 ps-logic) | 1 | hand: Sonar paper App. B2 | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-PS-XOR | PowerShell reader | -xor evaluates both operands, and crapkit counts it as a decision (probe ps1 ps-xor) | 2 | hand: PowerShell about_Logical_Operators | 1 | definition | convention_only |
| D2e | ccn_std, ccn_mod and gated ccn | PowerShell `$a ?? 0` (probe ps1 ps-coalesce) | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| D2f | Cognitive complexity | PowerShell `$a ?? 0` (probe ps1 ps-coalesce) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Ignore shorthand |
| AO-PS-KEYWORD-CASE | PowerShell reader | an uppercase IF (probe ps1 ps-upper-case) | 2 | hand: PowerShell about_Language_Keywords | 2 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_language_keywords |
| AO-PS-MOD-OVER-STD | PowerShell reader | rows whose ccn_mod exceeds their ccn_std (the probe PowerShell switches Get-Kind, Get-Size, Get-Indexed and Get-CapitalDefault, and Test-SwitchParam's [switch] parameter, analysis-oracles-110) | 0 | hand: lizard README option -m | 0 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-PY-NLOC-FSTRING | nloc | an f-string that spans lines (probe py fstring-lines) | 1 | hand: lizard README nloc | 5 | defect | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-PY-NLOC-TRIPLE | nloc | a triple-quoted string after yield (probe py triple-yield) | 2 | hand: lizard README nloc | 4 | defect | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-PY-NLOC-PREFIX | nloc | a prefixed triple-quoted string (r, b, u) inside an expression (probe py raw-triple) | 2 | hand: lizard README nloc | 4 | defect | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-N-CONDITION-C | Nesting depth | a logical operator in a parenthesized condition adds a level (probe c, cpp, objc, java c-logic) | 1 | hand: Sonar paper | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-LOGICAL-NO-PARENS | Nesting depth | each &amp;&amp; and \|\| in a condition without parentheses (probe go, rs, swift, sh c-logic) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-N-CASE-C | Nesting depth | a switch's case adds a level (probe c, cpp, objc, java c-pick) | 1 | hand: Sonar paper | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-CASES-STACK | Nesting depth | three case labels side by side (probe go, swift c-pick) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-N-MATCH | Nesting depth | a Rust match and a Zig switch open no level: lizard's ND set names `case`, and neither has one (probe rs, zig c-pick) | 1 | hand: Sonar paper | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-N-SH-CASE | Nesting depth | a shell case (probe sh c-pick) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-ND-LOOPS-IF | Nesting depth | an if inside two nested loops (probe c, cpp, objc, java, go c-outer) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-COG-RECURSION-BRACE | Cognitive complexity | direct recursion (probe go, java, rs, sh, swift, zig c-fact) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-COG-WHILE-AFTER-BRACE | Cognitive complexity | a while loop right after another loop's closing brace (probe rs, zig c-loops) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-SWIFT-COG-REPEAT | Cognitive complexity | a repeat-while loop (probe swift c-loops) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-SWIFT-COG-DO | Cognitive complexity | the do block of a do/catch (probe swift c-guarded) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-ZIG-COG-ELSE-PRONG | Cognitive complexity | a Zig switch's else prong (probe zig c-pick) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-ZIG-ELSE-PRONG | ccn_std, ccn_mod and gated ccn | a Zig switch's else prong (probe zig c-pick) | 4 | hand: NIST SP 500-235 sec. 4.1 | 4 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-ZIG-SWITCH-MOD | ccn_std, ccn_mod and gated ccn | a Zig switch with three prongs and else (probe zig c-pick) | 2 | hand: lizard README option -m | 2 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-ZIG-MOD-OVER-STD | ccn_std, ccn_mod and gated ccn | Zig rows whose ccn_mod exceeds their ccn_std (the switch picks in probes shapes.zig and constructs.zig) | 0 | hand: lizard README option -m | 0 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-SH-CASE-DEFAULT | Shell reader | a case's default arm `*)` counts: the shell reader counts each `;;` arm terminator (probe sh c-pick) | 5 | hand: NIST SP 500-235 sec. 4.1 | 4 | definition | convention_only |
| AO-SH-CASE-MOD | Shell reader | a shell case with three arms and a default (probe sh c-pick) | 5 | hand: lizard README option -m | 2 | definition | README.md#languages |
| AO-RS-MATCH-MOD-PICK | Rust reader (match arms) | a match with three arms and `_` (probe rs c-pick) | 4 | hand: lizard README option -m | 2 | definition | README.md#languages |
| AO-C-VOID-FNPTR-PARAM | Parameter list (params, packet.params) | a lone parameter pointing to a function or block that returns void (probe cpp, objc c-guarded) | 1 | hand: ISO/IEC 9899:2018 sec. 6.9.1 | 1 | fixed | https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2310.pdf#page=166 |
| AO-GO-FUNC-TYPE | Function discovery and spans | a var declaration of a function type before a function (probe go g-func-type) | 1 | hand: Go spec | 1 | fixed | https://go.dev/ref/spec#Function_types |
| AO-GO-INTERFACE-PARAM | Parameter list (params, packet.params) | a parameter of type interface{} (probe go g-interface-param) | 2 | hand: Go spec | 2 | fixed | https://go.dev/ref/spec#Function_types |
| AO-GO-FUNC-PARAM | Parameter list (params, packet.params) | a parameter of function type (probe go g-func-param) | 1 | hand: Go spec | 1 | fixed | https://go.dev/ref/spec#Function_types |
| AO-NLOC-CLOSE-LINE | nloc | the line where a nested function literal ends (probe go g-wrap) | 4 | hand: lizard README nloc | 5 | definition | convention_only |
| AO-ND-DEF | Nesting depth | a parameter named def (probe go g-def) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-COG-KEYWORD-NAMES-BRACE | Cognitive complexity | a call to a function named do (probe go g-do-call) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-N-ELSE | Nesting depth | an if inside an else block: lizard's ND adds no level for the else (probe go g-nested-else) | 2 | hand: Sonar paper | 2 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-GO-ND-INIT | Nesting depth | an if with an initializer statement after another if (probe go g-init-after-if) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-COG-RUNS-GO | Cognitive complexity | a case label A &amp;&amp; !B &amp;&amp; C &amp;&amp; !D(x, y) (probe go g-case-run) | 7 | hand: Sonar paper | 7 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-COG-RUNS-LINES | Cognitive complexity | an &amp;&amp; sequence broken over two lines (probe go g-long-run) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-RS-TRY-COG | Cognitive complexity | the ? operator (probe rs r-try) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-RS-TRY-ND | Nesting depth | the ? operator (probe rs r-try) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-RS-LETELSE | ccn_std, ccn_mod and gated ccn | a let-else (probe rs r-let-else) | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-RS-LETELSE-ND | Nesting depth | a let-else's else block: lizard's ND token set holds no let-else (probe rs r-let-else) | 1 | hand: Sonar paper | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-RS-EMPTY-CLOSURE | ccn_std, ccn_mod and gated ccn | a closure with no parameters, `move \|\| n` (probe rs r-empty-closure) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-RS-EMPTY-CLOSURE-COG | Cognitive complexity | a closure with no parameters, `move \|\| n` (probe rs r-empty-closure) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-RS-MAYBE-BOUND | ccn_std, ccn_mod and gated ccn | a ?Sized bound (probe rs r-maybe-sized) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-RS-MAYBE-BOUND-COG | Cognitive complexity | a ?Sized bound (probe rs r-maybe-sized) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-RS-MAYBE-BOUND-ND | Nesting depth | a ?Sized bound (probe rs r-maybe-sized) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-RS-TUPLE-PARAM | Parameter list (params, packet.params) | a parameter of tuple type (probe rs r-tuple-param) | 3 | hand: Rust Reference | 3 | fixed | https://doc.rust-lang.org/reference/items/functions.html#function-parameters |
| AO-RS-TRAIT-SIG | Function discovery and spans | a trait method with no body before a function (probe rs r-trait-sig) | 1 | hand: Rust Reference | 1 | fixed | https://doc.rust-lang.org/reference/items/traits.html |
| AO-RS-LOOP-COG | Cognitive complexity | a loop holding an if (probe rs r-spin) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-RS-LOOP-ND | Nesting depth | a loop holding an if (probe rs r-spin) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-RS-WHERE | ccn_std, ccn_mod and gated ccn | a where clause (probe rs r-where) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-COG-GUARD | Cognitive complexity | a match arm guard, or a Python case guard, reads as an if (+1 and the nesting level); the paper names no guard (probe rs r-guard; hand case AO-CXP-GUARD for Python) | 3 | hand: Sonar paper | 1 | definition | convention_only |
| AO-RS-LETELSE-COG | Cognitive complexity | a let-else inside a loop: crapkit charges its else +1 flat, the counter charges it as an if let, +1 and the nesting level (probe rs r-nested-let-else) | 2 | hand: Sonar paper | 3 | definition | convention_only |
| AO-N-CLOSURE | Nesting depth | a closure: lizard's ND counts no closure (probe rs r-closure-depth) | 0 | hand: Sonar paper | 1 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-LOOP-NO-CONDITION | ccn_std, ccn_mod and gated ccn | a loop with no condition (Go `for {}`) counts as a decision: lizard counts the keyword (probe go g-spin) | 3 | hand: NIST SP 500-235 sec. 4.1 | 2 | definition | convention_only |
| AO-RS-ARM-JUMP | Cognitive complexity | an unlabeled continue ending a match arm (probe rs r-arm-continue) | 5 | hand: Sonar paper | 5 | fixed | paper: Sonar Cognitive Complexity v1.7 Jumps to labels |
| AO-ND-SIBLING | Nesting depth | an if after a statement, inside a structure that follows another if (probe c c-sibling) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-C-PREPROC | ccn_std, ccn_mod and gated ccn | an #ifdef ... #else inside a function counts as a decision (probe c c-decimal-point) | 2 | hand: NIST SP 500-235 sec. 4.1 | 1 | definition | convention_only |
| AO-C-PREPROC-COG | Cognitive complexity | the code in every arm of an #if, #ifdef or #else counts as written, where the oracles read the arms otherwise (clang-tidy reads one arm, after the preprocessor), so a function holding a directive is set aside (probe c c-decimal-point: no structure in either arm, 0 on both sides; crapkit read 1 there until calc-bug analysis-oracles-3, `lconv-&gt;decimal_point` taken for recursion) | 0 | hand: Sonar paper | 0 | definition | convention_only |
| AO-C-PREPROC-NLOC | nloc | an #ifdef ... #else inside a function: only the #ifdef branch is read (probe c c-decimal-point) | 6 | hand: lizard README nloc | 9 | definition | convention_only |
| AO-SH-QUOTED-SUBST | Shell reader | a &amp;&amp; inside a double-quoted command substitution (probe sh s-quoted-subst) | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-ZIG-STRUCT-RETURN | Function discovery and spans | a function returning an anonymous struct (probe zig z-pair) | 7 | hand: Zig language reference | 7 | fixed | https://ziglang.org/documentation/0.15.1/#Functions |
| AO-ZIG-FN-PARAM | Parameter list (params, packet.params) | a parameter of function type (probe zig z-fn-param) | 3 | hand: Zig language reference | 3 | fixed | https://ziglang.org/documentation/0.15.1/#Functions |
| AO-ZIG-TRY-ND | Nesting depth | three try statements (probe zig z-tries) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-ZIG-OPTIONAL | Cognitive complexity | an optional return type ?usize (probe zig z-optional) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-ZIG-OPTIONAL-ND | Nesting depth | an optional return type ?usize (probe zig z-optional) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-ZIG-PAYLOAD-ELSE | Cognitive complexity | else \|err\| if (probe zig z-payload-else) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-ZIG-ARM-JUMP | Cognitive complexity | an unlabeled continue ending a switch prong (probe zig z-prong-continue) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 Jumps to labels |
| AO-ND-BRACELESS | Nesting depth | a braceless if before a loop (probe c c-braceless) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SH-HEREDOC-NLOC | nloc | a heredoc body counts no line: the shell reader blanks it as data (probe sh s-heredoc) | 3 | hand: lizard README nloc | 6 | definition | convention_only |
| AO-JAVA-ENUM-BODY | Function discovery and spans | a method inside an enum constant's body (probe java j-enum-body) | 1 | hand: JLS | 1 | fixed | https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.9.1 |
| AO-JAVA-ANNOTATION-NAME | Function discovery and spans | an annotation with arguments after another annotation (probe java j-annotated) | 1 | hand: JLS | 1 | fixed | https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.4 |
| AO-JAVA-LOCAL-ANNOTATION | Function discovery and spans | a method after one that annotates a local variable (probe java j-local-annotation) | 1 | hand: JLS | 1 | fixed | https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.4 |
| AO-JAVA-FIELD-ANON | Function discovery and spans | a method of an anonymous class in a field initializer (probe java j-field-anon) | 1 | hand: JLS | 1 | fixed | https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.4 |
| AO-JAVA-ANNOTATION-DEFAULT | Function discovery and spans | an annotation element with an array default {} (probe java j-annotation-default) | 0 | hand: JLS | 0 | fixed | https://docs.oracle.com/javase/specs/jls/se21/html/jls-9.html#jls-9.6.2 |
| AO-JAVA-ANON-FIELD-NLOC | nloc | a field of an anonymous class inside a method counts for no function (probe java j-anon-field) | 6 | hand: lizard README nloc | 6 | fixed | convention_only |
| AO-COG-TERNARY-NEST | Cognitive complexity | a conditional expression in another's operand: the inner one pays no nesting, as crapkit's cognitive extension documents (probe java j-nested-ternary) | 2 | hand: Sonar paper | 3 | definition | convention_only |
| AO-N-TRY | Nesting depth | a loop inside a try block: lizard's ND token set holds try (probe java j-try-loop) | 1 | hand: Sonar paper | 1 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-OBJC-PARAMS | Parameter list (params, packet.params) | a method with two arguments, `- (int)pairFor:(int)a to:(int)b` (probe objc o-params) | 2 | hand: Apple, Programming with Objective-C | 2 | fixed | https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/ProgrammingWithObjectiveC/DefiningClasses/DefiningClasses.html |
| AO-OBJC-IVAR-BLOCK | Function discovery and spans | a class extension with an instance-variable block (probes objc o-extension, o-extension-adopting) | 0 | hand: Apple, Programming with Objective-C | 0 | fixed | https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/ProgrammingWithObjectiveC/CustomizingExistingClasses/CustomizingExistingClasses.html |
| AO-OBJC-SELECTOR-RECURSION | Cognitive complexity | a message to self with a shorter selector, and a message to super (probes objc o-narrower, o-super) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-COG-CLOSURE | Cognitive complexity | an if inside a closure, lambda or block literal: crapkit raises the nesting level only inside if, switch, loops and catch, never inside a closure (probes rs r-closure-if, java j-closure-if, cpp c-closure-if, swift s-closure-if, objc o-block, ps1 ps-script-block-if) | 1 | hand: Sonar paper | 2 | definition | src/crapkit/lizardcognitive.py#L10 |
| AO-COG-RECURSION-NAME-C | Cognitive complexity | a local variable with the function's name (probes c c-name-variable, objc o-name-variable) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-C-DIRECTIVE-NLOC | nloc | #pragma lines inside a function are not counted, like the #ifdef lines of AO-C-PREPROC-NLOC (probe objc o-pragmas) | 4 | hand: lizard README nloc | 7 | definition | convention_only |
| AO-N-CLOSURE-IF | Nesting depth | an if inside a closure, lambda or block literal: lizard's ND raises no level for the closure, so the if reads one level (probes rs r-closure-if, java j-closure-if, cpp c-closure-if, swift s-closure-if, objc o-block) | 1 | hand: Sonar paper | 2 | definition | https://github.com/terryyin/lizard/blob/1.24.0/lizard_ext/lizardnd.py#L32 |
| AO-ND-TERNARY-PAIR | Nesting depth | two conditional expressions side by side, neither inside the other (probe c c-two-ternaries) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-REFUSED-SAME-BYTES | Unanalyzable files and twin-name notes | two refused files with identical bytes (count on stderr) | 2 | hand: CONTEXT.md Unanalyzable file | 2 | fixed | CONTEXT.md#unanalyzable-file |
| AO-DUP-CONTAINED | Near-duplicate functions | a twin in another file holding every shingle of the target (brief duplication_twins contained; outer.copy_a for copy_a) | false | docs: agent-json.md:490 | false | fixed | docs/agent-json.md#duplication-twins (:490 and :1175 read contained as nested spans) |
| AO-DUP-ENCLOSING-UNDER-MIN | Near-duplicate functions | a 3-line factory pairs, through its 10-line closure, with each of the 3 functions the closure pairs with | 0 | hand: README duplication --min-lines 8 | 0 | fixed | README.md#commands (duplication row: --min-lines 8, a function and its nested closure never pair) |
| AO-INIT-JEST-NO-JUNIT | init scaffolding | a jest repo without jest-junit in devDependencies: results_artifact on the lane init writes | absent | docs: agent-json.md:1017 | present | definition | docs/lanes.md#jest (:495 says to drop the reporter flags and results_artifact without jest-junit) |
| AO-PKT-TYPE-SPACING | Parameter list (params, packet.params) | a subscripted annotation b: list[int] (m.py typed) | list [ int ] | hand: ast.unparse (Python reference 8.7) | list[int] | definition | docs/agent-json.md#item-fields |
| AO-PKT-TS-REST | Parameter list (params, packet.params) | a rest parameter ...rest (t.ts withRest) | a,rest | TypeScript 6.0.2 compiler ParameterDeclaration | a,...rest | definition | convention_only |
| AO-PKT-PY-SEPARATORS | Parameter list (params, packet.params) | def posonly(a, /, b, *, c) (m.py posonly) | a,/,b,*,c | ast arguments (Python reference 8.7) | a,b,c | defect | https://docs.python.org/3/reference/compound_stmts.html#function-definitions |
| AO-PKT-PY-BARE-STAR | Parameter list (params, packet.params) | def kwonly(*, key) (m.py kwonly) | *,key | ast arguments (Python reference 8.7) | key | defect | https://docs.python.org/3/reference/compound_stmts.html#function-definitions |
| AO-PKT-PY-STAR-SPACE | Parameter list (params, packet.params) | an annotated *rest: str (m.py annotated_star) | a,* rest | ast arguments (Python reference 8.7) | a,*rest | defect | https://docs.python.org/3/reference/compound_stmts.html#function-definitions |
| AO-PKT-TS-TYPES | Parameter list (params, packet.params) | a: number, b?: string (t.ts annotated) | null,null | TypeScript 6.0.2 compiler ParameterDeclaration | number,string | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html |
| AO-PKT-TS-GENERIC | Parameter list (params, packet.params) | b: Map&lt;string, number[]&gt; (t.ts generic) | null,Map | TypeScript 6.0.2 compiler ParameterDeclaration | null,Map&lt;string,number[]&gt; | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html |
| AO-PKT-TS-DESTRUCTURE-TYPE | Parameter list (params, packet.params) | { x, y }: { x: number; y: number } (t.ts destructured) | null,xy | TypeScript 6.0.2 compiler ParameterDeclaration | {x:number;y:number} | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html |
| AO-PKT-TS-THIS | Parameter list (params, packet.params) | put(this: Shelf, item) (t.ts Shelf.put) | this,item | TypeScript 6.0.2 compiler ParameterDeclaration | item | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#declaring-this-in-a-function |
| AO-TS-PARAMS-THIS | Parameter list (params, packet.params) | put(this: Shelf, item) counts this (t.ts Shelf.put) | 2 | TypeScript 6.0.2 compiler ParameterDeclaration | 1 | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#declaring-this-in-a-function |
| AO-GO-COG-SELECT | Cognitive complexity | a Go select statement with two cases and a default (probe go g-select) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 (switch); https://go.dev/ref/spec#Select_statements |
| AO-GO-SELECT-MOD | ccn_std, ccn_mod and gated ccn | a Go select with two cases and a default in ccn_mod (probe go g-select) | 2 | hand: lizard README option -m | 2 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-GOCYCLO-FUNC-LITERAL | ccn_std, ccn_mod and gated ccn | gocyclo adds a func literal's decisions to the function that holds it, where crapkit scores the literal as its own row (hand case AO-GOCYCLO-FUNC-LITERAL: the parent's if plus the literal's if) | 2 | gocyclo 0.6.0 | 3 | definition | https://github.com/fzipp/gocyclo/blob/v0.6.0/README.md |
| AO-GOCOGNIT-ELSE-NESTING | Cognitive complexity | gocognit walks a plain else block at its if's nesting level, so a for inside an else scores +1 and an if inside that for +2, where the Sonar paper's B3 raises the level for an else and gives +2 and +3 (hand case AO-GOCOGNIT-ELSE-NESTING) | 7 | gocognit 1.2.1 | 5 | definition | paper: Sonar Cognitive Complexity v1.7 App. B3 (else increases the nesting level) |
| AO-GOCOGNIT-FUNC-LITERAL | Cognitive complexity | gocognit scores a func literal's structures in the function that holds it, one level deeper, where crapkit scores the literal as its own row from level 0 (hand case AO-GOCOGNIT-FUNC-LITERAL: set aside) | 1 | gocognit 1.2.1 | 3 | definition | paper: Sonar Cognitive Complexity v1.7 App. B3 (nested functions increase the nesting level) |
| AO-REVIVE-ELSE | Nesting depth | revive nests a plain else block, and each else if, one level below its if; lizard's ND adds no level for an else (hand case AO-REVIVE-ELSE: set aside) | 3 | revive 1.17.0 max-control-nesting | 3 | fixed | https://github.com/mgechev/revive/blob/v1.17.0/RULES_DESCRIPTIONS.md#max-control-nesting |
| AO-REVIVE-RANGE | Nesting depth | revive gives a for-range loop's body no nesting level, though the Go spec's range clause is a for statement like any other; the oracle misses it, so such functions are set aside (hand case AO-REVIVE-RANGE) | 2 | revive 1.17.0 max-control-nesting | 1 | definition | https://go.dev/ref/spec#For_range |
| AO-RCA-WILDCARD-ARM | ccn_std, ccn_mod and gated ccn | rust-code-analysis counts every match arm, the `_` arm too, where NIST leaves the default out (hand case AO-RCA-WILDCARD-ARM: three arms and `_`) | 4 | rust-code-analysis-cli 0.0.25 | 5 | definition | paper: NIST SP 500-235 sec. 4.1 |
| AO-RCA-LOOP | ccn_std, ccn_mod and gated ccn | rust-code-analysis counts a `loop` as a decision; a loop with no condition decides nothing (hand case AO-RCA-LOOP: a loop holding an if) | 2 | rust-code-analysis-cli 0.0.25 | 3 | definition | https://doc.rust-lang.org/reference/expressions/loop-expr.html#infinite-loops |
| AO-RCA-CLOSURE-BASE | ccn_std, ccn_mod and gated ccn | rust-code-analysis's function sum adds each closure's own base path, where crapkit counts a closure's decisions in the function that holds it and gives the closure no row (hand case AO-RCA-CLOSURE-BASE: a closure holding an if) | 2 | rust-code-analysis-cli 0.0.25 | 3 | definition | https://github.com/mozilla/rust-code-analysis/blob/v0.0.25/src/spaces.rs |
| AO-RCA-NESTED-FN | ccn_std, ccn_mod and gated ccn | rust-code-analysis's function sum holds a nested fn's value, which crapkit scores as its own row; such functions are set aside (hand case AO-RCA-NESTED-FN: an if in each) | 2 | rust-code-analysis-cli 0.0.25 | 4 | definition | https://github.com/mozilla/rust-code-analysis/blob/v0.0.25/src/spaces.rs |
| AO-RCA-NESTED-FN-DEPTH | Cognitive complexity | rust-code-analysis scores a nested fn's structures one level deeper, where crapkit scores the nested fn as its own row from level 0; nested fns are set aside (hand case AO-RCA-NESTED-FN-DEPTH) | 1 | rust-code-analysis-cli 0.0.25 | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B3 (nested functions increase the nesting level) |
| AO-RCA-RUN-OF-THREE | Cognitive complexity | oracle bug: rust-code-analysis charges `(a &amp;&amp; b &amp;&amp; c) \|\| d` 3, where the paper's one increment per sequence gives 2 and crapkit reads 2; functions holding a run of three next to another operator are set aside (hand case AO-RCA-RUN-OF-THREE) | 2 | rust-code-analysis-cli 0.0.25 | 3 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (one increment per sequence of like binary logical operators) |
| AO-RCA-RUN-AFTER-RUN | Cognitive complexity | oracle bug: rust-code-analysis leaves `a &amp;&amp; b` in a plain expression uncounted after `if k &amp;&amp; a`, 2 where the paper gives 3 and crapkit reads 3; a run outside an if or while condition after another run is set aside (hand case AO-RCA-RUN-AFTER-RUN) | 3 | rust-code-analysis-cli 0.0.25 | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (one increment per sequence of like binary logical operators) |
| AO-RCA-RECURSION | Cognitive complexity | rust-code-analysis leaves out the Sonar paper's +1 for recursion; crapkit counts a call that reaches the function, `self.walk(n - 1)` in an impl's `walk` among them (hand case AO-RCA-RECURSION) | 3 | rust-code-analysis-cli 0.0.25 | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (recursion) |
| AO-CARGOCRAP-WILDCARD-ARM | ccn_std, ccn_mod and gated ccn | cargo-crap counts every match arm, the `_` arm too, where NIST leaves the default out (hand case AO-CARGOCRAP-WILDCARD-ARM: three arms and `_`) | 4 | cargo-crap 0.5.0 | 5 | definition | paper: NIST SP 500-235 sec. 4.1 |
| AO-CARGOCRAP-LOOP | ccn_std, ccn_mod and gated ccn | cargo-crap counts a `loop` as a decision; a loop with no condition decides nothing (hand case AO-CARGOCRAP-LOOP: a loop holding an if) | 2 | cargo-crap 0.5.0 | 3 | definition | https://doc.rust-lang.org/reference/expressions/loop-expr.html#infinite-loops |
| AO-CARGOCRAP-GUARD | ccn_std, ccn_mod and gated ccn | cargo-crap counts no match guard, where NIST counts the guard's condition as a binary decision (hand case AO-CARGOCRAP-GUARD: three arms, one guarded) | 5 | cargo-crap 0.5.0 | 4 | definition | paper: NIST SP 500-235 sec. 4.1 |
| AO-CARGOCRAP-CLOSURE | ccn_std, ccn_mod and gated ccn | cargo-crap leaves a closure's decisions out of the function that holds it, where crapkit counts them there (hand case AO-CARGOCRAP-CLOSURE: a closure holding an if) | 2 | cargo-crap 0.5.0 | 1 | definition | https://github.com/minikin/cargo-crap/blob/v0.5.0/README.md |
| AO-CARGOCRAP-CFG-TEST | ccn_std, ccn_mod and gated ccn | cargo-crap lists no function inside a `#[cfg(test)]` module, where crapkit reads every function in a scope's files; such functions are set aside (hand case AO-CARGOCRAP-CFG-TEST) | 2 | cargo-crap 0.5.0 | absent | definition | https://github.com/minikin/cargo-crap/blob/v0.5.0/README.md |
| AO-CARGOCRAP-NESTED-FN | ccn_std, ccn_mod and gated ccn | cargo-crap lists no fn nested in another fn, where crapkit gives it its own row; such functions are set aside (hand case AO-CARGOCRAP-NESTED-FN) | 2 | cargo-crap 0.5.0 | absent | definition | https://github.com/minikin/cargo-crap/blob/v0.5.0/README.md |
| AO-CHECKSTYLE-DEPTH-BASE | Nesting depth | Checkstyle's NestedIfDepth, NestedForDepth and NestedTryDepth count the structures of one kind around a nested one, 0 for an if inside no other, where the nesting column counts the if itself (hand case AO-CHECKSTYLE-DEPTH-BASE: an if inside an if) | 2 | checkstyle 14.1.0 | 1 | definition | https://checkstyle.org/checks/coding/nestedifdepth.html |
| AO-CHECKSTYLE-ONE-KIND | Nesting depth | Checkstyle measures if, for and try depth each on its own and has no depth check for while, do, switch, catch, a ternary or a lambda, so only functions holding one of if, for or try compare; the rest are set aside (hand case AO-CHECKSTYLE-ONE-KIND: an if inside a while) | 2 | checkstyle 14.1.0 | 0 | definition | https://checkstyle.org/checks/coding/nestedfordepth.html |
| AO-PMD-INNER-CLASS | Cognitive complexity | PMD adds an anonymous or local class's method bodies to the method that declares the class, one level deeper, where crapkit scores each such method as its own row; such methods are set aside (hand case AO-PMD-INNER-CLASS: a ternary in an anonymous Runnable) | 0 | pmd 7.27.0 CognitiveComplexity | 2 | definition | paper: Sonar Cognitive Complexity v1.7 App. B3 (nested methods increase the nesting level) |
| AO-PMD-ELSE-IF-NESTING | Cognitive complexity | oracle bug: PMD nests each else if inside the one before it, so an if in the else after `if / else if` scores +3 where the Sonar paper's flat chain gives +2 and crapkit reads 5 in all; methods with a structure in a branch after an else if are set aside (hand case AO-PMD-ELSE-IF-NESTING) | 5 | pmd 7.27.0 CognitiveComplexity | 6 | definition | paper: Sonar Cognitive Complexity v1.7 App. B3 (else if and else add no nesting of their own) |
| AO-OCLINT-BODY-BLOCK | Nesting depth | OCLint's block depth counts compound statements, the function body's braces as depth 1, where the nesting column counts the structures inside it (hand case AO-OCLINT-BODY-BLOCK: an if inside a function) | 1 | oclint 26.02 DeepNestedBlock | 2 | definition | https://docs.oclint.org/en/stable/rules/size.html#deepnestedblock |
| AO-TIDY-BODY-BLOCK | Nesting depth | clang-tidy's NestingThreshold counts compound statements, the function body's braces as level 1, where the nesting column counts the structures inside it (hand case AO-TIDY-BODY-BLOCK: an if inside a function) | 1 | clang-tidy 23.1.2 readability-function-size | 2 | definition | https://clang.llvm.org/extra/clang-tidy/checks/readability/function-size.html (NestingThreshold) |
| AO-OCLINT-UNBRACED | Nesting depth | a ternary, or an if, else or loop whose body has no braces, nests without a compound statement, which is all OCLint counts, so such functions are set aside (hand case AO-OCLINT-UNBRACED: a function returning a ternary) | 1 | oclint 26.02 DeepNestedBlock | 1 | definition | https://docs.oclint.org/en/stable/rules/size.html#deepnestedblock |
| AO-TIDY-UNBRACED | Nesting depth | a ternary, or an if, else or loop whose body has no braces, nests without a compound statement, which is all clang-tidy counts, so such functions are set aside (hand case AO-TIDY-UNBRACED: a function returning a ternary) | 1 | clang-tidy 23.1.2 readability-function-size | 1 | definition | https://clang.llvm.org/extra/clang-tidy/checks/readability/function-size.html (NestingThreshold) |
| AO-TIDY-RECURSION | Cognitive complexity | clang-tidy leaves out the Sonar paper's +1 for a function in a recursion cycle; crapkit counts a direct call to itself (hand case AO-TIDY-RECURSION: a recursive factorial) | 2 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (recursion); https://clang.llvm.org/extra/clang-tidy/checks/readability/function-cognitive-complexity.html (Limitations) |
| AO-OCLINT-MACRO-DECISIONS | ccn_std, ccn_mod and gated ccn | OCLint counts the decisions a function-like macro expands to, where crapkit reads the invocation as written, a call; the transform subtracts the &amp;&amp;, \|\|, ?: and branch keywords of each invoked macro's replacement text (hand case AO-OCLINT-MACRO-DECISIONS: if (BOTH(a, b)) with BOTH expanding to &amp;&amp;) | 2 | oclint 26.02 HighCyclomaticComplexity | 3 | definition | convention_only |
| AO-OCLINT-MACRO-CONDITIONAL | ccn_std, ccn_mod and gated ccn | a macro with two #define lines, or one under #ifndef its own name (cJSON's isnan and isinf fallbacks, which math.h already defines), expands to what the build's defines pick, so functions invoking one are set aside for OCLint (hand case AO-OCLINT-MACRO-CONDITIONAL) | 2 | oclint 26.02 HighCyclomaticComplexity | 3 | definition | convention_only |
| AO-TIDY-MACRO-DECISIONS | Cognitive complexity | clang-tidy scores the branches and boolean operators a macro expands to, merged into the sequences around the invocation, where crapkit reads the invocation as a call; functions invoking a deciding macro are set aside (hand case AO-TIDY-MACRO-DECISIONS: if (BOTH(a, b)) with BOTH expanding to &amp;&amp;) | 1 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 2 | definition | convention_only |
| AO-OCLINT-MACRO-BLOCK | Nesting depth | a macro that expands to braces (do { ... } while (0)) adds compound statements OCLint counts and the source text does not show, so functions invoking one are set aside (hand case AO-OCLINT-MACRO-BLOCK) | 0 | oclint 26.02 DeepNestedBlock | 2 | definition | convention_only |
| AO-TIDY-MACRO-BLOCK | Nesting depth | a macro that expands to braces (do { ... } while (0)) adds compound statements clang-tidy counts and the source text does not show, so functions invoking one are set aside (hand case AO-TIDY-MACRO-BLOCK) | 0 | clang-tidy 23.1.2 readability-function-size | 2 | definition | convention_only |
| AO-CPP-TEMPLATE-DEFAULT-LESS | Function discovery and spans | a C++ template parameter whose default holds a less-than comparison, bool E = (N &lt; 19), hides every later function in the file; fmt's chrono.h keeps rows only up to line 943 of 2245 (probe cpp cpp-template-less) | 16 | hand: ISO/IEC 14882:2020 [temp.param] | 16 | fixed | https://eel.is/c++draft/temp.param (a default template argument is an expression; tree-sitter-cpp and clang list the function) |
| AO-SHELLMETRICS-LOGICAL | ccn_std, ccn_mod and gated ccn | shellmetrics counts one decision per then, do and non-default case arm and leaves out every &amp;&amp; and \|\|, where crapkit counts each short-circuit operator (hand case AO-SHELLMETRICS-LOGICAL: an if with one &amp;&amp; and one \|\|) | 4 | shellmetrics b3bfff2 | 2 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (a short-circuit operator adds 1); https://github.com/shellspec/shellmetrics/blob/b3bfff2af6880443112cdbf2ea449440b30ab9b0/shellmetrics (parse: marks c and l only) |
| AO-SHELLMETRICS-STRING-PAREN | ccn_std, ccn_mod and gated ccn | oracle bug: shellmetrics reads a line of a multi-line string that ends in `)` as a case arm and adds 1, where the McCabe text counts no decision in a string and crapkit reads none; functions holding such a string are set aside (hand case AO-SHELLMETRICS-STRING-PAREN) | 1 | shellmetrics b3bfff2 | 2 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (decisions are predicates of the flow graph); https://github.com/shellspec/shellmetrics/blob/b3bfff2af6880443112cdbf2ea449440b30ab9b0/shellmetrics (parse: a line ending in ")" marks c) |
| AO-SWIFTLINT-BASE | ccn_std, ccn_mod and gated ccn | SwiftLint counts decisions from 0, where McCabe and crapkit start at 1 (hand case AO-SWIFTLINT-BASE: a function with no decision) | 1 | SwiftLint 0.65.1 cyclomatic_complexity | 0 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (v(G) = decisions + 1); https://github.com/realm/SwiftLint/blob/0.65.1/Source/SwiftLintBuiltInRules/Rules/Metrics/CyclomaticComplexityRule.swift |
| AO-SWIFTLINT-LOGICAL | ccn_std, ccn_mod and gated ccn | SwiftLint counts no &amp;&amp; or \|\|, where crapkit counts each short-circuit operator; counted from tree-sitter tokens (hand case AO-SWIFTLINT-LOGICAL: an if with one &amp;&amp; and one \|\|) | 4 | SwiftLint 0.65.1 cyclomatic_complexity | 1 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (a short-circuit operator adds 1); https://github.com/realm/SwiftLint/blob/0.65.1/Source/SwiftLintBuiltInRules/Rules/Metrics/CyclomaticComplexityRule.swift |
| AO-SWIFTLINT-TERNARY | ccn_std, ccn_mod and gated ccn | SwiftLint counts no ?:, where crapkit counts a ternary as a decision (hand case AO-SWIFTLINT-TERNARY) | 2 | SwiftLint 0.65.1 cyclomatic_complexity | 0 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (binary decisions); https://github.com/realm/SwiftLint/blob/0.65.1/Source/SwiftLintBuiltInRules/Rules/Metrics/CyclomaticComplexityRule.swift |
| AO-SWIFTLINT-DEFAULT | ccn_std, ccn_mod and gated ccn | SwiftLint counts a switch default entry, where McCabe and crapkit leave the default out (hand case AO-SWIFTLINT-DEFAULT: two cases and a default) | 3 | SwiftLint 0.65.1 cyclomatic_complexity | 3 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (a switch adds one per case-labeled statement, default excluded); https://github.com/realm/SwiftLint/blob/0.65.1/Source/SwiftLintBuiltInRules/Rules/Metrics/CyclomaticComplexityRule.swift |
| AO-SWIFT-IF-CASE | ccn_std, ccn_mod and gated ccn | Swift `if case let .failure(e) = r` (probe swift s-if-case); guard, while and for case read the same | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | paper: NIST SP 500-235 sec. 4.1; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/patterns/#Enumeration-Case-Pattern |
| AO-SWIFT-IF-CASE-ND | Nesting depth | the body of a Swift `if case let` (probe swift s-if-case); while and for case read the same | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SWIFT-COALESCE | ccn_std, ccn_mod and gated ccn | Swift `return a ?? 0` (probe swift s-coalesce); ruling D2 gives ?? +1 ccn | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | paper: NIST SP 500-235 sec. 4.1; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/basicoperators/#Nil-Coalescing-Operator |
| AO-SWIFT-COALESCE-COG | Cognitive complexity | Swift `return a ?? 0` (probe swift s-coalesce); a run of ?? reads 1 as a whole | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Ignore shorthand |
| AO-SWIFT-SUPER-INIT | Function discovery and spans | rows named init in a class whose init calls super.init (probe swift s-super-init); Alamofire's DataRequest.swift reads an init spanning lines 54 to 459 | 1 | hand: The Swift Programming Language, Initialization | 1 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/initialization/#Initializer-Delegation-for-Class-Types |
| AO-SWIFT-SUPER-INIT-NEXT | Function discovery and spans | the method after an init that calls super.init (probe swift s-super-init) | 8 | hand: The Swift Programming Language, Declarations | 8 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/declarations/#Function-Declaration |
| AO-SWIFTLINT-COALESCING | ccn_std, ccn_mod and gated ccn | SwiftLint counts no ??, where McCabe counts a nil-coalescing pick as one binary decision; counted from tree-sitter nodes (hand case AO-SWIFTLINT-COALESCING: return a ?? 0; crapkit reads 1 there until calc-bug analysis-oracles-78 is fixed, so the hand case is a strict xfail under AO-SWIFT-COALESCE) | 2 | SwiftLint 0.65.1 cyclomatic_complexity | 0 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (binary decisions); https://github.com/realm/SwiftLint/blob/0.65.1/Source/SwiftLintBuiltInRules/Rules/Metrics/CyclomaticComplexityRule.swift |
| AO-SWIFTLINT-OPTIONAL-CHAIN | ccn_std, ccn_mod and gated ccn | SwiftLint counts no optional chain, where McCabe counts each `?` of one as a short-circuit decision; counted from tree-sitter nodes (hand case AO-SWIFTLINT-OPTIONAL-CHAIN: return a?.count; crapkit reads 1 there until calc-bug analysis-oracles-162 is fixed, so the hand case is a strict xfail under AO-SWIFT-OPTIONAL-CHAIN) | 2 | SwiftLint 0.65.1 cyclomatic_complexity | 0 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (a short-circuit operator adds 1); https://github.com/realm/SwiftLint/blob/0.65.1/Source/SwiftLintBuiltInRules/Rules/Metrics/CyclomaticComplexityRule.swift |
| AO-SWIFT-KEYWORD-LABEL | ccn_std, ccn_mod and gated ccn | Swift `func keywordLabel(for name: String)` (probe swift s-keyword-label); labels while, if, catch, guard and case read the same | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/functions/#Specifying-Argument-Labels |
| AO-SWIFT-KEYWORD-LABEL-COG | Cognitive complexity | Swift `func keywordLabel(for name: String)` (probe swift s-keyword-label) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-SWIFT-KEYWORD-LABEL-ND | Nesting depth | Swift `func keywordLabel(for name: String)` (probe swift s-keyword-label) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SWIFT-OPTIONAL-MARK | ccn_std, ccn_mod and gated ccn | Swift `func optionalMark(done: (() -&gt; Void)? = nil)` (probe swift s-optional-mark); `(any Error)?`, `[Int]?` and `Set&lt;Int&gt;?` read the same; the `?` of `f()?.g` starts an optional chain, whose ccn is AO-SWIFT-OPTIONAL-CHAIN's | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/types/#Optional-Type |
| AO-SWIFT-OPTIONAL-MARK-COG | Cognitive complexity | Swift `func optionalMark(done: (() -&gt; Void)? = nil)` (probe swift s-optional-mark) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-SWIFT-OPTIONAL-MARK-ND | Nesting depth | Swift `func optionalMark(done: (() -&gt; Void)? = nil)` (probe swift s-optional-mark) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SWIFT-OPTIONAL-CHAIN | ccn_std, ccn_mod and gated ccn | Swift `return a?.count` (probe swift s-optional-chain): each `?` of an optional chain is one short-circuit decision, as AO-TS-OPTIONAL-CHAIN counts `a?.b` in TypeScript; `f()?.g`, `self?.done()`, `c?()` and `d?[0]` read the same; `Int?.self` names a type, and `Empty?.none` and `Int?.some(1)` a member of it, no chain (probe swift s-optional-member) | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | paper: NIST SP 500-235 sec. 4.1 (a short-circuit operator adds 1); https://docs.swift.org/swift-book/documentation/the-swift-programming-language/optionalchaining/ |
| AO-SWIFT-OPTIONAL-CHAIN-ND | Nesting depth | Swift `return load()?.count` (probe swift s-call-chain) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SWIFT-ACCESSOR-WORD | Function discovery and spans | `return r.get()` in a method (probe swift s-accessor-word): its row runs to line 12; `case .get`, `[.get]`, `set(...)` and `private(set)` read the same | 5 | hand: The Swift Programming Language, Declarations | 5 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/declarations/#Function-Declaration |
| AO-SWIFT-ACCESSOR-WORD-NEXT | Function discovery and spans | the method after one that calls r.get() (probe swift s-accessor-word-next) | 7 | hand: The Swift Programming Language, Declarations | 7 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/declarations/#Function-Declaration |
| AO-SWIFT-PROTOCOL-WORD | Function discovery and spans | the method after one that passes a `protocol:` argument (probe swift s-protocol-word); `self.protocol` reads the same | 7 | hand: The Swift Programming Language, Declarations | 7 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/declarations/#Function-Declaration |
| AO-SWIFT-FAILABLE-INIT | Function discovery and spans | `init?(raw: Int)` (probe swift s-failable-init); `init!` reads the same | 1 | hand: The Swift Programming Language, Initialization | 1 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/initialization/#Failable-Initializers |
| AO-SWIFT-RAW-IDENTIFIER | Function discovery and spans | rows named `keeps onboarding if the gateway is down` in a struct that declares one (probe swift s-raw-identifier); a large consumer repo writes 3,060 Swift Testing tests this way | 1 | hand: The Swift Programming Language, Lexical Structure | 1 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/lexicalstructure/#Identifiers; https://github.com/swiftlang/swift-evolution/blob/main/proposals/0451-escaped-identifiers.md |
| AO-SWIFT-RAW-IDENTIFIER-CCN | ccn_std, ccn_mod and gated ccn | a call to `keeps onboarding if the gateway is down`() (probe swift s-raw-identifier-call) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/lexicalstructure/#Identifiers; https://github.com/swiftlang/swift-evolution/blob/main/proposals/0451-escaped-identifiers.md |
| AO-SWIFT-RAW-IDENTIFIER-COG | Cognitive complexity | a call to `keeps onboarding if the gateway is down`() (probe swift s-raw-identifier-call) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-SWIFT-RAW-IDENTIFIER-ND | Nesting depth | a call to `keeps onboarding if the gateway is down`() (probe swift s-raw-identifier-call) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SWIFT-HASH-LINE | Function discovery and spans | `func logged(file: String = #fileID)` (probe swift s-hash-default): no row for it or any later function in the file | 1 | hand: The Swift Programming Language, Expressions | 1 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/expressions/#Literal-Expression |
| AO-SWIFT-HASH-LINE-END | Function discovery and spans | `if #available(macOS 10.15, *) {` (probe swift s-hash-available): the function ends at the if's closing brace | 7 | hand: The Swift Programming Language, Statements | 7 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/statements/#Availability-Condition |
| AO-SWIFT-TYPE-WORD | Function discovery and spans | `return type` before a method's closing brace (probe swift s-type-word): the method runs to line 14 and the next method loses its row | 7 | hand: The Swift Programming Language, Declarations | 7 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/declarations/#Function-Declaration |
| AO-SWIFT-TYPE-COMMA | Parameter list (params, packet.params) | `func tupleParam(pair: (Int, Int))` (probe swift s-tuple-param); a function type with two parameters, `Result&lt;A, B&gt;` and a default `[1, 2]` read the same | 1 | hand: The Swift Programming Language, Functions | 1 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/functions/#Function-Parameters-and-Return-Values |
| AO-SWIFT-COMMA-BRACE | Function discovery and spans | `run(1, { x in ... })` (probe swift s-comma-brace): the function ends at the closure's closing brace | 6 | hand: The Swift Programming Language, Closures | 6 | fixed | https://docs.swift.org/swift-book/documentation/the-swift-programming-language/closures/#Closure-Expression-Syntax |
| AO-SWIFT-TRY-ND | Nesting depth | two `try` expressions (probe swift s-try-twice); each plain try adds a level | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-SWIFT-GUARD-ND | Nesting depth | a guard's else body (probe swift s-guard-alone) | 1 | hand: Sonar paper App. B2 | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/controlflow/#Early-Exit |
| AO-SWIFT-GUARD-COG | Cognitive complexity | a guard inside an if (probe swift s-guard-in-if) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1-B3 |
| AO-SWIFT-ACCESSOR-ROWS | Function discovery and spans | crapkit lists each accessor block (get, set, willSet, didSet) and each subscript declaration as its own row, as lizard 1.24.0's Swift reader does; the Swift book declares them inside a variable or as a subscript, not as functions (probes swift s-accessor-get, s-subscript); the docs name no rule (doc gap) | 1 | hand: The Swift Programming Language, Declarations | 0 | definition | convention_only |
| AO-SWIFT-DIRECTIVE-LOGICAL | ccn_std, ccn_mod and gated ccn | oracle bug: the tree-sitter counter counts &amp;&amp; and \|\| in a #if condition as decisions; the McCabe text counts decisions of the flow graph and #if only picks the code that compiles; crapkit drops the directive line and reads 1 as the text does (probe swift s-directive-and); functions holding one are set aside | 1 | tree-sitter counters (tree-sitter-swift 0.7.3) | 2 | definition | paper: NIST SP 500-235 sec. 4.1; https://docs.swift.org/swift-book/documentation/the-swift-programming-language/statements/#Conditional-Compilation-Block |
| AO-SWIFT-DIRECTIVE-LOGICAL-COG | Cognitive complexity | oracle bug: the counter charges the &amp;&amp; of a #if condition +1 as a sequence of logical operators; the paper scores the code that runs (probe swift s-directive-and); functions holding one are set aside | 0 | tree-sitter cognitive counter (tree-sitter-swift 0.7.3) | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-TSCOG-SWIFT-ELSE-COMMENT | Cognitive complexity | oracle bug: the Swift grammar hangs a comment right inside `else {` on the if statement, and the counter counts each such comment as one more else; the paper gives if +1 and else +1, and crapkit reads 2 (probe swift s-else-comment); functions holding one are set aside | 2 | tree-sitter cognitive counter (tree-sitter-swift 0.7.3) | 3 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-CPP-QUALIFIED-RECURSION | Cognitive complexity | a C++ function in a namespace or class body calls itself (probes cpp sc-pow10, sc-fact; fmt's detail::pow10) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-CPP-CLASS-NAME-RECURSION | Cognitive complexity | an out-of-class member that constructs its own class, File(fd) in File::open (probe cpp sc-open; fmt's file::open_windows_file and ostream::grow) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-COG-BRACELESS-BODY | Cognitive complexity | a structure in the braceless body of an if, else or loop, a conditional expression or an if (probes cpp sc-pick, c c-pick-braceless and c-walk-braceless, js corpus-braceless-body: for (const x of xs) if (x) visit(x)); Objective-C, Java and TypeScript read the same | 3 | hand: Sonar paper; eslint-plugin-sonarjs 4.2.1 | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 and B3 (nesting increments) |
| AO-C-PARAMS-UNNAMED | Parameter list (params, packet.params) | parameters with no name, first(int*, char) (probe cpp sc-first) | 2 | hand: ISO/IEC 14882:2020 [dcl.fct] | 2 | fixed | https://eel.is/c++draft/dcl.fct#3 |
| AO-C-PARAMS-ARRAY | Parameter list (params, packet.params) | a parameter declared as an array or a reference to one, head(const int (&amp;arr)[4]) and head_c(const int arr[4]) (probes cpp sc-head, c c-head-array) | 1 | hand: ISO/IEC 14882:2020 [dcl.fct] | 1 | fixed | https://eel.is/c++draft/dcl.fct#5 |
| AO-CPP-RVALUE-BODY | ccn_std, ccn_mod and gated ccn | an rvalue reference inside the body: a cast to Widget&amp;&amp;, a lambda taking auto&amp;&amp; (probes cpp rv-cast, rv-lambda) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-CPP-RVALUE-BODY-COG | Cognitive complexity | an rvalue reference inside the body: auto&amp;&amp; w = make(), a cast to Widget&amp;&amp;, a lambda taking auto&amp;&amp; (probes cpp rv-bind, rv-cast, rv-lambda) | 0 | hand: Sonar paper | 0 | fixed | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-CPP-RVALUE-FOR | ccn_std, ccn_mod and gated ccn | a range-for declaring auto&amp;&amp; (probe cpp rv-each) | 2 | hand: NIST SP 500-235 sec. 4.1 | 2 | fixed | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-CPP-RVALUE-FOR-COG | Cognitive complexity | a range-for declaring auto&amp;&amp; (probe cpp rv-each) | 1 | hand: Sonar paper | 1 | fixed | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-CPP-RVALUE-FOR-ND | Nesting depth | a range-for declaring auto&amp;&amp; (probe cpp rv-each) | 1 | hand: Sonar paper | 1 | fixed | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-CPP-RVALUE-ND | Nesting depth | an rvalue reference parameter, take(Widget&amp;&amp; w), no structure (probe cpp rv-take) | 0 | hand: Sonar paper | 0 | fixed | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-CPP-LOCAL-CLASS | ccn_std, ccn_mod and gated ccn | a local class's member function holds an if (probe cpp sc-outer; fmt's detail::get_container counts its local class's constructor line in nloc) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | https://eel.is/c++draft/class.local |
| AO-CPP-LOCAL-CLASS-ROW | Function discovery and spans | the local class's member function Local::twice (probe cpp sc-twice) | 1 | hand: ISO/IEC 14882:2020 [class.local] | 1 | fixed | https://eel.is/c++draft/class.local |
| AO-CPP-DECLTYPE-BRACE | Function discovery and spans | a member declaration whose trailing return type holds a braced initializer, -&gt; decltype(all(Tag{})) (probe cpp sc-check; fmt's is_tuple_formattable_::check) | 0 | hand: ISO/IEC 14882:2020 [dcl.fct.def.general] | 0 | fixed | https://eel.is/c++draft/dcl.fct.def.general |
| AO-CPP-DEFAULT-LESS-THAN | Parameter list (params, packet.params) | a `&lt;` comparison in a C++ default argument, int f(bool a = x &lt; 0, int b = 1): the later parameter merges into it; `x&lt;0` and a `&lt;&lt;` shift read the same, `(x &lt; 0)` does not (hand case in test_nloc_params) | 1 | hand: ISO/IEC 14882:2020 [dcl.fct.default]; tree-sitter-cpp | 2 | defect | https://eel.is/c++draft/dcl.fct.default |
| AO-CPP-DEFAULTED | Function discovery and spans | a function defined as = default or = delete runs no statement of its own, and crapkit lists no row for it, where the language, tree-sitter and clang read a definition (probe cpp sc-defaulted) | 0 | hand: ISO/IEC 14882:2020 [dcl.fct.def.general] | 2 | definition | convention_only |
| AO-TREE-RVALUE-LOGICAL | ccn_std, ccn_mod and gated ccn | oracle bug: the tree-sitter counters read the &amp;&amp; of a reference declarator as a logical operator and add 1 to ccn and cognitive for take(Widget&amp;&amp; w), where the language decides nothing and crapkit reads 1; functions holding an rvalue reference are set aside for ccn and cognitive (hand case AO-TREE-RVALUE-LOGICAL) | 1 | tree-sitter-cpp (oracles/treesitter_counters) | 2 | definition | https://eel.is/c++draft/dcl.ref (the &amp;&amp; of a declarator declares a reference) |
| AO-TREE-MACRO-DEFINITION | Function discovery and spans | oracle bug: tree-sitter reads a function-like macro invoked before a block, fmt's FMT_CATCH(...) {}, as a function definition with no type, where the macro expands to a catch clause and crapkit lists nothing; such a definition named in capitals and not after its class (no constructor) is set aside (hand case AO-TREE-MACRO-DEFINITION) | 0 | tree-sitter-cpp (oracles/treesitter_counters) | 1 | definition | https://eel.is/c++draft/cpp.replace |
| AO-TREE-NOT-A-FUNCTION | Function discovery and spans | oracle bug: tree-sitter reads struct FMT_API pipe { ... } as a function definition named pipe whose declarator has no parameter list, which no function definition lacks; crapkit lists nothing and the definition is set aside (hand case AO-TREE-NOT-A-FUNCTION) | 0 | tree-sitter-cpp (oracles/treesitter_counters) | 1 | definition | https://eel.is/c++draft/dcl.fct.def.general |
| AO-TREE-DECLARATION-ERROR | Function discovery and spans | oracle bug: tree-sitter reads a constructor after a macro, FMT_CONSTEXPR buffer(int grow, int sz) noexcept : size_(sz), cap_(sz) { ... }, as a field declaration it cannot finish and lists no function; crapkit's row there is not judged (hand case AO-TREE-DECLARATION-ERROR) | 1 | tree-sitter-cpp (oracles/treesitter_counters) | 0 | definition | https://eel.is/c++draft/dcl.fct.def.general |
| AO-OCLINT-NOT-COMPILED | ccn_std, ccn_mod and gated ccn | a function in a preprocessor branch the build leaves out (fmt's #ifdef _WIN32 code on the Linux cell, its C++23 std::expected formatter) reaches no compiler: OCLint reports no block inside it, so it is set aside for OCLint (hand case: a function under #ifdef NEVER_DEFINED) | 2 | oclint 26.02 DeepNestedBlock | 1 | definition | https://docs.oclint.org/en/stable/rules/size.html#deepnestedblock |
| AO-TIDY-NOT-COMPILED | Cognitive complexity | a function in a preprocessor branch the build leaves out reaches no compiler: clang-tidy notes no nesting level 1 at its body, so it is set aside for clang-tidy (hand case: a function under #ifdef NEVER_DEFINED) | 1 | clang-tidy 23.1.2 readability-function-size | 0 | definition | https://clang.llvm.org/extra/clang-tidy/checks/readability/function-size.html (NestingThreshold) |
| AO-OCLINT-RANGE-FOR | ccn_std, ccn_mod and gated ccn | OCLint's cyclomatic complexity counts no range-based for loop, where the McCabe text counts every loop; the transform adds 1 per range-for (hand case: one range-for over an array) | 2 | oclint 26.02 HighCyclomaticComplexity | 1 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (each loop is a decision) |
| AO-TIDY-BRACELESS-GOTO | Cognitive complexity | oracle bug: clang-tidy adds nothing for a goto that is the braceless body of an if, where the paper adds 1 per goto and crapkit reads 2 for if (a) goto out; the transform adds 1 per such goto (hand case AO-TIDY-BRACELESS-GOTO; fmt's dragonbox to_decimal) | 2 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (goto) |
| AO-TIDY-RUN-TREE | Cognitive complexity | clang-tidy starts a sequence at each logical operator whose parent is another operator, so a &amp;&amp; ((b &amp;&amp; c) \|\| d) scores 3; the paper counts sequences of like binary logical operators, eslint-plugin-sonarjs 4.2.1 flattens the expression left to right through parentheses (S3776 flattenLogicalExpression), and crapkit reads 2; the transform puts the left-to-right count in place of clang-tidy's (hand case AO-TIDY-RUN-TREE) | 3 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 4 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (sequences of like binary logical operators) |
| AO-TIDY-STICKY-ERROR | Cognitive complexity | clang-tidy prints Error while processing for the first file of a run that fails to compile and for every file after it, while it still reads and reports those files (hand case: a failing file before two that compile; the next file's function reads cognitive 1 in both tools); the adapter takes the failed files from OCLint, checks clang-tidy's flags are that tail, and keeps every clang-tidy answer | 1 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 1 | definition | https://clang.llvm.org/extra/clang-tidy/ |
| AO-TIDY-NESTING-SWEEP | Nesting depth | processes per shard: readability-function-size notes only the blocks exactly NestingThreshold + 1 deep and prints no maximum, so the adapter runs one clang-tidy process per threshold over the files still reporting, 1 + the deepest brace level (3 on the hand shard, deepest level 2), where tidy_cognitive and oclint start 1 each (OCLint names its failed files through LongLine in the same process) | 1 | clang-tidy 23.1.2 readability-function-size | 3 | definition | https://clang.llvm.org/extra/clang-tidy/checks/readability/function-size.html (NestingThreshold) |
| AO-C-ELVIS-COG | Cognitive complexity | a GNU `a ?: b`, the conditional with its middle operand left out (probe objc o-elvis); AFNetworking AFURLSessionManager.m serverTrustErrorForServerTrust:url: reads 5 where the paper and the tree-sitter counter give 6 | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 (ternary operator); https://gcc.gnu.org/onlinedocs/gcc/Conditionals.html |
| AO-TRAILING-ATTRIBUTE | Function discovery and spans | a method with an availability attribute after its last parameter, `- (int)run:(int)a` then `with:(int)b API_AVAILABLE(ios(10)) {` (probe objc o-trailing-attribute): crapkit names the row `)` and starts it on line 17; AFNetworking AFURLSessionManager.m:250 and :1096 start two lines late | 16 | hand: Apple, Programming with Objective-C | 16 | fixed | https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/ProgrammingWithObjectiveC/DefiningClasses/DefiningClasses.html |
| AO-ND-FOR-AFTER-BRACE | Nesting depth | a for loop holding an if, inside @autoreleasepool (probe objc o-for-after-brace); the same inside an if, a bare block, @synchronized or after an initializer list's braces | 2 | hand: Sonar paper App. B2 | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-OCLINT-ELVIS | ccn_std, ccn_mod and gated ccn | OCLint counts no GNU `a ?: b` (clang's BinaryConditionalOperator), where McCabe counts one binary decision; +1 per conditional with no middle operand, counted from tree-sitter nodes (hand case AO-OCLINT-ELVIS: return a ?: b) | 2 | oclint 26.02 HighCyclomaticComplexity | 1 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (binary decisions); https://gcc.gnu.org/onlinedocs/gcc/Conditionals.html |
| AO-TIDY-ELVIS | Cognitive complexity | clang-tidy scores no GNU `a ?: b`, where the Sonar paper counts a conditional operator +1 plus its nesting, so functions holding one are set aside (hand case AO-TIDY-ELVIS: return a ?: b; crapkit reads 0 there until calc-bug analysis-oracles-100 is fixed, so the hand case is a strict xfail under AO-C-ELVIS-COG) | 1 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 0 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (ternary operator); https://clang.llvm.org/extra/clang-tidy/checks/readability/function-cognitive-complexity.html |
| AO-TIDY-OBJC-METHOD | Cognitive complexity | clang-tidy scores C functions only and reports no Objective-C method, so methods are set aside (hand case AO-TIDY-OBJC-METHOD: a method holding one if) | 1 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 0 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (a method is scored like any function); https://clang.llvm.org/extra/clang-tidy/checks/readability/function-cognitive-complexity.html |
| AO-TIDY-OBJC-METHOD-SIZE | Nesting depth | readability-function-size reports no Objective-C method, so methods are set aside (hand case AO-TIDY-OBJC-METHOD-SIZE: a method holding one if) | 1 | clang-tidy 23.1.2 readability-function-size | 0 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2; https://clang.llvm.org/extra/clang-tidy/checks/readability/function-size.html |
| AO-TIDY-OBJC-FOR-IN | Cognitive complexity | clang-tidy scores no Objective-C for...in loop, neither its +1 nor the nesting it opens, so functions holding one are set aside (hand case AO-TIDY-OBJC-FOR-IN: an if inside a for...in) | 3 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1-B2 (foreach); https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/ProgrammingWithObjectiveC/FoundationTypesandCollections/FoundationTypesandCollections.html |
| AO-TIDY-OBJC-CATCH | Cognitive complexity | clang-tidy scores no Objective-C @catch, so functions holding one are set aside (hand case AO-TIDY-OBJC-CATCH: @try with one @catch) | 1 | clang-tidy 23.1.2 readability-function-cognitive-complexity | 0 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (catch); https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/ProgrammingWithObjectiveC/ErrorHandling/ErrorHandling.html |
| AO-OCLINT-OBJC-SCOPE | Nesting depth | OCLint counts the braces of @synchronized and @autoreleasepool as a block level, where the Sonar paper opens no nesting for either, so functions holding one are set aside (hand case AO-OCLINT-OBJC-SCOPE: an if inside @synchronized, block depth 3 with the body's braces) | 1 | oclint 26.02 DeepNestedBlock | 3 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 (the structures that increase nesting); https://docs.oclint.org/en/stable/rules/size.html#deepnestedblock |
| AO-TIDY-OBJC-SCOPE | Nesting depth | clang-tidy counts the braces of @synchronized and @autoreleasepool as a nesting level, where the Sonar paper opens none, so functions holding one are set aside (hand case AO-TIDY-OBJC-SCOPE: an if inside @synchronized, level 3 with the body's braces) | 1 | clang-tidy 23.1.2 readability-function-size | 3 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 (the structures that increase nesting); https://clang.llvm.org/extra/clang-tidy/checks/readability/function-size.html |
| AO-PS-PIPELINE-CHAIN | ccn_std, ccn_mod and gated ccn | &amp;&amp; and \|\| between pipelines (PowerShell 7) add nothing (probe ps1 ps-chain) | 3 | hand: NIST SP 500-235 sec. 4.1 | 3 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-PS-NEST-CHAIN | Nesting depth | &amp;&amp; and \|\| between pipelines each open a nesting level, outside any condition (probe ps1 ps-chain) | 0 | hand: Sonar paper App. B2 | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-PS-SCOPED-NAME | Function discovery and spans | function script:Get-Scoped gets no row (probe ps1 ps-scoped) | 1 | hand: PowerShell about_Functions, about_Scopes | 1 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_functions |
| AO-PS-DOTTED-NAME | Function discovery and spans | function Get.Dotted gets no row (probe ps1 ps-dotted) | 1 | hand: PowerShell Parser AST | 1 | fixed | https://learn.microsoft.com/dotnet/api/system.management.automation.language.functiondefinitionast |
| AO-PS-KEYWORD-CASE-DECL | Function discovery and spans | a function declared with Function (a capital F) gets no row (probe ps1 ps-capital-function) | 1 | hand: PowerShell about_Language_Keywords | 1 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_language_keywords |
| AO-PS-SWITCH-TYPE | ccn_std, ccn_mod and gated ccn | a [switch] parameter type counts as a switch in ccn_mod (probe ps1 ps-switch-param) | 2 | hand: lizard README option -m | 2 | fixed | https://github.com/terryyin/lizard/blob/1.24.0/README.rst |
| AO-PS-SWITCH-TYPE-COG | Cognitive complexity | a [switch] parameter type adds 2 to cognitive (probe ps1 ps-switch-param) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-PS-SWITCH-BRACKET | ccn_std, ccn_mod and gated ccn | a switch whose subject is $m['k'] counts none of its two arms (probe ps1 ps-switch-index) | 3 | hand: NIST SP 500-235 sec. 4.1 | 3 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-PS-DEFAULT-CASE | ccn_std, ccn_mod and gated ccn | a default arm written Default counts as an arm (probe ps1 ps-capital-default) | 2 | hand: PowerShell about_Language_Keywords | 2 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_language_keywords |
| AO-PS-OPERATOR-CASE | ccn_std, ccn_mod and gated ccn | -Or written with a capital is not counted (probe ps1 ps-capital-or) | 3 | hand: PowerShell about_Logical_Operators | 3 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_logical_operators |
| AO-PS-STRAY-KEYWORD | Function discovery and spans | dotnet build --configuration $x.Configuration: the function holding it gets no row (probe ps1 ps-stray-keyword) | 1 | hand: PowerShell about_Parsing | 1 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_parsing |
| AO-PS-STRAY-PHANTOM | Function discovery and spans | the same line opens a phantom row named $x (probe ps1 ps-stray-phantom) | 0 | hand: PowerShell about_Parsing | 0 | fixed | https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_parsing |
| AO-PS-COG-TRAP | Cognitive complexity | a trap statement (probe ps1 ps-trap) | 1 | hand: Sonar paper | 1 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 (catch) |
| AO-PS-COG-RECURSION | Cognitive complexity | direct recursion (probe ps1 ps-factorial) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-PS-COG-RUNS-NOT | Cognitive complexity | if (-not ($a -and $b) -and $c) (probe ps1 ps-not-run) | 3 | hand: Sonar paper | 3 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-PS-COG-RUNS-LINES | Cognitive complexity | an -and sequence broken over two lines (probe ps1 ps-split-run) | 2 | hand: Sonar paper | 2 | fixed | paper: Sonar Cognitive Complexity v1.7 Sequences of logical operators |
| AO-PS-CLASS-IN-FUNCTION | ccn_std, ccn_mod and gated ccn | a class declared inside a function: its method's if counts toward the function (probe ps1 ps-class-in-function) | 1 | hand: NIST SP 500-235 sec. 4.1 | 1 | fixed | paper: NIST SP 500-235 sec. 4.1 |
| AO-PS-CLASS-IN-FUNCTION-COG | Cognitive complexity | a class declared inside a function: its method's if counts toward the function (probe ps1 ps-class-in-function) | 0 | hand: Sonar paper | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-PS-WHERE-ALIAS | PowerShell reader | ? (the Where-Object alias) counts as a ternary, while ForEach-Object, Where-Object and % count nothing (probe ps1 ps-where-alias) | 2 | hand: NIST SP 500-235 sec. 4.1 | 1 | definition | convention_only |
| AO-PS-WHERE-ALIAS-COG | Cognitive complexity | ? (the Where-Object alias) scores as a ternary, +1 plus its nesting (probe ps1 ps-where-alias) | 1 | hand: Sonar paper | 0 | definition | convention_only |
| AO-PSAST-XOR | PowerShell reader | the parser counter counts no -xor (about_Logical_Operators: -xor evaluates both operands) and crapkit counts each; the transform adds each -xor to ccn_std and ccn_mod (hand case AO-PSAST-XOR: return $a -xor $b) | 2 | PowerShell Parser AST (Windows PowerShell 5.1, PowerShell 7.6.6) | 1 | definition | convention_only |
| AO-PSAST-WHERE-ALIAS | PowerShell reader | the parser counter counts no command call and crapkit counts each ? command (the Where-Object alias) as a ternary; the transform adds each ? command to ccn_std and ccn_mod (hand case AO-PSAST-WHERE-ALIAS: $xs \| ? { $_ }) | 2 | PowerShell Parser AST (Windows PowerShell 5.1, PowerShell 7.6.6) | 1 | definition | convention_only |
| AO-PSCX-FLOW-COMMAND | ccn_std, ccn_mod and gated ccn | PSComplexity adds 1 for each ForEach-Object, Where-Object, foreach, where, % and ? command, which NIST SP 500-235 counts as no decision; the transform takes each off (hand case AO-PSCX-FLOW-COMMAND: $xs \| ForEach-Object { $_ * 2 }) | 1 | PSComplexity 0.5.1 | 2 | definition | paper: NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1 (decisions are predicates of the flow graph); https://www.powershellgallery.com/packages/PSComplexity/0.5.1 (src/Cyclomatic.ps1: Get-PSCxCycFlowCommandRow) |
| AO-PSCX-XOR | ccn_std, ccn_mod and gated ccn | PSComplexity counts no -xor and crapkit counts each; the transform adds each -xor (hand case AO-PSCX-XOR: return $a -xor $b) | 2 | PSComplexity 0.5.1 | 1 | definition | convention_only |
| AO-PSCX-WHERE-ALIAS | ccn_std, ccn_mod and gated ccn | crapkit counts each ? command as a ternary; after AO-PSCX-FLOW-COMMAND takes PSComplexity's point off, the transform adds 1 back (hand case AO-PSCX-WHERE-ALIAS: $xs \| ? { $_ }) | 2 | PSComplexity 0.5.1 | 2 | definition | convention_only |
| AO-PSCX-COG-FLOW-COMMAND | Cognitive complexity | PSComplexity scores each ForEach-Object, Where-Object and alias command 1 plus its nesting, an addition to the Sonar paper, which scores no call; the transform takes those points off (hand case AO-PSCX-COG-FLOW-COMMAND: $xs \| ForEach-Object { $_ * 2 }) | 0 | PSComplexity 0.5.1 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B1 (a call is no increment); https://www.powershellgallery.com/packages/PSComplexity/0.5.1 (src/Cognitive.ps1: Get-PSCxCogFlowCommandRow) |
| AO-PSCX-COALESCE-COG | Cognitive complexity | PSComplexity scores ?? and ??= 1 plus their nesting, where the Sonar paper ignores null-coalescing operators; the transform takes those points off (hand case AO-PSCX-COALESCE-COG: return $a ?? 0, then pinned by D2f) | 0 | PSComplexity 0.5.1 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 Ignore shorthand; https://www.powershellgallery.com/packages/PSComplexity/0.5.1 (src/Cognitive.ps1: Get-PSCxCogNullCoalesceRow) |
| AO-PSCX-SCRIPT-BLOCK | Cognitive complexity | PSComplexity raises the nesting level inside every script block and crapkit inside no closure (AO-COG-CLOSURE); the transform takes 1 off per script block around each scored structure (hand case AO-PSCX-SCRIPT-BLOCK: ForEach-Object { if ($_ -gt 0) { $_ } }) | 1 | PSComplexity 0.5.1 | 3 | definition | src/crapkit/lizardcognitive.py#L10 |
| AO-PSCX-PAREN-RUN | Cognitive complexity | PSComplexity starts a new sequence at a parenthesized group inside a sequence of the same operator, so ($a -and $b) -and $c scores two sequences where crapkit reads one; the transform takes 1 off per such group (hand case AO-PSCX-PAREN-RUN) | 2 | PSComplexity 0.5.1 | 3 | definition | convention_only |
| AO-PSCX-WHERE-ALIAS-COG | Cognitive complexity | crapkit scores each ? command as a ternary, 1 plus the if, loop, switch, catch and trap bodies around it; after AO-PSCX-COG-FLOW-COMMAND the transform adds that back (hand case AO-PSCX-WHERE-ALIAS-COG: $xs \| ? { $_ } inside an if) | 3 | PSComplexity 0.5.1 | 3 | definition | convention_only |
| AO-PSCX-CONDITION-NESTING | Cognitive complexity | PSComplexity nests a structure that sits in an if's or loop's condition one level deeper, where crapkit nests only what sits in the body; such functions are set aside (hand case AO-PSCX-CONDITION-NESTING: if ($x ? $a : 0)) | 2 | PSComplexity 0.5.1 | 3 | definition | convention_only |
| AO-JS-PAREN-VALUE | ccn_std, ccn_mod and gated ccn | a value opening with a parenthesis after `=`: const y = (a \|\| b) (probe js corpus-paren-value) | 1 | hand: NIST SP 500-235 sec. 4.1; ESLint 10.11.0 complexity | 2 | defect | paper: NIST SP 500-235 sec. 4.1 |
| AO-JS-PAREN-VALUE-CALL | Function discovery and spans | a parenthesized call after `=`: const y = (g(n)) ends parenCall on line 2 (probe js corpus-paren-call) | 2 | TypeScript 6.0.2 compiler | 7 | defect | https://tc39.es/ecma262/2025/#sec-function-definitions |
| AO-JS-KEYWORD-NAME | ccn_std, ccn_mod and gated ccn | a method named catch: p.then(a).catch(b) (probe js corpus-keyword-member) | 2 | hand: NIST SP 500-235 sec. 4.1; ESLint 10.11.0 complexity | 1 | defect | paper: NIST SP 500-235 sec. 4.1 |
| AO-JS-KEYWORD-NAME-COG | Cognitive complexity | a method named catch: p.then(a).catch(b) (probe js corpus-keyword-member) | 0 | hand: Sonar paper; eslint-plugin-sonarjs 4.2.1 | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-JS-KEYWORD-FUNCTION-KEY | Function discovery and spans | a property named async: { async: true } runs context on to line 6 for 4, the next function's line (hand case cases/async_key.ts in test_js_corpus_oracles) | 6 | TypeScript 6.0.2 compiler | 4 | defect | https://tc39.es/ecma262/2025/#sec-object-initializer |
| AO-JS-REGEX-AS-CODE | ccn_std, ccn_mod and gated ccn | a regular expression after a space holding a `?`: const m = /(?:x)/ (probe ts corpus-regex-code) | 2 | hand: NIST SP 500-235 sec. 4.1; ESLint 10.11.0 complexity | 1 | defect | https://tc39.es/ecma262/2025/#sec-literals-regular-expression-literals |
| AO-JS-OPTIONAL-CALL-END | Function discovery and spans | an optional call a?.() ends callIfSet on its own line, 2 for 3 (probe ts corpus-optional-call) | 2 | TypeScript 6.0.2 compiler | 3 | defect | https://tc39.es/ecma262/2025/#sec-optional-chains |
| AO-JS-ARROW-BODY-BELOW | ccn_std, ccn_mod and gated ccn | an arrow argument whose concise body starts on the line after `=&gt;`: its conditional counts in the enclosing function, outer 2 for 1 (probe ts corpus-arrow-below) | 2 | hand: NIST SP 500-235 sec. 4.1; ESLint 10.11.0 complexity | 1 | defect | paper: NIST SP 500-235 sec. 4.1 |
| AO-TS-TYPE-QUESTION | ccn_std, ccn_mod and gated ccn | a conditional type: v as string extends keyof T ? X : Y (probe ts corpus-type-question) | 2 | hand: NIST SP 500-235 sec. 4.1; ESLint 10.11.0 complexity | 1 | defect | https://www.typescriptlang.org/docs/handbook/2/conditional-types.html |
| AO-TS-TYPE-QUESTION-COG | Cognitive complexity | a conditional type: v as string extends keyof T ? X : Y (probe ts corpus-type-question) | 1 | hand: Sonar paper; eslint-plugin-sonarjs 4.2.1 | 0 | defect | paper: Sonar Cognitive Complexity v1.7 App. B1 |
| AO-TS-TYPE-QUESTION-MEMBER | ccn_std, ccn_mod and gated ccn | an optional member after a string type argument: Props&lt;"a"&gt; &amp; { asChild?: boolean } (probe tsx corpus-type-member) | 2 | hand: NIST SP 500-235 sec. 4.1; ESLint 10.11.0 complexity | 1 | defect | https://www.typescriptlang.org/docs/handbook/2/objects.html#optional-properties |
| AO-TS-FUNCTION-TYPE-ROW | Function discovery and spans | a function type in an arrow's parameter list: (d: (q: number) =&gt; number) =&gt; ends wrap on line 2 for 9 (probe ts corpus-function-type) | 2 | TypeScript 6.0.2 compiler | 9 | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#function-type-expressions |
| AO-TS-FUNCTION-TYPE-ALIAS | Function discovery and spans | a function type in a type alias, type Parse = &lt;T&gt;(a: T) =&gt; T over three lines, is listed as a function on line 3 (hand case cases/function_type_alias.ts in test_js_corpus_oracles) | 1 | TypeScript 6.0.2 compiler | 0 | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#function-type-expressions |
| AO-TS-FUNCTION-TYPE-VAR | Function discovery and spans | a function type annotating the variable an arrow initializes: const parse: (e: E) =&gt; P = (e) =&gt; {...} lists two functions on line 1 (hand case cases/typed_initializer.ts in test_js_corpus_oracles) | 2 | TypeScript 6.0.2 compiler | 1 | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#function-type-expressions |
| AO-JS-TERNARY-CALL-ROW | Function discovery and spans | a property value over lines, `a: p` / `? darken(c, k)` / `: h`, lists darken as a function (probe ts corpus-ternary-call) | 1 | TypeScript 6.0.2 compiler | 0 | defect | https://tc39.es/ecma262/2025/#sec-conditional-operator |
| AO-JSX-SPREAD-ROWS | Function discovery and spans | a JSX spread attribute: Card holding &lt;div {...props} /&gt; and followed by sink gets no row (hand case cases/spread.tsx in test_js_corpus_oracles) | 0 | TypeScript 6.0.2 compiler | 1 | defect | https://facebook.github.io/jsx/#prod-JSXSpreadAttribute |
| AO-JSX-CHILD-TAG-LINE | Function discovery and spans | a child tag with attributes after a child expression: Card holding &lt;div className="card"&gt; / {a} / &lt;p className="body"&gt; ends on line 7, the compiler says 8, and sink after it starts on 9, not 10 (hand case cases/child_tag_line.tsx in test_js_corpus_oracles) | 7 | TypeScript 6.0.2 compiler | 8 | defect | https://facebook.github.io/jsx/#prod-JSXChildren |
| AO-TSX-OPTIONAL-CHAIN-ND | Nesting depth | an optional chain a?.focus() in a .tsx file (probe tsx corpus-optional-chain) | 0 | hand: Sonar paper App. B2; ESLint 10.11.0 max-depth | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-JS-COG-RECURSION-NAME | Cognitive complexity | this.router.route(path) inside route (probe js corpus-recursion-name) | 0 | hand: Sonar paper; eslint-plugin-sonarjs 4.2.1 | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 Recursion |
| AO-JS-FOREIGN-KEYWORD | Nesting depth | a parameter named def, spelled twice: wire(inst, def) (probe js corpus-def-param) | 0 | hand: Sonar paper App. B2; ESLint 10.11.0 max-depth | 0 | fixed | paper: Sonar Cognitive Complexity v1.7 App. B2 |
| AO-TS-PAREN-TYPE-PARAM | Function discovery and spans | a parenthesized type in a parameter list: norm(form?: "NFC" \| (string &amp; {})) gets no row (probe ts corpus-paren-type) | 0 | TypeScript 6.0.2 compiler | 1 | defect | https://www.typescriptlang.org/docs/handbook/2/everyday-types.html#union-types |
| AO-ESLINT-PATTERN-DEFAULTS | ccn_std, ccn_mod and gated ccn | a default value inside a destructured parameter, { orientation = "vertical" }: ESLint adds 1 (AssignmentPattern), crapkit adds nothing, as for a parameter's own default (hand case cases/pattern_defaults.ts) | 1 | ESLint 10.11.0 complexity | 2 | definition | convention_only |
| AO-N-PYLINT-ASYNC | Nesting depth | oracle bug: pylint's refactoring checker emits a def's left-over block stack in leave_functiondef and has no leave_asyncfunctiondef, so an async def's last group of nested blocks is never reported (an async def holding one if reads 0) and its stack leaks into the next def; crapkit reads 1; async defs are set aside from the pylint comparison (hand case AO-N-PYLINT-ASYNC; 18 defs in cpython-3.13/Lib/asyncio) | 1 | pylint 4.0.9 R1702 | 0 | definition | https://docs.python.org/3/reference/compound_stmts.html#coroutine-function-definition (an async def is a function definition); paper: Sonar Cognitive Complexity v1.7 App. B2 (if increases the nesting level); pylint 4.0.9 pylint/checkers/refactoring/refactoring_checker.py leave_functiondef |
| AO-N-PYLINT-WITH | Nesting depth | oracle bug: pylint stacks no with, so for an if, for, while or try right in the body of a with that sits inside another block, _check_nested_blocks finds no parent on its stack, pops every level and restarts the block at 1; the paper and crapkit keep the outer levels (if, with, if reads 2); defs holding such a with are set aside from the pylint comparison (hand case AO-N-PYLINT-WITH; 3 defs in cpython-3.13/Lib) | 2 | pylint 4.0.9 R1702 | 1 | definition | paper: Sonar Cognitive Complexity v1.7 App. B2 (nested if, loops and catch increase the nesting level; with is not in the list); pylint 4.0.9 pylint/checkers/refactoring/refactoring_checker.py _check_nested_blocks |
| AO-DUP-STAR-LINE | Near-duplicate functions | a Python `**spread(items),` argument line, the one line two 12-line functions differ in (star_a, star_b; hand containment 5/10) | none | hand: the stated line scheme (comment lines left out) | none | fixed | https://docs.python.org/3/reference/lexical_analysis.html#comments |
| AO-DUP-DOCSTRING-LINES | Near-duplicate functions | a one-line Python docstring, the one line besides the def line two functions differ in (doc_a, doc_b); a line starting with three quotes is left out | 0.875 | hand: the line scheme keeping string literals | 0.7778 | definition | convention_only |
| AO-SYMILAR-SCHEME | Near-duplicate functions | symilar strips only a line's two ends and keeps comment lines, so the adapter feeds it each function's scheme lines, every whitespace character removed and comment lines left out (hand case AO-SYMILAR-SCHEME: a copy with comment lines, blank lines and wider spacing; pairs found) | 1 | pylint 4.0.9 symilar | 0 | definition | https://github.com/pylint-dev/pylint/blob/v4.0.9/pylint/checkers/symilar.py (stripped_lines: line.strip()) |
| AO-SYMILAR-ONE-FILE | Near-duplicate functions | symilar compares each file with every other file and never with itself, so the adapter writes each function's lines to a file of its own (hand case AO-SYMILAR-ONE-FILE: two copies in one file; pairs found) | 1 | pylint 4.0.9 symilar | 0 | definition | https://github.com/pylint-dev/pylint/blob/v4.0.9/pylint/checkers/symilar.py (_iter_sims: linesets[idx + 1:]) |
| AO-SYMILAR-CONTENT-LINES | Near-duplicate functions | symilar counts only lines holding a word character toward a run's size, so a run of `grid = [`, `[`, `],` and `]` counts 1 line and is dropped; the adapter leads every line with x, which keeps equal lines equal (hand case AO-SYMILAR-CONTENT-LINES: one shared window of those 4 lines; pairs found) | 1 | pylint 4.0.9 symilar | 0 | definition | https://github.com/pylint-dev/pylint/blob/v4.0.9/pylint/checkers/symilar.py (filter_noncode_lines, REGEX_FOR_LINES_WITH_CONTENT) |
| AO-SYMILAR-THRESHOLD | Near-duplicate functions | symilar keeps a run of more than --duplicates lines, so at its default 4 a run of 4 lines, one shared window, is dropped; the adapter runs at 3 (hand case AO-SYMILAR-THRESHOLD: two functions sharing one 4-line window; pairs found) | 1 | pylint 4.0.9 symilar | 0 | definition | https://github.com/pylint-dev/pylint/blob/v4.0.9/pylint/checkers/symilar.py (_find_common: eff_cmn_nb &gt; min_similarity_lines) |
| AO-SYMILAR-EVERY-COUPLE | Near-duplicate functions | symilar's printed report drops a couple whose block another couple of the same size already names, so three copies print as one couple; the adapter reads every couple symilar finds (Symilar._iter_sims, the report's input) (hand case AO-SYMILAR-EVERY-COUPLE: three copies; pairs found) | 3 | pylint 4.0.9 symilar | 1 | definition | https://github.com/pylint-dev/pylint/blob/v4.0.9/pylint/checkers/symilar.py (_compute_sims: the no_duplicates break) |
| AO-HOOK-CR-ONLY | Source decoding and line normalization | a new file with CR-only line endings, staged: functions hook-precommit gates (two, ccn 2 each, target 1) | 2 | hand: git diff adds every line of a new file | 2 | fixed | README.md#exit-codes (6: a function the diff touched is over its ceiling) |
| AO-HOOK-LONE-CR-SHIFT | Source decoding and line normalization | an edit on a def line below one lone CR (git line 8, reader line 9): the function hook-precommit gates (ccn 2, target 1) | target | hand: the staged diff changes target's def line | target | fixed | README.md#exit-codes (6: a function the diff touched is over its ceiling) |
| AO-REVIVE-SWEEP | Nesting depth | revive reports each statement past its configured limit as `control flow nesting exceeds &lt;limit&gt;`, naming the limit and never the depth the statement reaches, so no one revive process gives a function's depth; go_adapters.revive_depths runs one process per limit, 0 upward over the files that still report, until none does, so a set whose deepest function nests d levels starts d + 1 processes where every other adapter starts one per shard (values: processes per shard, the adapter rule's 1 against revive's 4 for the hand case, a for holding an if holding an if, depth 3, every report at limit 0 naming 0) | 1 | revive 1.17.0 max-control-nesting | 4 | definition | https://github.com/mgechev/revive/blob/v1.17.0/RULES_DESCRIPTIONS.md#max-control-nesting |
| AO-JS-SWITCH-RETURN | Function discovery and spans | a switch whose last case ends in `return 'many'` with no semicolon before its closing brace: kind runs on over after, which gets no row (hand case SWITCH_RETURN in test_ts_compiler; express lib/response.js stringify's replace callback ends a line late the same way) | 0 | TypeScript 6.0.2 compiler | 1 | defect | https://tc39.es/ecma262/2025/#sec-function-definitions |

</details>

<details><summary><code>corpus_goldens</code>: 5 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| CG1 | Function discovery and spans | a function inside a .vue &lt;script&gt; block that does not open on line 1 | start 2, 9 wrong fields | the source file's own line numbers and the istanbul fnMap of the same file | start 6, 0 wrong fields | defect | https://vuejs.org/api/sfc-spec.html |
| CG2 | SARIF and GitHub annotations | the $schema property of every SARIF log crapkit writes | resolvable | SARIF 2.1.0 errata01 section 3.13.3 and an HTTP fetch of the printed URI | resolvable | fixed | https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/sarif-v2.1.0-errata01-os-complete.html#_Toc141790734 |
| CG3 | Printed commands | the next step a refusal prints on Windows when python -m crapkit started crapkit, pasted into Git Bash | exit 0 | Git Bash (bash 5 from git for Windows) | exit 0 | fixed | https://www.gnu.org/software/bash/manual/bash.html#Escape-Character |
| CG4 | Printed commands | the next step a refusal prints on Windows when the interpreter path holds a space, pasted into PowerShell | exit 0 | Windows PowerShell 5.1 and pwsh 7 | exit 0 | fixed | https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators#call-operator- |
| CG5 | Store and cache upgrade | 0.6.0 and this crapkit reading one working tree: 0.7.0 changed the path format both caches key on and kept the file names | none | the cache files' bytes before and after each install's read | none | fixed | README.md#subcommands |

</details>

<details><summary><code>coverage_oracles</code>: 18 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| CO3 | Istanbul attribution (vitest v8, nyc, Jest, c8) | a default parameter the call never uses, under @vitest/coverage-v8 5.0.1 | 1.0 | ground_truth.tsv arms_taken | 0.0 | definition | https://v8.dev/blog/javascript-code-coverage#block-coverage (V8 keeps no count for a default parameter, so the v8 provider's default-arg arm takes the function's count) |
| CO-B1 | Istanbul attribution (vitest v8, nyc, Jest, c8) | `const f = (x) =&gt; ...` never called: the declaration's statement runs at import and counts inside the arrow's span | 0.0 | ground_truth.tsv arms_taken | 0.0 | fixed | https://github.com/istanbuljs/istanbuljs/blob/istanbul-lib-instrument%406.0.3/packages/istanbul-lib-instrument/src/visitor.js#L480 (the declarator's counter covers the initializer, the arrow itself) |
| CO-B2 | coverage.py per-function coverage | a nested def on its encloser's first body line, in a coverage.py 7.10.6 or 7.13.0 report (no start_line) | 0.0 | ground_truth.tsv arms_taken | 0.0 | fixed | https://coverage.readthedocs.io/en/7.16.1/changes.html#version-7-13-1 (start_line arrived in 7.13.1; before it a region starts at its first body line) |
| CO-B3 | coverage.py per-function coverage | a function whose def line carries `# pragma: no cover` | crap = ccn | ground_truth.tsv (excluded) | crap = ccn | fixed | https://coverage.readthedocs.io/en/7.16.1/excluding.html#excluding-code |
| CO-B4 | Istanbul attribution (vitest v8, nyc, Jest, c8) | a function an `istanbul ignore next` or `v8 ignore next` hint drops from the artifact | crap = ccn | ground_truth.tsv (absent) | crap = ccn | fixed | https://github.com/istanbuljs/nyc/blob/v18.0.0/README.md#parsing-hints-ignoring-lines |
| CO-B5 | Coverage join | a TypeScript function with a return type annotation, next to a function its producer dropped | measured from another function | ground_truth.tsv (absent) and the source's closing brace | crap = ccn | defect | https://www.typescriptlang.org/docs/handbook/2/functions.html#function-type-expressions (the return type sits before the body; the body ends at its closing brace) |
| D7 | Istanbul attribution (vitest v8, nyc, Jest, c8) | raw c8 12.0.0 json: v8-to-istanbul writes every line as a statement and each V8 block range as a branch | 5 | ground_truth.tsv arms_taken | 0 | definition | https://github.com/istanbuljs/v8-to-istanbul/blob/v9.3.0/README.md (line and block ranges from V8, not the istanbul instrumenter's statements and branches) |
| CO4 | Istanbul attribution (vitest v8, nyc, Jest, c8) | jest 30.5.2 with coverageProvider v8: the same raw v8-to-istanbul shape as c8 | 10 | ground_truth.tsv arms_taken | 0 | definition | https://jestjs.io/docs/configuration#coverageprovider-string (v8 coverage is converted with v8-to-istanbul) |
| D6 | Dark lines and changed-line dead set | a line holding a statement that ran and one that never ran (js/shapes.js:60 idle: the arrow declaration runs at import, its body never does) | dark | istanbul-reports lcov DA (istanbul-lib-coverage getLineCoverage) | DA:60,1 | definition | docs/lanes.md#what-the-istanbul-parser-reads (statementMap and s are the only source of the uncovered lines; a statement that never ran marks its line) and https://github.com/istanbuljs/istanbuljs/blob/istanbul-lib-coverage%403.2.2/packages/istanbul-lib-coverage/lib/file-coverage.js#L221-L243 (the oracle keeps the largest hit count per line) |
| CO5 | Diff coverage | a changed file no coverage report names (py/new.py, added with one four-line function) | 4 | diff-cover 10.6.0 (Cobertura xml) | 0 | definition | README.md#exit-codes (exit 9: a changed file no lane artifact mentions counts every line of its functions); diff-cover measures only the files its reports name: https://github.com/Bachmann1234/diff_cover/blob/v10.6.0/README.rst#troubleshooting |
| CO6 | Shared span and def-line floor | a one-line def a test called with both arms: coverage.py records the import flow-on and the return from the call, 2 of 2 arcs | 0.0 | coverage.py 7.16.1 branch arcs (the call's return arc) | 1.0 | definition | README.md#remedy-what-to-do-about-it (a Python def whose body starts on the line its signature ends takes split-lines and scores uncovered whatever the tests do) |
| CO8 | Function coverage ratio | a function with no branch record in its span that never ran (py/shapes.py branchless, idle) | 0.0 | crap4py 0.1.1 (lcov BRDA) | 1.0 | definition | README.md#crapkit (cov is branch coverage inside the span; with no branches it falls back to statement coverage, and with no statements to invoked-or-not, so a half-executed straight-line function never reads as fully covered); crap4py reads it as covered: https://github.com/gabadi/crap4py/blob/v0.1.1/src/crap4py/coverage.py#L1-L8 |
| CO7 | Istanbul attribution (vitest v8, nyc, Jest, c8) | a function whose statement coverage is below its branch coverage (ts/shapes.ts ignoredV8 under vitest istanbul, call: 3 of 4 branches, 2 of 3 statements) | 0.75 | crap-typescript 0.5.2 | 0.6667 | definition | README.md#crapkit (cov is branch coverage inside the span; statements only when there is no branch); crap-typescript scores the smaller of the two measured percentages: https://github.com/barney-media/crap-typescript/blob/v0.5.2/README.md |
| CO9 | Istanbul attribution (vitest v8, nyc, Jest, c8) | a default parameter the call never uses (ts/shapes.ts defaultParam, call): istanbul writes a default-arg branch, the TypeScript compiler sees no branching construct | 0.0 | crap-typescript 0.5.2 | 1.0 | definition | docs/lanes.md#what-the-istanbul-parser-reads (branchMap and b are the branch coverage: every branch the artifact names counts) |
| CO10 | Istanbul attribution (vitest v8, nyc, Jest, c8) | an anonymous callback inside a function (ts/shapes.ts withCallback, call): crapkit scores the callback as its own function, crap-typescript folds its counters into the enclosing method | 1.0 | crap-typescript 0.5.2 | 0.5 | definition | docs/lanes.md#what-the-istanbul-parser-reads (each branch counts against the innermost function whose span holds it) |
| D8 | coverage.py per-function coverage | a match statement (py/shapes.py match_case, called with "go" and "halt"): coverage.py measures each case pattern's two exits, slipcover one arm per case body from the match line | 0.75 | slipcover 1.1.0, split by ast function bodies | 0.6667 | definition | docs/lanes.md#which-languages-a-lane-can-measure (a coveragepy lane reads coverage.py's JSON report; coverage.py is the Python producer) and https://github.com/coveragepy/coveragepy/blob/7.16.1/coverage/parser.py#L1118 (each refutable case pattern exits to its body or the next case) |
| CO11 | Coverage join | two arrows on one TypeScript line, one called with both arms of its ternary and one never called: crapkit floors the called one to uncovered, crap-typescript matches each arrow to its fnMap entry by column and reads it covered | 0.0 | crap-typescript 0.5.2 over istanbul-lib-instrument 6.0.3 coverage | 1.0 | definition | README.md#remedy-what-to-do-about-it (split-lines: another function shares its source lines, so the score stays at uncovered whatever the tests do); crap-typescript breaks the tie by column: https://unpkg.com/@barney-media/crap-typescript-core@0.5.2/dist/coverageAttribution.js (matchByCandidateSpans compares start and end columns). Doc gap: the README says coverage cannot tell functions on one line apart, but an istanbul fnMap carries columns and can; crapkit's function spans are whole lines, so it floors both on purpose |
| CO-READER-JOIN | Function coverage ratio | the small corpus files that carry a reader defect (src/web/Counter.vue, generic.ts, templates.ts): the artifact places each function on its own line, and crapkit gives it no row or a row on another line | 9 findings | counts_table.py over the recorded js lane (istanbul fnMap decl lines) | 0 findings | defect | https://vuejs.org/api/sfc-spec.html#language-blocks |

</details>

<details><summary><code>definitions</code>: 2 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| definitions-1 | Field definitions across docs and schemas | a TypeScript function declaration with a return type annotation ends on the line of the next statement, so its span and nloc take in lines outside it | 2 | nloc definition by hand; typescript 6.0.2 FunctionDeclaration end line | 1 | defect | https://github.com/terryyin/lizard#readme |
| definitions-2 | Field definitions across docs and schemas | a reader page gives est_uncovered_paths as a bare round((1 - cov) * ccn), which reads as half up to anyone who does not know Python rounds ties to even | none | D10: agent-json.md item row and the MCP schemas, half to even | none | fixed | https://docs.python.org/3/library/functions.html#round |

</details>

<details><summary><code>history_oracles</code>: 15 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| H1 | Churn counts and recency weight | a non-ASCII path under core.quotePath=true: git C-quotes it unless -z is given, so a walk that reads numstat lines must unquote; the first walk did not and missed the path (the oracle's fault) | src/ütf.py | git log --numstat without -z | "src/\303\274tf.py" | definition | https://git-scm.com/docs/git-config#Documentation/git-config.txt-corequotePath |
| H5 | Churn counts and recency weight | the newest commit in the window changes no file under the crapkit root (here one above a root below the git top); crapkit dates the recency range by the commits that change a file under the root, so src/b.py's newest commit weighs 0.5 | 0.5048 | README reading: the range runs to the newest commit git log lists | 0.0483 | definition | convention_only |
| H9 | Changed line ranges | a pure deletion (a hunk with no new-side line): crapkit marks the new-side line the hunk header names, the line before the deletion, so the function holding that line is touched; unidiff lists no target line for the hunk | f2 | unidiff 1.0.1 target lines | none | definition | https://www.gnu.org/software/diffutils/manual/html_node/Detailed-Unified.html#Detailed-Unified |
| H10 | Explain history | a commit body line that is \x02, or that starts with \x01 and holds three words: explain's log format frames each record with \x01 and \x02, so the body is cut at that line, and a \x01 line opens a phantom commit | [('stx body', 'line one\n\x02\nafter the stx line'), ('seed', '')] \| [('soh body', 'first\n\x01zz 2025-01-01 fake subject\nlast'), ('seed', '')] | git cat-file commit, cut as git %s and %b | [('stx body', 'line one\n\x02\nafter the stx line'), ('seed', '')] \| [('soh body', 'first\n\x01zz 2025-01-01 fake subject\nlast'), ('seed', '')] | fixed | https://git-scm.com/docs/git-log#Documentation/git-log.txt-emsem |
| H11 | Explain history | a commit body line that starts with \x01 and holds fewer than three words: explain --history stops with a ValueError traceback instead of listing the commits | exit 0 | git cat-file commit, cut as git %s and %b | exit 0 | fixed | https://git-scm.com/docs/git-log#Documentation/git-log.txt-emsem |
| H12 | Explain history | a commit body holding \r, \x0c, \x1c or \x85: explain reads git's output in text mode and splits it with str.splitlines, so each comes back as a newline | [('separators', 'half\rway, before\x0cafter\nsep\x1crecord, split\x85here'), ('seed', '')] | git cat-file commit, cut as git %s and %b | [('separators', 'half\rway, before\x0cafter\nsep\x1crecord, split\x85here'), ('seed', '')] | fixed | https://git-scm.com/docs/git-log#Documentation/git-log.txt-emsem |
| H6 | Change coupling | how strongly two files co-change: code-maat prints int(100 x shared / mean of the two files' revisions); crapkit prints the shared commits (support) and the larger of shared over each file's commits (confidence). The test predicts code-maat's degree from crapkit's support for every pair | 5 0.5556 | code-maat 1.0.4 coupling degree | 58 | definition | docs/agent-json.md#other-payloads |
| H7 | Change coupling | a commit of more than 30 files: crapkit counts it toward each file's commits (the confidence denominator) and pairs nothing; code-maat and a transaction set without bulk commits drop it from the file counts too, so src/a.py and src/b.py read 5 of 7 and not 5 of 9 | 0.5556 | mlxtend 0.25.0 association_rules over commits of 30 files or fewer | 0.7143 | definition | convention_only |
| H2 | Churn counts and recency weight | commits before a rename: CommitsCount files a renamed file's earlier commits under its newest name; crapkit reads git log without --follow, where a file's history stops at a rename, so the earlier commits stay under the old name | src/new_name.py 2, src/old_name.py 1 | PyDriller 2.12 CommitsCount | src/new_name.py 3 | definition | https://git-scm.com/docs/git-log#Documentation/git-log.txt---follow |
| H3 | Churn counts and recency weight | one author name under two e-mail addresses, and two names on one address: ContributorsCount counts addresses; crapkit counts author names (git log %an, no mailmap) | src/core.py 3, src/util.py 1 | PyDriller 2.12 ContributorsCount | src/core.py 2, src/util.py 2 | definition | convention_only |
| H4 | Churn counts and recency weight | a commit whose author date (t = 1/4) differs from its committer date (t = 9/10): bugspots weighs a fix by its commit time; crapkit weighs a commit by its author date and uses the commit date only for the window cutoff | 0.0001 | bugspots 0.2.2 under libfaketime at the newest commit | 0.2315 | definition | CONTEXT.md#the-worklist |
| H13 | Churn counts and recency weight | a merge commit: bugspots reads its diff against the first parent as the merge's own change, so src/core.py, changed on the merged branch, is weighed again at the merge; git log prints no file for a merge, so crapkit gives a merge no weight. The transform leaves merges out of the commits bugspots walks | 0.0026 | bugspots 0.2.2 under libfaketime, walking the window's commits with the merge among them | 0.0031 | definition | https://git-scm.com/docs/git-log#Documentation/git-log.txt---diff-mergesltformatgt |
| H14 | Change coupling | two pairs whose support x confidence are equal: 1 shared of 3 commits (0.3333) and 3 shared of 27 (3 x 0.1111 = 0.3333). crapkit multiplies in binary floating point, where 3 x 0.1111 is 0.33330000000000004, so the second pair outranks the first whatever their paths, and `--top` keeps one or the other by that noise | src/a.py src/b.py, src/y.py src/z.py | the rank order crapkit's code sorts by (coupling.py _rank_pairs: support x confidence, highest first, then the two paths), in exact arithmetic; no doc states the order | src/a.py src/b.py, src/y.py src/z.py | fixed | convention_only |
| H15 | Churn counts and recency weight | a rename: bugspots diffs each commit with no rename detection, so the rename commit counts under the old path and the new one; git log detects renames by default (diff.renames), so crapkit counts it under the new path only and src/old_name.py keeps its seed commit alone. The transform compares only files no rename names | 0.0 | bugspots 0.2.2 under libfaketime, walking the window's commits without merges | 0.0001 | definition | https://git-scm.com/docs/git-config#Documentation/git-config.txt-diffrenames |
| H16 | Churn counts and recency weight | a commit older than the churn window: bugspots has no window, so it weighs src/a.py's aged-out first commit and dates its range from it; crapkit reads the commits of the last churn_window_months (12) and dates the range from the oldest of those. The transform makes bugspots walk exactly the window's commits | 0.0 | bugspots 0.2.2 under libfaketime at the newest commit, walking the whole branch | 0.0034 | definition | CONTEXT.md#the-worklist |

</details>

<details><summary><code>runtime_guards</code>: 7 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| RG1 | Runtime invariant verdict | TypeScript expression arrow whose body starts on the line after its arrow | lines 2-2 nloc 2 | the span read off the source by hand | lines 2-3 nloc 2 | defect | docs/agent-json.md#item-fields |
| RG2 | Runtime invariant verdict | ratchet prune of a marks file written before stamping | no stamp line | the accuracy plan's dump_ratchet bound (a stamp is present) | stamp line | definition | docs/ratchet.md#the-metric-stamp |
| RG3 | Runtime invariant verdict | claude-hook stopped by an internal check | exit 0 silent | the accuracy plan's print-only sites (exit 5, kind internal) | exit 5 | definition | README.md#subcommands |
| RG4 | Runtime invariant verdict | a row whose nloc passes its span's line count | accepted | the documented span and nloc (a row's nloc counts lines of its span) | stopped | defect | docs/agent-json.md#item-fields |
| RG5 | Churn counts and recency weight | the dated four-commit history's worklist 1 day and 400 days after its newest commit | 400 days: 4 active | README.md:851-852: the window reaches `churn_window_months` back from HEAD's commit date, never from the wall clock, so a fixed tree ranks identically forever | 400 days: 4 active | fixed | README.md#risk-what-ranks-the-worklist |
| RG6 | Runtime invariant verdict | a row whose nloc is below 0 | accepted | the documented span and nloc (a row's nloc counts lines of its span, so it is at least 0) | stopped | defect | docs/agent-json.md#item-fields |
| RG7 | nloc | a Python function returning a triple-quoted f-string with an interpolation on each of its lines | lines 1-6 nloc -2 | the lines read off the source by hand, counted as a plain triple-quoted string's lines count (nloc 5 for the same shape without the f) | lines 1-6 nloc 6 | defect | docs/agent-json.md#item-fields |

</details>

<details><summary><code>score_model</code>: 25 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| D5 | CRAP score | exact 4 dp tie: CRAP(4, 1/40) = 18.82975 | 18.8297 | kit.exact half-even | 18.8298 | definition | https://docs.python.org/3/library/functions.html#round |
| D5.2 | CRAP score | exact 2 dp tie: CRAP(5, 1/10) = 23.225 | 23.23 | kit.exact half-even | 23.22 | definition | https://docs.python.org/3/library/functions.html#round |
| D5.1 | CRAP score | exact 1 dp tie: CRAP(20, 1/20) = 362.95 | 362.9 | kit.exact half-even | 363.0 | definition | https://docs.python.org/3/library/functions.html#round |
| D13 | CRAP score | the crap double equals ccn*ccn*(d*d*d)+ccn, d = 1 - cov, on the reduced grid | True | IEEE 754 basic operations | True | fixed | paper: IEEE 754-2019 section 5.4 (basic operations correctly rounded) and section 9.2 (pow recommended, not required) |
| SM-PHPUNIT-95 | CRAP score | CRAP(5, 95 percent) | 5.003125 | PHPUnit CrapIndex (php-code-coverage ee9fdcf) | 5 | definition | https://www.artima.com/weblogs/viewpost.jsp?thread=215899 |
| D10 | Work budget estimates | est_uncovered_paths at ccn 5, cov 1/2: (1 - 1/2) * 5 = 2.5 | 2 | round half up | 3 | definition | https://docs.python.org/3/library/functions.html#round |
| SM-BUDGET-TIE | Work budget estimates | est_uncovered_paths at ccn 6, cov 5/12: (1 - 5/12) * 6 = 3.5 exactly | 4 | kit.exact half-even | 4 | fixed | https://docs.python.org/3/tutorial/floatingpoint.html#representation-error |
| SM-CEILING-EQ | Remedy label | CRAP(18, 2/3) = 30 exactly against target = 30 | ok | model_score.remedy on the exact CRAP | ok | fixed | https://docs.python.org/3/tutorial/floatingpoint.html#representation-error |
| D11 | doctor --tune knobs and lane cost | lane durations (3, 3, 2, 2, 2) on 2 slots | 7.0 | brute-force optimal makespan | 6 | definition | paper: Graham 1969, Bounds on multiprocessing timing anomalies, SIAM J. Appl. Math. 17(2):416-429, section 3 |
| SM-TUNE-KNOBS | doctor --tune knobs and lane cost | analysis_workers and mutation_workers at 8 cpus: the docs show only the 24-cpu case | 7,2 | docs/configuration.md worked example | unspecified | definition | convention_only |
| SM-HOT-MIN | Queue admission and floors | hot promotion over 4 files weighing 0.1, 0.2, 0.3, 0.4: a ccn-4 function in the 0.4 file | 0 | README top 10 percent rule (numpy percentile, method lower) | 1 | definition | convention_only |
| SM-RANK-TAIL | Worklist ranking and dormant list | equal risk 4.0: ccn 8 at weight 0.5 against ccn 4 at weight 1.0 | ccn-8 first | docs sort (risk only) | unspecified | definition | convention_only |
| SM-NEXT-TAIL | next-item ranking and empty-queue reasons | equal crap 30.0 in two files with 1 and 4 commits | 4-commit file first | docs order (crap descending) | unspecified | definition | convention_only |
| SM-BATCH-50 | Batch split | a pair at confidence 0.49 and at 0.5 | apart,together | docs --batches (co-changing files kept together) | unspecified | definition | convention_only |
| SM-BATCH-LPT | Batch split | risks f0 3.53, f1 0.01, f2 2.19, f3 0.01, f4 3.49, f5 4.36; f1-f2 and f1-f4 coupled at 0.5; 2 batches (hundredths) | 789 | LPT on each coupled group's summed risk (oracles/makespan.lpt) | 789 | fixed | paper: Graham 1969, Bounds on multiprocessing timing anomalies, SIAM J. Appl. Math. 17(2):416-429, section 3 |
| SM-WORKLIST-STORED | Worklist ranking and dormant list | a ccn-5 function at CRAP 30 scored at target 6, with target = 30 edited in and no run since | add-tests | model_score.remedy at today's ceiling | ok | definition | docs/agent-json.md#worklist |
| SM-DIGEST-001 | Digest deltas | one function moves 0.00004; the CRAP load, average and share over keep their printed values | quiet | README digest (silent when nothing changed) | speaks | definition | convention_only |
| SM-OVERLAY-TWIN | Rescore overlay | two baseline twins of one name at lines 1 and 20, the fresh twin at line 18 | the line-20 twin's cov | README rescore (joined by name) | unspecified | definition | convention_only |
| SM-NEXT-DOC | next-item ranking and empty-queue reasons | the docs' one-run example: render (ccn 7, crap 56.0) and classify (ccn 14, crap 46.6095), both decompose, 2 commits each | render | docs/agent-json.md#worklist worked sentence (line 702) | classify | definition | docs/agent-json.md#next-item |
| SM-CRAPTS-TIE | CRAP score | exact 2 dp tie: CRAP(9, 5/6) = 9.375; each tool prints the side its own double falls on | 9.38 | crap-typescript-core 0.5.2 calculateCrapScore | 9.37 | definition | https://docs.python.org/3/tutorial/floatingpoint.html#representation-error |
| SM-CEILING-EQ-TOTALS | Run totals and trend rollup | CRAP(18, 2/3) = 30 exactly against target = 30: over_target of the digest totals and of a brief's file totals | 0,0 | README over-target rule on the exact CRAP | 0,0 | fixed | https://docs.python.org/3/tutorial/floatingpoint.html#representation-error |
| SM-LOAD-ORDER | Run totals and trend rollup | Python 3.11: eleven rows whose exact CRAP load is the 2 dp tie 507.625, summed in their drawn order and sorted by CRAP | 507.63 | exact sum of the stored doubles (math.fsum), half-even at 2 dp | 507.63 | fixed | https://docs.python.org/3/library/math.html#math.fsum |
| SM-LOAD-ORDER-312 | Run totals and trend rollup | Python 3.12 and later: the same eleven rows in both orders | 507.63 | exact sum of the stored doubles (math.fsum), half-even at 2 dp | 507.63 | fixed | https://docs.python.org/3/whatsnew/3.12.html#other-language-changes |
| SM-CARGOCRAP-TIE | CRAP score | exact 2 dp tie: CRAP(9, 5/6) = 9.375; each tool prints the side its own double falls on | 9.38 | cargo-crap 0.5.0 (--format json crap) | 9.37 | definition | https://docs.python.org/3/tutorial/floatingpoint.html#representation-error |
| SM-VUE-JOIN | Function coverage ratio | a function in a .vue &lt;script&gt; block below a &lt;template&gt; (small corpus src/web/Counter.vue): its scored row starts on the block's line count, where the artifact holds no function | 3 problems | oracles/coverage_counts.py over the recorded js lane (istanbul fnMap) | 0 problems | defect | https://vuejs.org/api/sfc-spec.html#language-blocks |

</details>

<details><summary><code>suite_strength</code>: 23 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| SS1 | Accuracy tier runner and receipts | a check whose pytest target file does not exist, run at -n 2 | fail | pytest without xdist: a missing target is a usage error (exit 4) | fail | fixed | https://docs.pytest.org/en/stable/reference/exit-codes.html |
| SS2 | Mutation survivor verdict | score.crap exponent 3 read as 4, under tests/unit/test_score.py and tests/accuracy/score_model | killed | hand canary: score.crap with (1 - cov) ** 4 must die | killed | fixed | https://www.artima.com/weblogs/viewpost.jsp?thread=210575 |
| SS3 | Mutation results | tests/accuracy/kit/test_kit_image.py's subprocess.run(..., timeout=60) calls, read by tests/unit/test_one_hang_bound.py in a clean checkout of HEAD | none | tests/unit/test_one_hang_bound.py must pass on a clean tree, as every test of the killer suite must; the tests it names FAILED or ERROR | none | fixed | docs/configuration.md#crapkit |
| SS4 | Mutation results | tests/unit lane tests that run a coveragepy lane, with CRAPKIT_INSIDE_CONTAINER=1 (the accuracy image holds /.dockerenv, which crapkit/lanes.py reads the same way) | none | the killer suite's lane tests must pass in the container accuracy.yml's mutation jobs run in; the tests they name FAILED or ERROR | none | fixed | docs/configuration.md#lane |
| SS5 | Mutation results | a Go text holding a send `ch &lt;- v` and a receive `v := &lt;-ch` | none | Go spec: `&lt;-` is the send statement's and the receive operator's arrow, not a comparison; a mutant of it does not compile | none | fixed | https://go.dev/ref/spec#Send_statements |
| SS6 | Mutation results | a temporary run receipt crapkit itself writes for 101 workers, read back by startup recovery (dry run) | planned | docs/configuration.md: mutation_workers is an int &gt;= 1 with no upper bound, and startup recovery removes recognized, abandoned temporary runs | planned | fixed | https://github.com/JeanFrancoisGagne/crapkit/blob/main/docs/configuration.md#mutation-worktrees |
| SS7 | Change-control verdict | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | tools/accuracy/change_control.py:plan_declare,tools/accuracy/change_control.py:pre_push | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS8 | File universe and scope ownership | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/universe.py:assign_files | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS9 | Parameter list (params, packet.params) | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/packet.py:params | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS10 | Source decoding and line normalization | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/analyze.py:read_source | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS11 | Function discovery and spans | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/analyze.py:analyze_source | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS12 | next-item ranking and empty-queue reasons | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/cli/queue.py:_skip_reason | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS13 | Run totals and trend rollup | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/store.py:SnapshotStore.run_totals | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS14 | doctor --tune knobs and lane cost | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/doctor.py:suggest_knobs | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS15 | Digest deltas | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/digest.py:build_digest | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS16 | Burn-down, mark age and debt policy | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/ratchet_report.py:mark_age_days,src/crapkit/ratchet_report.py:policy_violations | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS17 | Brief packet fields and regrowth | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/packet.py:file_totals | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS18 | Handles and NAME resolution | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/keys.py:handle_ordinal,src/crapkit/keys.py:select | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS19 | Metric stamp guard | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/ratchet.py:stamp_conflict | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS20 | Standing unmarked debt | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/verify.py:unmarked_over_ceiling | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS21 | Ratchet tighten, damping and delta | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/ratchet.py:ratchet_delta | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS22 | Audited override grant | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/override.py:_granted_marks,src/crapkit/override.py:_require_auditable_override,src/crapkit/override.py:record_override | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |
| SS23 | Ratchet seed and prune | each function the calcs.tsv row names, run by the golden CLI run or by the independent test at the push tier's example counts | src/crapkit/ratchet.py:prune_ratchet | coverage.py line data over the golden CLI run and the row's independent test (kit/reach.py) | none | defect | docs/accuracy.md#add-a-check |

</details>

<details><summary><code>verdict_model</code>: 21 rows</summary>

| Row | Calculation | Construct | crapkit | Oracle | Oracle's value | Ruling | Support |
|---|---|---|---|---|---|---|---|
| V1 | Run retention keep set | runs prune also keeps the baseline pick, the newest trusted run the taint rule passed over, and every failed verify after the pick; README.md:802 lists none of them | kept | README.md:802 keep list | not listed | definition | README.md#the-trusted-baseline |
| V2 | Ratchet file parse and dump | a raw mark row whose crap field is nan, inf or no number: refused with exit 3, file untouched; the docs say only that malformed encoded fields are refused | refused | docs/portable-records.md | undocumented | definition | docs/portable-records.md#portable-records |
| V3 | Ratchet file parse and dump | a hand-typed mark at a 4 dp tie, 1.00005: read as the double 1.0000500000000001 and rounded as round() rounds it | 1.0001 | decimal half-even of the written text | 1.0000 | definition | https://docs.python.org/3/library/functions.html#round |
| V4 | Ratchet file parse and dump | a hand-typed mark of -1, which no CRAP can reach, is read and rewritten as -1.0000 | -1.0000 | CRAP &gt;= ccn &gt;= 1 (Savoia and Evans) | refused | definition | convention_only |
| V4.0 | Ratchet file parse and dump | a hand-typed mark of 0, which no CRAP can reach, is read and rewritten as 0.0000 | 0.0000 | CRAP &gt;= ccn &gt;= 1 (Savoia and Evans) | refused | definition | convention_only |
| V5 | Lane command reading and the full-suite guard | a bare word after an option pytest does not know (--frobnicate level): the guard reads it as that option's value and loads the lane; pytest refuses the command line | ok | pytest option parser | refused | definition | docs/lanes.md#the-full-suite-rule |
| V5.1 | Lane command reading and the full-suite guard | a path after an option pytest does not know (--frobnicate pylib/unit): the guard refuses the path as a positional; pytest refuses the unknown option | pylib/unit | pytest option parser | refused | definition | docs/lanes.md#the-full-suite-rule |
| V6 | Suite drop and shrink warnings | a lane that wrote no JUnit this run, after a trusted run that counted 10 of its tests | silent | docs/lanes.md: both counts are optional and neither absence is an error | silent | fixed | docs/lanes.md#the-test-count-is-the-second-check |
| V7 | Lane reuse and artifact staleness | a file whose bytes match HEAD but whose index stat is stale (a touch, or a fresh copy), in a repo that sets diff.autoRefreshIndex=false | reused, lines kept | git status, which compares content | reused, lines kept | fixed | docs/lanes.md#reusing-artifacts |
| V8 | Baseline and run trust selection | a test that failed at the baseline run, still failing, verified against that baseline's --emit-baseline record | exit 0, new none | README.md exit 8: failures the baseline already had do not count | exit 0, new none | fixed | README.md#exit-codes |
| D14 | test-scoped routing | a file named test_*.py or *.spec.* outside every test directory and every scope, with one templated scope | exit 3 | the file universe: a test is a path with a test, tests or __tests__ directory component (README.md [exclude]: test directories leave the corpus on their own) | exit 3 | fixed | README.md#1-describe-the-repo |
| V9 | Lane command reading and the full-suite guard | an unquoted &amp;&amp; touching the word before it (--cov-report=json:x.json&amp;&amp; python -m coverage json): both shells end the pytest command there; crapkit splits commands only at an operator that stands as its own word, so it reads python, -m, coverage and json as pytest's arguments and refuses the lane | ok | sh and cmd.exe argv, then pytest's parser | ok | fixed | https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_03 |
| V10 | Lane reuse and artifact staleness | git's status read under a lane's inputs fails once while the lane is measured (a full disk, or a git process that cannot start), on a clean tree | rerun, other cause, then reused | git status, which reads the tree clean | rerun, other cause, then reused | fixed | docs/lanes.md#reusing-artifacts |
| V11 | Lane reuse and artifact staleness | one git read behind a lane's staleness verdict (merge-base --is-ancestor, or the diff since its stamp commit) exits 128 on a tree nobody touched since coverage | null, other cause | git status and git diff, which show no change | null, other cause | fixed | docs/agent-json.md#uncovered_lines-null-is-not- |
| V12 | Lane reuse and artifact staleness | src/app.py has the same bytes and a new mtime, then next-item (the lane staleness reads) or coverage (the reads that prove a lane): crapkit starts these git reads at once, and the worktree `git diff` among them refreshes the index and renames index.lock over .git/index while the other reads open it (git diff takes that lock whatever GIT_OPTIONAL_LOCKS says: measured with git 2.43.0.windows.1, and builtin/diff.c refresh_index_quietly checks no optional lock). On Windows an open that meets the rename fails with 'fatal: .git/index: index file open failed: Permission denied' (exit 128), and the failed read makes the lane read stale (V11's note) or stamp no proof (V10's sentence). Both Windows nightly flakes are this race: the history machine at runs_prune (next-item's own ls-files failed with that message) and test_lane_inputs_take_the_scope_path_spelling[plain] (4 of 4 repeat failures logged ls-files or diff --cached failing beside lane a's own worktree diff, which replaced the index). Without a seam, next-item on a touched tree nulls lane a's lines on a clean tree after 0.6 to 9.3 s of reads on Windows (7 runs) | index untouched | git --no-optional-locks status --porcelain -uall over the same paths, which answers the same question (every staged, unstaged or untracked change) and leaves .git/index as it was | index untouched | fixed | https://git-scm.com/docs/git-status#_background_refresh |
| V20 | Lane command reading and the full-suite guard | a redirection whose quoted target touches &gt; (&gt;"lane.log" on both shells, &gt;'lane.log' on sh) is read as an argument | ok | real shell argv and the shlex or CommandLineToArgvW split, docs' rules over it | ok | fixed | https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_07 |
| V20.1 | Lane command reading and the full-suite guard | cmd.exe: 2&gt;"lane err.log" is read as an argument | ok | real shell argv and the shlex or CommandLineToArgvW split, docs' rules over it | ok | fixed | https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_07 |
| V20.2 | Lane command reading and the full-suite guard | cmd.exe: in -k ^"x &amp; python -m pytest pylib/unit^" the caret-escaped quote opens no run for cmd.exe (lanes.md:72), so &amp; starts a second pytest; crapkit reads one -k value | pylib/unit | real shell argv and the shlex or CommandLineToArgvW split, docs' rules over it | pylib/unit | fixed | docs/lanes.md#how-a-lane-command-is-read |
| V20.3 | Lane command reading and the full-suite guard | cmd.exe: in -k "a\" tests \"b" the runner's reader keeps the run at \" and hands -k one value; crapkit ends the run and refuses tests | ok | real shell argv and the shlex or CommandLineToArgvW split, docs' rules over it | ok | fixed | https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-commandlinetoargvw |
| V9.1 | Lane command reading and the full-suite guard | a redirection glued to the word before it (tests&gt;lane.log under testpaths tests): the shell hands pytest tests | ok | real shell argv and the shlex or CommandLineToArgvW split, docs' rules over it | ok | fixed | https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_03 |
| V9.2 | Lane command reading and the full-suite guard | sh: a ; glued to the next word (py.json ;echo done) ends the command | ok | real shell argv and the shlex or CommandLineToArgvW split, docs' rules over it | ok | fixed | https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_03 |

</details>
<!-- /generated:rulings -->

## Past bugs

`tests/accuracy/suite_strength/retro/bugs.tsv` lists every past calculation bug:
its fix commits, the commit before them, and the check that must catch it.
`ledger.tsv` beside it records the last replay of each row. `tools/accuracy/retro.py`
replays a row: it checks out the commit before the fix and the fix, installs each
commit's crapkit in a venv of its own, and runs the check from this tree against it.

```
python tools/accuracy/retro.py run R57 --record
```

A replay counts only when the check fails on an AssertionError at the commit
before the fix and passes at the fix. A check that passes before the fix catches
nothing, and one that fails at the fix proves nothing; both are refused, the row
stays `pending`, and its ledger note says why. Each night accuracy.yml replays the
rows whose check changed and a seventh of the rest: the `retro` job in the Linux
image, and the Windows cell the rows whose `platform` is `windows`
(`retro.py nightly --platform-only`). The nightly job judges and never records,
so a row whose check changed replays every night until someone records it:
after changing a check, or anything it imports, list those rows with
`python tools/accuracy/retro.py stale`, replay them with `run <id> --record`
and commit `ledger.tsv`. A row whose test is not written yet never replays and
stays `pending`.

When the check cannot ask its question of the old commit (it reads a field the fix
added, or a later bug fails it too), write a probe: a script in `retro/probes/`
that asks only this bug's question through the CLI or API both commits have. It
exits 0 when the value holds and raises AssertionError when it does not, and its
`# source:` line names where the expected value comes from, never crapkit's output
at the fix. Name it in the row's `probe` cell.

The replayed check runs with `CRAPKIT_ACCURACY_PYTHON` set to the commit's venv,
that commit's crapkit (and nothing else from its venv) first on PYTHONPATH, and
`CRAPKIT_ACCURACY_CHECKOUT` naming the commit's checkout, where a check finds the
files a wheel does not carry, such as `action.yml`. Every tier's items run, but an
item a `python` marker keeps for a newer Python than the replay's is left out, since
it cannot run there. A row's `env` cell may set
`CRAPKIT_ACCURACY_LANGUAGES` and `CRAPKIT_ACCURACY_ROOT_PATHS` for a commit that
read fewer languages or refused a root scope of `.`. `CRAPKIT_RETRO_WORK` moves
the worktrees and venvs (default `.crapkit/accuracy/retro`), and rows R01 to R12
need `CRAPKIT_RETRO_BUNDLE`, the history bundle their commits live in.

## Change control

### The rule for goldens

A golden is crapkit's own output on a fixed corpus, kept under
`tests/accuracy/*/goldens/`. It cannot prove a number right, since crapkit wrote
it; it proves the number did not move. So no golden, and no expected value,
changes on its own. The goldens, rulings, hand tables, probes and oracle
adapters are locked in `tests/accuracy/change_control/goldens.lock`, and these
rules hold on every tree (`test_change_control.py`, on every push):

- Every golden equals what crapkit prints now.
- Every locked file's sha256 equals its lock row, and every lockable file has one.
- Each lock row names a change in `CHANGES.tsv`, and each change other than kind
  `none` has a line in CHANGELOG.md.
- The last `metric-digests.tsv` row is the running `ANALYSIS_VERSION`, lizard
  version and corpus, and its digest is the one computed over the small corpus
  now. A move of that digest takes a new `ANALYSIS_VERSION`.
- `test-counts.tsv` holds each packet's test function count.

### Declaring a change

A diff that moves a golden or a locked file, or that touches a module a
`calcs.tsv` row names, needs a declared change:

```
python tools/accuracy/change_control.py declare C12 --kind fix --calcs "CRAP score" --reason "..."
```

`declare` regenerates the goldens, judges every moved value against its outside
oracle, and stops on a move the oracle does not support ("crapkit now says 9,
radon says 7"); `--against-oracle RULING` accepts one that a rulings row of that
calc covers. It relocks the files, appends the `CHANGES.tsv` row and prints the
CHANGELOG line the commit needs. `--no-regenerate` judges the goldens as they
are, for a change that moves no crapkit output. `goldens/full.tsv` pins the full
corpus's exports, and `declare` remeasures it only when a built full corpus is
at hand (`CRAPKIT_ACCURACY_CORPUS`, the accuracy image's `/corpus`, or the cache
`python tools/accuracy/corpus.py fetch` fills). Without one it says so on its
last line, and the nightly's full-corpus check still compares that file, so
fetch the corpus before you declare a change that moves a metric.

| Kind | For | The same diff also needs |
|---|---|---|
| `fix` | crapkit was wrong | A `bugs.tsv` row and a check that fails at the commit before the fix |
| `definition` | crapkit now means something else on purpose | An edit to README.md, CONTEXT.md, docs/agent-json.md or this page; a rulings row covering every moved cell; a hand or probe row with an outside source for each moved calc |
| `feature` | crapkit computes something new | Nothing more; its calcs are the ones whose output moved |
| `none` | A refactor or a relock | A reason; no golden cell may move |

Rules that compare two commits run in three places: `git-hooks/pre-push` against
`origin/main`, CI's verdict job against `refs/accuracy/green` (the newest main
commit whose verdict passed, so a push that skipped CI is still judged), and the
release against the previous tag. On top of the in-tree rules they refuse a
change that drops or rewrites a recorded row (`CHANGES.tsv`, `bugs.tsv`, the
ledger, the retro tables, a rulings id), lowers a mutation floor, collects fewer
tests in a packet, or declares a calc that did not move. Each refusal prints the
command that fixes it. The rows of a bug `bugs.tsv` marks `open` are the one
exception: its fix waits off main and lands as other commits, so those rows follow
it, as long as the bug keeps a `bugs.tsv` row.

## Add a check

1. Put the test in its packet's directory. Its expected values come from the
   methods above, never from crapkit's output.
2. Name its file in a row of `tools/accuracy/checks/<packet>.py`, with its
   serial seconds on Ubuntu, and `os_sensitive: True` when the answer can change
   with the OS. The contract fails on a test module no check names, on a check
   whose file is not there, and on a push tier whose declared seconds pass 504,
   naming the five slowest checks.
3. A new calculation gets a `calcs.tsv` row naming its modules, its functions and
   its independent test. Then run `python tools/docs/generate.py`: it rewrites
   pyproject.toml's `[tool.mutmut]` `paths_to_mutate`, the union of every row's
   modules, and the tables on this page.
4. Take Hypothesis settings from `accuracy.kit.settings` (`pure` or `process`),
   dated repos from `make_repo`, and the measured small corpus from the
   `small_corpus` fixture.

`tests/accuracy/kit/test_kit_contract.py` holds the rules every packet follows:
no skip or xfail outside a rulings row, no crapkit import in an independent
test's closure, every hand table citing its source, every model citing doc lines
that still hash to their pins. Suite strength's nightly reach check
(`tests/accuracy/suite_strength/test_calc_reach.py`) runs each function a
calcs.tsv row names: on the golden CLI run over the small corpus, or else under
the row's independent test, both measured with coverage.py down to subprocess
children. A function neither run reaches fails it: the row's test then checks a
copy of the rule, or the row names the wrong function.

## Releases

`python tools/release/release.py run accuracy VERSION` runs after the verify
stage: the release tier here, then accuracy.yml's release mode on the tag commit.
Stage 2b publishes only when the local receipt passed at this HEAD and GitHub
holds a successful release run at the tag commit. See
[tools/release/README.md](../tools/release/README.md#the-accuracy-stage).
