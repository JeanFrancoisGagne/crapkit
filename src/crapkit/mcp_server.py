"""Minimal MCP stdio server. JSON-RPC 2.0, newline-delimited, no SDK dependency.

Read-side only: every tool shells to the CLI's own --json surface, so the MCP
view can never drift from what the CLI reports, and nothing here writes a
baseline, a ratchet, or a mutant. Runs that mutate state stay in the CLI.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .invocation import _self
from .rootfind import CONFIG_NAME, find_root

# Newest first. Everything this server does — tools, annotations, structured
# results over stdio — is inside the 2025-06-18 revision, and nothing it does
# was removed from the older two, so any of the three can be spoken verbatim.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_REPO = {"repo": {"type": "string", "description": (
    "path to the scored repo's root (default: the repo the server was started in)")}}

# brief and explain resolve NAME by one rule, so they describe it with one
# string. The bare identifier is the long name's leading token, which is all
# there is before the parameters in Rust and Go.
_NAME_DESCRIPTION = ("the bare identifier (classify, or route for a Rust "
                     "`route cmd : & Cmd`) or the whole long_name next_item "
                     "printed (classify( score , late )); both resolve, exact "
                     "match first")

# The partition a large repo needs before `top` means anything: one --scope
# per element, exact names as declared in crapkit.toml.
_SCOPE = {
    "type": "array",
    "description": ("restrict the ranking to these declared scopes (exact [[scope]] names from "
    "crapkit.toml, one --scope each)"),
    "items": {
        "type": "string"}}

TOOLS: tuple[dict, ...] = (
    {
        "name": "get_next_item",
        "title": "Next function to fix",
        "argv": ("next-item",),
        "json_flag": False,
        "positional": (),
        "flags": {
            "top": "--top",
            "exclude": "--exclude",
            "scope": "--scope"},
        "description": ("Returns the next function to fix as a work packet, the newest trusted run's "
        "worst row by crap. Use it to start a fix, list_worklist to survey the same "
        "run by risk, and get_function_brief once a function is chosen. It runs no "
        "tests, and empty true means the queue is spent, not the work, so read "
        "reasons. Filters cut before top counts: top 3 with exclude [\"tests/\"] "
        "returns the three worst rows outside tests, and an unknown scope name is a "
        "config error."),
        "properties": {
            "top": {
                "type": "integer",
                "description": ("return the next N packets as items instead of one item (N >= 1, "
                "default 1)")},
            "exclude": {
                "type": "array",
                "description": ("skip rows whose path or long function name contains any of these "
                "fragments (one --exclude each)"),
                "items": {
                    "type": "string"}},
            "scope": _SCOPE},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "run_id": {
                "type": "integer",
                "description": ("id of the run these numbers come from: the newest trusted run (a "
                "coverage run, or a verify run whose verdict passed)")},
            "commit": {
                "type": "string",
                "description": "that run's commit, full sha"},
            "stale": {
                "type": "boolean",
                "description": ("true when the run's commit is not HEAD, so cov, crap and "
                "uncovered_lines describe an older tree; crapkit coverage "
                "--reuse-unchanged (get_function_brief's commands.refresh) clears it")},
            "empty": {
                "type": "boolean",
                "description": ("true when the queue has nothing to hand out; then reasons is present "
                "and item and items are absent")},
            "skipped_no_lane": {
                "type": "integer",
                "description": ("rows above the floor that no lane measures, kept out of the ranking "
                "because their cov 0 is a tooling gap, not a testing gap")},
            "skipped_claimed": {
                "type": "integer",
                "description": ("rows another session's claim hid; present only when non-zero, and "
                "list_claims names the holders")},
            "item": {
                "type": "object",
                "description": "the one packet, present when empty is false and top is absent or 1",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "the declared scope that owns the file"},
                    "path": {
                        "type": "string",
                        "description": "repo-relative source path, forward slashes"},
                    "function": {
                        "type": "string",
                        "description": ("lizard long name with the spaced parameter list; "
                        "get_function_brief and get_function_history accept it as "
                        "name")},
                    "handle": {
                        "type": "string",
                        "description": ("short name form: the bare identifier, or (anonymous)#N for a "
                        "function lizard could not name; it names a position, not a "
                        "line, so it survives the edit this item asks for; pass it "
                        "back as name")},
                    "start": {
                        "type": "integer",
                        "description": "first line, 1-based inclusive"},
                    "end": {
                        "type": "integer",
                        "description": "last line, 1-based inclusive"},
                    "ccn": {
                        "type": "integer",
                        "description": ("min(ccn_std, ccn_mod): the complexity the gate and the "
                        "ratchet judge")},
                    "ccn_std": {
                        "type": "integer",
                        "description": "standard cyclomatic complexity"},
                    "cognitive": {
                        "type": "integer",
                        "description": "Sonar-spec cognitive complexity, reporting only, never gated"},
                    "nloc": {
                        "type": "integer",
                        "description": "non-comment lines of code"},
                    "nesting": {
                        "type": "integer",
                        "description": "maximum nesting depth"},
                    "cov": {
                        "type": "number",
                        "description": "branch coverage inside the span, 0.0 to 1.0"},
                    "flag": {
                        "type": "string",
                        "description": ("measured, untested, no-lane or cc-only: whether a lane "
                        "artifact could measure this span"),
                        "enum": ("measured", "untested", "no-lane", "cc-only")},
                    "crap": {
                        "type": "number",
                        "description": "the score: ccn^2 x (1 - cov)^3 + ccn"},
                    "remedy": {
                        "type": "string",
                        "description": ("decompose (ccn over ceiling), add-tests (coverage short) or "
                        "ok (nothing left to do)"),
                        "enum": ("decompose", "add-tests", "ok")},
                    "target": {
                        "type": "integer",
                        "description": "this scope's effective ccn ceiling"},
                    "commits": {
                        "type": "integer",
                        "description": "commits touching the file in the churn window"},
                    "authors": {
                        "type": "integer",
                        "description": "distinct authors of those commits"},
                    "est_splits": {
                        "type": "integer",
                        "description": ("0 when ccn <= target, else ceil(ccn / target): roughly how "
                        "many functions this must become")},
                    "est_uncovered_paths": {
                        "type": "integer",
                        "description": "round((1 - cov) x ccn): decision paths no test walks"},
                    "uncovered_lines": {
                        "type": ("array", "null"),
                        "description": ("line numbers no test ran; [] when the span is fully covered; "
                        "null when no artifact could answer, then "
                        "uncovered_lines_note says why"),
                        "items": {
                            "type": "integer"}},
                    "uncovered_lines_note": {
                        "type": "string",
                        "description": ("present only when uncovered_lines is null: the reason and "
                        "the move (stale artifact, no test imports the file, "
                        "coverage_optional scope)")}}},
            "items": {
                "type": "array",
                "description": ("up to top packets in crap-descending order, present when empty is "
                "false and top is above 1"),
                "items": {
                    "type": "object",
                    "description": "a packet, same shape as item",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "the declared scope that owns the file"},
                        "path": {
                            "type": "string",
                            "description": "repo-relative source path, forward slashes"},
                        "function": {
                            "type": "string",
                            "description": ("lizard long name with the spaced parameter list; "
                            "get_function_brief and get_function_history accept it as "
                            "name")},
                        "handle": {
                            "type": "string",
                            "description": ("short name form: the bare identifier, or (anonymous)#N "
                            "for a function lizard could not name; it names a "
                            "position, not a line, so it survives the edit this item "
                            "asks for; pass it back as name")},
                        "start": {
                            "type": "integer",
                            "description": "first line, 1-based inclusive"},
                        "end": {
                            "type": "integer",
                            "description": "last line, 1-based inclusive"},
                        "ccn": {
                            "type": "integer",
                            "description": ("min(ccn_std, ccn_mod): the complexity the gate and the "
                            "ratchet judge")},
                        "ccn_std": {
                            "type": "integer",
                            "description": "standard cyclomatic complexity"},
                        "cognitive": {
                            "type": "integer",
                            "description": ("Sonar-spec cognitive complexity, reporting only, never "
                            "gated")},
                        "nloc": {
                            "type": "integer",
                            "description": "non-comment lines of code"},
                        "nesting": {
                            "type": "integer",
                            "description": "maximum nesting depth"},
                        "cov": {
                            "type": "number",
                            "description": "branch coverage inside the span, 0.0 to 1.0"},
                        "flag": {
                            "type": "string",
                            "description": ("measured, untested, no-lane or cc-only: whether a lane "
                            "artifact could measure this span"),
                            "enum": ("measured", "untested", "no-lane", "cc-only")},
                        "crap": {
                            "type": "number",
                            "description": "the score: ccn^2 x (1 - cov)^3 + ccn"},
                        "remedy": {
                            "type": "string",
                            "description": ("decompose (ccn over ceiling), add-tests (coverage short) "
                            "or ok (nothing left to do)"),
                            "enum": ("decompose", "add-tests", "ok")},
                        "target": {
                            "type": "integer",
                            "description": "this scope's effective ccn ceiling"},
                        "commits": {
                            "type": "integer",
                            "description": "commits touching the file in the churn window"},
                        "authors": {
                            "type": "integer",
                            "description": "distinct authors of those commits"},
                        "est_splits": {
                            "type": "integer",
                            "description": ("0 when ccn <= target, else ceil(ccn / target): roughly "
                            "how many functions this must become")},
                        "est_uncovered_paths": {
                            "type": "integer",
                            "description": "round((1 - cov) x ccn): decision paths no test walks"},
                        "uncovered_lines": {
                            "type": ("array", "null"),
                            "description": ("line numbers no test ran; [] when the span is fully "
                            "covered; null when no artifact could answer, then "
                            "uncovered_lines_note says why"),
                            "items": {
                                "type": "integer"}},
                        "uncovered_lines_note": {
                            "type": "string",
                            "description": ("present only when uncovered_lines is null: the reason "
                            "and the move (stale artifact, no test imports the file, "
                            "coverage_optional scope)")}}}},
            "reasons": {
                "type": "object",
                "description": ("why the queue is empty, present only when empty is true; the stop "
                "condition is empty true with skipped_claimed and no_lane_over_target "
                "both 0 or absent"),
                "properties": {
                    "below_floor": {
                        "type": "integer",
                        "description": "rows under worklist_floor, every one at or under its ceiling"},
                    "no_lane": {
                        "type": "integer",
                        "description": "rows above the floor whose scope no lane covers"},
                    "no_lane_over_target": {
                        "type": "integer",
                        "description": ("the subset of no_lane rows over their ceiling: debt the "
                        "queue may not rank; non-zero means work remains")},
                    "no_churn_in_window": {
                        "type": "integer",
                        "description": "rows in files with no commits in the churn window"},
                    "excluded_by_flag": {
                        "type": "integer",
                        "description": "rows an exclude fragment matched"},
                    "churn_window_months": {
                        "type": "integer",
                        "description": "the window those counts used, echoed back"},
                    "all_remaining_at_or_under_target": {
                        "type": "integer",
                        "description": ("present only when candidates remained but every one has "
                        "remedy ok: the queue is finished")}}}},
    },
    {
        "name": "list_worklist",
        "title": "Risk ranking of every function",
        "argv": ("worklist",),
        "json_flag": True,
        "positional": (),
        "flags": {
            "top": "--top",
            "scope": "--scope"},
        "description": ("Lists the newest trusted run's whole risk ranking, every admitted function "
        "by ccn times recency-weighted churn, finished rows included, so it never "
        "empties. Use it to survey a repo or split work by file, and get_next_item "
        "for one crap-ranked packet instead. It runs no tests, and stale true means "
        "the run predates HEAD. scope narrows first and top caps after: scope "
        "[\"core\"] with top 20 returns core's 20 riskiest, and an unknown scope name "
        "is a config error."),
        "properties": {
            "top": {
                "type": "integer",
                "description": "cap the active list (default: the config's worklist_top, 50)"},
            "scope": _SCOPE},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "run_id": {
                "type": "integer",
                "description": ("id of the run these numbers come from: the newest trusted run (a "
                "coverage run, or a verify run whose verdict passed)")},
            "commit": {
                "type": "string",
                "description": "that run's commit, full sha"},
            "stale": {
                "type": "boolean",
                "description": ("true when the run's commit is not HEAD, so cov, crap and "
                "uncovered_lines describe an older tree; crapkit coverage "
                "--reuse-unchanged (get_function_brief's commands.refresh) clears it")},
            "floor": {
                "type": "integer",
                "description": ("the effective worklist_floor: rows under this ccn are listed only "
                "when over their ceiling or in a hot file")},
            "churn_window_months": {
                "type": "integer",
                "description": "months of git history the churn weights cover"},
            "active": {
                "type": "array",
                "description": ("the ranking: functions in files with churn in the window, risk "
                "descending, cut at top or worklist_top"),
                "items": {
                    "type": "object",
                    "description": "one ranked function",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "the declared scope that owns the file"},
                        "path": {
                            "type": "string",
                            "description": "repo-relative source path, forward slashes"},
                        "function": {
                            "type": "string",
                            "description": ("lizard long name with the spaced parameter list; pass it "
                            "to get_function_brief as name")},
                        "start": {
                            "type": "integer",
                            "description": "first line, 1-based"},
                        "end": {
                            "type": "integer",
                            "description": "last line, inclusive"},
                        "ccn": {
                            "type": "integer",
                            "description": "min(ccn_std, ccn_mod): what the gate judges"},
                        "ccn_std": {
                            "type": "integer",
                            "description": "standard cyclomatic complexity"},
                        "nloc": {
                            "type": "integer",
                            "description": "non-comment lines of code"},
                        "commits": {
                            "type": "integer",
                            "description": "commits touching the file in the churn window"},
                        "authors": {
                            "type": "integer",
                            "description": "distinct authors of those commits"},
                        "weight": {
                            "type": "number",
                            "description": ("recency-weighted churn of the file; 1.0 on every file "
                            "when commits share one timestamp")},
                        "risk": {
                            "type": "number",
                            "description": "ccn x weight, four decimals: the sort key"},
                        "flag": {
                            "type": ("string", "null"),
                            "description": ("measured, untested, no-lane or cc-only; null on an "
                            "inventory-only run"),
                            "enum": ("measured", "untested", "no-lane", "cc-only", None)},
                        "remedy": {
                            "type": "string",
                            "description": ("decompose, add-tests or ok; only decompose and add-tests "
                            "rows with a lane reach get_next_item"),
                            "enum": ("decompose", "add-tests", "ok")},
                        "crap": {
                            "type": ("number", "null"),
                            "description": ("the score from the ranked run; null on an inventory-only "
                            "run")},
                        "cov": {
                            "type": ("number", "null"),
                            "description": "branch coverage 0.0 to 1.0; null on an inventory-only run"},
                        "ratchet_mark": {
                            "type": ("number", "null"),
                            "description": ("the committed ratchet mark on this function, read under "
                            "its own ratchet key; null when it carries none or the "
                            "repo has no marks file")}}}},
            "active_total": {
                "type": "integer",
                "description": "active rows admitted before the cap: what top or worklist_top hid"},
            "dormant_count": {
                "type": "integer",
                "description": "ranked functions whose file had no commits in the window"},
            "dormant_top": {
                "type": "array",
                "description": ("the first 10 dormant functions, same shape as active: sleeping "
                "hazards kept out of the queue"),
                "items": {
                    "type": "object",
                    "description": "one ranked function",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "the declared scope that owns the file"},
                        "path": {
                            "type": "string",
                            "description": "repo-relative source path, forward slashes"},
                        "function": {
                            "type": "string",
                            "description": ("lizard long name with the spaced parameter list; pass it "
                            "to get_function_brief as name")},
                        "start": {
                            "type": "integer",
                            "description": "first line, 1-based"},
                        "end": {
                            "type": "integer",
                            "description": "last line, inclusive"},
                        "ccn": {
                            "type": "integer",
                            "description": "min(ccn_std, ccn_mod): what the gate judges"},
                        "ccn_std": {
                            "type": "integer",
                            "description": "standard cyclomatic complexity"},
                        "nloc": {
                            "type": "integer",
                            "description": "non-comment lines of code"},
                        "commits": {
                            "type": "integer",
                            "description": "commits touching the file in the churn window"},
                        "authors": {
                            "type": "integer",
                            "description": "distinct authors of those commits"},
                        "weight": {
                            "type": "number",
                            "description": ("recency-weighted churn of the file; 1.0 on every file "
                            "when commits share one timestamp")},
                        "risk": {
                            "type": "number",
                            "description": "ccn x weight, four decimals: the sort key"},
                        "flag": {
                            "type": ("string", "null"),
                            "description": ("measured, untested, no-lane or cc-only; null on an "
                            "inventory-only run"),
                            "enum": ("measured", "untested", "no-lane", "cc-only", None)},
                        "remedy": {
                            "type": "string",
                            "description": ("decompose, add-tests or ok; only decompose and add-tests "
                            "rows with a lane reach get_next_item"),
                            "enum": ("decompose", "add-tests", "ok")},
                        "crap": {
                            "type": ("number", "null"),
                            "description": ("the score from the ranked run; null on an inventory-only "
                            "run")},
                        "cov": {
                            "type": ("number", "null"),
                            "description": "branch coverage 0.0 to 1.0; null on an inventory-only run"},
                        "ratchet_mark": {
                            "type": ("number", "null"),
                            "description": ("the committed ratchet mark on this function, read under "
                            "its own ratchet key; null when it carries none or the "
                            "repo has no marks file")}}}}},
    },
    {
        "name": "list_runs",
        "title": "Scored run history",
        "argv": ("runs",),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Lists every run in the store, oldest first by id. Use it to date the store "
        "or to see which commit the other tools answer from, get_trend for per-run "
        "totals, and get_function_history for one function's scores per run. It reads "
        "the store only and spawns no git, and baseline true is not always the newest "
        "run. repo can be any directory under a measured checkout, and an unmeasured "
        "one answers isError true with the setup pointer."),
        "properties": {},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "runs": {
                "type": "array",
                "description": "every run in the store, oldest first",
                "items": {
                    "type": "object",
                    "description": "one run",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "run id, the run_id other payloads cite"},
                        "kind": {
                            "type": "string",
                            "description": ("inventory, coverage, partial, verify, hook or legacy; "
                            "only coverage, legacy and passing verify runs can be "
                            "trusted baselines"),
                            "enum": ("inventory", "coverage", "partial", "verify", "hook", "legacy")},
                        "verdict_ok": {
                            "type": ("boolean", "null"),
                            "description": "the verify verdict; null on every kind that renders none"},
                        "findings": {
                            "type": "integer",
                            "description": "findings a verify run recorded, 0 otherwise"},
                        "baseline": {
                            "type": "boolean",
                            "description": ("true on the one run verify compares against today, not "
                            "always the newest candidate")},
                        "commit": {
                            "type": "string",
                            "description": "the commit the run measured, full sha"},
                        "lanes": {
                            "type": "array",
                            "description": "names of the lanes that ran; empty on an inventory run",
                            "items": {
                                "type": "string"}},
                        "created_at": {
                            "type": "string",
                            "description": "UTC timestamp, ISO 8601"}}}}},
    },
    {
        "name": "get_trend",
        "title": "Debt trend per run",
        "argv": ("trend",),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Returns per-run totals for every trusted run, oldest first, with a grade per "
        "scope. Use it for the whole repo's trajectory, get_function_history for one "
        "function, and get_ratchet_report for marked debt. The first call on a large "
        "store sums every run into a rollup cache and takes seconds, later calls read "
        "it back. repo can be any directory under a measured checkout, and an "
        "unmeasured one answers isError true with the setup pointer."),
        "properties": {},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "target": {
                "type": "integer",
                "description": ("the [crapkit] target: the default ccn ceiling every scope inherits "
                "unless it sets its own")},
            "runs": {
                "type": "array",
                "description": "one row per trusted run, oldest first",
                "items": {
                    "type": "object",
                    "description": "one run's totals",
                    "properties": {
                        "run_id": {
                            "type": "integer",
                            "description": "run id"},
                        "commit": {
                            "type": "string",
                            "description": "the run's commit, full sha"},
                        "created_at": {
                            "type": "string",
                            "description": "UTC timestamp, ISO 8601"},
                        "functions": {
                            "type": "integer",
                            "description": "functions scored"},
                        "over_target": {
                            "type": "integer",
                            "description": "functions over their scope's ceiling"},
                        "crap_load": {
                            "type": "number",
                            "description": "sum of every function's crap, two decimals"},
                        "avg": {
                            "type": "number",
                            "description": "mean crap per function, four decimals"},
                        "by_scope": {
                            "type": "object",
                            "description": "scope name to that scope's totals and grade",
                            "additionalProperties": {
                                "type": "object",
                                "description": "one scope's totals",
                                "properties": {
                                    "functions": {
                                        "type": "integer",
                                        "description": "functions scored in the scope"},
                                    "over_target": {
                                        "type": "integer",
                                        "description": "of those, over the scope's ceiling"},
                                    "crap_load": {
                                        "type": "number",
                                        "description": "sum of crap over the scope"},
                                    "grade": {
                                        "type": "string",
                                        "description": ("A+ at zero over_target, then A under 2% "
                                        "over, B under 5%, C under 10%, D under 20%, "
                                        "else F"),
                                        "enum": ("A+", "A", "B", "C", "D", "F")}}}}}}}},
    },
    {
        "name": "get_function_brief",
        "title": "Start-editing packet for one function",
        "argv": ("brief",),
        "json_flag": True,
        "positional": ("path", "name"),
        "flags": {},
        "description": ("Returns one function's start-editing packet from the newest trusted run: "
        "scored row, uncovered lines and the refresh, test, gate and verify command "
        "lines, none of them run. Use it once a function is chosen, and "
        "get_function_history to follow its score across runs. Every call shingles "
        "the repo for twins, seconds on a large corpus, and reads git for churn. name "
        "takes the long name, bare identifier, start line or NAME#2, exact match "
        "first, and a miss lists the file's functions."),
        "properties": {
            "path": {
                "type": "string",
                "description": "repo-relative source file, forward slashes"},
            "name": {
                "type": "string",
                "description": ("the bare identifier (classify, or route for a Rust `route cmd : & "
                "Cmd`) or the whole long_name get_next_item printed (classify( score "
                ", late )); both resolve, exact match first")}},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "run_id": {
                "type": "integer",
                "description": ("id of the run these numbers come from: the newest trusted run (a "
                "coverage run, or a verify run whose verdict passed)")},
            "commit": {
                "type": "string",
                "description": "that run's commit, full sha"},
            "stale": {
                "type": "boolean",
                "description": ("true when the run's commit is not HEAD, so every number here "
                "describes an older tree; run commands.refresh first")},
            "path": {
                "type": "string",
                "description": "the resolved file, repo-relative"},
            "function": {
                "type": "string",
                "description": "the resolved lizard long name, whichever name form was asked with"},
            "handle": {
                "type": "string",
                "description": ("short name form: the bare identifier or (anonymous)#N, the same "
                "value get_next_item prints")},
            "remedy": {
                "type": "string",
                "description": ("decompose, add-tests or ok: the branch the session takes; "
                "scored.remedy carries the same value"),
                "enum": ("decompose", "add-tests", "ok")},
            "target": {
                "type": "integer",
                "description": ("this scope's effective ccn ceiling, the same value as "
                "gate_rule.ceiling")},
            "scored": {
                "type": "object",
                "description": "the whole scored row from the run",
                "properties": {
                    "long_name": {
                        "type": "string",
                        "description": "lizard long name with the spaced parameter list"},
                    "path": {
                        "type": "string",
                        "description": "repo-relative source path"},
                    "scope": {
                        "type": "string",
                        "description": "the scope that owns the file"},
                    "start": {
                        "type": "integer",
                        "description": "first line, 1-based"},
                    "end": {
                        "type": "integer",
                        "description": "last line, inclusive"},
                    "ccn": {
                        "type": "integer",
                        "description": "min(ccn_std, ccn_mod)"},
                    "ccn_std": {
                        "type": "integer",
                        "description": "standard cyclomatic complexity"},
                    "ccn_mod": {
                        "type": "integer",
                        "description": "modified cyclomatic complexity (a switch counts once)"},
                    "cognitive": {
                        "type": "integer",
                        "description": "cognitive complexity, reporting only"},
                    "nesting": {
                        "type": "integer",
                        "description": "maximum nesting depth"},
                    "nloc": {
                        "type": "integer",
                        "description": "non-comment lines of code"},
                    "params": {
                        "type": "integer",
                        "description": "parameter count"},
                    "cov": {
                        "type": "number",
                        "description": "branch coverage 0.0 to 1.0"},
                    "flag": {
                        "type": "string",
                        "description": ("measured, untested, no-lane or cc-only: whether a lane "
                        "artifact could measure this span"),
                        "enum": ("measured", "untested", "no-lane", "cc-only")},
                    "crap": {
                        "type": "number",
                        "description": "the score: ccn^2 x (1 - cov)^3 + ccn"},
                    "remedy": {
                        "type": "string",
                        "description": ("decompose (ccn over ceiling), add-tests (coverage short) or "
                        "ok (nothing left to do)"),
                        "enum": ("decompose", "add-tests", "ok")}}},
            "source": {
                "type": "string",
                "description": ("the function's own text, start to end inclusive, newlines intact: "
                "editable without reading the file")},
            "params": {
                "type": "array",
                "description": ("parameters in declaration order, so a test can call the function "
                "without opening the file"),
                "items": {
                    "type": "object",
                    "description": "one parameter",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "parameter name as declared"},
                        "type": {
                            "type": ("string", "null"),
                            "description": ("type annotation as lizard printed it, or null when "
                            "unannotated")}}}},
            "est_splits": {
                "type": "integer",
                "description": "0 when ccn <= target, else ceil(ccn / target)"},
            "est_uncovered_paths": {
                "type": "integer",
                "description": "round((1 - cov) x ccn)"},
            "uncovered_lines": {
                "type": ("array", "null"),
                "description": ("line numbers no test ran; [] when the span is fully covered; null "
                "when no artifact could answer, then uncovered_lines_note says why"),
                "items": {
                    "type": "integer"}},
            "uncovered_lines_note": {
                "type": "string",
                "description": ("present only when uncovered_lines is null: the reason and the move "
                "(stale artifact, no test imports the file, coverage_optional scope)")},
            "file_functions": {
                "type": "array",
                "description": ("every scored function in the same file: where an extracted helper "
                "lands, and which names are taken"),
                "items": {
                    "type": "object",
                    "description": "one scored function",
                    "properties": {
                        "function": {
                            "type": "string",
                            "description": "long name"},
                        "start": {
                            "type": "integer",
                            "description": "first line"},
                        "end": {
                            "type": "integer",
                            "description": "last line"},
                        "ccn": {
                            "type": "integer",
                            "description": "complexity"},
                        "crap": {
                            "type": "number",
                            "description": "score"},
                        "remedy": {
                            "type": "string",
                            "description": ("decompose (ccn over ceiling), add-tests (coverage short) "
                            "or ok (nothing left to do)"),
                            "enum": ("decompose", "add-tests", "ok")}}}},
            "file_totals": {
                "type": "object",
                "description": "the file rolled up",
                "properties": {
                    "functions": {
                        "type": "integer",
                        "description": "scored functions in the file"},
                    "over_target": {
                        "type": "integer",
                        "description": "of those, over their own scope's ceiling"},
                    "crap_load": {
                        "type": "number",
                        "description": "sum of crap over the file"}}},
            "gate_rule": {
                "type": "object",
                "description": "what check_gate will judge this edit by",
                "properties": {
                    "ceiling": {
                        "type": "integer",
                        "description": "the ccn the gate compares against, same as target"},
                    "binds": {
                        "type": "string",
                        "description": ("the gate's scope rule as one fixed sentence: changed "
                        "functions only; a ratchet mark pardons standing debt at or "
                        "under it")},
                    "ratchet_mark": {
                        "type": ("number", "null"),
                        "description": ("the mark this function carries; an edit at or under it "
                        "passes the gate, above it fails with exit 6")},
                    "mark_age_days": {
                        "type": ("integer", "null"),
                        "description": ("age of that mark, counted from the newest commit that "
                        "touched the ratchet file")},
                    "diff_uncovered_max": {
                        "type": ("integer", "null"),
                        "description": ("configured ceiling on changed lines with no coverage; null "
                        "means warn only")}}},
            "commands": {
                "type": "object",
                "description": ("the rest of the loop as whole command lines for this file and scope; "
                "run them as given"),
                "properties": {
                    "gate": {
                        "type": "string",
                        "description": ("the crapkit rescore PATH --gate call for this file: what "
                        "check_gate runs")},
                    "scoped_tests": {
                        "type": ("string", "null"),
                        "description": ("this scope's own test command with the file filled in, or "
                        "null when the scope declares no [crapkit.scoped_tests] "
                        "template")},
                    "scoped_tests_note": {
                        "type": "string",
                        "description": ("present only when scoped_tests is null: names the scope "
                        "missing a template")},
                    "verify": {
                        "type": "string",
                        "description": "the crapkit verify call: the only authoritative verdict"},
                    "refresh": {
                        "type": "string",
                        "description": ("crapkit coverage --reuse-unchanged: the cheapest run that "
                        "clears stale")},
                    "refresh_writes_run": {
                        "type": "boolean",
                        "description": ("always true: refresh is the one command here that writes a "
                        "run to the store; the other three change nothing")}}},
            "lane": {
                "type": ("object", "null"),
                "description": ("the lane whose artifact produced cov and uncovered_lines, verbatim "
                "from the config; null when no lane covers the scope"),
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "lane name"},
                    "command": {
                        "type": "string",
                        "description": "the lane's test command as declared"},
                    "artifact": {
                        "type": "string",
                        "description": "coverage artifact path it writes"},
                    "parser": {
                        "type": "string",
                        "description": "artifact parser, coveragepy or istanbul"},
                    "cwd": {
                        "type": "string",
                        "description": "working directory the lane runs in, empty for the root"},
                    "env": {
                        "type": "object",
                        "properties": {},
                        "description": "environment overrides declared for the lane"},
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "declared timeout, 0 for none"}}},
            "versions": {
                "type": "object",
                "description": "what produced these numbers",
                "properties": {
                    "crapkit": {
                        "type": "string",
                        "description": "crapkit version"},
                    "lizard": {
                        "type": "string",
                        "description": "lizard version"},
                    "python": {
                        "type": "string",
                        "description": "interpreter version"},
                    "analysis_version": {
                        "type": "integer",
                        "description": ("the metric's own version; marks and scores from two versions "
                        "are not one series")}}},
            "notes": {
                "type": "object",
                "description": "prose the config carries for whoever edits here",
                "properties": {
                    "repo": {
                        "type": ("array", "null"),
                        "description": "repo-wide notes lines from crapkit.toml, or null",
                        "items": {
                            "type": "string"}},
                    "scope": {
                        "type": ("array", "null"),
                        "description": "this scope's notes lines, or null",
                        "items": {
                            "type": "string"}}}},
            "attempts": {
                "type": "array",
                "description": ("every claim ever taken on this function, oldest first; [] on a first "
                "attempt, and a row with closed null is a claim still open"),
                "items": {
                    "type": "object",
                    "description": "one claim",
                    "properties": {
                        "opened": {
                            "type": "string",
                            "description": "UTC timestamp the claim was taken"},
                        "closed": {
                            "type": ("string", "null"),
                            "description": "UTC timestamp it was released, null while open"}}}},
            "regrowth": {
                "type": "object",
                "description": "did this get fixed before",
                "properties": {
                    "regrown": {
                        "type": "boolean",
                        "description": ("true when ccn fell across runs and later rose again: an "
                        "earlier decomposition did not hold")},
                    "history": {
                        "type": "array",
                        "description": ("one [run_id, ccn] pair per trusted run that scored the "
                        "function, oldest first"),
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "integer"},
                            "description": "[run_id, ccn]"}}}},
            "ratchet_mark": {
                "type": ("number", "null"),
                "description": ("the committed ratchet mark, or null when the function carries none "
                "or the repo has no ratchet file")},
            "churn": {
                "type": ("object", "null"),
                "description": "the file's churn, or null when it had no commits in the window",
                "properties": {
                    "commits": {
                        "type": "integer",
                        "description": "commits touching the file in the window"},
                    "authors": {
                        "type": "integer",
                        "description": "distinct authors"},
                    "weight": {
                        "type": "number",
                        "description": "recency-weighted churn"}}},
            "coupling": {
                "type": "array",
                "description": ("up to 5 change-coupling partners at support 5 and confidence 0.5, "
                "strongest first; empty when none qualify; list_coupled_files has the "
                "repo-wide list"),
                "items": {
                    "type": "object",
                    "description": "a change-coupling partner",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "the coupled file"},
                        "support": {
                            "type": "integer",
                            "description": "commits in which both files changed"},
                        "confidence": {
                            "type": "number",
                            "description": ("the larger of support / commits(a) and support / "
                            "commits(b), 0 to 1")},
                        "is_test": {
                            "type": "boolean",
                            "description": ("true when the partner is a test file: outside the scored "
                            "corpus, still the file the edit breaks")}}}},
            "duplication_twins": {
                "type": "array",
                "description": ("up to 10 near-duplicate functions at similarity 0.8, best first; "
                "empty is normal; list_duplicate_functions has the repo-wide pairs"),
                "items": {
                    "type": "object",
                    "description": "a near-duplicate function",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "repo-relative path of the twin"},
                        "long_name": {
                            "type": "string",
                            "description": "the twin's long name"},
                        "start": {
                            "type": "integer",
                            "description": "first line"},
                        "end": {
                            "type": "integer",
                            "description": "last line"},
                        "nloc": {
                            "type": "integer",
                            "description": "non-comment lines"},
                        "similarity": {
                            "type": "number",
                            "description": "shared shingles over the smaller function's, 0 to 1"},
                        "contained": {
                            "type": "boolean",
                            "description": ("true when every shingle of the smaller function appears "
                            "in the larger: one can call the other")}}}}},
    },
    {
        "name": "get_function_history",
        "title": "One function's score across runs",
        "argv": ("explain",),
        "json_flag": True,
        "positional": ("path", "name"),
        "flags": {
            "history": "--history",
            "tests": "--tests"},
        "description": ("Returns one function's ccn, coverage, crap and flag in every run that "
        "measured it, oldest first, plus its ratchet mark. Use it to tell improving "
        "from decaying or regrown, and get_function_brief to start an edit instead. "
        "history true spawns git log -L, capped at 10 commits, and tests true is null "
        "unless the lane recorded contexts. name resolves off the newest run that "
        "scored path, and a substring such as \"eval\" fans out to one entry per long "
        "name matched."),
        "properties": {
            "path": {
                "type": "string",
                "description": "repo-relative source file, forward slashes"},
            "name": {
                "type": "string",
                "description": ("the bare identifier (classify, or route for a Rust `route cmd : & "
                "Cmd`) or the whole long_name get_next_item printed (classify( score "
                ", late )); both resolve, exact match first")},
            "history": {
                "type": "boolean",
                "description": ("also list the commits that touched this function (git log -L), as "
                "commits")},
            "tests": {
                "type": "boolean",
                "description": ("also list the tests covering this function (coverage.py contexts), "
                "as tests")}},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "path": {
                "type": "string",
                "description": "the file asked about"},
            "name": {
                "type": "string",
                "description": "the name argument as given"},
            "functions": {
                "type": "array",
                "description": "the functions name resolved to, in store order",
                "items": {
                    "type": "object",
                    "description": "one function the name resolved to",
                    "properties": {
                        "long_name": {
                            "type": "string",
                            "description": ("the resolved long name; several entries when name "
                            "matched by substring or the signature changed across "
                            "runs")},
                        "history": {
                            "type": "array",
                            "description": "the score per run that measured the function, oldest first",
                            "items": {
                                "type": "object",
                                "description": "one run's measurement",
                                "properties": {
                                    "run_id": {
                                        "type": "integer",
                                        "description": "the run"},
                                    "kind": {
                                        "type": "string",
                                        "description": ("inventory, coverage, partial, verify, hook "
                                        "or legacy"),
                                        "enum": ("inventory", "coverage", "partial", "verify", "hook", "legacy")},
                                    "commit": {
                                        "type": "string",
                                        "description": "the run's commit, full sha"},
                                    "created_at": {
                                        "type": "string",
                                        "description": "UTC timestamp, ISO 8601"},
                                    "ccn": {
                                        "type": "integer",
                                        "description": "complexity in that run"},
                                    "cov": {
                                        "type": ("number", "null"),
                                        "description": ("branch coverage 0.0 to 1.0; null on an "
                                        "inventory run")},
                                    "crap": {
                                        "type": ("number", "null"),
                                        "description": "score; null on an inventory run"},
                                    "flag": {
                                        "type": ("string", "null"),
                                        "description": ("measured, untested, no-lane or cc-only; null "
                                        "on an inventory-only run"),
                                        "enum": ("measured", "untested", "no-lane", "cc-only", None)}}}},
                        "ratchet_mark": {
                            "type": ("number", "null"),
                            "description": "the committed ratchet mark, or null"},
                        "ratchet_mark_note": {
                            "type": "string",
                            "description": ("present only when the repo has no ratchet file, so null "
                            "means no file rather than no mark")},
                        "uncovered_lines": {
                            "type": ("array", "null"),
                            "description": ("lines no test ran in the newest run; [] when fully "
                            "covered; null when no artifact could answer or the "
                            "function is not in the newest run"),
                            "items": {
                                "type": "integer"}},
                        "uncovered_lines_note": {
                            "type": "string",
                            "description": "present only when uncovered_lines is null: the reason"},
                        "commits": {
                            "type": ("array", "null"),
                            "description": ("with history true: up to 10 commits that touched the "
                            "span, newest first; null otherwise, and null with "
                            "commits_note when the function is not in the newest run"),
                            "items": {
                                "type": "object",
                                "description": "one commit",
                                "properties": {
                                    "sha": {
                                        "type": "string",
                                        "description": "abbreviated sha"},
                                    "date": {
                                        "type": "string",
                                        "description": "commit date, YYYY-MM-DD"},
                                    "subject": {
                                        "type": "string",
                                        "description": "first line of the message"},
                                    "body": {
                                        "type": "string",
                                        "description": "the rest of the message"}}}},
                        "commits_note": {
                            "type": "string",
                            "description": "present only when commits is null for a reason: the reason"},
                        "tests": {
                            "type": ("array", "null"),
                            "description": ("with tests true: test ids whose coverage context ran the "
                            "span; null when the lane recorded no contexts or the "
                            "function is not in the newest run"),
                            "items": {
                                "type": "string"}},
                        "tests_note": {
                            "type": "string",
                            "description": ("present only when tests is null for want of contexts: "
                            "how to record them (dynamic_context = test_function and "
                            "a --show-contexts report)")}}}}},
    },
    {
        "name": "check_config",
        "title": "Config and repo health check",
        "argv": ("doctor",),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Checks that crapkit.toml agrees with the repo: typo keys, empty scopes, "
        "missing lane cwds, runners that fail to start. Run it first when any tool "
        "answers strangely or the ranking misses a file, and list_runs when only the "
        "history is in question. It needs no run, probes each runner once, runs no "
        "lane, and any problem arrives with isError true. repo can be any directory "
        "under the checkout, and one with no crapkit.toml above it answers a pointer, "
        "never a parent's config."),
        "properties": {},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "problems": {
                "type": "array",
                "description": ("FAIL findings as sentences naming the fix; non-empty is exit 1 and "
                "the result carries isError true"),
                "items": {
                    "type": "string"}},
            "warnings": {
                "type": "array",
                "description": ("WARN findings: unmeasured directories, scopes with a lane but no "
                "scoped_tests template, artifacts written outside .crapkit, lanes "
                "without results_artifact; exit stays 0"),
                "items": {
                    "type": "string"}},
            "versions": {
                "type": "object",
                "description": "the tools behind every number",
                "properties": {
                    "crapkit": {
                        "type": "string",
                        "description": "crapkit version"},
                    "lizard": {
                        "type": ("string", "null"),
                        "description": "lizard version, null when not importable (a FAIL)"},
                    "python": {
                        "type": "string",
                        "description": "interpreter version"}}},
            "analysis_version": {
                "type": "integer",
                "description": ("the analysis semantics version; with the lizard version it stamps "
                "every ratchet mark, so a bump refuses old marks until the repo "
                "re-seeds")},
            "store": {
                "type": "object",
                "description": "the run store",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ".crapkit/crap.sqlite, repo-relative"},
                    "present": {
                        "type": "boolean",
                        "description": "whether the store exists"},
                    "size_bytes": {
                        "type": "integer",
                        "description": "its size, 0 on a fresh repo"}}},
            "newest_run": {
                "type": ("object", "null"),
                "description": "the newest run, or null when nothing has run",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "run id"},
                    "kind": {
                        "type": "string",
                        "description": "run kind",
                        "enum": ("inventory", "coverage", "partial", "verify", "hook", "legacy")},
                    "verdict_ok": {
                        "type": ("boolean", "null"),
                        "description": "verify verdict, null on other kinds"}}},
            "lanes": {
                "type": "array",
                "description": "per declared lane",
                "items": {
                    "type": "object",
                    "description": "one declared lane",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "lane name"},
                        "artifact": {
                            "type": "string",
                            "description": "coverage artifact path"},
                        "artifact_present": {
                            "type": "boolean",
                            "description": "whether the artifact is on disk now"},
                        "commit": {
                            "type": ("string", "null"),
                            "description": ("commit stamped on the artifact, null for a lane that "
                            "never ran")},
                        "seconds": {
                            "type": ("number", "null"),
                            "description": "how long the lane took last time, null when it never ran"}}}}},
    },
    {
        "name": "list_coupled_files",
        "title": "Files that change together",
        "argv": ("coupling",),
        "json_flag": True,
        "positional": (),
        "flags": {
            "min_support": "--min-support",
            "min_confidence": "--min-confidence"},
        "description": ("Lists file pairs that keep landing in the same commits over the churn "
        "window, strongest first, at most 50. Use it before editing a file to learn "
        "what an edit drags along, and list_duplicate_functions for copied code "
        "rather than co-change. It reads git, not the scored run, and non-default "
        "thresholds re-read git log. A pair must clear both: min_support 5 needs five "
        "shared commits, and min_confidence 0.5 means the rarer file moved with its "
        "partner half the time, 1.0 always."),
        "properties": {
            "min_support": {
                "type": "integer",
                "description": "minimum shared commits before a pair counts (default 5)"},
            "min_confidence": {
                "type": "number",
                "description": "minimum P(pair changes together), 0 to 1 (default 0.5)"}},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "window_months": {
                "type": "integer",
                "description": ("months of git history the pairs were counted over (the config's "
                "churn_window_months)")},
            "pairs": {
                "type": "array",
                "description": ("pairs clearing both thresholds, ordered by support x confidence "
                "descending, at most 50"),
                "items": {
                    "type": "object",
                    "description": "one coupled pair",
                    "properties": {
                        "files": {
                            "type": "array",
                            "description": ("the two repo-relative paths, sorted; any path in the "
                            "history can appear, tests and docs included"),
                            "items": {
                                "type": "string"}},
                        "support": {
                            "type": "integer",
                            "description": ("commits in which both files changed, commits over 30 "
                            "files excluded")},
                        "confidence": {
                            "type": "number",
                            "description": ("the larger of support / commits(a) and support / "
                            "commits(b), 0 to 1, four decimals")}}}}},
    },
    {
        "name": "list_duplicate_functions",
        "title": "Near-duplicate function pairs",
        "argv": ("duplication",),
        "json_flag": True,
        "positional": (),
        "flags": {
            "similarity": "--similarity"},
        "description": ("Lists near-duplicate function pairs in the newest run, best containment "
        "first, at most 50. Use it before a refactor so twins are folded together, "
        "not fixed one at a time, and get_function_brief for one function's twins. It "
        "shingles the source on every call, skips functions under 8 lines and drops "
        "pairs within one file. similarity is shared shingles over the smaller "
        "function: 1.0 admits only a function found whole inside another, 0.8 four "
        "lines in five, 0.5 loose pairs."),
        "properties": {
            "similarity": {
                "type": "number",
                "description": "containment threshold, shared over smaller, 0 to 1 (default 0.8)"}},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "run_id": {
                "type": "integer",
                "description": "the newest run whose rows were compared"},
            "pairs": {
                "type": "array",
                "description": "pairs at or above similarity, best containment first, at most 50",
                "items": {
                    "type": "object",
                    "description": "one near-duplicate pair",
                    "properties": {
                        "similarity": {
                            "type": "number",
                            "description": ("shared shingles over the smaller function's shingles, 0 "
                            "to 1, four decimals")},
                        "contained": {
                            "type": "boolean",
                            "description": ("always false here: pairs whose spans nest in one file "
                            "are dropped before ranking; kept so pairs and "
                            "get_function_brief's duplication_twins share one shape")},
                        "functions": {
                            "type": "array",
                            "description": "the two functions of the pair",
                            "items": {
                                "type": "object",
                                "description": "one member",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "repo-relative path"},
                                    "long_name": {
                                        "type": "string",
                                        "description": "lizard long name"},
                                    "start": {
                                        "type": "integer",
                                        "description": "first line"},
                                    "end": {
                                        "type": "integer",
                                        "description": "last line"},
                                    "nloc": {
                                        "type": "integer",
                                        "description": "non-comment lines"}}}}}}}},
    },
    {
        "name": "get_ratchet_report",
        "title": "Ratchet debt burn-down",
        "argv": ("ratchet", "report"),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Reports the ratchet debt burn-down: open marks with their age, repayments "
        "and policy findings. Use it to judge whether marked debt is repaid or piling "
        "up, and get_function_history for one function's mark. It reads the marks "
        "file and its git log only, ages count back from the newest commit touching "
        "that file, never the clock, and no marks file means zeros. repo can be any "
        "directory under a measured checkout, and an unmeasured one answers isError "
        "true with the setup pointer."),
        "properties": {},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "open": {
                "type": "integer",
                "description": "marks open on disk, working tree included: an uncommitted seed counts"},
            "uncommitted": {
                "type": "integer",
                "description": ("marks the working tree and the newest committed ratchet file "
                "disagree on: added, repaid or tightened but not committed")},
            "dropped_total": {
                "type": "integer",
                "description": "marks repaid over the whole committed history"},
            "dropped_last_30d": {
                "type": "integer",
                "description": "marks repaid in the 30 days before anchor_ts"},
            "dropped_last_90d": {
                "type": "integer",
                "description": "marks repaid in the 90 days before anchor_ts"},
            "oldest": {
                "type": "array",
                "description": "up to 20 open marks, oldest first",
                "items": {
                    "type": "object",
                    "description": "one open mark",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "repo-relative path"},
                        "long_name": {
                            "type": "string",
                            "description": "the marked function's long name"},
                        "age_days": {
                            "type": "integer",
                            "description": "days the mark has stood, counted back from anchor_ts"}}}},
            "anchor_ts": {
                "type": "integer",
                "description": ("unix seconds of the newest commit that touched the ratchet file; "
                "every age and window counts back from here, never from the wall "
                "clock; 0 with no ratchet history")},
            "policy_violations": {
                "type": ("array", "null"),
                "description": ("null when no debt policy is configured, [] when the policy ran "
                "clean, else the findings as sentences"),
                "items": {
                    "type": "string"}}},
    },
    {
        "name": "check_gate",
        "title": "Commit gate verdict for an edited file",
        "argv": ("rescore", "--gate"),
        "json_flag": True,
        "positional": ("path",),
        "flags": {},
        "verdict_exits": (6,),
        "description": ("Checks whether an edited file clears the commit gate as the hook will: fresh "
        "ccn per changed function against its scope's ceiling, less pardoned ratchet "
        "debt. Call it after an edit and before committing, once get_function_brief "
        "has stated the rule. It re-measures ccn off git diff, runs no tests, and a "
        "breach answers gate.ok false, not a tool error. path is repo-relative: a "
        "tracked file is judged where it differs from HEAD, an untracked one in full, "
        "an unchanged one judges 0."),
        "properties": {
            "path": {
                "type": "string",
                "description": "repo-relative source file to judge as edited"}},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "baseline_run": {
                "type": "integer",
                "description": "id of the run whose coverage was reused"},
            "baseline_commit": {
                "type": "string",
                "description": "that run's commit, full sha"},
            "note": {
                "type": "string",
                "description": ("fixed reminder that coverage is the baseline run's and complexity "
                "the working tree's; crapkit verify gives the real verdict")},
            "functions": {
                "type": "array",
                "description": "every function in the file, rescored",
                "items": {
                    "type": "object",
                    "description": "one rescored function",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "the scope that owns the file"},
                        "path": {
                            "type": "string",
                            "description": "repo-relative path"},
                        "function": {
                            "type": "string",
                            "description": "long name"},
                        "start": {
                            "type": "integer",
                            "description": "first line"},
                        "end": {
                            "type": "integer",
                            "description": "last line"},
                        "ccn": {
                            "type": "integer",
                            "description": "complexity measured fresh from the working tree"},
                        "cov": {
                            "type": "number",
                            "description": "branch coverage from the baseline run, 0.0 to 1.0"},
                        "flag": {
                            "type": "string",
                            "description": ("measured, untested, no-lane or cc-only: whether a lane "
                            "artifact could measure this span"),
                            "enum": ("measured", "untested", "no-lane", "cc-only")},
                        "crap": {
                            "type": "number",
                            "description": "score from fresh ccn and baseline cov"},
                        "remedy": {
                            "type": "string",
                            "description": ("decompose (ccn over ceiling), add-tests (coverage short) "
                            "or ok (nothing left to do)"),
                            "enum": ("decompose", "add-tests", "ok")},
                        "stale_coverage": {
                            "type": "boolean",
                            "description": ("always true: complexity is the working tree's, coverage "
                            "is the baseline run's")}}}},
            "gate": {
                "type": "object",
                "description": "the verdict block",
                "properties": {
                    "ok": {
                        "type": "boolean",
                        "description": ("true when breaches is empty; false is the verdict (CLI exit "
                        "6), delivered as a normal result")},
                    "judged": {
                        "type": "integer",
                        "description": ("functions the working tree changed since HEAD, an untracked "
                        "file counted in full; 0 on an unchanged path")},
                    "ceilings": {
                        "type": "object",
                        "properties": {},
                        "description": "map of path to the ccn ceiling it was judged against"},
                    "breaches": {
                        "type": "array",
                        "description": "the failing functions; empty when ok",
                        "items": {
                            "type": "object",
                            "description": ("one judged function over its ceiling that no ratchet "
                            "mark covers"),
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "repo-relative path"},
                                "function": {
                                    "type": "string",
                                    "description": "long name"},
                                "start": {
                                    "type": "integer",
                                    "description": "first line"},
                                "ccn": {
                                    "type": "integer",
                                    "description": "fresh complexity"},
                                "cov": {
                                    "type": "number",
                                    "description": "baseline coverage"},
                                "crap": {
                                    "type": "number",
                                    "description": "score"},
                                "remedy": {
                                    "type": "string",
                                    "description": ("decompose (ccn over ceiling), add-tests "
                                    "(coverage short) or ok (nothing left to do)"),
                                    "enum": ("decompose", "add-tests", "ok")},
                                "key_name": {
                                    "type": "string",
                                    "description": "the ratchet key form of the name"},
                                "ceiling": {
                                    "type": "integer",
                                    "description": "the ceiling it exceeds"}}}},
                    "untracked": {
                        "type": "array",
                        "description": "rescored paths git tracks nothing of, judged in full",
                        "items": {
                            "type": "string"}}}}},
    },
    {
        "name": "list_claims",
        "title": "Open queue claims",
        "argv": ("claims", "list"),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Lists the open claims sessions hold on queue items, oldest first. Use it "
        "when get_next_item answers empty or skipped_claimed above 0, and "
        "get_function_brief's attempts for one function. It reads the store only. No "
        "tool on this server writes a claim by design: a stale one is released in the "
        "CLI with crapkit claims release PATH NAME, and a claim closes itself when "
        "verify finds the function at its ceiling. repo can be any directory under a "
        "measured checkout."),
        "properties": {},
        "output": {
            "schema": {
                "type": "integer",
                "description": "payload schema version, 1"},
            "open": {
                "type": "integer",
                "description": "number of open claims"},
            "claims": {
                "type": "array",
                "description": "open claims, oldest first",
                "items": {
                    "type": "object",
                    "description": "one open claim",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "claim id"},
                        "path": {
                            "type": "string",
                            "description": "repo-relative source file"},
                        "long_name": {
                            "type": "string",
                            "description": "the function's long name"},
                        "handle": {
                            "type": ("string", "null"),
                            "description": ("the short name it was handed out under: bare identifier "
                            "or (anonymous)#N; null on a claim taken before handles "
                            "existed")},
                        "commit": {
                            "type": "string",
                            "description": "HEAD when the claim was taken, not the run's commit"},
                        "created_at": {
                            "type": "string",
                            "description": "UTC timestamp, ISO 8601"}}}}},
    },
)

# Every tool shells to a read-only CLI command against a store on this machine:
# nothing writes, reruns answer the same, and no call leaves the repo. Declared
# once, in the field clients read, so the module docstring's promise reaches the
# model that plans with these tools.
_ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                "openWorldHint": False}


def _accepted(tool: dict) -> dict:
    """Every argument one tool takes, `repo` first, in the order the listing
    shows them: the one table both the schema and the refusals read from."""
    return {**_REPO, **tool["properties"]}


def _schema(tool: dict) -> dict:
    """The positionals are the required arguments; `required` is left out when
    there are none, which JSON Schema reads the same way."""
    schema = {"type": "object", "properties": _accepted(tool)}
    if tool["positional"]:
        schema["required"] = list(tool["positional"])
    return schema


def _listing_entry(tool: dict) -> dict:
    """One tool as `tools/list` serves it. `title` and `outputSchema` appear only
    when the table declares them: a null title or an empty object would read as
    a defect to a client that grades definitions, and the wire form stays what
    earlier clients saw when a tool carries neither."""
    entry = {"name": tool["name"], "description": tool["description"],
             "annotations": dict(_ANNOTATIONS), "inputSchema": _schema(tool)}
    if tool.get("title"):
        entry["title"] = tool["title"]
    if tool.get("output"):
        entry["outputSchema"] = {"type": "object", "properties": dict(tool["output"])}
    return entry


def tool_listing() -> list[dict]:
    return [_listing_entry(t) for t in TOOLS]


def _flag_values(value) -> list:
    """What one argument contributes to argv: nothing when absent or false, one
    bare flag for true, one flag per element for a list, else one flag."""
    if value is None or value is False:
        return []
    return value if isinstance(value, list) else [value]


def _flag_args(flags: dict, arguments: dict) -> list[str]:
    out: list[str] = []
    for key, flag in flags.items():
        for v in _flag_values(arguments.get(key)):
            out += [flag] if v is True else [flag, str(v)]
    return out


def build_argv(tool: dict, arguments: dict) -> list[str]:
    argv = list(tool["argv"])
    argv += [str(arguments[p]) for p in tool["positional"]]
    argv += _flag_args(tool["flags"], arguments)
    if tool["json_flag"]:
        argv.append("--json")
    return argv


def _result(text: str, *, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _no_config_result(repo: str) -> dict:
    """What every tool answers in a directory crapkit has never measured.

    A tool result, never a JSON-RPC error: the client keeps the session, and the
    caller reads one sentence that says what to run instead of a transport
    failure. isError stays true so nothing reads an unmeasured directory as a
    repo with nothing to report.
    """
    return _result(f"no crapkit.toml in {repo} - nothing measured here. "
                   f"Run `{_self()} init` in the repo you want scored, or pass this tool a "
                   "`repo` argument (or start the server with --repo) pointing at one.",
                   is_error=True)


def _run_cli(tool: dict, arguments: dict, repo: str) -> dict:
    """One tool, run as the CLI command it maps to, at the root it serves: a
    server started in a workspace must not hand the command a working
    directory below the root, because `path` is repo-relative on every tool's
    schema. A command that printed nothing answers with its stderr, so a
    refusal reaches the caller as text. An exit the tool declares in
    `verdict_exits` is an answer, not a failure: `gate` exits 6 on a breach
    and its payload says so in `gate.ok`."""
    argv = build_argv(tool, arguments) + ["--repo", repo]
    proc = subprocess.run([sys.executable, "-m", "crapkit", *argv], cwd=repo,
                          capture_output=True, text=True, timeout=600)
    text = proc.stdout if proc.stdout.strip() else proc.stderr
    failed = proc.returncode != 0 and proc.returncode not in tool.get("verdict_exits", ())
    return _structured(_result(text, is_error=failed))


# JSON Schema type names to the Python shapes json.loads produces for them. A
# bool is an int in Python and never one in JSON, so integer and number say so.
_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
}
_TYPE_NAMES = {"string": "a string", "integer": "an integer", "number": "a number",
               "boolean": "a boolean", "array": "an array of strings"}


def _missing_positional(tool: dict, arguments: dict) -> str | None:
    """A positional left out or sent as null: the served schema declares it a
    required string, so either is refused here and never reaches argv."""
    for key in tool["positional"]:
        if arguments.get(key) is None:
            return f"{tool['name']} needs {key} (see inputSchema.required)"
    return None


def _unknown_key(tool: dict, arguments: dict) -> str | None:
    accepted = _accepted(tool)
    for key in arguments:
        if key not in accepted:
            return f"{tool['name']} does not take {key!r}; accepted: {', '.join(accepted)}"
    return None


def _wrong_type(tool: dict, arguments: dict) -> str | None:
    for key, prop in _accepted(tool).items():
        value = arguments.get(key)
        if value is not None and not _TYPES[prop["type"]](value):
            return f"{key} must be {_TYPE_NAMES[prop['type']]} (got {json.dumps(value)})"
    return None


def _argument_error(tool: dict, arguments: dict) -> str | None:
    """The first refusal the tool's own table finds, or None when the call can run.

    Answered as a tool result with isError true, in the tool's vocabulary, not
    as the protocol's -32602 example: ADR 0001 keeps the house precedent set by
    the unknown-tool and missing-config answers, because a coding agent reads
    tool results and corrects its next call, while a protocol error surfaces in
    many clients as a transport failure the agent never sees.
    """
    return (_missing_positional(tool, arguments) or _unknown_key(tool, arguments)
            or _wrong_type(tool, arguments))


def _tool_named(name: str) -> dict | None:
    return next((t for t in TOOLS if t["name"] == name), None)


def _config_root(repo: str) -> Path | None:
    """The crapkit root at or above `repo`, a call's own argument, found the
    way every command finds it (ADR 0002). A `repo` naming no directory finds
    nothing: a typo must not be adopted by an ancestor's configuration and
    read back as data."""
    start = Path(repo).resolve()
    return find_root(start) if start.is_dir() else None


def _served_root(root: Path, arguments: dict) -> tuple[str, Path | None]:
    """The repo a call names and the crapkit root that serves it. A call's own
    `repo` argument is walked up to the nearest crapkit.toml; the server's
    root was settled once at start, where `crapkit mcp` walks without `--repo`
    and a given `--repo` names an exact root, as on every subcommand."""
    repo = arguments.get("repo")
    if repo:
        return repo, _config_root(repo)
    return str(root), root if (root / CONFIG_NAME).is_file() else None


def _call_tool(root: Path, name: str, arguments: dict) -> dict:
    """Name lookup, then the arguments against the table, then the repo the
    call names, then the run. Every refusal is decided before a CLI spawns."""
    tool = _tool_named(name)
    if tool is None:
        return _result(f"unknown tool {name!r}", is_error=True)
    refusal = _argument_error(tool, arguments)
    if refusal:
        return _result(refusal, is_error=True)
    repo, found = _served_root(root, arguments)
    if found is None:
        return _no_config_result(repo)
    return _run_cli(tool, arguments, str(found))


# What a connected model needs before its first call, in the one field the
# protocol reserves for it. The ten error results a model would otherwise
# collect from an unmeasured repo teach the same thing ten times, slower.
_INSTRUCTIONS = (
    "crapkit scores every function as ccn^2 x (1 - coverage)^3 + ccn; these twelve tools read "
    "the newest scored run and every one is read-only: nothing here runs a test suite or "
    "writes to the repo. They need a repo measured once (crapkit init, then crapkit "
    "coverage); an unmeasured repo answers with a one-line pointer instead of data. Start "
    "with get_next_item for one function to fix, list_worklist for the whole ranking, "
    "get_function_brief for everything about one function, and check_gate after an edit to "
    "learn whether the file clears the commit gate.")


def _negotiated(params: dict) -> str:
    """The client's revision when this server implements it, else the newest it
    does; the spec leaves proceeding or disconnecting to the client from there."""
    offered = params.get("protocolVersion")
    return offered if offered in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]


def _structured(result: dict) -> dict:
    """Attach the parsed object beside the text when the text is a JSON object.

    The --json commands print for machines; a client on the 2025-06-18 revision
    reads structuredContent directly. Prose, JSON arrays and error text stay
    text-only rather than getting wrapped into shapes the tools never promised.
    """
    if result["isError"]:
        return result
    try:
        parsed = json.loads(result["content"][0]["text"])
    except ValueError:
        return result
    if isinstance(parsed, dict):
        return {**result, "structuredContent": parsed}
    return result


def _respond(msg_id, result=None, error=None) -> dict:
    resp = {"jsonrpc": "2.0", "id": msg_id}
    resp["error" if error else "result"] = error if error else result
    return resp


def _initialize_result(params: dict) -> dict:
    return {"protocolVersion": _negotiated(params),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "crapkit", "version": _version()},
            "instructions": _INSTRUCTIONS}


# The methods that need no repo, keyed as the wire spells them. ping answers
# the empty object the spec asks for, so a client's keepalive is not -32601.
_METHODS = {"initialize": _initialize_result,
            "tools/list": lambda params: {"tools": tool_listing()},
            "ping": lambda params: {}}


def _tools_call(root: Path, params: dict) -> dict:
    return _call_tool(root, params.get("name", ""), params.get("arguments") or {})


def _handle(root: Path, msg: dict) -> dict | None:
    if "id" not in msg:
        return None  # a notification (e.g. notifications/initialized) needs no reply
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if method == "tools/call":
        return _respond(msg["id"], _tools_call(root, params))
    handler = _METHODS.get(method)
    if handler is None:
        return _respond(msg["id"], error={"code": -32601,
                                          "message": f"unknown method {method!r}"})
    return _respond(msg["id"], handler(params))


def _version() -> str:
    from . import __version__
    return __version__


def _parse(line: str) -> dict | None:
    """One request object, or None for a blank line, junk, or a frame that is
    not an object: those get no reply, and the loop reads on."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _reply(root: Path, msg: dict) -> dict | None:
    """The reply to one message. An exception escaping a handler becomes the
    JSON-RPC -32603 reply instead of the end of the session; a notification
    that raised gets nothing, since it asked for nothing."""
    try:
        return _handle(root, msg)
    except Exception as exc:  # noqa: BLE001 - the loop must outlive any one call
        if "id" not in msg:
            return None
        return _respond(msg["id"], error={"code": -32603,
                                          "message": f"{type(exc).__name__}: {exc}"})


def serve(root: Path) -> int:
    """Newline-delimited JSON-RPC over stdio until EOF."""
    for line in sys.stdin:
        msg = _parse(line)
        if msg is None:
            continue
        resp = _reply(root, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0
