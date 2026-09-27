"""Minimal MCP stdio server. JSON-RPC 2.0, newline-delimited, no SDK dependency.

Every tool shells to the CLI's own --json surface. Calls can write caches and
store metadata; coverage runs, verification, ratchet changes and mutations stay
in the CLI.
"""
from __future__ import annotations

import json
import os
import re
from .procs import CommandCancelled, run_owned
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from ._package import upgraded_to
from .agent_fields import schema_of
from .cli._shared import SCHEMA_VERSION, _load_repo_config, _on_its_drive
from .errors import UNREAD_NAME_REASON, CrapkitError
from .gitpaths import readable, shown
from .invocation import _self
from .plaintext import strip_escapes
from .repopath import typed, typed_path
from .repotext import json_kind
from .rootfind import CONFIG_NAME, find_root

# Newest first. Everything this server does — tools, annotations, structured
# results over stdio — is inside the 2025-06-18 revision, and nothing it does
# was removed from the older two, so any of the three can be spoken verbatim.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_REPO = {"repo": {"type": "string", "description": (
    "path to the scored repo's root (default: the repo the server was started in); a leading ~ "
    "is the home directory")}}

# brief and explain resolve NAME by one rule, so they describe it with one
# string. The bare identifier is the long name's leading token, which is all
# there is before the parameters in Rust and Go.
_NAME_DESCRIPTION = ("the bare identifier (classify, or route for a Rust "
                     "`route cmd : & Cmd`) or the whole long_name next_item "
                     "printed (classify( score , late )); both resolve, exact "
                     "match first")

# The scorer's remedy vocabulary, said once: a structured result carrying a value
# its outputSchema does not list is rejected whole by a validating client.
_REMEDIES = ("decompose", "split-lines", "add-tests", "ok")
_REMEDY_DESCRIPTION = ("decompose (ccn over ceiling), split-lines (another function shares its "
                       "source lines, or a Python def's body starts on the line its signature "
                       "ends, where coverage.py reads it as the def statement, so coverage cannot "
                       "tell them apart and no test lowers the score until they sit on separate "
                       "lines), add-tests (coverage short) or ok (nothing left to do)")
_REMEDY = {"type": "string", "description": _REMEDY_DESCRIPTION, "enum": _REMEDIES}

# Every function row carries it (docs/agent-json.md), so every row schema says so once.
_OCCURRENCE = {"type": "integer", "description": (
    "source creation order among functions sharing start, from 1; 0 on an older row with no "
    "recorded position")}

# The partition a large repo needs before `top` means anything: one --scope
# per element, exact names as declared in crapkit.toml.
_SCOPE = {
    "type": "array",
    "description": ("restrict the ranking to these declared scopes (exact [[scope]] names from "
    "crapkit.toml, one --scope each)"),
    "items": {
        "type": "string"}}


def _unread_files_schema(payload: str, key: str) -> dict:
    """The one shape an unread-file finding has in every payload that carries it."""
    return {**schema_of(payload, key), "items": {
        "type": "object", "description": "one changed file the gate refused unread",
        "properties": {name: schema_of(payload, f"{key}[].{name}")
                       for name in ("path", "reason", "dirty")}}}


# The fields 0.8.1 adds take their schema from the one declaration of them.
_SHALLOW_CHURN = schema_of("worklist --json", "shallow")
_UNMEASURED = schema_of("next-item", "item.unmeasured")

# Whether the ranked run still describes the files on disk, said once for every
# payload that carries it. `stale` keeps its schema 1 meaning, the run's commit
# against HEAD; `scored_changes`, the content answer beside it, and the
# commands.refresh that answers both are 0.8.1 fields, declared once in
# agent_fields.
_STALE = {
    "type": "boolean",
    "description": ("true when the run's commit is not HEAD. It judges the commit, not the "
    "files: an amend or a commit that touched no scored file sets it, and an uncommitted "
    "edit leaves it false; scored_changes counts the files whose content moved. "
    "Schema 2 redefines it as that content difference")}
# brief's commands.refresh is 0.8.0's field; it names the same call the added one does.
_REFRESH = schema_of("next-item", "commands.refresh")


def _refresh_commands(payload: str) -> dict:
    return {"type": "object",
            "description": "the call that answers stale and scored_changes; run it as given",
            "properties": {"refresh": schema_of(payload, "commands.refresh")}}

_PACKET_PROPERTIES = {'scope': {'type': 'string', 'description': 'the declared scope that owns the file'},
 'path': {'type': 'string', 'description': 'repo-relative source path, forward slashes'},
 'function': {'type': 'string',
              'description': 'lizard long name with the spaced parameter list; get_function_brief '
                             'and get_function_history accept it as name'},
 'handle': {'type': 'string',
            'description': 'short name form: the bare identifier, or (anonymous)#N for a function '
                           'lizard could not name; it names a position, not a line, so it survives '
                           'the edit this item asks for; pass it back as name'},
 'start': {'type': 'integer', 'description': 'first line, 1-based inclusive'},
 'end': {'type': 'integer', 'description': 'last line, 1-based inclusive'},
 'occurrence': _OCCURRENCE,
 'ccn': {'type': 'integer',
         'description': 'min(ccn_std, ccn_mod): the complexity the gate and the ratchet judge'},
 'ccn_std': {'type': 'integer', 'description': 'standard cyclomatic complexity'},
 'cognitive': {'type': 'integer',
               'description': 'Sonar-spec cognitive complexity, reporting only, never gated'},
 'nloc': {'type': 'integer', 'description': 'non-comment lines of code'},
 'nesting': {'type': 'integer', 'description': 'maximum nesting depth'},
 'cov': {'type': 'number', 'description': 'branch coverage inside the span, 0.0 to 1.0'},
 'flag': {'type': 'string',
          'description': 'measured, untested, no-lane or cc-only: whether a lane artifact could '
                         'measure this span',
          'enum': ('measured', 'untested', 'no-lane', 'cc-only')},
 'crap': {'type': 'number', 'description': 'the score: ccn^2 x (1 - cov)^3 + ccn'},
 'remedy': _REMEDY,
 'target': {'type': 'integer', 'description': "this scope's effective ccn ceiling"},
 'commits': {'type': 'integer', 'description': 'commits touching the file in the churn window'},
 'authors': {'type': 'integer', 'description': 'distinct authors of those commits'},
 'est_splits': {'type': 'integer',
                'description': '0 when ccn <= target, else ceil(ccn / target): roughly how many '
                               'functions this must become'},
 'est_uncovered_paths': {'type': 'integer',
                         'description': 'round((1 - cov) x ccn): decision paths no test walks'},
 'uncovered_lines': {'type': ('array', 'null'),
                     'description': 'line numbers no test ran; [] when the span is fully covered; '
                                    'null when no artifact could answer, then uncovered_lines_note '
                                    'says why',
                     'items': {'type': 'integer'}},
 'uncovered_lines_note': {'type': 'string',
                          'description': 'present only when uncovered_lines is null: the reason '
                                         'and the move (stale artifact, no test imports the file, '
                                         'coverage_optional scope)'},
 'unmeasured': _UNMEASURED}

