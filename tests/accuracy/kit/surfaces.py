"""Read every surface crapkit prints into one map: (path, handle) -> fields.

A cross-surface check reads the same function off two surfaces and compares.
Each reader here takes one surface's text or parsed JSON and returns a Surface:
a dict keyed by (path, handle) whose values are the fields that surface prints
for the function, under the names it prints them. A surface that names no
function (an error envelope, the run list, a digest body) uses the empty path
and a key of its own.

Handles are the kit's, derived from the docs (AGENTS.md "Naming the function"):
a function's bare name is its long name up to the first "("; a name one file
gives to several functions takes `#N` in (start, occurrence) order, and an
anonymous function always does. A Roster built from a run's scored or
inventory export maps (path, long name, start) to that handle, so surfaces that
print only a line number and surfaces that print only a name land on one key.

TSV rows are read as docs/portable-records.md tells another tool to read them.

normalize() replaces what differs between two correct runs (times, durations,
absolute roots, temp paths, the crapkit and Python versions) with placeholders,
so goldens and cross-run comparisons see only what a calculation decided.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote

import html5lib

Key = tuple[str, str]
Surface = dict

MARKER = "@crapkit-record-v1"
VOLATILE_KEYS = frozenset({"generated_at", "created_at", "timestamp", "duration", "elapsed",
                           "crapkit", "python", "crapkit_version", "python_version"})
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
_FINDING = re.compile(r"CRAP (?P<crap>[\d.]+)(?: over ceiling (?P<ceiling>\d+))? "
                      r"\(ccn (?P<ccn>\d+), cov (?P<cov>\d+)%\) -> (?P<remedy>[\w-]+)")


# --- normalization --------------------------------------------------------

@dataclass(frozen=True)
class Volatile:
    """What two correct runs of one tree print differently."""
    roots: tuple[str, ...] = ()
    versions: dict = field(default_factory=dict)

    def text(self, value: str) -> str:
        for root in self.roots:
            value = value.replace(root, "<root>")
        for name, version in self.versions.items():
            value = value.replace(version, f"<{name}>")
        return _TIME.sub("<time>", value)


def spellings(root: Path) -> tuple[str, ...]:
    """Every way a printed path can spell `root`: as given, resolved, with
    forward slashes, and doubled backslashes as JSON escapes them. Longest first."""
    forms = {str(root), str(Path(root).resolve())}
    forms |= {form.replace("\\", "/") for form in list(forms)}
    forms |= {form.replace("\\", "\\\\") for form in list(forms) if "\\" in form}
    return tuple(sorted(forms, key=len, reverse=True))


def _volatile_key(key) -> bool:
    return key in VOLATILE_KEYS or str(key).endswith("_seconds")


def _normal_item(key, item, volatile: Volatile) -> tuple:
    return key, f"<{key}>" if _volatile_key(key) else normalize(item, volatile)


def _normal_dict(value: dict, volatile: Volatile) -> dict:
    return dict(_normal_item(key, item, volatile) for key, item in value.items())


def _normal_list(value: list, volatile: Volatile) -> list:
    return [normalize(item, volatile) for item in value]


_NORMALIZERS = {dict: _normal_dict, list: _normal_list,
                str: lambda value, volatile: volatile.text(value)}


def normalize(value, volatile: Volatile):
    """A copy of a parsed payload, or a text, with every volatile value replaced."""
    handler = _NORMALIZERS.get(type(value))
    return handler(value, volatile) if handler else value


# --- portable TSV -----------------------------------------------------------

def _fields(line: str) -> list[str]:
    parts = line.split("\t")
    if len(parts) == 2 and parts[0] == MARKER:
        return json.loads(parts[1])
    if parts[0].startswith("@crapkit-record-"):
        raise ValueError(f"unknown record encoding {parts[0]!r}")
    return parts


def _is_comment(line: str) -> bool:
    return line.startswith("#") and "\t" not in line


def read_tsv(text: str) -> tuple[list[str], list[dict]]:
    """(comment lines, rows as dicts by the header). Physical rows split at LF,
    one trailing CR removed; a `#` line without a tab is a comment."""
    lines = _physical_rows(text)
    comments = list(filter(_is_comment, lines))
    body = [_fields(line) for line in lines if not _is_comment(line)]
    header, rows = (body[0], body[1:]) if body else ([], [])
    return comments, [dict(zip(header, row)) for row in rows]


def _physical_rows(text: str) -> list[str]:
    return [line.removesuffix("\r") for line in text.split("\n") if line.strip()]


# --- handles ----------------------------------------------------------------

def bare_name(long_name: str) -> str:
    name = long_name.split("(", 1)[0].strip()
    return name or "(anonymous)"


def _order(row: dict) -> tuple[int, int]:
    return int(row.get("start") or 0), int(row.get("occurrence") or 0)


def _numbered(name: str, group: list[dict]) -> list[tuple[dict, str]]:
    if len(group) == 1 and name != "(anonymous)":
        return [(group[0], name)]
    ordered = sorted(group, key=_order)
    return [(row, f"{name}#{number}") for number, row in enumerate(ordered, start=1)]


class Roster:
    """(path, long name, start) -> the kit's handle, from one run's rows."""

    def __init__(self, rows: list[dict]):
        groups = defaultdict(list)
        for row in rows:
            groups[(row["path"], bare_name(row["long_name"]))].append(row)
        self.by_start: dict = {}
        self.by_name: dict = defaultdict(list)
        for (path, name), group in groups.items():
            self._index(path, name, group)

    def _index(self, path: str, name: str, group: list[dict]) -> None:
        for row, handle in _numbered(name, group):
            self.by_start[(path, int(row["start"]))] = handle
            self.by_name[(path, row["long_name"])].append(handle)

    def key(self, path: str, long_name: str = "", start=None) -> Key:
        """The key for a function a surface names by line, by name, or both."""
        if start is not None and (path, int(start)) in self.by_start:
            return path, self.by_start[(path, int(start))]
        named = self.by_name.get((path, long_name), [])
        if len(named) == 1:
            return path, named[0]
        raise KeyError(f"no single function {long_name!r} at {path}:{start} in the roster")


def roster_from_tsv(text: str) -> Roster:
    return Roster(read_tsv(text)[1])


# --- surfaces keyed by function -------------------------------------------------

def _put(surface: Surface, key: Key, fields: dict, section: str = "") -> None:
    entry = surface.setdefault(key, {})
    for name, value in fields.items():
        if section:
            entry[f"{section}.{name}"] = value
        entry.setdefault(name, value)


def from_tsv(text: str, roster: Roster | None = None) -> Surface:
    """An inventory, scored or baseline export, or a ratchet file."""
    rows = read_tsv(text)[1]
    roster = roster or Roster([row for row in rows if "start" in row])
    surface: Surface = {}
    for row in rows:
        _put(surface, roster.key(row["path"], row["long_name"], row.get("start")), row)
    return surface


def _function_of(node: dict) -> str | None:
    return node.get("long_name") or node.get("function") or node.get("key_name")


def _is_row(node) -> bool:
    return isinstance(node, dict) and "path" in node and _function_of(node) is not None


def _scalars(node: dict) -> dict:
    return {name: value for name, value in node.items() if not isinstance(value, (dict, list))}


def _children(node, section: str) -> list:
    """(section, child) for each child; a list item keeps its list's name."""
    if isinstance(node, dict):
        return [(str(name), child) for name, child in node.items()]
    if isinstance(node, list):
        return [(section, child) for child in node]
    return []


