"""Every agent JSON payload matches its one declaration in crapkit.agent_fields.

Each command prints its real payload over a real repo, in the states that make
its conditional keys, its list items and its nulls appear. Every key a payload
prints must be declared, and every value must carry one of its declared JSON
types, a null only where the declaration allows one. A check of key names alone
let a null pass for a number, and doctor's lane `refusal` shipped with no
declaration at all. The MCP tools serve the same declarations as their
outputSchema. The fields 0.8.1 adds (ADDED) must also print, print their null
and their value where they may be null, and be named in docs/agent-json.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_inproc_repo import (KNOTTY, add_knotty, commit_all, git, repo,  # noqa: F401
                             seed_artifacts, template_repo)

from crapkit import mcp_server
from crapkit.agent_fields import (ADDED, ERROR_OBJECT, FIELDS, MCP_TOOLS, PAYLOADS, VERSION,
                                  AgentField, added_field)
from crapkit.cli import main
from crapkit.ratchet import KEY_VERSION, RatchetEntry, dump_ratchet, metric_version
from name_bytes import NOT_UTF8_NAMES

ROOT = Path(__file__).resolve().parents[2]
MARKS = "crapkit-ratchet.tsv"
# One TypeScript arrow the reader refuses: the file scores zero functions.
ARROW = "export const pick = (x: number) => convert<string, number>(x);\n"
# A file a scope takes whose name is not UTF-8: on POSIX byte e9, on NTFS a lone
# surrogate. Every command refuses it with the error object.
UNREADABLE = "src/caf\udce9.ts"
_JSON_TYPES = {"boolean": (bool,), "integer": (int,), "number": (int, float), "string": (str,),
               "array": (list,), "object": (dict,), "null": (type(None),)}


def json_type_ok(value, types: tuple[str, ...]) -> bool:
    """A bool is not a number here, though Python calls it one."""
    if isinstance(value, bool):
        return "boolean" in types
    return any(isinstance(value, _JSON_TYPES[t]) for t in types)


def values_at(payload, key: str) -> list:
    """Every value at KEY: dots walk into objects, `[]` into each array item.
    An object that lacks the next key contributes nothing."""
    values = [payload]
    for part in key.split("."):
        values = _step(values, part)
    return values


def _step(values: list, part: str) -> list:
    name = part.removesuffix("[]")
    found = [value[name] for value in values if _holds(value, name)]
    return _spread(found) if part.endswith("[]") else found


def _holds(value, name: str) -> bool:
    return isinstance(value, dict) and name in value


def _spread(lists: list) -> list:
    return [item for value in lists for item in value]


def printed(value, maps: set[str], key: str = ""):
    """(key, value) for everything a payload prints, keyed the way FIELDS keys
    it: a key of an object declared as a map (`KEY.*` in MAPS) reads as `*`."""
    if isinstance(value, dict):
        for name, inner in value.items():
            yield from _entry(_child(key, name, maps), inner, maps)
    elif isinstance(value, list):
        for inner in value:
            yield from _entry(f"{key}[]", inner, maps)


def _entry(key: str, value, maps: set[str]):
    yield key, value
    yield from printed(value, maps, key)


def _child(key: str, name: str, maps: set[str]) -> str:
    if f"{key}.*" in maps:
        return f"{key}.*"
    return f"{key}.{name}" if key else name


def run_json(repo: Path, capsys, *argv: str) -> dict:
    main([*argv, "--repo", str(repo)])
    return json.loads(capsys.readouterr().out)


def _print(out: dict, repo: Path, capsys, name: str, *argv: str) -> None:
    out.setdefault(name, []).append(run_json(repo, capsys, *argv))


def _print_all(out: dict, repo: Path, capsys, commands) -> None:
    for name, argv in commands:
        _print(out, repo, capsys, name, *argv)


def _verify(repo: Path, capsys, *flags: str) -> dict:
    return run_json(repo, capsys, "verify", "--reuse-artifacts", *flags, "--json")


def _write_marks(repo: Path) -> None:
    """A mark above knotty's score, so the verify over it tightens the mark."""
    marks = dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 80.0)],
                         stamp=metric_version(), key_version=KEY_VERSION)
    (repo / MARKS).write_text(marks, encoding="utf-8", newline="\n")


# The payloads that rank one run and say whether it still describes the files.
_RANKED = {"worklist --json": ("worklist", "--json"),
           "worklist --batches --json": ("worklist", "--batches", "2", "--json"),
           "next-item": ("next-item",),
           "brief --json": ("brief", "src/app.ts", "knotty", "--json"),
           "brief --batch": ("brief", "--batch", "2")}

