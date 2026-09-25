"""Write crapkit's server into a harness's own config file, and read it back
the way that harness reads it.

The config text comes from docs/harnesses.md: the first fence under the
section whose heading is the profile's name, with /absolute/path/to/your/repo
standing for the repo. Until the stamped tree carries that page, the profile's
[doc].config stands in and the cell's transcript says so; once the page
exists, a missing section fails the cell naming the page and the heading.

    text, source = writers.doc_config(profile)
    path = writers.write(profile, text, box, repo)
    server = writers.parse(profile, path)      # Server(command, args, env, cwd, env_vars)

Formats: mcpservers-json (Claude Code, Cursor, Cline, Gemini and most
others), vscode-json, codex-toml, opencode-json, zed-json, amp-json,
crush-json, goose-yaml, continue-yaml and aider-yaml. The YAML reader knows
the block mappings, block sequences, flow lists and scalars a config fence
uses, because the runner has no YAML library.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kit import docsnip

PLACEHOLDER = "/absolute/path/to/your/repo"
WINDOWS = os.name == "nt"
PAGE = "docs/harnesses.md"
NAME = "crapkit"


@dataclass(frozen=True)
class Server:
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    env_vars: list[str] = field(default_factory=list)

    @property
    def argv(self) -> list[str]:
        return [self.command, *self.args]


# --- where the text comes from ------------------------------------------------------

def doc_config(profile, base: Path | None = None) -> tuple[str, str]:
    """The config a user pastes, and where it came from."""
    root = base or docsnip.root()
    if not (root / PAGE).exists():
        return profile.doc["config"], f"profile [doc].config: {PAGE} is not in the stamped tree"
    block = docsnip.fence(PAGE, profile.doc["heading"], base=root)
    return block.text, f"{PAGE} > {profile.doc['heading']} (line {block.line})"


def filled(text: str, repo: Path) -> str:
    """The placeholder replaced by the repo, spelled with forward slashes so
    it stays a valid JSON, TOML or YAML string on Windows."""
    return text.replace(PLACEHOLDER, repo.as_posix())


def _relative(doc: dict) -> str:
    return doc.get("path_windows") or doc["path"] if WINDOWS else doc["path"]


def target(profile, box, repo: Path) -> Path:
    """The file the harness reads: under the repo for a project scope, under
    HOME for a user scope (or under the root a variable such as GOOSE_PATH_ROOT
    moves it to), beside the sandbox for an SDK's in-code options."""
    doc = profile.doc
    if doc.get("env_root") in box.env:
        return Path(box.env[doc["env_root"]]) / doc["env_path"]
    base = {"project": repo, "user": box.home}.get(doc["scope"], box.root / "sdk-options")
    return base / _relative(doc)


def merged(path: Path, text: str) -> str:
    """The config added to the file already there, the way a user pastes a
    block into their settings: JSON key by key, TOML appended as a new table;
    a YAML file is replaced."""
    if not path.is_file() or path.suffix not in (".json", ".toml"):
        return text
    old = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        return old.rstrip("\n") + "\n\n" + text
    return json.dumps(_deep(json.loads(old), json.loads(text)), indent=2) + "\n"


