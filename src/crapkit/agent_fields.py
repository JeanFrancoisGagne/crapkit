"""Every field of every agent JSON payload, each declared once.

A payload is the object one command prints on stdout: a `--json` command's,
`next-item`'s, and the error object any `--json` command prints when it dies.
PAYLOADS holds each one as the JSON schema properties of that object: every key,
its JSON types (`null` among them only where it may be null) and one line saying
what it means. The MCP tools serve these objects as their outputSchema, so a
tool and the command it runs describe a field one way. FIELDS lists the same
declarations one row per field: dots into objects, `[]` into the items of an
array, `*` for any key of a map.

JSON schema 1 keeps every existing field's meaning, and a release may add
fields. ADDED names each field 0.8.1 adds, and a payload takes such a field's
entry from there (schema_of). tests/unit/test_agent_fields.py prints every
payload over a real repo and fails on a key no declaration names, on a value
whose type its declaration does not allow, a null included, and on an added
field docs/agent-json.md does not name.
"""
from __future__ import annotations

from typing import Iterator, NamedTuple

# The MCP tool that returns each payload, for the payloads a tool returns.
MCP_TOOLS = {"worklist --json": "list_worklist", "next-item": "get_next_item",
             "brief --json": "get_function_brief", "ratchet report --json": "get_ratchet_report",
             "rescore --gate --json": "check_gate", "doctor --json": "check_config",
             "runs --json": "list_runs", "trend --json": "get_trend",
             "explain --json": "get_function_history", "coupling --json": "list_coupled_files",
             "duplication --json": "list_duplicate_functions", "claims --json": "list_claims"}


_EMPTY_SCOPES = ("each declared scope that claims no file, or whose every file no reader could "
                 "read, and how many files it claims (0 when it claims none); a scope whose "
                 "readable files hold no function is not listed")
_UNREADABLE_NAMES = ("tracked or staged files no scope takes whose names git gives in bytes that "
                     "are not UTF-8, left out of the run, each such byte spelled \\xNN; [] when "
                     "every name is UTF-8")


class AgentField(NamedTuple):
    payload: str
    key: str
    types: tuple[str, ...]
    description: str

    @property
    def nullable(self) -> bool:
        return "null" in self.types

    def schema(self) -> dict:
        """The JSON schema fragment a payload declares for it."""
        kind = self.types[0] if len(self.types) == 1 else self.types
        return {"type": kind, "description": self.description}


_SHALLOW = ("true when this checkout is a shallow clone: {counts} count only the commits the "
            "clone holds; set fetch-depth: 0 on the checkout or run git fetch --unshallow for "
            "the real counts")
_UNMEASURED = ("true when no measurement stands behind cov: flag no-lane (no lane covers the "
               "scope) or cc-only (the scope asks for no coverage). cov 0.0 and "
               "est_uncovered_paths are then stand-ins, not a count of paths no test walks")
_UNREAD = ("changed files no reader could read, so none of their functions was judged; any "
           "entry fails the gate (exit 6, or for a file whose name is not UTF-8 the exit-3 "
           "refusal, which check_gate answers as this verdict)")
_UNREAD_PATH = "repo-relative path of the file, each byte that is not UTF-8 spelled \\xNN"
_UNREAD_REASON = "the reader's refusal, naming the line and what to change"
_SCORED_CHANGES = ("how many files the run scored hold other content now than the run "
                   "recorded, deleted files included; 0 means the numbers describe the files on "
                   "disk; null when crapkit cannot compare: the run recorded no content (crapkit "
                   "0.8.0 or older wrote it) or git failed reading the tree; treat null like any "
                   "count above 0 and run commands.refresh")
_REFRESH = ("crapkit coverage --reuse-unchanged: the cheapest run that brings scored_changes to 0 "
            "and clears stale")
_UNREAD_DIRTY = "true when the file has uncommitted edits or is untracked"
_REFUSED_NAMES = ("every file the command refused because its name is not UTF-8 (exit 3), "
                  "where the message names the first and counts the rest")
# The object any --json command prints on stdout when it dies before its own payload.
ERROR_OBJECT = "--json error object"
# `crapkit --version --json`: the build a crapkit runs from.
VERSION = "--version --json"


def _unread_fields(payload: str, key: str, dirty: str,
                   listed: str = _UNREAD) -> tuple[AgentField, ...]:
    return (AgentField(payload, key, ("array",), listed),
            AgentField(payload, f"{key}[].path", ("string",), _UNREAD_PATH),
            AgentField(payload, f"{key}[].reason", ("string",), _UNREAD_REASON),
            AgentField(payload, f"{key}[].dirty", ("boolean",), dirty))