def _walk(node, section: str, found: list) -> None:
    if _is_row(node):
        found.append((section, node))
    for name, child in _children(node, section):
        _walk(child, name, found)


def from_json(payload, roster: Roster) -> Surface:
    """Every function row anywhere in a --json payload or MCP structuredContent.
    A field reads bare and as `section.field` (`gate_violations.crap`)."""
    found: list = []
    _walk(payload, "", found)
    surface: Surface = {}
    for section, row in found:
        key = roster.key(row["path"], _function_of(row), row.get("start"))
        _put(surface, key, _scalars(row), section)
    return surface


def _finding(message: str) -> dict:
    match = _FINDING.search(message)
    return {name: value for name, value in match.groupdict().items()} if match else {}


def from_sarif(payload: dict, roster: Roster) -> Surface:
    surface: Surface = {}
    for result in payload["runs"][0]["results"]:
        where = result["locations"][0]["physicalLocation"]
        uri = unquote(where["artifactLocation"]["uri"])
        message = result["message"]["text"]
        fields = {"rule": result["ruleId"], "level": result["level"], "message": message,
                  **_finding(message)}
        _put(surface, roster.key(uri, message.split(": CRAP", 1)[0], where["region"]["startLine"]),
             fields, result["ruleId"])
    return surface


_ANNOTATION = re.compile(r"^::(?P<level>\w+) (?P<props>[^:]*)::(?P<message>.*)$")


