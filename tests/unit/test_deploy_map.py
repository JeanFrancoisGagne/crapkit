"""tests/deploy/MAP.toml ties what a user can do with crapkit to the deploy
cells that do it, and says why when no cell does.

A command a user copies from README, docs/*.md, the handbook, CHANGELOG's
"Upgrading from" sections or a plugin skill maps to cells or to an exclusion
by page and heading. One that installs crapkit, wires it into a harness, or
writes a hook or git config maps to cells: nothing else proves it still works.
Each cell the map names is a @cell in tests/deploy once its packet has landed,
and each @cell pytest collects is in the map with the cadence, OS, image and
packet the map gives, since run.py selects on exactly those.
"""
import dataclasses
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "tests" / "deploy"
MAP = tomllib.loads((DEPLOY / "MAP.toml").read_text(encoding="utf-8"))
PINS = tomllib.loads((ROOT / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))


def _load(name, path):
    """A kit module by path, so the unit session never puts tests/deploy on sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


docsnip = _load("deploy_map_docsnip", DEPLOY / "kit" / "docsnip.py")
kitcells = _load("deploy_map_cells", DEPLOY / "kit" / "cells.py")


# --- which fences a user copies ---------------------------------------------------

LAUNCH = r"(?:(?:python(?:\d(?:\.\d+)?)?|py)(?:\s+-\d+(?:\.\d+)?)?\s+-m\s+)?"
SURFACE_LINE = re.compile(rf"^(?:sudo\s+)?{LAUNCH}(?:pip3?|pipx|uvx?)\s|^(?:claude|codex)\s+plugin\b|^git\s+config\b")
HOOK_WRITE = re.compile(r"(?:>|\bSet-Content\b|\bOut-File\b|\btee\b|\bcp\b|\binstall\b).*"
                        r"(?:\.git/hooks/|githooks/|git-hooks/)|chmod\s+\+x\s+\S*hooks/|--git-common-dir\)/hooks/", re.I)
CRAPKIT_LINE = re.compile(rf"^{LAUNCH}crapkit(?:$|\s+(?:-|[a-z][a-z-]*(?:\s|$)))")
ENV_PREFIX = re.compile(r"^(?:[A-Za-z_]\w*=\S*\s+)+")
CONFIG_LANGS = {"json", "jsonc", "toml", "yaml", "yml"}
WIRING = re.compile(r'"command":\s*"crapkit|command\s*=\s*"crapkit|crapkit (?:mcp|claude-hook)|JeanFrancoisGagne/crapkit')


def line_kind(line):
    """surface: installs, wires or writes a hook or git config; crapkit: runs crapkit."""
    line = ENV_PREFIX.sub("", line.strip())
    if SURFACE_LINE.match(line) or HOOK_WRITE.search(line):
        return "surface"
    return "crapkit" if CRAPKIT_LINE.match(line) else None


def fence_kinds(fence):
    if fence.lang in CONFIG_LANGS and WIRING.search(fence.text):
        return {"surface"}
    return set(map(line_kind, docsnip.commands(fence))) - {None}


def fence_kind(fence):
    """The strongest kind of any line the fence holds, or None when it runs nothing."""
    kinds = fence_kinds(fence)
    return "surface" if "surface" in kinds else min(kinds, default=None)


def pages(root=ROOT):
    docs = sorted(path.relative_to(root).as_posix() for path in (root / "docs").glob("*.md"))
    skills = sorted(path.relative_to(root).as_posix() for path in (root / "plugin" / "skills").rglob("SKILL.md"))
    return ["README.md", *docs, "docs/handbook.html", "CHANGELOG.md", *skills]


def in_scope(page, fence):
    """CHANGELOG counts only under its "Upgrading from" headings."""
    return page != "CHANGELOG.md" or fence.heading.startswith("Upgrading from")


def documented_commands(root=ROOT):
    """(page, heading, line, kind) for every fence the map must cover."""
    found = []
    for page in pages(root):
        for fence in docsnip.fences(page, base=root):
            kind = fence_kind(fence) if in_scope(page, fence) else None
            if kind:
                found.append((page, fence.heading, fence.line, kind))
    return found


def doc_entries(data=MAP):
    """(page, heading) -> the [[doc]] entry that covers it."""
    return {(entry["page"], heading): entry for entry in data["doc"] for heading in entry["headings"]}


COMMANDS = documented_commands()


def _fence(text, lang=""):
    return docsnip.Fence("page.md", "heading", lang, text, 1)


@pytest.mark.parametrize("text, lang, kind", [
    ("pip install crapkit", "", "surface"),
    ("python3.12 -m pip install --user crapkit", "", "surface"),
    ("uvx crapkit init", "", "surface"),
    ("claude plugin marketplace add JeanFrancoisGagne/crapkit", "", "surface"),
    ('git config merge.crapkit-ratchet.driver "crapkit ratchet merge %O %A %B"', "", "surface"),
    ("cat > .git/hooks/pre-commit <<'EOF'\n#!/bin/sh\nexec crapkit hook-precommit\nEOF", "sh", "surface"),
    ('hook="$(git rev-parse --git-common-dir)/hooks/pre-commit"\ncat > "$hook" <<\'EOF\'\n#!/bin/sh\nEOF', "sh",
     "surface"),
    ('$hook = "$(git rev-parse --git-common-dir)/hooks/pre-commit"\nSet-Content -Path $hook -Value x', "powershell",
     "surface"),
    ('{"mcpServers": {"crapkit": {"command": "crapkit", "args": ["mcp"]}}}', "json", "surface"),
    ("$ crapkit doctor\ncrapkit: lane 'py': positional argument narrows", "", "crapkit"),
    ("CRAPKIT_OVERRIDE_REASON=hotfix python -m crapkit verify", "", "crapkit"),
    ("crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6", "", None),
    ('{"flag": "measured", "uncovered_lines": null}', "json", None),
    ("npm i -D @vitest/coverage-v8@3", "", None),
])
def test_a_fence_counts_by_the_strongest_command_it_holds(text, lang, kind):
    assert fence_kind(_fence(text, lang)) == kind


def test_changelog_counts_only_its_upgrading_sections():
    fence = docsnip.Fence("CHANGELOG.md", "A composite action", "yaml", "uses: JeanFrancoisGagne/crapkit@v0.4.8", 1)

    assert in_scope("CHANGELOG.md", fence) is False
    assert in_scope("CHANGELOG.md", dataclasses.replace(fence, heading="Upgrading from 0.4.6")) is True


def unmapped(commands=COMMANDS, data=MAP):
    """Documented commands under a heading no [[doc]] entry covers."""
    entries = doc_entries(data)
    return [f"{page}:{line} under {heading!r}" for page, heading, line, _ in commands if (page, heading) not in entries]


def stale(commands=COMMANDS, data=MAP):
    """[[doc]] headings that no longer hold a documented command."""
    documented = {(page, heading) for page, heading, _, _ in commands}
    return [key for key in doc_entries(data) if key not in documented]


def test_every_documented_command_maps_to_cells_or_an_exclusion():
    assert unmapped() == [], "add a [[doc]] entry to tests/deploy/MAP.toml naming the cells that run it"


def test_a_heading_the_map_drops_is_caught():
    first = MAP["doc"][0]
    dropped = unmapped(data={**MAP, "doc": MAP["doc"][1:]})

    assert dropped and all(f"under {heading!r}" in " ".join(dropped) for heading in first["headings"])


def test_an_install_hook_or_config_fence_maps_to_cells_not_an_exclusion():
    entries = doc_entries()
    excluded = [f"{page}:{line} under {heading!r}" for page, heading, line, kind in COMMANDS
                if kind == "surface" and not entries.get((page, heading), {}).get("cells")]

    assert excluded == []


def test_every_doc_entry_still_covers_a_documented_command():
    assert stale() == []


def test_a_doc_entry_for_a_heading_that_moved_is_caught():
    moved = {**MAP, "doc": [*MAP["doc"], {"page": "README.md", "headings": ["A heading no page has"], "exclude": "x"}]}

    assert stale(data=moved) == [("README.md", "A heading no page has")]


def test_each_doc_entry_names_cells_or_why_no_cell_runs_it():
    both_or_neither = [entry["page"] for entry in MAP["doc"] if bool(entry.get("cells")) == bool(entry.get("exclude"))]

    assert both_or_neither == []


# --- the map holds together ---------------------------------------------------------

LIST_SECTIONS = ("channels", "harnesses", "upgrade_sources", "state", "use_cases")


def _listed(data, section):
    return [(section, key, cell) for key, cells in data[section].items() for cell in cells]


def _doc_refs(data):
    return [("doc", entry["page"], cell) for entry in data["doc"] for cell in entry.get("cells", [])]


def _single(data, section, field):
    return [(section, key, item[field]) for key, item in data[section].items() if field in item]


def referenced_cells(data=MAP):
    """(section, key, cell) for every cell a section of the map names."""
    refs = _doc_refs(data) + _single(data, "pairs", "cell") + _single(data, "unknowns", "probe")
    return refs + [ref for section in LIST_SECTIONS for ref in _listed(data, section)]


def test_every_cell_a_section_names_is_a_cell_of_the_map():
    assert [ref for ref in referenced_cells() if ref[2] not in MAP["cell"]] == []


def test_a_reference_to_a_cell_the_map_lacks_is_caught():
    data = {**MAP, "channels": {"pip venv": ["lin-no-such-cell"]}}

    assert ("channels", "pip venv", "lin-no-such-cell") in referenced_cells(data)


def _known(value, allowed, what):
    return [] if value in allowed else [f"{what} {value!r}"]


def _placement(cell):
    """A job cell names workflow:job; a pytest cell names its os, and an image when it has one."""
    if "job" in cell:
        return _known(cell["job"].partition(":")[0], ("ci.yml", "deploy.yml"), "workflow")
    return _known(cell.get("os"), kitcells.OSES, "os") + _known(cell.get("image", "cells"), kitcells.IMAGES, "image")


def cell_problems(cell, data=MAP):
    problems = _known(cell["packet"], data["packets"], "packet") + _placement(cell)
    problems += [problem for cadence in cell["cadence"].split("+")
                 for problem in _known(cadence, kitcells.CADENCES, "cadence")]
    return problems + _known(cell.get("blocked", "none"), {"none", *data["gaps"]}, "gap")


def test_every_cell_names_a_known_packet_cadence_placement_and_gap():
    wrong = {cell_id: cell_problems(cell) for cell_id, cell in MAP["cell"].items() if cell_problems(cell)}

    assert wrong == {}


def test_a_cell_with_an_unknown_cadence_or_os_is_caught():
    cell = {"packet": "deploy-git", "cadence": "hourly", "os": "plan9", "blocked": "no-such-gap"}

    assert cell_problems(cell) == ["os 'plan9'", "cadence 'hourly'", "gap 'no-such-gap'"]


def _blockers(data):
    return {item.get("blocked") for section in ("cell", "jobs") for item in data[section].values()}


def test_every_gap_blocks_something_and_says_what_is_missing():
    assert sorted(set(MAP["gaps"]) - _blockers(MAP)) == []
    assert all(len(text.split()) > 5 for text in MAP["gaps"].values())


def test_every_wheelhouse_release_is_an_upgrade_source_or_excluded():
    covered = set(MAP["upgrade_sources"]) | set(MAP["excluded"]["upgrade_source"])

    assert sorted(set(PINS["wheelhouse"]["crapkit"]) - covered) == []
    assert "N-1" in MAP["upgrade_sources"]


@pytest.mark.parametrize("kind", sorted(MAP["excluded"]))
def test_every_exclusion_has_a_reason_and_a_revisit_date(kind):
    entries = MAP["excluded"][kind].values()

    assert all(entry["reason"] and isinstance(entry["revisit"], date) for entry in entries)


def test_every_pair_names_its_consumer_and_a_cell_or_a_reason():
    wrong = [key for key, pair in MAP["pairs"].items()
             if pair["consumer"] not in ("git", "plugin") or ("cell" in pair) == ("reason" in pair)]

    assert wrong == []


def test_every_open_question_names_a_probe_cell_or_a_manual_check():
    assert [key for key, item in MAP["unknowns"].items() if ("probe" in item) == ("manual" in item)] == []


def test_every_scope_path_is_in_the_tree():
    assert [path for path in MAP["scope"]["paths"] if not (ROOT / path).exists()] == []


def test_every_harness_profile_in_the_tree_has_a_harness_entry():
    profiles = DEPLOY / "profiles"
    if not profiles.is_dir():
        pytest.skip("deploy-harnesses has not landed: tests/deploy/profiles/ is not in the tree")

    assert sorted(path.stem for path in profiles.glob("*.toml") if path.stem not in MAP["harnesses"]) == []


# --- the map against the @cell decorators in tests/deploy ---------------------------

PLUGIN = '''
import json
import os


def pytest_collection_finish(session):
    found = []
    for item in session.items:
        mark = item.get_closest_marker("deploy_cell")
        if mark is not None:
            found.append({**mark.kwargs, "nodeid": item.nodeid,
                          "module_packet": getattr(item.module, "PACKET", None)})
    with open(os.environ["DEPLOY_MAP_CELLS"], "w", encoding="utf-8") as out:
        json.dump({"items": len(session.items), "cells": found}, out, default=str)
'''


@pytest.fixture(scope="module")
def tree_cells(tmp_path_factory):
    """Every @cell pytest collects under tests/deploy, with its decorator's fields."""
    where = tmp_path_factory.mktemp("cells")
    (where / "deploy_map_cells.py").write_text(PLUGIN, encoding="utf-8")
    found = where / "cells.json"
    paths = [str(where), *filter(None, [os.environ.get("PYTHONPATH")])]
    env = dict(os.environ, CRAPKIT_DEPLOY="1", DEPLOY_MAP_CELLS=str(found), PYTHONPATH=os.pathsep.join(paths))
    argv = [sys.executable, "-m", "pytest", "tests/deploy", "--collect-only", "-q", "-p", "no:cacheprovider",
            "-p", "no:randomly", "-p", "deploy_map_cells"]
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=HANG_SECONDS)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(found.read_text(encoding="utf-8"))