_ADDED = (
    AgentField("worklist --json", "shallow", ("boolean",),
               _SHALLOW.format(counts="commits, authors and the churn the ranking reads")),
    AgentField("next-item", "shallow", ("boolean",),
               _SHALLOW.format(counts="commits, authors and the churn the ranking reads")),
    AgentField("next-item", "item.unmeasured", ("boolean",), _UNMEASURED),
    AgentField("brief --json", "shallow", ("boolean",),
               _SHALLOW.format(counts="churn and gate_rule.mark_age_days")),
    AgentField("brief --json", "unmeasured", ("boolean",), _UNMEASURED),
    *(AgentField(payload, "scored_changes", ("integer", "null"), _SCORED_CHANGES)
      for payload in ("worklist --json", "next-item", "brief --json")),
    *(AgentField(payload, "commands.refresh", ("string",), _REFRESH)
      for payload in ("worklist --json", "next-item")),
    AgentField("ratchet report --json", "shallow", ("boolean",),
               _SHALLOW.format(counts="ages and repayments") + ", and crapkit ratchet report "
               "--enforce refuses to judge the debt policy there (exit 4)"),
    AgentField("rescore --gate --json", "functions[].unmeasured", ("boolean",),
               "true when no measurement stands behind cov: the baseline run holds no row this "
               "function joins by name (it was added or renamed since), or its scope has no lane "
               "or asks for none. cov 0.0 is then a stand-in"),
    *_unread_fields("rescore --gate --json", "gate.unread_files",
                    "always true here: the gate judges the working tree's changes since HEAD"),
    *_unread_fields("verify --json", "unread_files", _UNREAD_DIRTY),
    *_unread_fields(ERROR_OBJECT, "error.unread_files", _UNREAD_DIRTY, _REFUSED_NAMES),
    AgentField("verify --json", "lanes_without_results", ("array",),
               "lanes that declare no results_artifact, so nothing checked their tests for new "
               "failures"),
    AgentField("verify --json", "lanes_without_baseline_results", ("array",),
               "lanes with a new failure whose baseline, and every trusted run behind it, "
               "recorded no failure list: those failures may predate the change"),
    AgentField("verify --json", "changed_paths", ("array",),
               "the files behind changed_files, sorted: every file the diff since the baseline "
               "commit changed, uncommitted edits included"),
    AgentField("verify --json", "untracked_in_scope", ("array",),
               "source files inside a scope that git does not track; verify judges git-tracked "
               "files only, so these were not judged; git add them to judge them"),
    AgentField("verify --json", "ratchet_source", ("string",),
               "which marks verify judged against: tree, the marks file as read, or committed, "
               "the newest marks committed since the baseline when that file is missing or blank"),
    AgentField("verify --json", "ratchet_source_commit", ("string", "null"),
               "the commit whose marks verify judged when ratchet_source is committed; null "
               "for tree"),
    AgentField("verify --json", "ratchet_source_sha256", ("string", "null"),
               "digest of the marks verify judged against; null when there were no marks"),
    AgentField("verify --json", "findings", ("array",),
               "every finding once: the kinds in exit order (unreadable_name, gate_violation, "
               "unread_file, ratchet_regression, new_failure, diff_uncovered, overridden), each "
               "kind's items in the order its own list gives them; diff_uncovered lists at most "
               "50, and counts.diff_uncovered_count counts them all"),
    AgentField("verify --json", "findings[].kind", ("string",),
               "unreadable_name, gate_violation, unread_file, ratchet_regression, new_failure, "
               "diff_uncovered or overridden; the fields beside the common six are the kind's own"),
    AgentField("verify --json", "findings[].fails", ("boolean",),
               "true when this item fails the verdict: its kind fires an exit code and the "
               "verdict holds it; a diff_uncovered item fails only past diff_uncovered_max, and "
               "an overridden one never does"),
    AgentField("verify --json", "findings[].exit_code", ("integer", "null"),
               "the exit code the item fires when it fails: 3 unreadable_name, 6 gate_violation "
               "and unread_file, 7 ratchet_regression, 8 new_failure, 9 diff_uncovered; null when "
               "fails is false. The first failing item's exit_code is verify's exit"),
    AgentField("verify --json", "findings[].overridable", ("boolean",),
               "true for a gate_violation, the one kind an --override can grant; an override "
               "still grants nothing while an item of a kind that refuses one is present "
               "(unreadable_name, unread_file, ratchet_regression, new_failure)"),
    AgentField("verify --json", "findings[].dirty", ("boolean",),
               "true when the item's file has uncommitted edits or git does not track it; for a "
               "new_failure, when its test id names such a file"),
    AgentField("verify --json", "findings[].rule", ("string",),
               "the label of the item's rule: complexity gate (gate_violation, unread_file), "
               "ratchet regressions, new test failures, diff-coverage ceiling, unreadable name or "
               "override"),
    AgentField("verify --json", "counts", ("object",),
               "the numbers beside findings, which its items do not carry"),
    AgentField("verify --json", "counts.diff_uncovered_count", ("integer",),
               "changed lines no test ran, every one, where findings lists the first 50"),
    AgentField("verify --json", "counts.diff_uncovered_max", ("integer", "null"),
               "the ceiling diff_uncovered_count is judged against (exit 9); null when the repo "
               "set none"),
    AgentField("coverage --json", "empty_scopes", ("object",),
               _EMPTY_SCOPES),
    AgentField("inventory --json", "empty_scopes", ("object",),
               _EMPTY_SCOPES),
    *(AgentField(payload, "unreadable_names", ("array",), _UNREADABLE_NAMES)
      for payload in ("inventory --json", "coverage --json", "verify --json")),
    AgentField("mutate --json", "timed_out", ("integer",),
               "mutants whose suite ran past mutation_timeout_seconds, a count inside killed"),
    AgentField("mutate --json", "no_verdict", ("integer",),
               "mutants whose suite ran no test (exit 5), a count inside killed"),
    AgentField(VERSION, "version", ("string",),
               "the installed distribution's version, the number the text line prints"),
    AgentField(VERSION, "commit", ("string", "null"),
               "the commit, full sha, this crapkit was built from: read from git for a source "
               "checkout or an editable install, and from the stamp the build wrote for an "
               "installed wheel; null for a build made with no git checkout at hand and for a "
               "checkout git cannot read"),
    AgentField(VERSION, "dirty", ("boolean", "null"),
               "true when that checkout holds changes the commit does not (for an installed "
               "wheel, held them when it was built): staged or unstaged edits, or a file git "
               "neither tracks nor ignores; null when commit is null"),
    AgentField(VERSION, "analysis_version", ("integer",),
               "the analysis semantics version, the number doctor --json reports and the "
               "ratchet's metric stamp carries"),
    AgentField("doctor --json", "lanes[].refusal", ("string", "null"),
               "why --reuse-artifacts will not score the lane's artifact on disk: the lane's "
               "last attempt wrote no artifact and the file predates it, or "
               ".crapkit/artifacts.json cannot be read; null when reuse would score it"),
    AgentField("doctor --json", "lanes[].toolchain", ("object",),
               "the runner the lane runs and where crapkit read it, from the lane's command, "
               "else the package.json script it runs, else devDependencies; no config key "
               "names it"),
    AgentField("doctor --json", "lanes[].toolchain.name", ("string", "null"),
               "the runner: pytest, vitest, jest, bun, deno, cargo llvm-cov, go test or c8; "
               "null when crapkit knows none the lane runs, or the lane runs two"),
    AgentField("doctor --json", "lanes[].toolchain.source", ("string", "null"),
               "where crapkit read the runner: command (the lane's command names it), script "
               "(the package.json script the command runs names it) or package.json (only "
               "devDependencies name it, so no runner check keys on it); null when name is "
               "null"),
)