def _deep(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        both = isinstance(value, dict) and isinstance(out.get(key), dict)
        out[key] = _deep(out[key], value) if both else value
    return out


def write(profile, text: str, box, repo: Path) -> Path:
    path = target(profile, box, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged(path, filled(text, repo)), encoding="utf-8", newline="\n")
    return path


def parse(profile, path: Path) -> Server:
    return FORMATS[profile.doc["format"]](path.read_text(encoding="utf-8"))


# --- one reader per format ------------------------------------------------------------

def _server(entry: dict, command: str = "command", args: str = "args", env: str = "env") -> Server:
    return Server(str(entry[command]), [str(arg) for arg in entry.get(args) or []],
                  dict(entry.get(env) or {}), entry.get("cwd"), list(entry.get("env_vars") or []))


def mcpservers_json(text: str) -> Server:
    return _server(json.loads(text)["mcpServers"][NAME])


def vscode_json(text: str) -> Server:
    return _server(json.loads(text)["servers"][NAME])


def codex_toml(text: str) -> Server:
    return _server(tomllib.loads(text)["mcp_servers"][NAME])


def opencode_json(text: str) -> Server:
    entry = json.loads(text)["mcp"][NAME]
    command = list(entry["command"])
    return Server(command[0], command[1:], dict(entry.get("environment") or {}))


def zed_json(text: str) -> Server:
    entry = json.loads(text)["context_servers"][NAME]
    nested = entry["command"] if isinstance(entry.get("command"), dict) else None
    return _server(nested, "path") if nested else _server(entry)


def amp_json(text: str) -> Server:
    return _server(json.loads(text)["amp.mcpServers"][NAME])


def crush_json(text: str) -> Server:
    return _server(json.loads(text)["mcp"][NAME])


def goose_yaml(text: str) -> Server:
    return _server(yaml_load(text)["extensions"][NAME], "cmd", "args", "envs")


def continue_yaml(text: str) -> Server:
    entries = yaml_load(text)["mcpServers"]
    return _server(next(entry for entry in entries if entry.get("name") == NAME))


def aider_yaml(text: str) -> Server:
    """Aider runs `lint-cmd` with the edited file appended; no MCP server."""
    command = yaml_load(text)["lint-cmd"]
    command = command[0] if isinstance(command, list) else command
    words = shlex.split(command.split(": ", 1)[-1])
    return Server(words[0], words[1:])


FORMATS = {"mcpservers-json": mcpservers_json, "vscode-json": vscode_json, "codex-toml": codex_toml,
           "opencode-json": opencode_json, "zed-json": zed_json, "amp-json": amp_json, "crush-json": crush_json,
           "goose-yaml": goose_yaml, "continue-yaml": continue_yaml, "aider-yaml": aider_yaml}


# --- a YAML subset ---------------------------------------------------------------------

@dataclass
class _Line:
    indent: int
    text: str


PAIR = re.compile(r"""^[^\s"'\[{#-][^:]*:(\s|$)""")


def _uncommented(raw: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", raw).rstrip()


def _lines(text: str) -> list[_Line]:
    kept = [_uncommented(raw) for raw in text.splitlines()]
    return [_Line(len(line) - len(line.lstrip()), line.strip()) for line in kept if line.strip()]


def yaml_load(text: str):
    lines = _lines(text)
    return _block(lines, 0, lines[0].indent)[0] if lines else None


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _block(lines: list[_Line], i: int, indent: int):
    reader = _sequence if _is_item(lines[i].text) else _mapping
    return reader(lines, i, indent)


def _at(lines: list[_Line], i: int, indent: int) -> bool:
    return i < len(lines) and lines[i].indent == indent


def _mapping(lines: list[_Line], i: int, indent: int):
    result = {}
    while _at(lines, i, indent) and not _is_item(lines[i].text):
        key, _, rest = lines[i].text.partition(":")
        result[_unquoted(key.strip())], i = _value(lines, i + 1, indent, rest.strip())
    return result, i


def _nested(lines: list[_Line], i: int, indent: int) -> bool:
    """The next line opens this key's block: deeper, or a sequence at the key's own indent."""
    if i >= len(lines):
        return False
    return lines[i].indent > indent or (lines[i].indent == indent and _is_item(lines[i].text))


def _value(lines: list[_Line], i: int, indent: int, rest: str):
    if rest:
        return _scalar(rest), i
    if _nested(lines, i, indent):
        return _block(lines, i, lines[i].indent)
    return None, i


def _sequence(lines: list[_Line], i: int, indent: int):
    items = []
    while _at(lines, i, indent) and _is_item(lines[i].text):
        item, i = _item(lines, i, indent)
        items.append(item)
    return items, i


def _item(lines: list[_Line], i: int, indent: int):
    body = lines[i].text[1:].strip()
    if not PAIR.match(body):
        return _scalar(body), i + 1
    lines[i] = _Line(indent + 2, body)
    return _mapping(lines, i, indent + 2)


def _unquoted(text: str) -> str:
    quoted = len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"
    return text[1:-1] if quoted else text


SCALARS = {"true": True, "false": False, "null": None, "~": None}


def _flow(text: str) -> list:
    """A flow sequence: [a, "b", 3]."""
    return [_scalar(part.strip()) for part in text[1:-1].split(",") if part.strip()]


def _scalar(text: str):
    if text.startswith("[") and text.endswith("]"):
        return _flow(text)
    if text in SCALARS:
        return SCALARS[text]
    return int(text) if text.isdigit() else _unquoted(text)
