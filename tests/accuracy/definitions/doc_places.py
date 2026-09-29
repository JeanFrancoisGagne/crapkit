"""The five places a field is defined, each read as text without importing crapkit.

- README.md: the section under a GitHub heading anchor.
- CONTEXT.md: a glossary term, `**Term**:` and the lines under it, without its
  `_Avoid_` line.
- docs/agent-json.md: every table row whose first cell names the field.
- MCP: every output-schema property named after the field, from the tools/list
  answer of a live `crapkit mcp` session.
- SARIF: the rules' short descriptions and every result message sarif.py
  builds, read off its syntax tree with each placeholder written as
  `{name:spec}`.

A phrase matches after normalize(): lower case, backticks and `**` dropped,
whitespace collapsed. "-" in a definitions.tsv cell means the place defines no
such field, and the absence readers below say whether that still holds.
"""
from __future__ import annotations

import ast
import csv
import itertools
import json
from pathlib import Path
import re

import mcp_stdio

REPO = Path(__file__).resolve().parents[3]
TABLE = Path(__file__).resolve().parent / "definitions.tsv"
PLACES = ("readme", "context", "agent_json", "mcp", "sarif")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TERM = re.compile(r"^\*\*(.+?)\*\*:\s*$")


def normalize(text: str) -> str:
    plain = text.replace("`", "").replace("**", "")
    return " ".join(plain.split()).lower()


def phrases(cell: str) -> list[str]:
    """The phrases a cell asks for; none for "-"."""
    if cell.strip() == "-":
        return []
    return [part.strip() for part in cell.split("|") if part.strip()]


def missing(text: str, wanted: list[str]) -> list[str]:
    said = normalize(text)
    return [phrase for phrase in wanted if normalize(phrase) not in said]


def rows(path: Path = TABLE) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))


def read(relative: str) -> str:
    return (REPO / relative).read_bytes().decode("utf-8")


# --- README ---------------------------------------------------------------------------

def slug(title: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    kept = re.sub(r"[^\w\- ]", "", title.replace("`", "").lower())
    return kept.replace(" ", "-")


def _numbered(seen: dict, base: str) -> str:
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}-{count}"


def _heading(line: str, fenced: bool) -> str | None:
    match = None if fenced else _HEADING.match(line)
    return match.group(2) if match else None


def sections(text: str) -> dict[str, str]:
    """{anchor: the lines under that heading up to the next heading of any level}.
    A `#` line inside a code fence is code, never a heading."""
    found, seen, current, fenced = {}, {}, None, False
    for line in text.splitlines():
        title = _heading(line, fenced)
        fenced ^= bool(_FENCE.match(line))
        if title is not None:
            current = _numbered(seen, slug(title))
            found[current] = ""
        elif current is not None:
            found[current] += line + "\n"
    return found


def readme_text(anchors: str) -> str:
    """The README sections the `;`-separated anchors name; a missing one raises."""
    table = sections(read("README.md"))
    return "\n".join(table[anchor.strip()] for anchor in anchors.split(";"))


def readme_defines(field: str) -> list[str]:
    """README lines that open a definition of the field: "`field` is" or a table row."""
    pattern = re.compile(rf"(^|\W)`{re.escape(field)}` is\b|^\| `{re.escape(field)}` \|")
    return [line for line in read("README.md").splitlines() if pattern.search(line)]


# --- CONTEXT.md -----------------------------------------------------------------------

def _in_term(line: str) -> bool:
    return bool(line.strip()) and not _TERM.match(line)


def _term_body(lines: list[str]) -> str:
    body = itertools.takewhile(_in_term, lines)
    return "\n".join(line for line in body if not line.startswith("_Avoid_"))


def terms(text: str) -> dict[str, str]:
    """{term in lower case: its definition} for every `**Term**:` in the glossary."""
    lines = text.splitlines()
    return {match.group(1).lower(): _term_body(lines[number + 1:])
            for number, match in enumerate(map(_TERM.match, lines)) if match}


# --- docs/agent-json.md ---------------------------------------------------------------

def _first_cell_names(line: str) -> list[str]:
    cells = line.split("|")
    return re.findall(r"`([^`]+)`", cells[1]) if len(cells) > 2 else []


def table_rows(text: str, field: str) -> list[str]:
    """Every markdown table row whose first cell names `field` in backticks."""
    return [line for line in text.splitlines()
            if line.startswith("|") and field in _first_cell_names(line)]


# --- MCP output schemas ---------------------------------------------------------------

def _frame(number: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params})


def mcp_tools(python: str, cwd: Path, env: dict) -> list[dict]:
    """tools/list from a `crapkit mcp` session started in `cwd`. With no
    crapkit.toml there the server still answers initialize and tools/list."""
    frames = [_frame(0, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "definitions", "version": "1"}}),
              json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
              _frame(1, "tools/list", {})]
    done = mcp_stdio.run([python, "-m", "crapkit", "mcp"], cwd=cwd, frames="\n".join(frames),
                         env=env, encoding="utf-8", errors="replace")
    replies = {reply["id"]: reply for reply in map(json.loads, done.stdout.splitlines())}
    return replies[1]["result"]["tools"]


def _children(node: dict) -> list[dict]:
    kids = list((node.get("properties") or {}).values())
    items = node.get("items")
    return kids + ([items] if isinstance(items, dict) else [])


def _own_description(node: dict, field: str) -> list[str]:
    own = (node.get("properties") or {}).get(field)
    return [own["description"]] if isinstance(own, dict) and "description" in own else []


def _descriptions(node: dict, field: str) -> list[str]:
    nested = (text for child in _children(node) for text in _descriptions(child, field))
    return _own_description(node, field) + list(nested)


def schema_descriptions(tools: list[dict], field: str) -> list[tuple[str, str]]:
    """(tool name, description) for every output-schema property named `field`."""
    return [(tool["name"], text) for tool in tools
            for text in _descriptions(tool.get("outputSchema") or {}, field)]


# --- SARIF ----------------------------------------------------------------------------

def _placeholder(node: ast.FormattedValue) -> str:
    name = node.value.attr if isinstance(node.value, ast.Attribute) else ast.unparse(node.value)
    spec = "".join(part.value for part in getattr(node.format_spec, "values", ()))
    return "{" + name + (":" + spec if spec else "") + "}"


def template(node: ast.AST) -> str:
    """A message argument as text: a literal as is, an f-string with placeholders."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    parts = node.values if isinstance(node, ast.JoinedStr) else []
    return "".join(_placeholder(part) if isinstance(part, ast.FormattedValue)
                   else str(part.value) for part in parts)


def _assigns_rules(node: ast.AST) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else []
    return any(getattr(target, "id", "") == "_RULES" for target in targets)


def _rule_texts(tree: ast.Module) -> list[str]:
    rules = ast.literal_eval(next(filter(_assigns_rules, tree.body)).value)
    return [rule["shortDescription"]["text"] for rule in rules]


def _is_message_call(node: ast.AST) -> bool:
    """A `_result(rule, level, path, line, message)` call."""
    call = node if isinstance(node, ast.Call) else None
    return call is not None and getattr(call.func, "id", "") == "_result" and len(call.args) == 5


def _message_texts(tree: ast.Module) -> list[str]:
    return [template(node.args[4]) for node in ast.walk(tree) if _is_message_call(node)]


def sarif_texts() -> list[str]:
    """Every rule's short description and every result message sarif.py writes."""
    tree = ast.parse(read("src/crapkit/sarif.py"))
    return _rule_texts(tree) + _message_texts(tree)


def names_word(texts: list[str], word: str) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    return [text for text in texts if pattern.search(text)]