def _copied(payload: str, keys: tuple[str, ...], to: str, prefix: str = "") -> tuple:
    """The added fields of PAYLOAD named by KEYS, as TO prints them under PREFIX:
    the variants of a payload carry the fields it adds with the same meaning."""
    return tuple(AgentField(to, prefix + f.key, f.types, f.description) for f in _ADDED
                 if f.payload == payload and f.key in keys)


_RANKED = ("shallow", "scored_changes", "commands.refresh")
ADDED = (
    *_ADDED,
    AgentField("next-item", "items[].unmeasured", ("boolean",), _UNMEASURED),
    *_copied("worklist --json", _RANKED, "worklist --batches --json"),
    *_copied("brief --json", ("shallow", "scored_changes"), "brief --batch"),
    *_copied("worklist --json", ("commands.refresh",), "brief --batch"),
    *_copied("brief --json", ("shallow", "scored_changes", "unmeasured"), "brief --batch",
             "packets[]."),
    *_copied("rescore --gate --json", ("functions[].unmeasured",), "rescore --json"),
)


def added_field(payload: str, key: str) -> AgentField:
    (found,) = [f for f in ADDED if (f.payload, f.key) == (payload, key)]
    return found


def schema_of(payload: str, key: str) -> dict:
    """The schema fragment of one added field, from its one declaration."""
    return added_field(payload, key).schema()


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

# The field definitions every row schema shares, said once so no tool's schema
# drifts from docs/agent-json.md (tests/accuracy/definitions reads both).
_COV_DESCRIPTION = ("branch coverage inside the span, 0.0 to 1.0; statement coverage when the "
                    "span has no branches, and invoked-or-not (1.0 or 0.0) when it has no "
                    "statements; Python and/or add to ccn but coverage.py records no branch arc "
                    "for them; 0.0 on an untested, excluded, no-lane or cc-only row")
_CRAP_DESCRIPTION = "ccn^2 x (1 - cov)^3 + ccn, or ccn on a cc-only or excluded row"
_CEILING_DESCRIPTION = "the highest CRAP a function may carry, and so also the highest ccn"
_UNCOVERED_PATHS_DESCRIPTION = "(1 - cov) x ccn rounded half to even, so 2.5 reads 2"


def _unread_files_schema(payload: str, key: str) -> dict:
    """The one shape an unread-file finding has in every payload that carries it."""
    return {**schema_of(payload, key), "items": {
        "type": "object", "description": "one changed file the gate refused unread",
        "properties": {name: schema_of(payload, f"{key}[].{name}")
                       for name in ("path", "reason", "dirty")}}}