def _property(pair: str) -> tuple[str, str]:
    name, _, value = pair.partition("=")
    return name, unquote(value)


def split_workflow_commands(stdout: str) -> tuple[str, str]:
    """`--github` prints its `::` workflow-command lines on stdout ahead of the
    rest: (those lines, everything else)."""
    lines = stdout.splitlines(keepends=True)
    commands = "".join(line for line in lines if line.startswith("::"))
    return commands, "".join(line for line in lines if not line.startswith("::"))


def from_annotations(text: str, roster: Roster) -> Surface:
    """GitHub workflow-command lines: %25, %0D, %0A, %3A and %2C decoded."""
    surface: Surface = {}
    for match in filter(None, map(_ANNOTATION.match, text.splitlines())):
        props = dict(map(_property, match["props"].split(",")))
        message = unquote(match["message"])
        name = message.split(": ", 1)[0]
        key = _annotation_key(roster, props, name)
        _put(surface, key, {"level": match["level"], "rule": props.get("title", ""),
                            "message": message, **_finding(message)}, props.get("title", ""))
    return surface


def _annotation_key(roster: Roster, props: dict, name: str) -> Key:
    try:
        return roster.key(props["file"], name, props.get("line"))
    except KeyError:
        return props["file"], f"line {props.get('line')}"


_XHTML = "{http://www.w3.org/1999/xhtml}"


def _cells(row) -> list:
    return row.findall(f"{_XHTML}td")


def _text(element) -> str:
    return "".join(element.itertext()).strip()


def _html_row(row, headers: list[str]) -> dict:
    cells = _cells(row)
    fields = dict(zip(headers, map(_text, cells)))
    divs = [_text(div) for div in cells[headers.index("Function")].iter(f"{_XHTML}div")]
    fields["long_name"], fields["loc"] = divs[0], divs[1]
    return fields


def _worklist_section(text: str):
    tree = html5lib.parse(text)
    return next(node for node in tree.iter(f"{_XHTML}section") if node.get("id") == "worklist")


def from_html(text: str, roster: Roster) -> Surface:
    """The report's worklist rows (tr.wl), cells by column heading."""
    section = _worklist_section(text)
    headers = [_text(th) for th in section.iter(f"{_XHTML}th")]
    surface: Surface = {}
    for row in filter(lambda tr: tr.get("class") == "wl", section.iter(f"{_XHTML}tr")):
        fields = _html_row(row, headers)
        path, _, line = fields["loc"].rpartition(":")
        _put(surface, roster.key(path, fields["long_name"], line), fields)
    return surface


_PR_ROW = re.compile(r"^\| `(?P<path>.+):(?P<start>\d+)` \| `(?P<long_name>.+)` \| "
                     r"(?P<ccn>\d+) \| (?P<risk>[\d.]+) \| (?P<remedy>.+) \|$")
