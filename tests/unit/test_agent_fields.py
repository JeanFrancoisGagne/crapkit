"""The fields 0.8.1 adds to the agent payloads match their one declaration.

`crapkit.agent_fields.ADDED` names each added field with its payload, its JSON
types and whether it may be null. The MCP name check walks key names only, so
a null where a schema says number passed it. Here each command prints its real
payload over a real repo, and every declared field must be present with one of
its declared types; the MCP output schema must declare the same types; and
docs/agent-json.md must name the field.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_inproc_repo import (add_knotty, commit_all, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from crapkit import mcp_server
from crapkit.agent_fields import ADDED, ERROR_OBJECT, MCP_TOOLS, AddedField
from crapkit.cli import main
from crapkit.ratchet import KEY_VERSION, RatchetEntry, dump_ratchet, metric_version

ROOT = Path(__file__).resolve().parents[2]
MARKS = "crapkit-ratchet.tsv"
# One TypeScript arrow the reader refuses: the file scores zero functions.
ARROW = "export const pick = (x: number) => convert<string, number>(x);\n"
# A file a scope takes whose name is not UTF-8: on POSIX byte e9, on NTFS a lone
# surrogate. Every command refuses it with the error object.
UNREADABLE = "src/caf\udce9.ts"
_JSON_TYPES = {"boolean": (bool,), "integer": (int,), "string": (str,), "array": (list,),
               "object": (dict,), "null": (type(None),)}


def json_type_ok(value, types: tuple[str, ...]) -> bool:
    """A bool is not an integer here, though Python calls it one."""
    if isinstance(value, bool):
        return "boolean" in types
    return any(isinstance(value, _JSON_TYPES[t]) for t in types)


def values_at(payload, key: str) -> list:
    """Every value at KEY: dots walk into objects, `[]` into each array item.
    A missing key raises, since a declared field is always present."""
    values = [payload]
    for part in key.split("."):
        values = _step(values, part)
    return values


def _step(values: list, part: str) -> list:
    found = [value[part.removesuffix("[]")] for value in values]
    return [item for value in found for item in value] if part.endswith("[]") else found


def run_json(repo: Path, capsys, *argv: str) -> dict:
    main([*argv, "--repo", str(repo)])
    return json.loads(capsys.readouterr().out)


def _verify(repo: Path, capsys) -> dict:
    return run_json(repo, capsys, "verify", "--reuse-artifacts", "--json")


def _write_marks(repo: Path) -> None:
    marks = dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 72.0)],
                         stamp=metric_version(), key_version=KEY_VERSION)
    (repo / MARKS).write_text(marks, encoding="utf-8", newline="\n")


# The payloads that rank one run and say whether it still describes the files.
_RANKED = {"worklist --json": ("worklist", "--json"), "next-item": ("next-item",),
           "brief --json": ("brief", "src/app.ts", "knotty", "--json")}


def _forget_content(repo: Path) -> None:
    """Drop every run's content record: the store 0.8.0 wrote held none, so
    `scored_changes` reads null."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(repo / ".crapkit" / "crap.sqlite")) as db, db:
        db.execute("DELETE FROM run_sources")


@pytest.fixture()
def payloads(repo, capsys) -> dict[str, list]:
    """One real payload per declared payload name, from one measured repo.
    The ranked payloads run twice, the second time over a run whose content
    record is gone, as on a run crapkit 0.8.0 wrote. verify runs three times:
    with no marks anywhere, over its marks file, and with that file deleted,
    so every nullable field prints both forms."""
    from crapkit.cli.analyses import _mutation_payload
    from crapkit.mutate import Mutant
    from crapkit.mutate_pool import MutantVerdict

    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    out = {"coverage --json": [run_json(repo, capsys, "coverage", "--reuse-artifacts", "--json")],
           "inventory --json": [run_json(repo, capsys, "inventory", "--json")],
           "worklist --json": [run_json(repo, capsys, "worklist", "--json")],
           "next-item": [run_json(repo, capsys, "next-item")],
           "brief --json": [run_json(repo, capsys, "brief", "src/app.ts", "knotty", "--json")],
           "ratchet report --json": [run_json(repo, capsys, "ratchet", "report", "--json")]}
    _forget_content(repo)
    for name, argv in _RANKED.items():
        out[name].append(run_json(repo, capsys, *argv))
    (repo / "src" / "b.ts").write_text(ARROW, encoding="utf-8")
    out["rescore --gate --json"] = [run_json(repo, capsys, "rescore", "src/app.ts", "src/b.ts",
                                             "--gate", "--json")]
    (repo / UNREADABLE).write_text(ARROW, encoding="utf-8")
    out[ERROR_OBJECT] = [run_json(repo, capsys, "rescore", "--gate", "--json", UNREADABLE)]
    (repo / UNREADABLE).unlink()
    commit_all(repo, "an unread file")
    verify = [_verify(repo, capsys)]
    _write_marks(repo)
    commit_all(repo, "marks")
    verify.append(_verify(repo, capsys))
    (repo / MARKS).unlink()
    verify.append(_verify(repo, capsys))
    out["verify --json"] = verify
    mutant = Mutant(path="a.py", line=1, op="> -> >=", original="x > 1", mutated="x >= 1")
    out["mutate --json"] = [_mutation_payload([mutant], [MutantVerdict.NO_VERDICT], [])]
    return out