# The fields 0.8.1 adds take their schema from the one declaration of them.
_SHALLOW_CHURN = schema_of("worklist --json", "shallow")
_UNMEASURED_SCHEMA = schema_of("next-item", "item.unmeasured")

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
_REFRESH_SCHEMA = schema_of("next-item", "commands.refresh")


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
 'cov': {'type': 'number', 'description': _COV_DESCRIPTION},
 'flag': {'type': 'string',
          'description': 'measured, untested, excluded, no-lane or cc-only: whether a lane '
                         'artifact could measure this span',
          'enum': ('measured', 'untested', 'excluded', 'no-lane', 'cc-only')},
 'crap': {'type': 'number', 'description': 'the score: ' + _CRAP_DESCRIPTION},
 'remedy': _REMEDY,
 'target': {'type': 'integer',
            'description': "this scope's effective ceiling: " + _CEILING_DESCRIPTION},
 'commits': {'type': 'integer', 'description': 'commits touching the file in the churn window'},
 'authors': {'type': 'integer', 'description': 'distinct authors of those commits'},
 'est_splits': {'type': 'integer',
                'description': '0 when ccn <= target, else ceil(ccn / target): roughly how many '
                               'functions this must become'},
 'est_uncovered_paths': {'type': 'integer',
                         'description': _UNCOVERED_PATHS_DESCRIPTION + ': decision paths no '
                                        'test walks'},
 'uncovered_lines': {'type': ('array', 'null'),
                     'description': 'line numbers no test ran; [] when the span is fully covered; '
                                    'null when no artifact could answer, then uncovered_lines_note '
                                    'says why',
                     'items': {'type': 'integer'}},
 'uncovered_lines_note': {'type': 'string',
                          'description': 'present only when uncovered_lines is null: the reason '
                                         'and the move (stale artifact, no test imports the file, '
                                         'coverage_optional scope)'},
 'unmeasured': _UNMEASURED_SCHEMA}

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
                         'description': 'measured, untested, excluded, no-lane or cc-only; null '
                                        'on an inventory-only run',
                         'enum': ('measured', 'untested', 'excluded', 'no-lane', 'cc-only', None)},
                'remedy': {'type': ('string', 'null'),
                           'description': 'decompose, split-lines, add-tests or ok, as the run '
                                          'judged it; null on an inventory-only run. A row a '
                                          'lane measures reaches get_next_item unless its remedy '
                                          'is ok. After a crapkit.toml ceiling edit no run has '
                                          "scored, get_next_item's own remedy decides that, "
                                          'and it can differ from this one',
                           'enum': (*_REMEDIES, None)},
                'crap': {'type': ('number', 'null'),
                         'description': ('the score from the ranked run, ' + _CRAP_DESCRIPTION
                                         + '; null on an inventory-only run')},
                'cov': {'type': ('number', 'null'),
                        'description': _COV_DESCRIPTION + '; null on an inventory-only run'},
                'ratchet_mark': {'type': ('number', 'null'),
                                 'description': 'the committed ratchet mark on this function, read '
                                                'under its own ratchet key; null when it carries '
                                                'none or the repo has no marks file'},
                'occurrence': _OCCURRENCE,
                'handle': {'type': 'string',
                           'description': 'short name form: the bare identifier, or '
                                          '(anonymous)#N for a function lizard could not name; '
                                          'pass it to get_function_brief as name'}}}


_NEXT_ITEM = {
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
                "remedy ok: the queue is finished")}}}}


_WORKLIST = {
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
        "description": ("months of git history the churn weights cover, counted "
        "back from the commit date of HEAD")},
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
        "items": _WORKLIST_ITEM}}


_RUNS = {
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
                    "description": "UTC timestamp, ISO 8601"}}}}}


_TREND = {
    "schema": {
        "type": "integer",
        "description": "payload schema version, 1"},
    "target": {
        "type": "integer",
        "description": ("the [crapkit] target: the default ceiling, "
        + _CEILING_DESCRIPTION + ", that every scope inherits unless it sets its own")},
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
                    "description": "exact sum of every function's crap (math.fsum), two decimals"},
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
                                "description": "exact sum of crap over the scope (math.fsum), two decimals"},
                            "grade": {
                                "type": "string",
                                "description": ("A+ at zero over_target, then A under 2% "
                                "over, B under 5%, C under 10%, D under 20%, "
                                "else F"),
                                "enum": ("A+", "A", "B", "C", "D", "F")}}}}}}}}


_BRIEF = {
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
        "description": ("this scope's effective ceiling: " + _CEILING_DESCRIPTION
        + "; the same value as gate_rule.ceiling")},
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
                "description": _COV_DESCRIPTION},
            "flag": {
                "type": "string",
                "description": ("measured, untested, excluded, no-lane or cc-only: whether a "
                "lane artifact could measure this span"),
                "enum": ("measured", "untested", "excluded", "no-lane", "cc-only")},
            "crap": {
                "type": "number",
                "description": "the score: " + _CRAP_DESCRIPTION},
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
        "description": _UNCOVERED_PATHS_DESCRIPTION},
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
                    "description": "score: " + _CRAP_DESCRIPTION},
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
                "description": "exact sum of crap over the file (math.fsum), two decimals"}}},
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
            "refresh": _REFRESH_SCHEMA,
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
                    "description": ("true when the twin and this function nest in one file, "
                    "one defined inside the other")}}}}}


_EXPLAIN = {
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
                                "description": (_COV_DESCRIPTION
                                                + "; null on an inventory run")},
                            "crap": {
                                "type": ("number", "null"),
                                "description": ("score: " + _CRAP_DESCRIPTION
                                                + "; null on an inventory run")},
                            "flag": {
                                "type": ("string", "null"),
                                "description": ("measured, untested, excluded, no-lane or "
                                "cc-only; null on an inventory-only run"),
                                "enum": ("measured", "untested", "excluded", "no-lane",
                                         "cc-only", None)}}}},
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
                    "a --show-contexts report)")}}}}}


_DOCTOR = {
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
                "refusal": schema_of("doctor --json", "lanes[].refusal"),
                "seconds": {
                    "type": ("number", "null"),
                    "description": "how long the lane took last time, null when it never ran"},
                "toolchain": {
                    **schema_of("doctor --json", "lanes[].toolchain"),
                    "properties": {
                        "name": schema_of("doctor --json", "lanes[].toolchain.name"),
                        "source": schema_of("doctor --json", "lanes[].toolchain.source")}}}}}}


