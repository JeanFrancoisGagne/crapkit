"""The fields 0.8.1 adds to the agent JSON payloads, each declared once.

JSON schema 1 keeps every existing field's meaning, and a release may add
fields. Each field 0.8.1 adds is declared here: the payload that carries it,
its key (dots into objects, `[]` into the items of an array), its JSON types
(`null` among them when it may be null), and one line saying what it means.
The MCP output schemas take their entries for these fields from here, and
tests/unit/test_agent_fields.py checks the payloads the commands print, the
MCP results and docs/agent-json.md against the same table, types and nulls
included, where a check of key names alone let a null pass for a number.
"""
from __future__ import annotations

from typing import NamedTuple

# The MCP tool that returns each payload, for the payloads a tool returns.
MCP_TOOLS = {"worklist --json": "list_worklist", "next-item": "get_next_item",
             "brief --json": "get_function_brief", "ratchet report --json": "get_ratchet_report",
             "rescore --gate --json": "check_gate"}


_EMPTY_SCOPES = ("each declared scope that claims no file, or whose every file no reader could "
                 "read, and how many files it claims (0 when it claims none); a scope whose "
                 "readable files hold no function is not listed")


class AddedField(NamedTuple):
    payload: str
    key: str
    types: tuple[str, ...]
    description: str

    @property
    def nullable(self) -> bool:
        return "null" in self.types

    def schema(self) -> dict:
        """The JSON schema fragment an MCP output schema declares for it."""
        kind = self.types[0] if len(self.types) == 1 else self.types
        return {"type": kind, "description": self.description}


_SHALLOW = ("true when this checkout is a shallow clone: {counts} count only the commits the "
            "clone holds; set fetch-depth: 0 on the checkout or run git fetch --unshallow for "
            "the real counts")
_UNMEASURED = ("true when no measurement stands behind cov: flag no-lane (no lane covers the "
               "scope) or cc-only (the scope asks for no coverage). cov 0.0 and "
               "est_uncovered_paths are then stand-ins, not a count of paths no test walks")
_UNREAD = ("changed files no reader could read, so none of their functions was judged; any "
           "entry fails the gate (exit 6)")
_UNREAD_PATH = "repo-relative path of the file"
_UNREAD_REASON = "the reader's refusal, naming the line and what to change"
_UNREAD_DIRTY = "true when the file has uncommitted edits or is untracked"


def _unread_fields(payload: str, key: str, dirty: str) -> tuple[AddedField, ...]:
    return (AddedField(payload, key, ("array",), _UNREAD),
            AddedField(payload, f"{key}[].path", ("string",), _UNREAD_PATH),
            AddedField(payload, f"{key}[].reason", ("string",), _UNREAD_REASON),
            AddedField(payload, f"{key}[].dirty", ("boolean",), dirty))


ADDED = (
    AddedField("worklist --json", "shallow", ("boolean",),
               _SHALLOW.format(counts="commits, authors and the churn the ranking reads")),
    AddedField("next-item", "shallow", ("boolean",),
               _SHALLOW.format(counts="commits, authors and the churn the ranking reads")),
    AddedField("next-item", "item.unmeasured", ("boolean",), _UNMEASURED),
    AddedField("brief --json", "shallow", ("boolean",),
               _SHALLOW.format(counts="churn and gate_rule.mark_age_days")),
    AddedField("brief --json", "unmeasured", ("boolean",), _UNMEASURED),
    AddedField("ratchet report --json", "shallow", ("boolean",),
               _SHALLOW.format(counts="ages and repayments") + ", and crapkit ratchet report "
               "--enforce refuses to judge the debt policy there (exit 4)"),
    AddedField("rescore --gate --json", "functions[].unmeasured", ("boolean",),
               "true when no measurement stands behind cov: the baseline run holds no row this "
               "function joins by name (it was added or renamed since), or its scope has no lane "
               "or asks for none. cov 0.0 is then a stand-in"),
    *_unread_fields("rescore --gate --json", "gate.unread_files",
                    "always true here: the gate judges the working tree's changes since HEAD"),
    *_unread_fields("verify --json", "unread_files", _UNREAD_DIRTY),
    AddedField("verify --json", "lanes_without_results", ("array",),
               "lanes that declare no results_artifact, so nothing checked their tests for new "
               "failures"),
    AddedField("verify --json", "lanes_without_baseline_results", ("array",),
               "lanes with a new failure whose baseline, and every trusted run behind it, "
               "recorded no failure list: those failures may predate the change"),
    AddedField("verify --json", "ratchet_source", ("string",),
               "which marks verify judged against: tree, the marks file as read, or committed, "
               "the newest marks committed since the baseline when that file is missing or blank"),
    AddedField("verify --json", "ratchet_source_commit", ("string", "null"),
               "the commit whose marks verify judged when ratchet_source is committed; null "
               "for tree"),
    AddedField("verify --json", "ratchet_source_sha256", ("string", "null"),
               "digest of the marks verify judged against; null when there were no marks"),
    AddedField("coverage --json", "empty_scopes", ("object",),
               _EMPTY_SCOPES),
    AddedField("inventory --json", "empty_scopes", ("object",),
               _EMPTY_SCOPES),
    AddedField("mutate --json", "timed_out", ("integer",),
               "mutants whose suite ran past mutation_timeout_seconds, a count inside killed"),
    AddedField("mutate --json", "no_verdict", ("integer",),
               "mutants whose suite ran no test (exit 5), a count inside killed"),
)


def added_field(payload: str, key: str) -> AddedField:
    (found,) = [f for f in ADDED if (f.payload, f.key) == (payload, key)]
    return found


def schema_of(payload: str, key: str) -> dict:
    """The MCP output schema fragment for one declared field."""
    return added_field(payload, key).schema()