_PR_GATE = re.compile(r"^- gate: `(?P<path>.+):(?P<start>\d+)` `(?P<long_name>.+)` ccn "
                      r"(?P<ccn>\d+), cov (?P<cov>\d+)%, crap (?P<crap>[\d.]+) -> (?P<remedy>\S+)$")


def from_pr_comment(text: str, roster: Roster) -> Surface:
    """The Action's comment: worklist table rows and gate bullets."""
    surface: Surface = {}
    for pattern, section in ((_PR_ROW, "worklist"), (_PR_GATE, "gate")):
        for match in filter(None, map(pattern.match, text.splitlines())):
            fields = match.groupdict()
            _put(surface, roster.key(fields["path"], fields["long_name"], fields["start"]),
                 fields, section)
    return surface


_WORKLIST_ROW = re.compile(r"^\s+risk\s+(?P<risk>[\d.]+)\s+ccn\s+(?P<ccn>\d+)\s+crap\s+"
                           r"(?P<crap>[\d.]+)\s+cov\s+(?P<cov>\d+)%\s+(?P<commits>\d+)c/"
                           r"(?P<authors>\d+)a\s+(?P<path>.+?):(?P<start>\d+)\s+"
                           r"(?P<long_name>.+?)(?:\s{2}(?P<marker>\w+))?$")


def from_worklist_text(text: str, roster: Roster) -> Surface:
    surface: Surface = {}
    for match in filter(None, map(_WORKLIST_ROW.match, text.splitlines())):
        fields = match.groupdict()
        _put(surface, roster.key(fields["path"], fields["long_name"], fields["start"]), fields)
    return surface


_STORE_ROWS = ("select i.scope, i.path, i.long_name, f.*, fl.name as flag_name, "
               "r.name as remedy_name from functions f join identities i on i.id = f.identity_id "
               "left join flags fl on fl.id = f.flag left join remedies r on r.id = f.remedy "
               "where f.run_id = ?")


def from_store(db: Path, run_id: int) -> Surface:
    """One run's function rows, read with sqlite3 opened read-only."""
    connection = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(_STORE_ROWS, (run_id,))]
    finally:
        connection.close()
    roster = Roster(rows)
    surface: Surface = {}
    for row in rows:
        _put(surface, roster.key(row["path"], row["long_name"], row["start"]), row)
    return surface


# --- surfaces that name no function ------------------------------------------

def error_envelope(payload: dict) -> Surface:
    """The --json error object: {"error": {"exit", "kind", "message"}, "schema": 1}."""
    return {("", "error"): {**payload["error"], "schema": payload.get("schema")}}


def mcp_result(result: dict, roster: Roster) -> Surface:
    """An MCP tool result: its structuredContent's rows, or its error text."""
    if result.get("isError"):
        return {("", "mcp_error"): {"text": result["content"][0]["text"]}}
    return from_json(result.get("structuredContent", {}), roster)


def runs(payload: dict) -> Surface:
    return {("", f"run {run['id']}"): _scalars(run) for run in payload["runs"]}


def lines(text: str, name: str) -> Surface:
    """A prose surface (a digest --alert body, a refusal): its non-empty lines."""
    return {("", name): {"lines": [line for line in text.splitlines() if line.strip()]}}


def verify_receipt(db: Path, run_id: int, roster: Roster) -> Surface:
    """What a verify run stored: its verdict and the findings it recorded."""
    connection = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        kind, ok, findings = connection.execute(
            "select kind, verdict_ok, findings from runs where id = ?", (run_id,)).fetchone()
    finally:
        connection.close()
    surface = {("", f"run {run_id}"): {"kind": kind, "verdict_ok": ok}}
    if isinstance(findings, str) and findings.startswith(("{", "[")):
        surface.update(from_json(json.loads(findings), roster))
    return surface