_COUPLING = {
    "schema": {
        "type": "integer",
        "description": "payload schema version, 1"},
    "window_months": {
        "type": "integer",
        "description": ("months of git history the pairs were counted over, back from "
        "the commit date of HEAD (the config's churn_window_months)")},
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
                    "commits(b), 0 to 1, four decimals")}}}}}


_DUPLICATION = {
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
                                "description": "non-comment lines"}}}}}}}}


_RATCHET_REPORT = {
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
    "shallow": schema_of("ratchet report --json", "shallow")}


_RESCORE_GATE = {
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
                    "description": "from the baseline run: " + _COV_DESCRIPTION},
                "flag": {
                    "type": "string",
                    "description": ("measured, untested, excluded, no-lane or cc-only: whether a "
                    "lane artifact could measure this span"),
                    "enum": ("measured", "untested", "excluded", "no-lane", "cc-only")},
                "crap": {
                    "type": "number",
                    "description": ("score from fresh ccn and baseline cov: "
                                    + _CRAP_DESCRIPTION)},
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
                            "description": "from the baseline run: " + _COV_DESCRIPTION},
                        "crap": {
                            "type": "number",
                            "description": "score: " + _CRAP_DESCRIPTION},
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
                                                 "gate.unread_files")}}}


_CLAIMS = {
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
                    "description": "UTC timestamp, ISO 8601"}}}}}


# The payloads only the CLI prints, and the CLI variants of the ones above.
_SCHEMA = {"type": "integer", "description": "payload schema version, 1"}


def _strings(description: str) -> dict:
    return {"type": "array", "description": description, "items": {"type": "string"}}


def _added_strings(payload: str, key: str) -> dict:
    """An added field whose value is a list of strings."""
    return {**schema_of(payload, key), "items": {"type": "string"}}


def _run_summary(payload: str) -> dict:
    """What inventory --json prints, and coverage --json prints under the same keys."""
    return {
        "schema": _SCHEMA,
        "run_id": {"type": "integer", "description": "the run this command wrote"},
        "commit": {"type": "string", "description": "the commit the run measured, full sha"},
        "db": {"type": "string", "description": "absolute path of the snapshot store"},
        "files": {"type": "integer", "description": "source files in the scored corpus"},
        "functions": {"type": "integer", "description": "functions in the scored corpus"},
        "cache_hits": {"type": "integer",
                       "description": "files served from the content-hash analysis cache"},
        "skipped_max_bytes": {"type": "integer",
                              "description": "files [exclude] max_file_bytes left out"},
        "empty_scopes": {**schema_of(payload, "empty_scopes"), "additionalProperties": {
            "type": "integer",
            "description": "the files the scope claims, 0 when it claims none"}},
        "unreadable_names": _added_strings(payload, "unreadable_names")}


def _count(description: str) -> dict:
    return {"type": "integer", "description": description}


_INVENTORY = _run_summary("inventory --json")

_COVERAGE = {
    **_run_summary("coverage --json"),
    "kind": {"type": "string", "enum": ("coverage", "partial"),
             "description": ("coverage for a full run, partial when a lane was skipped (--lane) "
                             "or failed; a partial run is never a baseline")},
    "measured": _count("functions flagged measured; the five flag counts sum to functions"),
    "untested": _count("functions flagged untested"),
    "excluded": _count("functions flagged excluded: a lane's artifact measured the file and was "
                       "told to leave the function out"),
    "no_lane": _count("functions flagged no-lane: no lane covers their scope"),
    "cc_only": _count("functions flagged cc-only: their scope asks for no coverage"),
    "over_target": _count("functions over their scope's ceiling, counted over the scopes this "
                          "run measured"),
    "crap_load": {"type": "number",
                  "description": ("sum of crap over the functions over_target is counted over, "
                                  "two decimals")},
    "grade": {"type": "string", "enum": ("A+", "A", "B", "C", "D", "F"),
              "description": ("the letter for over-ceiling density over the same functions; A+ "
                              "only at zero")},
    "by_scope": {
        "type": "object", "description": "scope name to that scope's totals and grade",
        "additionalProperties": {
            "type": "object", "description": "one scope's totals",
            "properties": {
                "functions": _count("functions scored in the scope"),
                "over_target": _count("of those, over the scope's ceiling"),
                "crap_load": {"type": "number", "description": "sum of crap over the scope"},
                "grade": {"type": "string", "description": "the scope's letter"}}}},
    "lanes": {
        "type": "object", "description": "lane name to the provenance of each lane that succeeded",
        "additionalProperties": {
            "type": "object", "description": "what the lane's artifacts were",
            "properties": {
                "artifact_sha256": {"type": "string",
                                    "description": "digest of the coverage artifact read"},
                "exit_code": {"type": ("integer", "null"),
                              "description": "the command's exit code; null when the artifact "
                                             "was reused"},
                "parser": {"type": "string", "description": "the artifact parser"},
                "scopes": _strings("the scopes the lane measures"),
                "results_artifact_sha256": {
                    "type": "string",
                    "description": "digest of the junit read; present when the lane declares a "
                                   "results_artifact"},
                "failures": _strings("the failing test ids the junit names; present with "
                                     "results_artifact_sha256"),
                "tests_total": _count("tests the junit counts; present with "
                                      "results_artifact_sha256"),
                "tests_skipped": _count("tests it counts skipped; present with "
                                        "results_artifact_sha256"),
                "rerun_reason": {"type": "string",
                                 "description": ("under --reuse-unchanged: empty when the "
                                                 "artifact was reused, else why the lane "
                                                 "reran")}}}},
    "lane_failures": {
        "type": "object",
        "description": ("lane name to failure text for each lane that produced no artifact or "
                        "one reaching none of its scopes' paths; non-empty makes the run partial"),
        "additionalProperties": {"type": "string", "description": "the failure, plain text"}},
    "unmeasured_scopes": _strings("scopes a declared lane measures that no succeeding lane "
                                  "reached this run, in declaration order; [] on a full run"),
    "ceilings": {
        "type": "object",
        "description": ("the ceilings in force: default (the [crapkit] target) and each scope "
                        "whose own target differs from it"),
        "additionalProperties": {"type": "integer", "description": "the ceiling"}}}