# The read commands over the measured run, before anyone claims a function.
_READS = (
    ("coverage --json", ("coverage", "--reuse-artifacts", "--json")),
    ("inventory --json", ("inventory", "--json")),
    *_RANKED.items(),
    ("next-item", ("next-item", "--scope", "web")),
    ("ratchet report --json", ("ratchet", "report", "--json")),
    ("runs --json", ("runs", "--json")),
    ("trend --json", ("trend", "--json")),
    ("explain --json", ("explain", "src/app.ts", "knotty", "--json")),
    ("explain --json", ("explain", "src/app.ts", "knotty", "--history", "--json")),
    ("explain --json", ("explain", "src/app.ts", "knotty", "--tests", "--json")),
    ("coupling --json", ("coupling", "--min-support", "1", "--min-confidence", "0.1", "--json")))

# A claim taken, seen from the queue, the claim list and the brief, then handed back.
_CLAIMS = (
    ("next-item", ("next-item", "--top", "2", "--claim")),
    ("next-item", ("next-item",)),
    ("brief --batch", ("brief", "--batch", "2")),
    ("claims --json", ("claims", "--json")),
    ("brief --json", ("brief", "src/app.ts", "knotty", "--json")),
    ("claims release --json", ("claims", "release", "--all", "--json")))

# A changed function over its ceiling: the gate's breach, verify's violation,
# the override that exempts it, and the pair it makes with knotty.
_OVERRIDE = (
    ("rescore --gate --json", ("rescore", "src/app.ts", "--gate", "--json")),
    ("verify --json", ("verify", "--reuse-artifacts", "--json")),
    ("verify --json", ("verify", "--reuse-artifacts", "--override", "a reviewed exemption",
                       "--json")),
    ("overrides --json", ("overrides", "--json")),
    ("duplication --json", ("duplication", "--similarity", "0.3", "--min-lines", "3", "--json")),
    ("brief --json", ("brief", "src/app.ts", "knotty", "--json")))


def _forget_content(repo: Path) -> None:
    """Drop every run's content record: the store 0.8.0 wrote held none, so
    `scored_changes` reads null."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(repo / ".crapkit" / "crap.sqlite")) as db, db:
        db.execute("DELETE FROM run_sources")


def _version_payloads(capsys, monkeypatch, where: Path) -> list[dict]:
    """`--version --json` run from a checkout, then from an installed build, so
    `commit` and `dirty` print their value once and their null once."""
    from crapkit.cli import parser

    checkout = where / "checkout"
    (checkout / "src" / "crapkit").mkdir(parents=True)
    (checkout / "src" / "crapkit" / "__init__.py").write_text("", encoding="utf-8")
    git(checkout, "init", "-q")
    commit_all(checkout, "a build")
    installed = where / "site-packages" / "crapkit"
    installed.mkdir(parents=True)
    printed = []
    for package in (checkout / "src" / "crapkit", installed):
        monkeypatch.setattr(parser, "_package_dir", lambda package=package: package)
        main(["--version", "--json"])
        printed.append(json.loads(capsys.readouterr().out))
    return printed


def _doctor_payloads(repo: Path, capsys) -> list[dict]:
    """doctor over the seeded artifacts, then with the stamp file unreadable,
    so each lane's `refusal` prints its null once and its sentence once. The
    first runs with the unit lane spelling pytest, so its `toolchain` prints a
    name and a source beside the ui lane's nulls."""
    stamps = repo / ".crapkit" / "artifacts.json"
    kept = stamps.read_bytes() if stamps.is_file() else None
    config = (repo / "crapkit.toml").read_bytes()
    unit = b'command = "python -c pass"'  # the unit lane's, the first one the file holds
    (repo / "crapkit.toml").write_bytes(config.replace(unit, b'command = "python -m pytest"', 1))
    clean = run_json(repo, capsys, "doctor", "--json")
    (repo / "crapkit.toml").write_bytes(config)
    stamps.write_text("not json", encoding="utf-8")
    refused = run_json(repo, capsys, "doctor", "--json")
    stamps.unlink()
    if kept is not None:
        stamps.write_bytes(kept)
    return [clean, refused]


def _mutate_payload(capsys) -> dict:
    """mutate --json as it prints one mutant no test judged and one that survived."""
    from crapkit.cli._shared import _print_json
    from crapkit.cli.analyses import _mutation_payload
    from crapkit.mutate import Mutant
    from crapkit.mutate_pool import MutantVerdict

    judged = [Mutant(path="a.py", line=1, op="> -> >=", original="x > 1", mutated="x >= 1"),
              Mutant(path="a.py", line=2, op="== -> !=", original="y == 1", mutated="y != 1")]
    _print_json(_mutation_payload(judged, [MutantVerdict.NO_VERDICT, MutantVerdict.SURVIVED],
                                  ["docs/notes.md"]))
    return json.loads(capsys.readouterr().out)