def test_every_payload_the_declaration_names_is_printed_here(payloads):
    assert {f.payload for f in ADDED} == set(payloads)


def test_each_added_field_carries_a_declared_type_and_null_only_where_declared(payloads):
    wrong = [f"{f.payload} {f.key} = {value!r}" for f in ADDED
             for payload in payloads[f.payload] for value in values_at(payload, f.key)
             if not json_type_ok(value, f.types)]

    assert wrong == []


def test_the_nullable_fields_print_both_forms(payloads):
    """The verify pair holds the marks file on the tree, then none: each
    nullable field shows its value once and its null once."""
    nullable = [f for f in ADDED if f.nullable]
    seen = {(f.payload, f.key): _null_forms(payloads, f) for f in nullable}

    assert seen == {(f.payload, f.key): {True, False} for f in nullable}


def _null_forms(payloads: dict, field: AddedField) -> set[bool]:
    """Whether the field printed null, its value, or both, across its payloads."""
    return {value is None for payload in payloads[field.payload]
            for value in values_at(payload, field.key)}


def test_the_unread_finding_has_one_shape_in_every_payload(payloads):
    shapes = {f.payload: f.key for f in ADDED if f.key.endswith("unread_files")}
    keys = {payload: _entry_keys(payloads[payload][0], key) for payload, key in shapes.items()}

    assert set(shapes.values()) == {"gate.unread_files", "unread_files", "error.unread_files"}
    assert keys == {payload: [["dirty", "path", "reason"]] for payload in shapes}


@pytest.mark.parametrize("page", ["AGENTS.md", "docs/agent-json.md"])
def test_the_check_gate_row_names_the_unread_key_the_gate_block_carries(page: str):
    """An MCP client reads the key off this row; the old name finds nothing."""
    import re

    rows = [line for line in (ROOT / page).read_text(encoding="utf-8").splitlines()
            if line.startswith("| `check_gate` |")]

    assert len(rows) == 1 and "unread_files" in rows[0], rows
    assert not re.search(r"`(gate\.)?unread`", rows[0]), rows[0]


def _entry_keys(payload: dict, key: str) -> list[list[str]]:
    return [sorted(entry) for entry in values_at(payload, f"{key}[]")]


def _schema_at(schema: dict, key: str) -> dict:
    for part in key.split("."):
        schema = schema["properties"][part.removesuffix("[]")]
        if part.endswith("[]"):
            schema = schema["items"]
    return schema


def _declared_types(schema: dict) -> tuple[str, ...]:
    kind = schema["type"]
    return (kind,) if isinstance(kind, str) else tuple(kind)


@pytest.mark.parametrize("field", [f for f in ADDED if f.payload in MCP_TOOLS],
                         ids=lambda f: f"{MCP_TOOLS[f.payload]}:{f.key}")
def test_the_mcp_output_schema_declares_the_same_types(field: AddedField):
    (tool,) = [t for t in mcp_server.tool_listing() if t["name"] == MCP_TOOLS[field.payload]]
    schema = _schema_at(tool["outputSchema"], field.key.removesuffix("[]"))

    assert _declared_types(schema) == field.types
    assert schema["description"] == field.description


def test_the_agent_page_names_every_added_field():
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    names = {f.key.rsplit(".", 1)[-1].replace("[]", "") for f in ADDED}

    assert sorted(name for name in names if f"`{name}`" not in page) == []