_FINDING_PATH = {"type": "string", "description": "repo-relative path"}
_FINDING_DIRTY = {"type": "boolean",
                  "description": "true when the finding's file has uncommitted tracked edits"}
_GATE_VIOLATION = {
    "type": "object", "description": "one changed function over its ceiling no mark pardons",
    "properties": {
        "path": _FINDING_PATH,
        "long_name": {"type": "string", "description": "the function's long name"},
        "start": {"type": "integer", "description": "first line"},
        "ccn": {"type": "integer", "description": "the complexity the gate judged"},
        "cov": {"type": "number", "description": "coverage this run measured, 0.0 to 1.0"},
        "crap": {"type": "number", "description": "the score"},
        "remedy": _REMEDY,
        "dirty": _FINDING_DIRTY,
        "key_name": {"type": "string",
                     "description": ("the ratchet key: long_name, or long_name#2 for the second "
                                     "function the file gives that name")}}}

# Each kind's own fields on a findings item, beside the six every item carries.
_FINDING_FIELDS = {
    "path": {"type": "string",
             "description": ("repo-relative path of the item's file, each byte that is not UTF-8 "
                             "spelled \\xNN; every kind but new_failure")},
    "scope": {"type": "string",
              "description": "unreadable_name: the declared scope that takes the file"},
    "reason": {"type": "string",
               "description": ("unread_file: the reader's refusal, naming the line and what to "
                               "change; unreadable_name: the sentence naming the file, its scope "
                               "and the git mv rename to a UTF-8 name")},
    **{key: _GATE_VIOLATION["properties"][key]
       for key in ("long_name", "start", "ccn", "cov", "crap", "remedy", "key_name")},
    "recorded": {"type": "number", "description": "ratchet_regression: the mark"},
    "fresh_crap": {"type": "number",
                   "description": "ratchet_regression: the score this run measured"},
    "test": {"type": "string",
             "description": "new_failure: the test id that fails now and passed in the baseline"},
    "line": {"type": "integer", "description": "diff_uncovered: the changed line no test ran"}}
_FINDING = {
    "type": "object",
    "description": ("one finding: kind, fails, exit_code, overridable, dirty and rule, then its "
                    "kind's own fields (gate_violation and overridden: path, long_name, start, "
                    "ccn, cov, crap, remedy, key_name; unread_file: path, reason; "
                    "ratchet_regression: path, long_name, recorded, fresh_crap; new_failure: "
                    "test; diff_uncovered: path, line; unreadable_name: path, scope, reason)"),
    "properties": {
        **{key: schema_of("verify --json", f"findings[].{key}")
           for key in ("kind", "fails", "exit_code", "overridable", "dirty", "rule")},
        **_FINDING_FIELDS}}