def _gate_and_errors(out: dict, repo: Path, capsys) -> None:
    """rescore with and without the gate over a file the reader refuses, and
    the error object: a name that is not UTF-8, then a function no run holds."""
    _print(out, repo, capsys, "rescore --json", "rescore", "src/app.ts", "--json")
    (repo / "src" / "b.ts").write_text(ARROW, encoding="utf-8")
    _print(out, repo, capsys, "rescore --gate --json",
           "rescore", "src/app.ts", "src/b.ts", "--gate", "--json")
    (repo / UNREADABLE).write_text(ARROW, encoding="utf-8")
    _print(out, repo, capsys, ERROR_OBJECT, "rescore", "--gate", "--json", UNREADABLE)
    (repo / UNREADABLE).unlink()
    _print(out, repo, capsys, ERROR_OBJECT, "brief", "src/app.ts", "nosuch", "--json")
    commit_all(repo, "an unread file")


def _verifies(out: dict, repo: Path, capsys) -> None:
    """verify with no marks anywhere, over its marks file, and with that file
    deleted, so every nullable field prints both forms; the report reads the
    committed marks between. The file the reader refuses fails the first
    verify and is gone from the second, which passes and tightens the mark."""
    verify = [_verify(repo, capsys)]
    (repo / "src" / "b.ts").unlink()
    _write_marks(repo)
    commit_all(repo, "marks")
    verify.append(_verify(repo, capsys))
    _print(out, repo, capsys, "ratchet report --json", "ratchet", "report", "--json")
    (repo / MARKS).unlink()
    verify.append(_verify(repo, capsys))
    out["verify --json"] = verify


def _verify_stop(out: dict, repo: Path, capsys) -> None:
    """verify --json at the claimed-name stop: a file a scope takes whose name
    is not UTF-8 in the index and nowhere on disk, which every OS can hold. The
    payload is a verify payload with run_id null; the name leaves the index after."""
    import subprocess

    name = UNREADABLE.encode("utf-8", "surrogateescape")
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=ARROW.encode(),
                          capture_output=True, check=True).stdout.strip()
    subprocess.run(["git", "update-index", "--add", "-z", "--index-info"], cwd=repo,
                   input=b"100644 " + blob + b"\t" + name + b"\0", capture_output=True, check=True)
    out["verify --json"].append(_verify(repo, capsys))
    subprocess.run(["git", "update-index", "--force-remove", "-z", "--stdin"], cwd=repo, input=name + b"\0",
                   capture_output=True, check=True)


def _every_kind_payload(capsys) -> dict:
    """verify --json's verdict part holding one finding of every kind, past a
    diff-coverage ceiling, so each kind's findings item and the ceiling's
    integer print: a run over one repo reaches a few kinds at a time."""
    from crapkit.cli._shared import _print_json
    from crapkit.cli.verifying import _verify_result
    from crapkit.gate import Unread, UnreadableName
    from crapkit.verify import GateViolation, RatchetRegression, Verdict, settle_verdict, with_diff_coverage

    gate = GateViolation("src/app.ts", "knotty ( n )", 21, 8, 0.25, 34.75, "decompose", True, "knotty ( n )")
    verdict = settle_verdict(Verdict.passing()._replace(
        claimed_names=(UnreadableName(UNREADABLE, "src", True),), gate_violations=[gate],
        unread_files=(Unread("src/b.ts", "src/b.ts:1: arrow refused", True),),
        ratchet_regressions=[RatchetRegression("src/app.ts", "dispatch ( kind )", 9.0, 12.5)],
        new_failures=["tests/t.py::test_a"], overridden=(gate._replace(start=40),)))
    lines = [("src/app.ts", 22)]
    verdict = with_diff_coverage(verdict, lines, 0, {"src/app.ts"})
    _print_json(_verify_result(verdict, 7, {"id": 1, "commit": "a" * 40}, "a" * 40, {"src/app.ts": [(21, 30)]},
                               lines, 0, 0, {"src/app.ts"}))
    return json.loads(capsys.readouterr().out)


def _override(out: dict, repo: Path, capsys) -> None:
    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(KNOTTY.replace("knotty", "knottier"))
    _print_all(out, repo, capsys, _OVERRIDE)