def cell_id(meta):
    """A parametrized cell's id without its parameter."""
    return meta["id"].split("[")[0]


def landed(packet, data=MAP, root=ROOT):
    return any((root / module).exists() for module in data["packets"][packet]["modules"])


def pytest_cells(packet, data=MAP):
    """The cells pytest runs for a packet; a job cell is a workflow job instead."""
    return sorted(cell for cell, meta in data["cell"].items() if meta["packet"] == packet and "job" not in meta)


def test_the_plugin_records_each_cells_decorator_fields_and_the_item_count(tmp_path, monkeypatch):
    namespace = {}
    exec(PLUGIN, namespace)
    mark = SimpleNamespace(kwargs={"id": "lin-x", "cadence": "push"})
    cell = SimpleNamespace(nodeid="tests/deploy/test_x.py::test_x", module=SimpleNamespace(PACKET="deploy-git"),
                           get_closest_marker=lambda name: mark)
    kit = SimpleNamespace(nodeid="tests/deploy/test_kit.py::test_y", module=SimpleNamespace(),
                          get_closest_marker=lambda name: None)
    monkeypatch.setenv("DEPLOY_MAP_CELLS", str(tmp_path / "cells.json"))

    namespace["pytest_collection_finish"](SimpleNamespace(items=[cell, kit]))

    assert json.loads((tmp_path / "cells.json").read_text(encoding="utf-8")) == {"items": 2, "cells": [
        {"id": "lin-x", "cadence": "push", "nodeid": "tests/deploy/test_x.py::test_x", "module_packet": "deploy-git"}]}