_VERIFY = {
    "schema": _SCHEMA,
    "ok": {"type": "boolean",
           "description": ("the verdict after allowed overrides and flake retests; it agrees "
                           "with the stored run and the exit code")},
    "run_id": {"type": ("integer", "null"),
               "description": ("the run this verify wrote; null when it stopped before any lane "
                               "ran, on a file a scope takes whose name is not UTF-8")},
    "findings": {**schema_of("verify --json", "findings"), "items": _FINDING},
    "counts": {**schema_of("verify --json", "counts"), "properties": {
        key: schema_of("verify --json", f"counts.{key}")
        for key in ("diff_uncovered_count", "diff_uncovered_max")}},
    "baseline_run": {"type": "integer", "description": "the run it was measured against"},
    "baseline_commit": {"type": "string", "description": "that run's commit, full sha"},
    "commit": {"type": "string", "description": "the commit the verified tree is at"},
    "changed_files": _count("files in the diff being judged"),
    "changed_paths": _added_strings("verify --json", "changed_paths"),
    "gate_violations": {"type": "array", "items": _GATE_VIOLATION,
                        "description": "changed functions over their ceiling (exit 6)"},
    "unread_files": _unread_files_schema("verify --json", "unread_files"),
    "ratchet_regressions": {
        "type": "array", "description": "marks the fresh score rose past (exit 7)",
        "items": {
            "type": "object", "description": "one mark a function's score rose past",
            "properties": {
                "path": _FINDING_PATH,
                "long_name": {"type": "string",
                              "description": "the function's ratchet key, as the marks file "
                                             "holds it"},
                "recorded": {"type": "number", "description": "the mark"},
                "fresh_crap": {"type": "number", "description": "the score this run measured"},
                "dirty": _FINDING_DIRTY}}},
    "new_failures": _strings("test ids that fail now and passed in the baseline (exit 8)"),
    "dirty_failures": _strings("the new failures whose test id names a file with uncommitted "
                               "edits"),
    "forgiven_failures": _strings("test ids the fresh run and the baseline both failed"),
    "retried_passes": _strings("new failures that passed their flake retry"),
    "overridden": {"type": "array", "items": _GATE_VIOLATION,
                   "description": "gate violations an --override exempted"},
    "diff_uncovered_count": _count("changed lines no test ran"),
    "diff_uncovered": {
        "type": "array", "description": "the first 50 of those lines",
        "items": {"type": "object", "description": "one changed line no test ran",
                  "properties": {"path": _FINDING_PATH,
                                 "line": {"type": "integer", "description": "the line"}}}},
    "diff_uncovered_max": {"type": ("integer", "null"),
                           "description": ("the ceiling diff_uncovered_count is judged against "
                                           "(exit 9); null when the repo set none")},
    "unmarked_over_target": _count("functions over their ceiling that carry no ratchet mark"),
    "committed_findings": _count("findings whose file is clean"),
    "dirty_findings": _count("findings whose file has uncommitted edits"),
    "tool_versions": {
        "type": "object", "description": "the metric identity behind the numbers",
        "properties": {
            "crapkit": {"type": "string", "description": "crapkit version"},
            "lizard": {"type": "string", "description": "lizard version"},
            "analysis_version": {"type": "string",
                                 "description": "the analysis semantics version, as a string"}}},
    "ratchet_sha256": {"type": ("string", "null"),
                       "description": ("digest of the marks file on the tree as read; null when "
                                       "the tree has none")},
    "ratchet_source": schema_of("verify --json", "ratchet_source"),
    "ratchet_source_commit": schema_of("verify --json", "ratchet_source_commit"),
    "ratchet_source_sha256": schema_of("verify --json", "ratchet_source_sha256"),
    "ratchet_changes": {
        "type": ("object", "null"),
        "description": "what this run's tighten did to the marks file; null when it wrote nothing",
        "properties": {"dropped": _count("marks whose function is now at or under its ceiling"),
                       "tightened": _count("marks that fell")}},
    "lanes_without_results": _added_strings("verify --json", "lanes_without_results"),
    "lanes_without_baseline_results": _added_strings("verify --json",
                                                     "lanes_without_baseline_results"),
    "unreadable_names": _added_strings("verify --json", "unreadable_names"),
    "untracked_in_scope": _added_strings("verify --json", "untracked_in_scope")}

_MUTATE = {
    "schema": _SCHEMA,
    "mutants": _count("mutants run, after --max-mutants"),
    "killed": _count("mutants the suite failed on, timed_out and no_verdict included"),
    "survived": _count("mutants the suite passed on; killed + survived is mutants"),
    "timed_out": schema_of("mutate --json", "timed_out"),
    "no_verdict": schema_of("mutate --json", "no_verdict"),
    "survivors": {
        "type": "array", "description": "each mutant the suite passed on",
        "items": {"type": "object", "description": "one surviving mutant",
                  "properties": {
                      "path": _FINDING_PATH,
                      "line": {"type": "integer", "description": "the mutated line, 1-based"},
                      "op": {"type": "string", "description": "the flip, as `a -> b`"},
                      "original": {"type": "string", "description": "the line as written"},
                      "mutated": {"type": "string", "description": "the line as mutated"}}}},
    "outside_corpus": _strings("the paths asked about that the scored corpus does not hold, "
                               "sorted; they grew no mutants")}

_VERSION = {
    "schema": _SCHEMA,
    **{key: schema_of(VERSION, key) for key in ("version", "commit", "dirty",
                                                "analysis_version")}}

_ERROR = {
    "schema": _SCHEMA,
    "error": {
        "type": "object", "description": "why the command died",
        "properties": {
            "exit": {"type": "integer", "description": "the exit code, 1, 3, 4 or 5"},
            "kind": {"type": "string", "enum": ("state", "config", "git", "tool"),
                     "description": "state (1), config (3), git (4) or tool (5)"},
            "message": {"type": "string",
                        "description": "the stderr line without its `crapkit: ` prefix"},
            "unread_files": {
                **schema_of(ERROR_OBJECT, "error.unread_files"),
                "items": {"type": "object", "description": "one file refused for its name",
                          "properties": {name: schema_of(ERROR_OBJECT,
                                                         f"error.unread_files[].{name}")
                                         for name in ("path", "reason", "dirty")}}}}}}

_OVERRIDES = {
    "schema": _SCHEMA,
    "overrides": {
        "type": "array", "description": "every override the store holds, oldest first",
        "items": {"type": "object", "description": "one exempted gate violation",
                  "properties": {
                      "run_id": {"type": "integer", "description": "the verify run it passed"},
                      "commit": {"type": "string", "description": "that run's commit"},
                      "created_at": {"type": "string", "description": "UTC timestamp, ISO 8601"},
                      "path": _FINDING_PATH,
                      "function": {"type": "string", "description": "the function's long name"},
                      "crap": {"type": "number", "description": "the score it was exempted at"},
                      "reason": {"type": "string",
                                 "description": "the --override reason"}}}}}