def _empty_scope(out: dict, repo: Path, capsys) -> None:
    """inventory over a scope that claims no file, so `empty_scopes` holds one."""
    with open(repo / "crapkit.toml", "a", encoding="utf-8", newline="\n") as fh:
        fh.write('\n[[scope]]\nname = "gone"\npaths = ["gone"]\nlanguages = ["typescript"]\n')
    _print(out, repo, capsys, "inventory --json", "inventory", "--json")


@pytest.fixture()
def payloads(repo, capsys, monkeypatch, tmp_path_factory) -> dict[str, list]:
    """Every declared payload, printed over one measured repo. The ranked
    payloads run twice, the second time over a run whose content record is
    gone, as on a run crapkit 0.8.0 wrote."""
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    out: dict[str, list] = {}
    _print_all(out, repo, capsys, _READS)
    out["doctor --json"] = _doctor_payloads(repo, capsys)
    _print_all(out, repo, capsys, _CLAIMS)
    _forget_content(repo)
    _print_all(out, repo, capsys, _RANKED.items())
    _gate_and_errors(out, repo, capsys)
    _verifies(out, repo, capsys)
    _verify_stop(out, repo, capsys)
    out["verify --json"].append(_every_kind_payload(capsys))
    _override(out, repo, capsys)
    _print(out, repo, capsys, "clean --json", "clean", "--dry-run", "--json")
    _empty_scope(out, repo, capsys)
    _print(out, repo, capsys, "runs prune --json", "runs", "prune", "--json")
    out["mutate --json"] = [_mutate_payload(capsys)]
    out[VERSION] = _version_payloads(capsys, monkeypatch, tmp_path_factory.mktemp("version"))
    return out


@NOT_UTF8_NAMES
def test_every_declared_payload_is_printed_here(payloads):
    assert set(PAYLOADS) == set(payloads)


def _maps(payload: str) -> set[str]:
    return {f.key.removesuffix(".*") + ".*" for f in FIELDS
            if f.payload == payload and f.key.endswith(".*")}


def _declared(payload: str) -> dict[str, AgentField]:
    return {f.key: f for f in FIELDS if f.payload == payload}


def _undeclared(name: str, samples: list) -> list[str]:
    declared, maps = _declared(name), _maps(name)
    return sorted({f"{name} {key}" for sample in samples
                   for key, _ in printed(sample, maps) if key not in declared})


def _mistyped(name: str, samples: list) -> list[str]:
    declared, maps = _declared(name), _maps(name)
    return sorted({f"{name} {key} = {value!r:.60}" for sample in samples
                   for key, value in printed(sample, maps)
                   if key in declared and not json_type_ok(value, declared[key].types)})


@NOT_UTF8_NAMES
def test_every_key_a_payload_prints_is_declared(payloads):
    """doctor --json printed each lane's `refusal` and nothing declared it."""
    gaps = [gap for name, samples in payloads.items() for gap in _undeclared(name, samples)]

    assert gaps == []


@NOT_UTF8_NAMES
def test_every_printed_value_carries_a_declared_type_and_null_only_where_declared(payloads):
    wrong = [bad for name, samples in payloads.items() for bad in _mistyped(name, samples)]

    assert wrong == []


def test_the_walk_keys_a_map_by_star_and_an_item_by_brackets():
    walked = dict(printed({"a": {"x.py": 6}, "b": [{"c": None}]}, {"a.*"}))

    assert walked == {"a": {"x.py": 6}, "a.*": 6, "b": [{"c": None}], "b[]": {"c": None},
                      "b[].c": None}


def test_a_bool_is_no_number_and_an_integer_is_one():
    assert not json_type_ok(True, ("integer",)) and not json_type_ok(False, ("number",))
    assert json_type_ok(3, ("number",)) and not json_type_ok(3.0, ("integer",))


def test_every_named_field_says_what_it_means():
    """A list's items and a map's values may go unnamed; a key may not."""
    silent = [f"{f.payload} {f.key}" for f in FIELDS
              if not f.key.endswith(("[]", "*")) and not f.description]

    assert silent == []


def test_each_added_field_is_the_declaration_its_payload_carries():
    declared = {(f.payload, f.key): f for f in FIELDS}

    assert [f for f in ADDED if declared.get((f.payload, f.key)) != f] == []


@NOT_UTF8_NAMES
def test_each_added_field_is_printed(payloads):
    missing = [f"{f.payload} {f.key}" for f in ADDED
               if not [v for payload in payloads[f.payload] for v in values_at(payload, f.key)]]

    assert missing == []