_WORKLIST_ITEM = {'type': 'object',
 'description': 'one ranked function',
 'properties': {'scope': {'type': 'string', 'description': 'the declared scope that owns the file'},
                'path': {'type': 'string',
                         'description': 'repo-relative source path, forward slashes'},
                'function': {'type': 'string',
                             'description': 'lizard long name with the spaced parameter list; pass '
                                            'it to get_function_brief as name'},
                'start': {'type': 'integer', 'description': 'first line, 1-based'},
                'end': {'type': 'integer', 'description': 'last line, inclusive'},
                'ccn': {'type': 'integer',
                        'description': 'min(ccn_std, ccn_mod): what the gate judges'},
                'ccn_std': {'type': 'integer', 'description': 'standard cyclomatic complexity'},
                'nloc': {'type': 'integer', 'description': 'non-comment lines of code'},
                'commits': {'type': 'integer',
                            'description': 'commits touching the file in the churn window'},
                'authors': {'type': 'integer', 'description': 'distinct authors of those commits'},
                'weight': {'type': 'number',
                           'description': 'recency-weighted churn of the file; 1.0 on every file '
                                          'when commits share one timestamp'},
                'risk': {'type': 'number',
                         'description': 'ccn x weight, four decimals: the sort key'},
                'flag': {'type': ('string', 'null'),
                         'description': 'measured, untested, no-lane or cc-only; null on an '
                                        'inventory-only run',
                         'enum': ('measured', 'untested', 'no-lane', 'cc-only', None)},
                'remedy': {'type': ('string', 'null'),
                           'description': 'decompose, split-lines, add-tests or ok, as the run '
                                          'judged it; null on an inventory-only run. A row a '
                                          'lane measures reaches get_next_item unless its remedy '
                                          'is ok. After a crapkit.toml ceiling edit no run has '
                                          "scored, get_next_item's own remedy decides that, "
                                          'and it can differ from this one',
                           'enum': (*_REMEDIES, None)},
                'crap': {'type': ('number', 'null'),
                         'description': 'the score from the ranked run; null on an inventory-only '
                                        'run'},
                'cov': {'type': ('number', 'null'),
                        'description': 'branch coverage 0.0 to 1.0; null on an inventory-only run'},
                'ratchet_mark': {'type': ('number', 'null'),
                                 'description': 'the committed ratchet mark on this function, read '
                                                'under its own ratchet key; null when it carries '
                                                'none or the repo has no marks file'},
                'occurrence': _OCCURRENCE,
                'handle': {'type': 'string',
                           'description': 'short name form: the bare identifier, or '
                                          '(anonymous)#N for a function lizard could not name; '
                                          'pass it to get_function_brief as name'}}}

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
            "stale": _STALE,
            "scored_changes": schema_of("next-item", "scored_changes"),
            "commands": _refresh_commands("next-item"),
            "shallow": _SHALLOW_CHURN,
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
                "properties": _PACKET_PROPERTIES},
            "items": {
                "type": "array",
                "description": ("up to top packets in crap-descending order, present when empty is "
                "false and top is above 1"),
                "items": {
                    "type": "object",
                    "description": "a packet, same shape as item",
                    "properties": _PACKET_PROPERTIES}},
            "reasons": {
                "type": "object",
                "description": ("why the queue is empty, present only when empty is true; the stop "
                "condition is empty true with skipped_claimed and no_lane_over_target "
                "both 0 or absent and scored_changes 0"),
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
        "ordered by ccn times recency-weighted churn. Use it to survey a repo or "
        "split work by file, and get_next_item for the one crap-ranked packet to fix "
        "now. It runs no tests, keeps finished rows so it never empties, and reads "
        "the churn cache, not git. scope narrows before top caps, so scope [\"core\"] "
        "with top 20 returns the 20 riskiest in core, an unknown scope name is a "
        "config error, and repo may be any directory under the measured checkout."),
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
            "stale": _STALE,
            "scored_changes": schema_of("worklist --json", "scored_changes"),
            "commands": _refresh_commands("worklist --json"),
            "shallow": _SHALLOW_CHURN,
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
                "items": _WORKLIST_ITEM},
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
                "items": _WORKLIST_ITEM}},
    },
    {
        "name": "list_runs",
        "title": "Scored run history",
        "argv": ("runs",),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Lists every run in the store, oldest first by id. Use it to date the store "
        "or to see which commit the other tools answer from. Use get_trend for "
        "per-run totals and get_function_history for one function's scores per run. "
        "It reads the store only and spawns no git. repo may be any directory under "
        "the checkout, because the server walks up to the nearest crapkit.toml, and a "
        "relative path resolves from the server's start directory. No crapkit.toml "
        "above it answers an init pointer, and a checkout never scored answers a "
        "coverage pointer, both as isError true."),
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
        "scope. Use it for the whole repo's trajectory. Use get_function_history for "
        "one function and get_ratchet_report for marked debt. It runs no lane and "
        "spawns no git. The first call on a large store sums every run into a rollup "
        "cache and takes seconds. Later calls read the cache back. repo may be any "
        "directory under a checkout where crapkit init and crapkit coverage have run. "
        "An unmeasured one answers isError true with the setup pointer."),
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
        "lines, none of them run. Use it once a function is chosen. Skip it for "
        "picking what to fix, that is get_next_item, and for a score across runs, "
        "get_function_history. Twins come from the run's stored index, whose first "
        "build takes seconds. name must live in path. name takes the long name, a "
        "bare identifier, a start line or NAME#2, exact match first. A miss lists the "
        "file's functions instead of erroring."),
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
            "stale": _STALE,
            "scored_changes": schema_of("brief --json", "scored_changes"),
            "shallow": schema_of("brief --json", "shallow"),
            "unmeasured": schema_of("brief --json", "unmeasured"),
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
                "description": ("decompose, split-lines, add-tests or ok: the branch the "
                "session takes; scored.remedy carries the same value"),
                "enum": _REMEDIES},
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
                    "occurrence": _OCCURRENCE,
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
                    "remedy": _REMEDY}},
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
                        "remedy": _REMEDY,
                        "occurrence": _OCCURRENCE}}},
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
                        "description": ("the crapkit test-scoped call for this literal file, or "
                        "null when the scope declares no [crapkit.scoped_tests] "
                        "template")},
                    "scoped_tests_note": {
                        "type": "string",
                        "description": ("present only when scoped_tests is null: names the scope "
                        "missing a template")},
                    "verify": {
                        "type": "string",
                        "description": "the crapkit verify call: the only authoritative verdict"},
                    "refresh": _REFRESH,
                    "refresh_writes_run": {
                        "type": "boolean",
                        "description": ("always true: refresh writes a coverage run to the store; "
                        "other commands can also write caches or test artifacts")}}},
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
                        "description": ("one [run_id, ccn] pair for every stored run that "
                        "scored the function, whatever its kind, oldest first"),
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
        "description": ("Returns one function's ccn, coverage, crap and flag per run, oldest "
        "first, plus its ratchet mark. Use it to tell improving from decaying or "
        "regrown, and get_function_brief to start an edit. history true spawns git log "
        "-L capped at 10 commits, and tests true is null unless the lane recorded "
        "contexts. name matches the long names any run scored in path, so a fragment "
        "returns one entry per match, and a bare twin name picks the worst twin. A "
        "same-line twin's history skips runs stored before same-line positions. repo "
        "may be any directory under the checkout."),
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
        "history is in question. It needs no run, probes each runner once per lane "
        "directory and environment, runs no "
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
            "resources": {
                "type": "object",
                "description": "effective resource policy; limits and estimates, not sampled utilization",
                "properties": {
                    "available_cpus": {"type": "integer", "description": "CPUs visible to this process"},
                    "cpu_probe": {"type": "string", "description": "CPU affinity or fallback probe used"},
                    "requested_analysis_workers": {"type": "integer", "description": "configured per-pool ceiling; zero selects the default"},
                    "shared_pool_limit": {"type": "integer", "description": "shared numbered pool slot ceiling"},
                    "default_chunks_per_worker": {"type": "integer", "description": "chunk target used by automatic pool sizing"},
                    "default_source_bytes_per_worker": {"type": ("integer", "null"), "description": "source-byte target for automatic spawn sizing; null for other start methods"},
                    "pool_worker_limit": {"type": "integer", "description": "per-pool ceiling after CPU, memory and inherited limits"},
                    "estimated_pool_memory_mb": {"type": "integer", "description": "estimated memory for the allowed worker count"},
                    "inherited_analysis_workers": {"type": ("integer", "null"), "description": "valid inherited worker ceiling or null"},
                    "memory_budget_mb": {"type": ("integer", "null"), "description": "inherited memory sizing hint or null"},
                    "worker_memory_estimate_mb": {"type": "integer", "description": "memory estimate per analysis worker"},
                    "memory_is_hard_limit": {"type": "boolean", "description": "false: memory policy sizes workers without an OS allocation limit"},
                    "budget_directory": {"type": "string", "description": "coordination directory; status does not create it"},
                    "serial_fallback": {"type": "boolean", "description": "busy slots allow serial work without waiting"},
                    "coordination": {"type": "string", "description": "worker slot ownership scope"},
                    "log_max_bytes": {"type": "integer", "description": "byte limit per current and backup lane log; zero is unlimited"},
                    "test_retention_days": {"type": "integer", "description": "deprecated, always 0: crapkit applies no test evidence retention"},
                    "test_retention_count": {"type": "integer", "description": "deprecated, always 0: crapkit applies no test evidence retention"}}},
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
                        "refusal": {
                            "type": ("string", "null"),
                            "description": ("why --reuse-artifacts will not score the artifact on "
                            "disk (the lane's last attempt left it unwritten), null when it would")},
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
        "what an edit drags along. Use list_duplicate_functions for copied code "
        "rather than co-change. It reads git log once per call, not the scored run. "
        "An empty list means no pair cleared both thresholds, not a missing run. A "
        "pair must clear both thresholds: min_support 5 needs five shared commits, "
        "min_confidence 0.5 means the rarer file moved with its partner half the "
        "time. repo defaults to the server's own root."),
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
        "description": ("Lists near-duplicate function pairs in the newest run, at most 50. Use it "
        "before a refactor so twins are folded together, and get_function_brief for "
        "one function's twins. It reads the run's stored index, whose first build "
        "takes seconds on a large repo, skips functions under 8 lines and same-file "
        "pairs, and an empty list means no pair reached similarity. similarity is "
        "shared shingles over the smaller function: 1.0 admits only a function found "
        "whole inside another, 0.8 four lines in five, and repo may be any directory "
        "under the checkout."),
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
                    "type": "string"}},
            "shallow": schema_of("ratchet report --json", "shallow")},
    },
    {
        "name": "check_gate",
        "title": "rescore --gate verdict for an edited file",
        "argv": ("rescore", "--gate"),
        "json_flag": True,
        "positional": ("path",),
        "flags": {},
        "verdict_exits": (6,),
        "unread_verdict": True,
        "description": ("Checks an edited file by rescore --gate's rule: each changed function's "
        "ccn against its scope's ceiling, pardoned only while its crap is at or under its "
        "ratchet mark. The hook's commit gate pardons any marked function, so this is "
        "stricter and a breach predicts a verify refusal. Call it after an edit once "
        "get_function_brief states the rule. It runs no tests, and a breach reads gate.ok "
        "false, not an error. Marks are read only on a breach, so a clean gate skips a "
        "broken marks file. A tracked file is judged on its diff from HEAD, an untracked "
        "one in full."),
        "properties": {
            "path": {
                "type": "string",
                "description": ("repo-relative, or absolute inside repo, source file to judge "
                "as edited. Outside the repo or missing answers a config error, and an "
                "unchanged or unscoped file judges 0. A file a scope takes whose name is not UTF-8 "
                "answers gate.ok false with the name in gate.unread_files. repo may be any directory "
                "under the checkout, and path stays relative to the root it walks up to.")}},
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
                        "remedy": _REMEDY,
                        "occurrence": _OCCURRENCE,
                        "stale_coverage": {
                            "type": "boolean",
                            "description": ("always true: complexity is the working tree's, coverage "
                            "is the baseline run's")},
                        "unmeasured": schema_of("rescore --gate --json",
                                                "functions[].unmeasured")}}},
            "gate": {
                "type": "object",
                "description": "the verdict block",
                "properties": {
                    "ok": {
                        "type": "boolean",
                        "description": ("true when breaches and unread_files are empty; false is the "
                        "verdict (CLI exit 6, or 3 when a file's name is not UTF-8), delivered as "
                        "a normal result")},
                    "judged": {
                        "type": "integer",
                        "description": ("functions the working tree changed since HEAD, an untracked "
                        "file counted in full; 0 on an unchanged path")},
                    "ceilings": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": {"type": "integer"},
                        "description": ("map of repo-relative path to the ccn ceiling it was "
                        "judged against")},
                    "breaches": {
                        "type": "array",
                        "description": "the failing functions; empty when ok",
                        "items": {
                            "type": "object",
                            "description": ("one judged function over its ceiling, unmarked or "
                            "with its crap past its ratchet mark"),
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
                                "remedy": _REMEDY,
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
                            "type": "string"}},
                    "unread_files": _unread_files_schema("rescore --gate --json",
                                                         "gate.unread_files")}}},
    },
    {
        "name": "list_claims",
        "title": "Open queue claims",
        "argv": ("claims", "list"),
        "json_flag": True,
        "positional": (),
        "flags": {},
        "description": ("Lists open claims on queue items, oldest first. Use it when get_next_item "
        "answers empty or skipped_claimed above 0, and use get_function_brief to see "
        "one function's own attempts. No tool here writes a claim: the CLI releases a "
        "stale one with crapkit claims release PATH NAME, and verify closes one at "
        "the ceiling. repo may be any directory under the checkout, since the server "
        "walks up to the nearest crapkit.toml. No crapkit.toml above it answers an "
        "init pointer, and a checkout never scored answers a coverage pointer, both "
        "as isError true."),
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

# The longest answer a tool gives, counted as the characters its text takes in
# a client's JSON of the result. Cline keeps 8,000 characters of that JSON and
# cuts the middle out; a brief on a 300-line function was 15 to 27 KB, so its
# model got JSON it could not parse. The other clients measured keep much
# longer results whole, and they get the same answer, so every client reads
# one payload.
ANSWER_CHARS = 7_500

# A field shorter than this is never cut: a path, a sha or a name is worth
# more whole than the characters it frees.
_CUTTABLE_CHARS = 500


# What every answer carries when it was cut to fit one tool result.
_TRUNCATED = {
    "type": "object",
    "description": ("present only when the whole answer was longer than one tool result the "
                    f"server sends ({ANSWER_CHARS:,} characters): each list, string or object "
                    "field it cut, at any depth, keeps its start, and full is the CLI command "
                    "that prints the whole answer"),
    "properties": {
        "fields": {"type": "object",
                   "description": ("each cut field, named by its keys joined with dots "
                                   "(gate.breaches) -> kept and of: elements for a list, entries "
                                   "for an object, characters for a string"),
                   "additionalProperties": {
                       "type": "object",
                       "properties": {
                           "kept": {"type": "integer", "description": "how many the answer keeps"},
                           "of": {"type": "integer", "description": "how many the whole answer has"}}}},
        "full": {"type": "string",
                 "description": "the crapkit command that prints the whole answer, spelled for "
                                "the server's shell"}}}

# These annotations describe score and source inspection. Cache, store migration
# and rollup writes are documented in the initialization response.
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


def _listing_entry(tool: dict, structured: bool = True) -> dict:
    """One tool as `tools/list` serves it. `title` and `outputSchema` appear only
    when the table declares them: a null title or an empty object would read as
    a defect to a client that grades definitions, and the wire form stays what
    earlier clients saw when a tool carries neither. `outputSchema` also needs a
    `structured` session, one whose revision defines structuredContent."""
    entry = {"name": tool["name"], "description": tool["description"],
             "annotations": dict(_ANNOTATIONS), "inputSchema": _schema(tool)}
    if tool.get("title"):
        entry["title"] = tool["title"]
    if structured and tool.get("output"):
        entry["outputSchema"] = {"type": "object",
                                 "properties": {**tool["output"], "truncated": _TRUNCATED}}
    return entry


def tool_listing(structured: bool = True) -> list[dict]:
    """Every tool as `tools/list` serves it. A session on a revision older than
    2025-06-18 gets no outputSchema: those revisions define no structuredContent
    to hold to one, and the TypeScript SDK 1.12, which offers 2025-03-26, fails
    every call to a tool that lists a schema and returns none."""
    return [_listing_entry(t, structured) for t in TOOLS]


def _flag_values(value) -> list:
    """What one argument contributes to argv: nothing when absent or false, one
    bare flag for true, one flag per element for a list, else one flag."""
    if value is None or value is False:
        return []
    return value if isinstance(value, list) else [value]


def _flag_args(flags: dict, arguments: dict) -> list[str]:
    """Each option bound to its value in one word, `--exclude=-legacy`: split
    in two, a value that starts with `-` is read by argparse as a flag."""
    out: list[str] = []
    for key, flag in flags.items():
        for v in _flag_values(arguments.get(key)):
            out.append(flag if v is True else f"{flag}={v}")
    return out


def build_argv(tool: dict, arguments: dict, repo: str) -> list[str]:
    """The CLI argv for one call: the options bound to their values, then
    `--repo=` and `--json`, then `--` and the positionals. After `--` argparse
    reads every word as a value, so `path="--help"` is a path, never brief's
    help answered as a successful result."""
    argv = [*tool["argv"], *_flag_args(tool["flags"], arguments), f"--repo={repo}"]
    if tool["json_flag"]:
        argv.append("--json")
    positionals = [str(arguments[p]) for p in tool["positional"]]
    return argv + ["--", *positionals] if positionals else argv


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


def _run_cli(tool: dict, arguments: dict, repo: str, *, owner=None) -> dict:
    """One tool, run as the CLI command it maps to, at the root it serves: a
    server started in a workspace must not hand the command a working
    directory below the root, because `path` is repo-relative on every tool's
    schema. A command that printed nothing answers with its stderr, so a
    refusal reaches the caller as text, with its escape codes removed: the
    child shares the client's environment, and under FORCE_COLOR or
    PYTHON_COLORS=1 a 3.13+ traceback or a 3.14 argparse message arrives
    coloured. An exit the tool declares in `verdict_exits` is an answer, not
    a failure: `gate` exits 6 on a breach and its payload says so in `gate.ok`.
    A call that came before the client named its workspace folders waits for
    them here, in the worker. The command a cut answer names is spelled before
    the spawn: an upgrade that lands while the command runs must not load the
    next release's packet.py into this process."""
    if isinstance(repo, _Session):
        return repo.run(tool, arguments, owner)
    unread = _unreadable_path(tool, arguments, repo)
    if unread is not None:
        return _unread_name_answer(tool, Path(repo), unread)
    argv = build_argv(tool, arguments, repo)
    full = _full_command(tool, arguments, repo)
    proc = run_owned([sys.executable, "-m", "crapkit", *argv], cwd=repo,
                     capture_output=True, timeout=600, owner=owner)
    text = proc.stdout if proc.stdout.strip() else strip_escapes(proc.stderr)
    failed = proc.returncode != 0 and proc.returncode not in tool.get("verdict_exits", ())
    return _structured(_result(text, is_error=failed), lambda: full)


def _full_command(tool: dict, arguments: dict, repo: str) -> str:
    """The CLI command that prints a call's whole answer, spelled for the
    host's shell as the packet's own commands are."""
    from .packet import console_command
    return console_command(build_argv(tool, arguments, repo))


def _text_chars(payload: dict) -> int:
    return len(json.dumps(json.dumps(payload, sort_keys=True) + "\n"))


def _fields(value: dict, path: tuple = ()):
    """Every list, string and object below `value` that object keys reach,
    with its path of keys. A list's elements are cut with the list, never one
    by one, so nothing inside a list is a field of its own."""
    for key, child in value.items():
        if isinstance(child, (list, str, dict)):
            yield (*path, key), child
        if isinstance(child, dict):
            yield from _fields(child, (*path, key))


def _cut_order(path: tuple, value) -> tuple:
    """Lists first, since a list's first rows still answer; then strings, since
    a brief's `source` is what the edit is made from; objects last, deepest
    first, so a map keyed by data (check_gate's `gate.ceilings`, one entry per
    file) goes before the object whose named fields hold it. The largest first
    within each."""
    size = -len(json.dumps(value))
    if isinstance(value, dict):
        return (2, -len(path), size, path)
    return (int(isinstance(value, str)), 0, size, path)


def _cuttable(payload: dict) -> list[tuple]:
    """The paths of the fields worth cutting, in the order they are cut."""
    worth = [(path, value) for path, value in _fields(payload)
             if len(json.dumps(value)) >= _CUTTABLE_CHARS]
    return [path for path, _ in sorted(worth, key=lambda field: _cut_order(*field))]


def _at(payload, path: tuple):
    """The field at `path`, or None where a cut ancestor no longer holds it."""
    for key in path:
        payload = payload.get(key) if isinstance(payload, dict) else None
    return payload


def _replaced(payload: dict, path: tuple, value) -> dict:
    """`payload` with the field at `path` replaced, sharing everything else."""
    head, rest = path[0], path[1:]
    return {**payload, head: _replaced(payload[head], rest, value) if rest else value}


def _start(value, count: int):
    """The first `count` elements, entries or characters of `value`."""
    return dict(list(value.items())[:count]) if isinstance(value, dict) else value[:count]


def _fitting(payload: dict, path: tuple) -> int:
    """The longest start of the field at `path` with which `payload` fits, 0
    when none does."""
    whole = _at(payload, path)
    low, high = 0, len(whole)
    while low < high:
        middle = (low + high + 1) // 2
        fits = _text_chars(_replaced(payload, path, _start(whole, middle))) <= ANSWER_CHARS
        low, high = (middle, high) if fits else (low, middle - 1)
    return low


def _counts(payload: dict, cut: dict, paths: list) -> dict:
    """Each cut field, by its dotted path, with what it kept of what it had."""
    counts = {".".join(path): {"kept": len(_at(cut, path) or ()), "of": len(_at(payload, path))}
              for path in paths}
    return {name: count for name, count in counts.items() if count["kept"] < count["of"]}


def _budgeted(payload: dict, full) -> dict:
    """`payload` cut to ANSWER_CHARS, one field at a time in _cuttable's
    order, each keeping its start. `truncated` says what each kept of what it
    had, and names the command that prints everything; the worst case of it is
    counted while cutting, so the answer that carries it still fits."""
    paths = _cuttable(payload)
    kept = {"fields": {".".join(path): dict.fromkeys(("kept", "of"), len(_at(payload, path)))
                       for path in paths}, "full": full()}
    cut = {**payload, "truncated": kept}
    for path in paths:
        cut = _replaced(cut, path, _start(_at(cut, path), _fitting(cut, path)))
    kept["fields"] = _counts(payload, cut, paths)
    return cut


def _unreadable_path(tool: dict, arguments: dict, repo: str) -> str | None:
    """The root-relative name a tool's `path` gives, when a file on disk has
    that name and it is not UTF-8; None for any other call.

    Such a name never goes on a child's argv. On Windows the child read a lone
    surrogate there as U+FFFD and looked up a file nobody named: check_gate
    answered isError true with `does not exist`, and get_function_brief and
    get_function_history with `no function ... in src/caf\\ufffd.ts`, where the
    CLI refuses the name at exit 3 and lists it in `unread_files`."""
    if "path" not in tool["positional"]:
        return None
    rel = typed(str(arguments["path"]), repo)
    if rel is None or readable(rel) or not os.path.lexists(Path(repo) / rel):
        return None
    return rel


def _unread_name_answer(tool: dict, root: Path, rel: str) -> dict:
    """The answer to a `path` whose name is not UTF-8: check_gate's verdict, and
    for every other tool the error object the CLI prints when it refuses the
    argument, `unread_files` and all."""
    from .cli._shared import _name_refusal

    if tool.get("unread_verdict"):
        return _unread_name_result(root, rel)
    return _result(_error_text(_name_refusal(rel, root)), is_error=True)


def _unread_name_result(root: Path, rel: str) -> dict:
    """check_gate's answer on a file whose name is not UTF-8, decided here.

    Every CLI gate exits 3 on such a name when a scope takes it, since no
    reader can key it. A tool that speaks MCP maps that refusal to its own
    protocol, where a tool error reads as a broken tool: the answer is the
    verdict the refusal is. A name no scope takes judges 0 and passes, as any
    unscoped file does (Q17). A repo the CLI would refuse first (no config, no
    scored run) answers that refusal as the CLI's error object."""
    try:
        payload = _unread_name_verdict(root, rel)
    except CrapkitError as exc:
        return _result(_error_text(exc), is_error=True)
    return _structured(_result(json.dumps(payload, sort_keys=True), is_error=False))


def _unread_name_verdict(root: Path, rel: str) -> dict:
    """`rescore --gate --json`'s payload for the one file: the baseline every
    verdict names, no function, and the name in `gate.unread_files` when a
    scope takes it, in the entry shape the CLI lists (`dirty` is true, since
    the gate judges the working tree)."""
    from .cli.scoring import RESCORE_NOTE, _rescore_baseline
    from .universe import claiming_scope

    cfg = _load_repo_config(root)
    store, latest = _rescore_baseline(root)
    store.close()
    unread = [] if claiming_scope(rel, cfg) is None else [
        {"path": shown(rel), "reason": UNREAD_NAME_REASON, "dirty": True}]
    return {"baseline_run": latest["id"], "baseline_commit": latest["commit"], "functions": [],
            "note": RESCORE_NOTE, "schema": SCHEMA_VERSION, "gate": {
                "ok": not unread, "judged": 0, "ceilings": {}, "breaches": [], "untracked": [],
                "unread_files": unread}}


def _error_text(exc: CrapkitError) -> str:
    """The CLI's `--json` error object for a refusal the server met in process."""
    return json.dumps({"error": {"exit": exc.exit_code, "kind": exc.kind, "message": str(exc),
                                 **exc.json_fields()}, "schema": SCHEMA_VERSION}, sort_keys=True)


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


def _argument_error(tool: dict, arguments) -> str | None:
    """The first refusal the tool's own table finds, or None when the call can run.

    Answered as a tool result with isError true, in the tool's vocabulary, not
    as the protocol's -32602 example: ADR 0001 keeps the house precedent set by
    the unknown-tool and missing-config answers, because a coding agent reads
    tool results and corrects its next call, while a protocol error surfaces in
    many clients as a transport failure the agent never sees. Arguments that
    are not an object, by-position ones included, are refused before any key
    is read: a string's characters read as undeclared keys.
    """
    if not isinstance(arguments, dict):
        return f"arguments must be an object (got {json_kind(arguments)})"
    return (_missing_positional(tool, arguments) or _unknown_key(tool, arguments)
            or _wrong_type(tool, arguments))


def _tool_named(name: str) -> dict | None:
    return next((t for t in TOOLS if t["name"] == name), None)


# 0.6.0 renamed every tool to verb_noun (CHANGELOG, "The MCP tools follow one
# naming pattern"). A client that pinned a 0.5.x name, a Codex `enabled_tools`
# list or a Claude Code `mcp__...` allowlist among them, keeps sending it after
# an upgrade, and a bare "unknown tool" gave its model nothing to try next.
RENAMED_IN_0_6_0 = {
    "next_item": "get_next_item", "worklist": "list_worklist", "runs": "list_runs",
    "brief": "get_function_brief", "explain": "get_function_history", "doctor": "check_config",
    "coupling": "list_coupled_files", "duplication": "list_duplicate_functions",
    "ratchet_report": "get_ratchet_report", "gate": "check_gate",
}


def _unknown_tool(name: str) -> str:
    """The refusal for a name no tool carries, with the new name when a 0.5.x
    client sent the old one. A name that is not a string is named as sent."""
    renamed = RENAMED_IN_0_6_0.get(name) if isinstance(name, str) else None
    if renamed is None:
        return f"unknown tool {name!r}"
    return (f"unknown tool {name!r}: renamed {renamed} in 0.6.0, with the same arguments "
            f"and result; call {renamed}")


def _config_root(repo: str) -> Path | None:
    """The crapkit root at or above `repo`, a call's own argument, found the
    way every command finds it (ADR 0002). A `repo` naming no directory finds
    nothing: a typo must not be adopted by an ancestor's configuration and
    read back as data. A repo on a mapped drive keeps its letter, as `--repo`
    does: the CLI refuses a root on a network share. A leading `~` is the
    user's home, as on `--repo`."""
    start = _on_its_drive(typed_path(os.path.expanduser(repo)))
    return find_root(start) if start.is_dir() else None


# The revisions that define a result's structuredContent.
_STRUCTURED_REVISIONS = ("2025-06-18",)

# How long a call waits for the client to answer roots/list before it is
# answered as if the client named no folder, and how often the wait looks at a
# cancellation meanwhile.
ROOTS_SECONDS = 10
_ROOTS_SLICE = .05

# The variables a client names its plugin's install directory in, on the
# plugin's MCP server: GitHub Copilot CLI sets all three, VS Code the first and
# the last, Claude Code the last.
PLUGIN_ROOT_VARS = ("PLUGIN_ROOT", "COPILOT_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT")

# VS Code writes a drive's colon encoded (file:///c%3A/...), and url2pathname
# reads that as a directory named `c:` below the current drive's root.
_ENCODED_DRIVE = re.compile(r"^/([A-Za-z])%3[Aa]")


def started_in_plugin(start: Path) -> bool:
    """True when the client started the server at or below its plugin's own
    install directory: that directory is never the workspace, and walking up
    from a plugin loaded out of a crapkit checkout finds crapkit's own repo."""
    return any(start.is_relative_to(Path(root).resolve())
               for root in filter(None, map(os.environ.get, PLUGIN_ROOT_VARS)))


def _folder_path(uri) -> Path | None:
    """A `file:` URI from roots/list as a local path, None for anything else."""
    if not isinstance(uri, str) or not uri.startswith("file:"):
        return None
    from urllib.request import url2pathname
    parts = urlsplit(uri)
    host = "" if parts.netloc in ("", "localhost") else f"//{parts.netloc}"
    return Path(url2pathname(host + _ENCODED_DRIVE.sub(r"/\1:", parts.path)))


def _uris(result) -> list:
    """The `uri` of each root a roots/list result names, whatever its shape."""
    roots = result.get("roots") if isinstance(result, dict) else None
    if not isinstance(roots, list):
        return []
    return [root.get("uri") for root in roots if isinstance(root, dict)]


def _folder_root(folder: Path) -> Path | None:
    return find_root(folder.resolve()) if folder.is_dir() else None


# The one line of a GitHub Copilot CLI session's workspace.yaml that names the
# folder the session works in, as the CLI writes it at the file's top level.
_CWD_LINE = re.compile(r"^cwd:[ \t]*(.*?)[ \t]*$", re.MULTILINE)


def _yaml_string(value: str) -> str:
    """A scalar as a YAML writer quotes it: plain unless the value needs
    quotes, then single-quoted with '' for a quote, or double-quoted with the
    escapes JSON shares. A double-quoted value that does not parse is empty."""
    if value[:1] == "'":
        return value[1:-1].replace("''", "'")
    if value[:1] != '"':
        return value
    try:
        return json.loads(value)
    except ValueError:
        return ""


def _session_cwd(record: Path) -> Path | None:
    """The `cwd` a Copilot CLI session's workspace.yaml names, or None."""
    try:
        match = _CWD_LINE.search(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = _yaml_string(match.group(1)) if match else ""
    return Path(value) if value else None


def copilot_workspace() -> Path | None:
    """The folder the GitHub Copilot CLI session that started this server
    works in, or None outside one.

    Copilot CLI starts a plugin's server in the plugin's install directory,
    puts any `cwd` the plugin's config names outside it back there, and
    declares no roots, so nothing on the wire names the workspace. It gives
    every MCP server COPILOT_AGENT_SESSION_ID, and the session keeps its
    working directory in `session-state/<id>/workspace.yaml` under
    COPILOT_HOME, `~/.copilot` by default. Read at each call, so a session
    that moves with /cwd is followed."""
    session = os.environ.get("COPILOT_AGENT_SESSION_ID")
    if not session:
        return None
    from .userhome import user_home

    home = Path(os.path.expanduser(os.environ.get("COPILOT_HOME") or user_home() / ".copilot"))
    return _session_cwd(home / "session-state" / session / "workspace.yaml")


def _cancelled(owner) -> None:
    if getattr(owner, "cancelled", False):
        raise CommandCancelled("request was cancelled while it waited for the client's roots")


class _Session:
    """One client's session: where a call without a `repo` argument is served,
    and what the negotiated protocol revision can carry.

    A crapkit.toml at or above the directory the server started in settles the
    root (ADR 0002). Clients do not all start the server in the workspace: VS
    Code starts a user-level server in the home directory and a plugin's server
    in the plugin directory, and every call there answered `no crapkit.toml`
    inside a measured repo. A client that declares the `roots` capability names
    its workspace folders on request, so a server whose start directory serves
    nothing asks once the client says it is initialized, and again whenever
    the client says they changed, and serves the first folder a crapkit.toml
    claims. A call that arrives before the answer waits for it in the worker
    thread, never in the loop that reads the answer. A GitHub Copilot CLI
    session names its folder in its own record instead (copilot_workspace),
    which comes after the client's folders. A start given with --repo is
    `exact`: it is served or refused as named, and nothing replaces it."""

    def __init__(self, start: Path, *, plugin: bool = False, exact: bool = False):
        self.start, self.plugin, self.exact = start, plugin, exact
        self.capable = self.timed_out = False
        self.asked, self.pending, self.folders = 0, None, None
        self.answered = threading.Event()
        self.revision = SUPPORTED_PROTOCOLS[0]

    def greet(self, params: dict) -> None:
        capabilities = params.get("capabilities")
        self.capable = isinstance(capabilities, dict) and "roots" in capabilities
        self.revision = _negotiated(params)

    @property
    def structured(self) -> bool:
        """Whether this session's revision defines structuredContent and outputSchema."""
        return self.revision in _STRUCTURED_REVISIONS

    def run_cli(self, tool: dict, arguments: dict, repo, *, owner=None) -> dict:
        """A call's answer in the shape this session's revision defines:
        structuredContent is a 2025-06-18 field, and a client on an older one
        that serializes the whole result (Cline speaks 2024-11-05) carried the
        answer twice."""
        result = _run_cli(tool, arguments, repo, owner=owner)
        if self.structured:
            return result
        return {key: value for key, value in result.items() if key != "structuredContent"}

    def hint(self) -> str:
        """What the instructions add when nothing names the workspace: GitHub
        Copilot CLI starts a plugin's server in the plugin's install directory
        and declares no roots, and a session id whose record names no folder
        (a config directory set with --config-dir) names nothing either, the
        same test each tool result makes."""
        if self.plugin and not self.capable and not self._session_folder():
            return (" This server started in its plugin's install directory, not in your "
                    "workspace, and the client names no workspace folders: pass a `repo` "
                    "argument with the absolute path of the repo you want scored on every call.")
        return ""

    def claims_start(self) -> bool:
        return not self.plugin and (self.start / CONFIG_NAME).is_file()

    def ask(self) -> dict | None:
        """The roots/list request to send, or None when nothing needs it."""
        if not self.capable or self.exact or self.claims_start():
            return None
        self.asked += 1
        self.pending, self.folders, self.timed_out = f"crapkit-roots-{self.asked}", None, False
        self.answered.clear()
        return {"jsonrpc": "2.0", "id": self.pending, "method": "roots/list"}

    def take(self, message: dict) -> None:
        """The client's answer to the newest roots/list; any other response is
        dropped, since a response never gets a reply."""
        if self.pending is None or message.get("id") != self.pending:
            return None
        self.folders = [path for path in map(_folder_path, _uris(message.get("result"))) if path]
        self.pending, self.timed_out = None, False
        self.answered.set()
        return None

    def served(self):
        """The root a call runs at: a path, this session while the client's
        answer is still out, or None when nothing is measured."""
        if self.claims_start():
            return str(self.start)
        if self.exact:
            return None
        if self.pending is not None and not self.timed_out:
            return self
        return self._claimed_folder()

    def _session_folder(self) -> Path | None:
        return None if self.exact else copilot_workspace()

    def _named(self) -> list[Path]:
        """The client's workspace folders, then its Copilot CLI session's."""
        session = self._session_folder()
        return [*(self.folders or ()), *([session] if session else [])]

    def _claimed_folder(self) -> str | None:
        found = next(filter(None, map(_folder_root, self._named())), None)
        return str(found) if found else None

    def run(self, tool: dict, arguments: dict, owner=None) -> dict:
        """A call that arrived before the answer, in the worker thread."""
        deadline = time.monotonic() + ROOTS_SECONDS
        while not self.answered.wait(_ROOTS_SLICE):
            _cancelled(owner)
            self.timed_out = time.monotonic() >= deadline
            if self.timed_out:
                break
        root = self.served()
        return _run_cli(tool, arguments, root, owner=owner) if isinstance(root, str) else self.missing()

    def missing(self) -> dict:
        if self.plugin and not self._named():
            return _result(f"this crapkit MCP server started in {self.start}, the plugin's install "
                           "directory, not in your workspace, and the client names no workspace "
                           "folders. Pass this tool a `repo` argument with the absolute path of "
                           "the repo you want scored.", is_error=True)
        return _no_config_result(self._searched())

    def _searched(self) -> str:
        """Where the refusal says the server looked, in the order it looked: a
        plugin's install directory is never one of them."""
        places = ([] if self.plugin else [str(self.start)]) + self._folder_places()
        session = self._session_folder()
        if session:
            places.append(f"the folder the GitHub Copilot CLI session works in ({session})")
        return " or in ".join(places) + self._silence()

    def _folder_places(self) -> list[str]:
        if not self.folders:
            return []
        return [f"the workspace folders the client named ({', '.join(map(str, self.folders))})"]

    def _silence(self) -> str:
        if self.timed_out:
            return f", and the client did not name its workspace folders within {ROOTS_SECONDS} s"
        return ", and the client named no workspace folder" if self.folders == [] else ""


def _session(root) -> _Session:
    return root if isinstance(root, _Session) else _Session(root)


def _call_root(session: _Session, arguments: dict):
    """The root a call runs at, or None. A call's own `repo` argument is walked
    up to the nearest crapkit.toml; without one the session answers."""
    repo = arguments.get("repo")
    if not repo:
        return session.served()
    found = _config_root(repo)
    return str(found) if found else None


# Keys a client adds to every tool's input schema for its own use and then
# forwards with the call. Gemini CLI 0.61.0 adds `wait_for_previous`, a boolean
# its scheduler reads to order the calls of one turn. They are the named
# exceptions to ADR 0001's refusal of an undeclared key: the value is the
# client's, so the call runs as it would without it.
CLIENT_KEYS = frozenset({"wait_for_previous"})


def _own_arguments(arguments):
    """The call's arguments without the client's keys; anything but an object
    is left for the table to refuse."""
    if not isinstance(arguments, dict):
        return arguments
    return {key: value for key, value in arguments.items() if key not in CLIENT_KEYS}


def _upgraded_under_us(name: str) -> str | None:
    """The restart the caller needs when the package on disk is no longer the
    one this process loaded, else None.

    The server imports some modules only at its first tools/call: the Windows
    Job, the process family, and the measurement owner it starts with runpy
    from the package directory. After `pip install -U` those are the new
    release's files, and the old process answered JSON-RPC -32603 with a
    TypeError between two releases' signatures, which named no restart and
    looked random because a session that had already served a call kept
    working. Checked before anything is imported or spawned."""
    installed, loaded = upgraded_to(), _version()
    if installed is None:
        return None
    return (f"crapkit was upgraded from {loaded} to {installed} while this MCP server ran, and "
            f"the server still runs {loaded}'s code, which cannot load the new files. Restart the "
            f"crapkit MCP server (reconnect it in your client, or start a new session), then call "
            f"{name} again.")


def _table_refusal(tool: dict | None, name: str, arguments: dict) -> str | None:
    if tool is None:
        return _unknown_tool(name)
    return _argument_error(tool, arguments)


def _missing(session: _Session, repo: str | None) -> dict:
    return _no_config_result(repo) if repo else session.missing()


def _call_tool(root, name: str, arguments: dict, run_cli=None) -> dict:
    """The package on disk, then name lookup, then the arguments against the
    table, then the repo the call names, then the run. Every refusal is decided
    before a CLI spawns. `root` is the session, or a plain start directory."""
    tool, arguments = _tool_named(name), _own_arguments(arguments)
    refusal = _upgraded_under_us(name) or _table_refusal(tool, name, arguments)
    if refusal:
        return _result(refusal, is_error=True)
    session = _session(root)
    served = _call_root(session, arguments)
    if served is None:
        return _missing(session, arguments.get("repo"))
    return (run_cli or _run_cli)(tool, arguments, served)


# What a connected model needs before its first call, in the one field the
# protocol reserves for it. The ten error results a model would otherwise
# collect from an unmeasured repo teach the same thing ten times, slower.
_INSTRUCTIONS = (
    "crapkit scores every function as ccn^2 x (1 - coverage)^3 + ccn; these twelve tools "
    "read scores and source without running test suites or editing source files. Calls can "
    "write caches, initialize or migrate the snapshot store, and fill rollups. "
    "get_next_item takes no claim; check_gate runs rescore and records no verification run. "
    "They need a repo measured once (crapkit init, then crapkit "
    "coverage); an unmeasured repo answers with a one-line pointer instead of data. Start "
    "with get_next_item for one function to fix, list_worklist for the whole ranking, "
    "get_function_brief for everything about one function, and check_gate after an edit to "
    "learn whether the file clears rescore --gate, which is stricter than the commit hook.")


def _negotiated(params: dict) -> str:
    """The client's revision when this server implements it, else the newest it
    does; the spec leaves proceeding or disconnecting to the client from there."""
    offered = params.get("protocolVersion")
    return offered if offered in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]


def _structured(result: dict, full=lambda: "") -> dict:
    """Attach the parsed object beside the text when the text is a JSON object,
    both cut to ANSWER_CHARS when the object is longer.

    The --json commands print for machines; a client on the 2025-06-18 revision
    reads structuredContent directly. Prose, JSON arrays and error text stay
    text-only rather than getting wrapped into shapes the tools never promised.
    An error whose text is a JSON object, such as a failing doctor's report,
    stays text-only and is cut the same way.
    """
    parsed = _json_object(result["content"][0]["text"])
    if parsed is None:
        return result
    if _text_chars(parsed) > ANSWER_CHARS:
        parsed = _budgeted(parsed, full)
        result = _json_result(parsed, is_error=result["isError"])
    return result if result["isError"] else {**result, "structuredContent": parsed}


def _json_object(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_result(payload: dict, *, is_error: bool) -> dict:
    """A result whose text is `payload` printed the way the CLI prints it."""
    return _result(json.dumps(payload, sort_keys=True) + "\n", is_error=is_error)


def _respond(msg_id, result=None, error=None) -> dict:
    resp = {"jsonrpc": "2.0", "id": msg_id}
    resp["error" if error else "result"] = error if error else result
    return resp


def _initialize_result(params, session: _Session) -> dict:
    session.greet(params)
    return {"protocolVersion": _negotiated(params),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "crapkit", "version": _version()},
            "instructions": _INSTRUCTIONS + session.hint()}


# The methods that need no repo, keyed as the wire spells them. ping answers
# the empty object the spec asks for, so a client's keepalive is not -32601.
_METHODS = {"initialize": _initialize_result,
            "tools/list": lambda params, session: {"tools": tool_listing(session.structured)},
            "ping": lambda params, session: {}}

# The notifications on which a server whose start directory serves nothing
# asks the client for its workspace folders.
_ASK_ROOTS_ON = ("notifications/initialized", "notifications/roots/list_changed")


def _tools_call(root, params: dict, run_cli=None) -> dict:
    """Null or absent arguments read as none given, the rule `_params` keeps
    for params. An empty string, 0, false and [] are values of the wrong type,
    so they reach `_argument_error`: read as none given, a tool with no
    required argument ran its CLI on them."""
    arguments = params.get("arguments")
    return _call_tool(root, params.get("name", ""), {} if arguments is None else arguments,
                      run_cli)


def _method_handler(method):
    """The handler for a method name, or None: a name that is not a string is
    an unknown method, never a lookup that raises."""
    return _METHODS.get(method) if isinstance(method, str) else None


# The methods that read params by name, and what the object must hold. ping and
# tools/list read none, so they answer whatever params are.
_PARAMS_HOLD = {"initialize": "carrying protocolVersion",
                "tools/call": "naming the tool and its arguments"}
def _params(msg: dict):
    """The request's params, with null or absent read as the empty object."""
    params = msg.get("params")
    return {} if params is None else params


def _invalid_params(method, params) -> dict | None:
    """JSON-RPC -32602 for params that are not an object on a method that reads
    them by name, or None. Arguments that are not an object belong to a named
    tool and answer in its words (ADR 0001); such params name no tool, so the
    protocol answers. By-position params are valid JSON-RPC and not MCP."""
    holds = _PARAMS_HOLD.get(method) if isinstance(method, str) else None
    if holds is None or isinstance(params, dict):
        return None
    return {"code": -32602,
            "message": f"params must be an object {holds} (got {json_kind(params)})"}


def _notified(session: _Session, method) -> dict | None:
    """A notification gets no reply. Two of them are when the server asks the
    client for its roots, and that request is what goes out instead."""
    return session.ask() if method in _ASK_ROOTS_ON else None


def _handler(method, run_cli):
    if method == "tools/call":
        return lambda params, session: _tools_call(session, params, run_cli)
    return _method_handler(method)


def _request(session: _Session, msg: dict, run_cli=None) -> dict:
    """An unknown method is -32601. `params` that is not an object, on a method
    that reads it by name, is -32602 (`_invalid_params`): malformed JSON-RPC
    rather than a tool's arguments (ADR 0001), which the handler would
    otherwise have read as an object and answered -32603."""
    method, params = msg["method"], _params(msg)
    invalid = _invalid_params(method, params)
    if invalid:
        return _respond(msg["id"], error=invalid)
    handler = _handler(method, run_cli)
    if handler is None:
        return _respond(msg["id"], error={"code": -32601,
                                          "message": f"unknown method {method!r}"})
    return _respond(msg["id"], handler(params, session))


def _handle(root, msg: dict, run_cli=None) -> dict | None:
    """A response to the server's own request, a notification, or a request.
    Only a request gets a reply; `root` is the session, or a plain start
    directory."""
    session = _session(root)
    if "method" not in msg:
        return _without_method(session, msg)
    if "id" not in msg:
        return _notified(session, msg["method"])
    return _request(session, msg, run_cli)


def _without_method(session: _Session, msg: dict) -> dict | None:
    """A response is taken and gets no reply, as does a message with no id.
    A message with an id and no result or error is neither: the client waits
    on that id, so it gets the -32600 JSON-RPC gives an invalid request."""
    if "result" in msg or "error" in msg or "id" not in msg:
        return session.take(msg)
    return _respond(msg["id"], error={"code": -32600, "message": (
        "invalid request: a message with an id needs a method, or a result or an error when "
        "it answers the server's request")})


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


def _reply(root, msg: dict, run_cli=None) -> dict | None:
    """The reply to one message. An exception escaping a handler becomes the
    JSON-RPC -32603 reply instead of the end of the session; a notification or
    a response gets no reply even then."""
    try:
        return _handle(root, msg, run_cli)
    except Exception as exc:  # noqa: BLE001 - the loop must outlive any one call
        if "id" not in msg or "method" not in msg:
            return None
        return _respond(msg["id"], error={"code": -32603,
                                          "message": f"{type(exc).__name__}: {exc}"})


def serve(root: Path, *, plugin: bool = False, exact: bool = False) -> int:
    """Newline-delimited JSON-RPC; EOF cancels active work and closes the session.
    `plugin` says the client started the server in its plugin's install
    directory, which serves nothing whatever lies above it; `exact` says
    `root` came from --repo, which no folder the client names replaces."""
    from ._mcp_stdio import serve as stdio
    session = _Session(root, plugin=plugin, exact=exact)
    return stdio(sys.stdin, sys.stdout, lambda msg, run: _reply(session, msg, run), session.run_cli)
