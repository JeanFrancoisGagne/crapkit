"""One command, one family.

`crapkit runs list` used to import admin, analyses, queue, ratchet_cmds,
reports, scoring and verifying — every subcommand handler in the tool — because
the parser named all of them in `set_defaults(func=...)` while building the
parser. Naming a handler is not calling it, so the import was pure cost on
every invocation, `hook-precommit` at every git commit included.

These tests pin the mechanism (which modules end up in sys.modules) rather than
a clock, and they pin the two surfaces the mechanism could break: every
subcommand still reaches its handler, and `from crapkit.cli import <name>` still
answers for every name it answered for before.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import crapkit
import crapkit.cli
from crapkit.cli import build_parser

FAMILIES = ("admin", "analyses", "queue", "ratchet_cmds", "reports", "scoring", "verifying")


def _child_env() -> dict:
    """The subprocess imports the crapkit this test imported, not an installed one."""
    env = dict(os.environ)
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH", "")) if p)
    return env


def _loaded_cli_modules(snippet: str, tmp_path) -> set[str]:
    """The crapkit.cli.* modules a fresh interpreter ends up holding."""
    probe = (snippet + "\nimport sys, json\n"
             "print(json.dumps(sorted(m for m in sys.modules if m.startswith('crapkit.cli.'))))\n")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env=_child_env(), cwd=str(tmp_path))
    return set(json.loads(done.stdout.splitlines()[-1]))


def test_building_the_parser_loads_no_command_family(tmp_path):
    loaded = _loaded_cli_modules("from crapkit.cli.parser import build_parser\nbuild_parser()", tmp_path)

    assert loaded == {"crapkit.cli.parser"}


def test_running_a_command_loads_its_family_and_no_other(tmp_path):
    """`runs list` on a repo with no snapshot: the report family runs, refuses,
    and the six families it never calls stay unimported."""
    loaded = _loaded_cli_modules(
        "from crapkit.cli import main\nmain(['runs', 'list', '--repo', '.'])", tmp_path)

    assert "crapkit.cli.reports" in loaded
    assert loaded.isdisjoint({f"crapkit.cli.{f}" for f in FAMILIES if f != "reports"})


def test_asking_for_help_loads_no_command_family(tmp_path):
    """--help prints help strings the parser already holds; no handler runs."""
    probe = ("from crapkit.cli import main\n"
             "try:\n    main(['--help'])\nexcept SystemExit:\n    pass")

    assert _loaded_cli_modules(probe, tmp_path) == {"crapkit.cli.parser"}


def _subcommands() -> dict:
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


@pytest.mark.parametrize("name", sorted(_subcommands()))
def test_every_subcommand_dispatches_to_the_handler_named_for_it(name, monkeypatch):
    """The parser's own choices list drives this: whatever `crapkit X` is, it
    calls cmd_X, resolved from the family module at the moment it runs."""
    handler = "cmd_" + name.replace("-", "_")
    owner = __import__(f"crapkit.cli.{_owner_of(handler)}", fromlist=["x"])
    monkeypatch.setattr(owner, handler, lambda args: f"ran {handler}")

    func = _subcommands()[name].get_default("func")

    assert func(argparse.Namespace()) == f"ran {handler}"


def _owner_of(handler: str) -> str:
    from crapkit.cli import _OWNER

    return _OWNER[handler]


def test_the_help_text_still_names_every_subcommand():
    text = build_parser().format_help()

    assert [n for n in sorted(_subcommands()) if n not in text] == []


# Every name `dir(crapkit.cli)` answered for at 6120197, before the re-export went
# lazy. Dozens of tests and the console script reach crapkit.cli by name; the
# families are attributes too, because __init__ used to import all nine modules.
# A name that leaves this module is a break for someone, so a name leaves the list
# only when the commit that deletes it says so (0.5.0 dropped `_rescore_gate`, a
# wrapper with no caller once cmd_rescore judged before printing).
BASELINE_SURFACE = """
    SCHEMA_VERSION _AMBIGUOUS_TEST _Corpus _SCRIPT_SUFFIXES _VersionAction _actionable
    _analysis_tools _analysis_workers _analyzed_corpus _apply_verify_override _at_level
    _baseline_behind _baseline_failures _batch_json _brief_churn _brief_coupling _brief_lines_text
    _brief_mark _brief_payload _brief_twins _build_inventory _by_scope _ceiling_breaches
    _churn_text _claimable _claims_summary _claims_to_release _collect_lanes _collect_mutants
    _contexts_for_span _coverage_summary _covered_scope_names _diff_cover_breach _digest_pair
    _dirty_tag _doctor_findings _doctor_hook_modes _doctor_keys _doctor_lane_summary _doctor_lanes
    _doctor_oversized _doctor_report _doctor_scope_files _doctor_scopes _doctor_tools _doctor_tune
    _doctor_unclaimed _doctor_uncovered _doctor_unmeasured _emit_baseline _emit_coverage_findings
    _emit_doctor _emit_findings _emit_next _emit_verify_findings _empty_reasons _entry_json
    _excluded_item _execute_lanes _execute_parallel _explain_commits _explain_extras
    _explain_tests _export_scored _extend_gitignore _file_sizer _flag_counts _flake_retry
    _gate_candidates _gate_line _grant_env_override _group_files_by_scope _guard_ratchet_stamp
    _hook_modes _interpreter _is_test_path _junit_seconds _lane_command_problems _lane_durations
    _lane_problem _lane_problems _lane_report _lane_reports _lane_reuse _lane_seconds
    _latest_full_run _latest_scored _latest_span _listed_files _lizard_version
    _load_ratchet_or_die _load_repo_config _load_sources _mark_text _matching_rows _maybe_claim
    _maybe_flake_retry _merge_stamp _missing_named_script _mutation_targets _name_matches
    _name_prefix _named_baseline _named_claims _newest_coverage_run _newest_run_report _next_head
    _next_item_payload _next_ranked _next_reasons _no_baseline _no_lane_debt _no_lane_gap
    _no_match_message _no_scopes_reason _note_stale_staged _open_store _owning_scope _package_json
    _pick_baseline _pick_function _policy_findings _present_markers _present_on_disk
    _print_batches _print_brief _print_brief_neighbours _print_claims _print_coverage
    _print_duplication _print_finding_split _print_findings _print_history _print_init_summary
    _print_json _print_mutation _print_prune _print_ratchet_report _print_released
    _print_rescore_table _print_trend _print_uncovered _print_verify_findings _progress
    _prune_renames _pruned _pushdown_floor _range_lines _rankable _ratchet_entries _ratchet_mark
    _ratchet_merge _ratchet_move _ratchet_report _ratchet_sha256 _records_by_scope _release_claims
    _release_target _report_verify _require_ancestor _rescore_analyze _rescore_json
    _rescore_overlay _rescored_records _resolve_batches _resolve_top _route_unowned _row_marker
    _run_kind _run_lanes _run_line _run_one_lane _runs_list _runs_prune _scope_rollup_from_agg
    _scoped_command _scored_run _scored_store _select_lanes _send_digest_alert _settle_verify
    _shared _skip_reason _split_worklist _stale_warning _store_if_any _store_path _store_report
    _taint_note _traced_lane _tracked_files _trend_row _tsv_baseline _unclaimed _uncovered_fields
    _uncovered_scopes _uncovered_text _under_scope_path _unknown_key_text _unmarked_breaches
    _untracked_of _verify_attribution _verify_baseline _verify_basis _verify_exit_code
    _verify_result _verify_store _version_line _version_report _warn_diff_uncovered
    _warn_suite_shrink _warn_untracked _watch_banner _watch_cycle _watch_cycles _watch_rescore
    _watched_files _working_marks _worklist_batches _worklist_marks _worklist_payload
    _worklist_print _write_tsv admin analyses annotations build_parser cmd_brief cmd_claims
    cmd_coupling cmd_coverage cmd_digest cmd_doctor cmd_duplication cmd_explain cmd_hook_precommit
    cmd_init cmd_inventory cmd_mcp cmd_mutate cmd_next_item cmd_overrides cmd_ratchet cmd_rescore
    cmd_runs cmd_test_scoped cmd_trend cmd_verify cmd_watch cmd_worklist main parser queue
    ratchet_cmds reports scoring verifying