@NOT_UTF8_NAMES
def test_doctors_lane_toolchain_prints_a_runner_and_both_nulls(payloads):
    """`lanes[].toolchain` is {name, source}: a named runner and where it was
    read, or two nulls; three declarations, one per field."""
    printed = [lane["toolchain"] for payload in payloads["doctor --json"]
               for lane in payload["lanes"]]

    assert {"name": "pytest", "source": "command"} in printed
    assert {"name": None, "source": None} in printed
    assert {f.key for f in ADDED if f.key.startswith("lanes[].toolchain")} == {
        "lanes[].toolchain", "lanes[].toolchain.name", "lanes[].toolchain.source"}


@NOT_UTF8_NAMES
def test_doctors_lane_refusal_is_declared_as_a_sentence_or_null(payloads):
    """0.8.1 gives each doctor --json lane a `refusal`; the declaration left it
    out, so no check caught a type or a null the key does not allow."""
    field = added_field("doctor --json", "lanes[].refusal")

    assert field.types == ("string", "null")
    assert _null_forms(payloads, field) == {True, False}


@NOT_UTF8_NAMES
def test_the_nullable_fields_print_both_forms(payloads):
    """The verify trio holds no marks, the marks file on the tree, then none:
    each nullable field shows its value once and its null once."""
    nullable = [f for f in ADDED if f.nullable]
    seen = {(f.payload, f.key): _null_forms(payloads, f) for f in nullable}

    assert seen == {(f.payload, f.key): {True, False} for f in nullable}


def _null_forms(payloads: dict, field: AgentField) -> set[bool]:
    """Whether the field printed null, its value, or both, across its payloads."""
    return {value is None for payload in payloads[field.payload]
            for value in values_at(payload, field.key)}


@NOT_UTF8_NAMES
def test_the_unread_finding_has_one_shape_in_every_payload(payloads):
    """verify lists an unread file as an unread_file item in `findings`, so
    the gate block and the error object are the two lists left."""
    shapes = {f.payload: f.key for f in ADDED if f.key.endswith("unread_files")}
    keys = {payload: _entry_keys(payloads[payload][0], key) for payload, key in shapes.items()}

    assert set(shapes.values()) == {"gate.unread_files", "error.unread_files"}
    assert keys == {payload: [["dirty", "path", "reason"]] for payload in shapes}


FINDING_KINDS = ("unreadable_name", "gate_violation", "unread_file", "ratchet_regression", "new_failure",
                 "diff_uncovered", "overridden")


@NOT_UTF8_NAMES
def test_verify_prints_a_findings_item_of_every_kind_against_the_declaration(payloads):
    """The union item: each kind's item carries the six common fields and its
    own, each declared; the type checks above hold them to the declared types."""
    declared = {f.key.removeprefix("findings[].") for f in FIELDS
                if f.payload == "verify --json" and f.key.startswith("findings[].")}
    printed = [item for payload in payloads["verify --json"] for item in payload.get("findings", [])]

    assert sorted({item["kind"] for item in printed}) == sorted(FINDING_KINDS)
    assert sorted({key for item in printed for key in item} - declared) == []


@NOT_UTF8_NAMES
def test_verify_at_the_claimed_name_stop_prints_a_declared_verify_payload(payloads):
    (stop,) = [payload for payload in payloads["verify --json"] if payload["run_id"] is None]
    (item,) = stop["findings"]

    assert (item["kind"], item["exit_code"], stop["ok"]) == ("unreadable_name", 3, False)
    assert _undeclared("verify --json", [stop]) == [] and _mistyped("verify --json", [stop]) == []
    assert sorted(stop) == sorted(payloads["verify --json"][0])


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


def _served(payload: str) -> dict:
    (tool,) = [t for t in mcp_server.tool_listing() if t["name"] == MCP_TOOLS[payload]]
    return tool["outputSchema"]


@pytest.mark.parametrize("payload", sorted(MCP_TOOLS))
def test_each_mcp_tool_serves_its_payloads_declaration(payload: str):
    """`truncated` is the server's own: the answer budget adds it, no command prints it."""
    properties = dict(_served(payload)["properties"])
    del properties["truncated"]

    assert properties == PAYLOADS[payload]


@pytest.mark.parametrize("field", [f for f in ADDED if f.payload in MCP_TOOLS],
                         ids=lambda f: f"{MCP_TOOLS[f.payload]}:{f.key}")
def test_the_mcp_output_schema_declares_the_same_types(field: AgentField):
    schema = _schema_at(_served(field.payload), field.key.removesuffix("[]"))

    assert _declared_types(schema) == field.types
    assert schema["description"] == field.description


def test_the_agent_page_names_every_added_field():
    page = (ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8")
    names = {f.key.rsplit(".", 1)[-1].replace("[]", "") for f in ADDED}

    assert sorted(name for name in names if f"`{name}`" not in page) == []