_RUNS_PRUNE = {
    "schema": _SCHEMA,
    "pruned_runs": _count("runs deleted"),
    "kept_runs": _count("runs kept"),
    "freed_bytes": _count("bytes the VACUUM gave back")}

_CLAIMS_RELEASE = {"schema": _SCHEMA, "released": _count("claims this call closed")}

_CLEAN = {
    "schema": _SCHEMA,
    "dry_run": {"type": "boolean", "description": "true under --dry-run: nothing was removed"},
    "test_runs": {
        "type": "object",
        "description": "kept so readers keep every key; clean applies no test evidence retention",
        "properties": {status: _strings("always empty")
                       for status in ("removed", "planned", "active", "unproven", "changed")}},
    "temporary_mutations": {
        "type": "array", "description": "each temporary mutation checkout clean looked at",
        "items": {"type": "object", "description": "one checkout",
                  "properties": {
                      "path": {"type": "string", "description": "the checkout"},
                      "status": {"type": "string",
                                 "enum": ("recovered", "planned", "active", "unproven", "failed"),
                                 "description": ("recovered, planned, active, unproven or "
                                                 "failed; a failed recovery exits 1")},
                      "reason": {"type": "string", "description": "why it has that status"}}}}}

# rescore without --gate prints the rows and no verdict.
_RESCORE = {**{key: value for key, value in _RESCORE_GATE.items() if key != "gate"},
            "functions": {**_RESCORE_GATE["functions"], "items": {
                **_RESCORE_GATE["functions"]["items"], "properties": {
                    **_RESCORE_GATE["functions"]["items"]["properties"],
                    "unmeasured": schema_of("rescore --json", "functions[].unmeasured")}}}}

_WORKLIST_BATCHES = {
    **_WORKLIST,
    "scored_changes": schema_of("worklist --batches --json", "scored_changes"),
    "commands": _refresh_commands("worklist --batches --json"),
    "shallow": schema_of("worklist --batches --json", "shallow"),
    "batches": {
        "type": "array",
        "description": ("the active list split into batches that share no file, co-changing "
                        "files kept together: one per agent session"),
        "items": {"type": "object", "description": "one batch",
                  "properties": {
                      "entries": {"type": "array", "items": _WORKLIST_ITEM,
                                  "description": "the batch's functions, ranked"},
                      "files": _strings("the files those functions sit in")}}}}

_BRIEF_BATCH = {
    "schema": _SCHEMA,
    "run_id": _BRIEF["run_id"],
    "commit": _BRIEF["commit"],
    "stale": _STALE,
    "scored_changes": schema_of("brief --batch", "scored_changes"),
    "shallow": schema_of("brief --batch", "shallow"),
    "commands": _refresh_commands("brief --batch"),
    "skipped_claimed": _count("queue rows an open claim hid; present only when one did"),
    "packets": {
        "type": "array",
        "description": "one brief per queue item, in queue order",
        "items": {"type": "object", "description": "one function's brief, as brief --json has it",
                  "properties": {
                      **{key: value for key, value in _BRIEF.items() if key != "schema"},
                      **{key: schema_of("brief --batch", f"packets[].{key}")
                         for key in ("shallow", "scored_changes", "unmeasured")}}}}}


# Every agent JSON payload by the command that prints it. The MCP tools serve
# the ones MCP_TOOLS names as their outputSchema properties.
PAYLOADS: dict[str, dict] = {
    "next-item": _NEXT_ITEM,
    "worklist --json": _WORKLIST,
    "worklist --batches --json": _WORKLIST_BATCHES,
    "brief --json": _BRIEF,
    "brief --batch": _BRIEF_BATCH,
    "explain --json": _EXPLAIN,
    "runs --json": _RUNS,
    "runs prune --json": _RUNS_PRUNE,
    "trend --json": _TREND,
    "overrides --json": _OVERRIDES,
    "claims --json": _CLAIMS,
    "claims release --json": _CLAIMS_RELEASE,
    "coupling --json": _COUPLING,
    "duplication --json": _DUPLICATION,
    "ratchet report --json": _RATCHET_REPORT,
    "rescore --json": _RESCORE,
    "rescore --gate --json": _RESCORE_GATE,
    "inventory --json": _INVENTORY,
    "coverage --json": _COVERAGE,
    "verify --json": _VERIFY,
    "doctor --json": _DOCTOR,
    "clean --json": _CLEAN,
    "mutate --json": _MUTATE,
    VERSION: _VERSION,
    ERROR_OBJECT: _ERROR,
}


def _types(schema: dict) -> tuple[str, ...]:
    kind = schema["type"]
    return (kind,) if isinstance(kind, str) else tuple(kind)


def _declared(payload: str, key: str, schema: dict) -> Iterator[AgentField]:
    """The field at KEY and every field inside it."""
    yield AgentField(payload, key, _types(schema), schema.get("description", ""))
    for name, inner in schema.get("properties", {}).items():
        yield from _declared(payload, f"{key}.{name}", inner)
    for suffix, inner in (("[]", schema.get("items")), (".*", schema.get("additionalProperties"))):
        if isinstance(inner, dict):
            yield from _declared(payload, key + suffix, inner)


FIELDS = tuple(field for payload, properties in PAYLOADS.items()
               for name, schema in properties.items()
               for field in _declared(payload, name, schema))