def test_the_collection_reads_the_deploy_tree_not_an_empty_one(tree_cells):
    """The kit's own tests are always there, so zero items means the CRAPKIT_DEPLOY gate kept the tree out."""
    assert tree_cells["items"] > 0


def test_every_cell_in_the_tree_is_in_the_map(tree_cells):
    assert sorted({cell_id(meta) for meta in tree_cells["cells"]} - set(MAP["cell"])) == []


@pytest.mark.parametrize("packet", sorted(MAP["packets"]))
def test_every_mapped_cell_of_a_landed_packet_is_a_cell_in_the_tree(packet, tree_cells):
    wanted = pytest_cells(packet)
    if wanted and not landed(packet):
        pytest.skip(f"{packet} has not landed (none of its modules is in the tree): {', '.join(wanted)}")

    assert sorted(set(wanted) - {cell_id(meta) for meta in tree_cells["cells"]}) == []


def _parts(value):
    """A cadence or os as a set: "weekly+published", "linux,windows" or a list."""
    if isinstance(value, (list, tuple)):
        return frozenset(value)
    return frozenset(part.strip() for part in re.split(r"[+,]", value or "") if part.strip())


FLAGS = {"online": False, "docker_host": False, "nonblocking": False, "real_cli": True}


def _selection(fields):
    """What run.py selects a cell on. The image counts only for Linux, the one os run in a container."""
    image = fields.get("image") if _parts(fields.get("os")) == {"linux"} else "native"
    return {"cadence": _parts(fields.get("cadence")), "os": _parts(fields.get("os")), "image": image,
            "packet": fields.get("packet"), **{flag: bool(fields.get(flag, default)) for flag, default in FLAGS.items()}}


def disagreements(meta, data=MAP):
    """Each field where a @cell and its map entry differ."""
    mapped = data["cell"].get(cell_id(meta))
    if mapped is None:
        return []
    actual = _selection({**meta, "packet": meta.get("packet") or meta.get("module_packet")})
    wanted = _selection(mapped)
    return [f"{cell_id(meta)} {field}: @cell {actual[field]!r}, map {wanted[field]!r}"
            for field in wanted if actual[field] != wanted[field]]


def test_each_cell_in_the_tree_carries_the_fields_the_map_gives(tree_cells):
    assert [problem for meta in tree_cells["cells"] for problem in disagreements(meta)] == []


def test_a_native_linux_cell_left_on_the_default_image_is_caught():
    meta = {"id": "lin-native-start", "cadence": "push", "os": "linux", "image": "core", "packet": "deploy-channels"}

    assert disagreements(meta) == ["lin-native-start image: @cell 'core', map None"]


def test_a_windows_cell_matches_whatever_image_its_decorator_defaulted_to():
    meta = {"id": "win-pip-start", "cadence": "push", "os": "windows", "image": "core", "packet": "deploy-channels"}

    assert disagreements(meta) == []