""".split()


def test_the_import_surface_did_not_shrink():
    missing = [name for name in BASELINE_SURFACE if not hasattr(crapkit.cli, name)]

    assert missing == []


def test_dir_still_lists_every_name_it_listed():
    listed = set(dir(crapkit.cli))

    assert [name for name in BASELINE_SURFACE if name not in listed] == []


def test_a_name_no_family_owns_is_still_an_attribute_error():
    with pytest.raises(AttributeError):
        crapkit.cli.cmd_not_a_command


# --- a path where a subcommand belongs ---------------------------------------

@pytest.mark.parametrize("arg", ["~/some-repo", "./mini", "/abs/path", ".."])
def test_a_path_as_the_first_argument_names_the_repo_flag(arg, capsys):
    """`crapkit ./mini` reads as "score this repo". argparse answered it with the
    invalid-choice dump of every subcommand and never printed the word repo,
    which is where the path goes."""
    from crapkit.cli import main

    code = main([arg])

    err = capsys.readouterr().err
    assert code == 2
    assert "--repo" in err
    assert "invalid choice" not in err


def test_a_misspelled_subcommand_is_still_argparses_error():
    """Shape is the whole trigger, so a typo carrying no path separator still
    gets the usage dump a human is there to read."""
    from crapkit.cli import main

    with pytest.raises(SystemExit) as exit_code:
        main(["inventry"])

    assert exit_code.value.code == 2
