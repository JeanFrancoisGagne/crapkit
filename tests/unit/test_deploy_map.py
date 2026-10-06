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
import warnings
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


def oses(cell):
    """The OSes a cell names: one, or a list when its tests run on each of them."""
    value = cell.get("os")
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _where(cell):
    problems = [problem for name in oses(cell) for problem in _known(name, kitcells.OSES, "os")]
    return problems + _known(cell.get("image", "cells"), kitcells.IMAGES, "image")


def _placement(cell):
    """A job cell names workflow:job, and the os and image of the @cell that models the job when
    one does; a pytest cell names its os, and an image when it has one."""
    if "job" not in cell:
        return _where(cell)
    workflow = _known(cell["job"].partition(":")[0], ("ci.yml", "deploy.yml"), "workflow")
    return workflow + (_where(cell) if "os" in cell else [])


def blocking_gaps(item):
    """The gaps a cell or a job names: one that blocks all of it, or one per os it blocks."""
    blocked = item.get("blocked")
    if isinstance(blocked, dict):
        return list(blocked.values())
    return [blocked] if blocked else []


def _row(cell, data):
    """A cell of an every-harness row names a key of [every_harness]."""
    return _known(cell["row"], data.get("every_harness", {}), "row") if "row" in cell else []


def cell_problems(cell, data=MAP):
    problems = _known(cell["packet"], data["packets"], "packet") + _placement(cell)
    problems += [problem for cadence in cell["cadence"].split("+")
                 for problem in _known(cadence, kitcells.CADENCES, "cadence")]
    problems += [problem for gap in blocking_gaps(cell) for problem in _known(gap, data["gaps"], "gap")]
    return problems + _row(cell, data)


def test_every_cell_names_a_known_packet_cadence_placement_and_gap():
    wrong = {cell_id: cell_problems(cell) for cell_id, cell in MAP["cell"].items() if cell_problems(cell)}

    assert wrong == {}


def test_a_cell_with_an_unknown_cadence_or_os_is_caught():
    cell = {"packet": "deploy-git", "cadence": "hourly", "os": "plan9", "blocked": "no-such-gap"}

    assert cell_problems(cell) == ["os 'plan9'", "cadence 'hourly'", "gap 'no-such-gap'"]


def test_a_cell_on_two_oses_names_each_one_known():
    cell = {"packet": "deploy-channels", "cadence": "push", "os": ["linux", "windows"], "image": "core"}

    assert cell_problems(cell) == []
    assert cell_problems({**cell, "os": ["linux", "plan9"]}) == ["os 'plan9'"]


def test_a_job_cell_that_a_test_also_models_names_a_known_os_and_image():
    cell = {"packet": "deploy-action", "cadence": "push", "job": "ci.yml:deploy-action", "os": "linux", "image": "ci"}

    assert cell_problems(cell) == []
    assert cell_problems({**cell, "os": "plan9", "image": "huge"}) == ["os 'plan9'", "image 'huge'"]


def test_a_cell_whose_row_the_every_harness_table_lacks_is_caught_by_name():
    planted = {"packet": "deploy-harnesses", "cadence": "nightly", "os": "linux", "image": "full",
               "row": "criterion-11"}
    data = {**MAP, "cell": {**MAP["cell"], "lin-planted": planted}}

    assert {cell_id: cell_problems(cell, data) for cell_id, cell in data["cell"].items()
            if cell_problems(cell, data)} == {"lin-planted": ["row 'criterion-11'"]}
    assert cell_problems({**planted, "row": "lanes-visible-04"}) == []


def test_a_gap_that_blocks_one_os_of_a_cell_is_a_known_gap():
    cell = {"packet": "deploy-channels", "cadence": "nightly", "os": ["linux", "windows"], "docker_host": True}

    assert cell_problems({**cell, "blocked": {"windows": next(iter(MAP["gaps"]))}}) == []
    assert cell_problems({**cell, "blocked": {"windows": "no-such-gap"}}) == ["gap 'no-such-gap'"]


def _blockers(data):
    return {gap for section in ("cell", "jobs") for item in data[section].values() for gap in blocking_gaps(item)}


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


# --- the every-harness rows -------------------------------------------------------
#
# A row is a set of cells that runs under each harness pins.toml pins, fresh
# and upgrade. Its tag says what a nightly run takes: "all" every harness,
# "core" the 3 core-image ones. Every other cadence takes every harness.
# These are the rows the 0.9.0 tickets add, keyed by the ticket whose Deploy
# cells section adds the row's cells (build-map-13 section 1); a row a later
# ticket adds passes beside them.

ROWS = {
    "lanes-visible-06": "all", "m1-readers-09": "all", "mission-4-09/harness": "all", "mission-9-06": "all",
    "protocol-2-step-08": "all", "schema-2-15": "all",
    "gate-group-06": "core", "lanes-visible-04": "core", "lanes-visible-05": "core", "lanes-visible-08": "core",
    "lanes-visible-09": "core", "lanes-visible-10": "core", "m1-readers-03": "core", "m1-readers-04": "core",
    "m1-readers-05": "core", "m1-readers-06": "core", "m1-readers-07": "core", "m5-comment-fixture-03": "core",
    "m5-comment-fixture-04": "core", "m5-comment-fixture-05": "core", "m5-comment-fixture-07": "core",
    "m5-comment-fixture-13": "core", "mission-2-14": "core", "mission-4-09/cli": "core",
}
TAGS = ("all", "core")


def tag_problems(data=MAP):
    """Each [every_harness] entry that is not exactly `nightly = "all"` or `nightly = "core"`."""
    return [f"{key}: {entry!r}" for key, entry in data["every_harness"].items()
            if set(entry) != {"nightly"} or entry["nightly"] not in TAGS]


def _with_row(key, entry):
    return {**MAP, "every_harness": {**MAP["every_harness"], key: entry}}


def test_every_row_has_one_key_nightly_tagged_all_or_core():
    assert tag_problems() == []


@pytest.mark.parametrize("entry", [{"nightly": "some"}, {"nightly": "core", "release": "core"}, {}])
def test_a_row_with_another_tag_or_a_second_key_is_caught(entry):
    assert tag_problems(_with_row("lin-planted-row", entry)) == [f"lin-planted-row: {entry!r}"]


def row_drift(data=MAP):
    """Each row of ROWS that [every_harness] lacks or tags otherwise."""
    tags = {key: entry.get("nightly") for key, entry in data["every_harness"].items()}
    return [f"{key}: map {tags.get(key)!r}, ticket {tag!r}" for key, tag in ROWS.items() if tags.get(key) != tag]


def test_every_harness_holds_each_ticket_row_with_its_tag():
    assert (len(ROWS), list(ROWS.values()).count("all")) == (24, 6)
    assert row_drift() == []


def test_a_dropped_or_flipped_row_is_caught_and_a_later_row_passes():
    dropped = {key: entry for key, entry in MAP["every_harness"].items() if key != "m1-readers-09"}

    assert row_drift({**MAP, "every_harness": dropped}) == ["m1-readers-09: map None, ticket 'all'"]
    assert row_drift(_with_row("lanes-visible-04", {"nightly": "all"})) == [
        "lanes-visible-04: map 'all', ticket 'core'"]
    assert row_drift(_with_row("a-later-ticket-01", {"nightly": "core"})) == []


def row_cells(row, data=MAP):
    """The [cell] entries that carry this row."""
    return sorted(cell for cell, entry in data["cell"].items() if entry.get("row") == row)


def pending(row, data=MAP):
    """Why the row's check skips while no [cell] entry carries it, else None."""
    if row_cells(row, data):
        return None
    return (f"{row} is pending: no [cell] entry in tests/deploy/MAP.toml carries row = {row!r} yet, so the ticket "
            f"that adds the row's cells has not landed; at the release candidate a skip here names untagged cells")


@pytest.mark.parametrize("row", sorted(MAP["every_harness"]))
def test_every_row_is_carried_by_the_cells_its_ticket_adds(row):
    reason = pending(row)
    if reason:
        pytest.skip(reason)

    assert [cell for cell in row_cells(row) if "job" in MAP["cell"][cell]] == [], "a row's cells are pytest cells"


def _meta(image="full", row="lanes-visible-04", os="linux"):
    """A cell's metadata as cells.cell_meta reads it off the item @cell marked. The unit
    session registers none of the deploy markers, which tests/deploy/conftest.py does."""
    def test():
        pass
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnknownMarkWarning)
        test = kitcells.cell("lin-row-x", channel="pip venv", harness="gemini-cli", scenario="fresh: x",
                             use_cases="x", os=os, image=image, cadence="nightly", row=row)(test)
    mark = next(mark for mark in test.pytestmark if mark.name == "deploy_cell")
    item = SimpleNamespace(get_closest_marker=lambda name: mark if name == "deploy_cell" else None,
                           module=SimpleNamespace(PACKET="lanes-visible-04"))
    return kitcells.cell_meta(item)


KEEPS = [  # (the item, what a nightly run does with it)
    (_meta("full"), False),
    (_meta("gui"), False),
    (_meta("core"), True),
    (_meta(None, os="windows"), True),
    (_meta("full", row="lanes-visible-06"), True),
    (_meta("full", row=None), True),
]


def test_cell_records_the_row_with_the_cells_other_fields():
    assert _meta()["row"] == "lanes-visible-04"
    assert ("cell_row", "lanes-visible-04") in kitcells.properties(_meta())
    assert "cell_row" not in dict(kitcells.properties(_meta(row=None)))


@pytest.mark.parametrize("meta, kept", KEEPS)
def test_a_nightly_run_drops_a_core_rows_items_on_the_full_image_and_those_built_on_it(meta, kept):
    assert kitcells.nightly_keeps(meta, "nightly", MAP["every_harness"]) is kept


@pytest.mark.parametrize("cadence", ["push", "weekly", "release", "published", None])
@pytest.mark.parametrize("meta", [meta for meta, _ in KEEPS])
def test_every_other_cadence_keeps_every_row_on_every_harness(meta, cadence):
    assert kitcells.nightly_keeps(meta, cadence, MAP["every_harness"]) is True


@pytest.mark.parametrize("cadence", ["nightly", "push", "release", None])
def test_an_item_whose_row_the_map_lacks_is_refused_naming_the_row(cadence):
    with pytest.raises(ValueError, match=r"lin-row-x.*'criterion-11'.*\[every_harness\]"):
        kitcells.nightly_keeps(_meta(row="criterion-11"), cadence, MAP["every_harness"])


def test_a_row_turns_from_pending_to_carried_and_back_with_its_cells():
    # The map's cells with their rows stripped, so the check holds after the
    # ticket that adds lanes-visible-04's cells tags them in the live map.
    bare = {**MAP, "cell": {cell: {key: value for key, value in entry.items() if key != "row"}
                            for cell, entry in MAP["cell"].items()}}
    entry = {"packet": "deploy-harnesses", "cadence": "nightly", "os": "linux", "image": "full",
             "row": "lanes-visible-04"}
    carried = {**bare, "cell": {**bare["cell"], "lin-planted": entry}}

    assert "lanes-visible-04 is pending" in pending("lanes-visible-04", bare)
    assert pending("lanes-visible-04", carried) is None
    assert "lanes-visible-04 is pending" in pending("lanes-visible-04", {**carried, "cell": bare["cell"]})


# --- the cell ids the 0.9.0 tickets name -----------------------------------------
#
# Each id enters the map under the packet of the ticket whose Deploy cells
# section adds its test, so the landed-packet check below skips it by name
# until that ticket's module is in the tree (build-map-13 section 3). An id
# whose module the tree already holds sits under `unowned` instead, and its
# ticket moves it in the change that adds its @cell.

ADDED = {
    "lin-cc-only-to-measured": "metric-stamp-07",
    **dict.fromkeys(["lin-criterion-fresh", "lin-criterion-upgrade", "win-criterion-fresh", "win-criterion-upgrade"],
                    "criterion-13"),
    **dict.fromkeys(["lin-marked-advisory", "lin-marked-override", "lin-marked-rise-precommit",
                     "lin-marked-rise-route1", "lin-marked-warn", "lin-up-marked-advisory-n1", "lin-up-marked-rise-n1",
                     "win-marked-advisory", "win-marked-rise-ps51", "win-up-marked-rise-n1"], "mission-3"),
    "lin-mixed-0.8.0-reads-0.9.0-marks": "metric-stamp-04",
    **dict.fromkeys(["lin-signature-carry", "lin-up-signature-carry-n1", "win-m4-carry", "win-up-m4-carry"],
                    "mission-4-09"),
    **dict.fromkeys(["lin-step-coverage-py-fresh", "lin-step-coverage-py-upgrade", "win-step-coverage-py-fresh",
                     "win-step-coverage-py-upgrade"], "m6-step-coverage-06"),
    **dict.fromkeys(["lin-step-coverage-jest-fresh", "lin-step-coverage-vitest-fresh", "win-step-coverage-jest-fresh",
                     "win-step-coverage-vitest-fresh"], "unowned"),
    **dict.fromkeys(["lin-step-neutral-fresh", "lin-step-neutral-upgrade", "win-step-neutral-fresh"],
                    "protocol-2-step-03a"),
    "lin-ts-provider-mismatch": "unowned",
    **dict.fromkeys(["lin-up-languages-0.8.1", "win-up-languages-0.8.1"], "mission-2-14"),
    "lin-up-pip-0.8.1-seed-converts": "metric-stamp-06",
    "win-claude-bash-fresh": "protocol-2-step-07",
    "win-claude-p2-fresh": "protocol-2-step-06",
    "win-codex-adapter-fresh": "protocol-2-step-11",
    "win-cursor-adapter-fresh": "protocol-2-step-12",
}
# The module each adding ticket creates, which its packet names.
TICKET_MODULES = {
    "metric-stamp-07": "test_cc_only_to_measured.py", "criterion-13": "test_criterion.py",
    "mission-3": "test_mission3_marked_rise.py", "metric-stamp-04": "test_mixed_versions.py",
    "mission-4-09": "test_mission4_signature_carry.py", "m6-step-coverage-06": "test_step_coverage_cells.py",
    "protocol-2-step-03a": "test_step_neutral_cells.py", "mission-2-14": "test_language_map_cells.py",
    "metric-stamp-06": "test_seed_converts.py", "protocol-2-step-07": "test_claude_bash_cells.py",
    "protocol-2-step-06": "test_claude_p2_cells.py", "protocol-2-step-11": "test_codex_adapter_cells.py",
    "protocol-2-step-12": "test_cursor_adapter_cells.py",
}


def unlisted(data=MAP):
    """The ids of ADDED the map lacks, or holds with a problem cell_problems names."""
    return {cell: cell_problems(data["cell"][cell], data) if cell in data["cell"] else ["not in [cell]"]
            for cell in ADDED if cell not in data["cell"] or cell_problems(data["cell"][cell], data)}


def test_every_cell_id_the_tickets_name_is_a_cell_of_the_map():
    assert len(ADDED) == 39
    assert unlisted() == {}


def test_a_ticket_cell_id_the_map_drops_is_caught():
    dropped = {cell: entry for cell, entry in MAP["cell"].items() if cell != "win-m4-carry"}

    assert unlisted({**MAP, "cell": dropped}) == {"win-m4-carry": ["not in [cell]"]}


def test_each_adding_tickets_packet_names_the_module_it_creates():
    named = {packet: f"tests/deploy/{module}" in MAP["packets"].get(packet, {}).get("modules", [])
             for packet, module in TICKET_MODULES.items()}

    assert [packet for packet, found in named.items() if not found] == []


def test_each_ticket_cell_id_waits_in_its_tickets_packet_until_its_cell_is_in_the_tree(tree_cells):
    """Its ticket may move the id once its @cell exists (out of `unowned`, into the module's packet)."""
    in_tree = {cell_id(meta) for meta in tree_cells["cells"]}
    moved = {cell: MAP["cell"][cell]["packet"] for cell, packet in ADDED.items()
             if cell not in in_tree and MAP["cell"][cell]["packet"] != packet}

    assert moved == {}


# --- no release entry runs a cell that needs the tag on GitHub ---------------------------------
#
# The release cadence runs on the tag commit before stage 2b pushes the tag,
# PyPI files and registry entry. A cell the published cadence runs copies the
# README lines that name vVERSION, so in a release run it fails on a surface
# that does not exist yet: 0.8.1's release run spent weekly-online's 30-minute
# entry on "git checkout v0.8.1 failed", and latest-harnesses selected the same
# cells through the release cadence's `online` term.

def needs_the_tag(cell) -> bool:
    """A cell the published cadence runs reads a surface stage 2b publishes."""
    return "published" in _parts(cell["cadence"])


def tag_bound_in_release(jobs=MAP["jobs"], cells=MAP["cell"]):
    """The cells that need the tag on GitHub and that a release-cadence entry
    runs, by run.py's own -m expression for each of the entry's calls."""
    from test_deploy_workflows import invocations, reaches
    calls = [(when, argv) for when, argv in invocations(jobs) if when == "release"]
    return sorted(cell_id for cell_id, cell in cells.items()
                  if needs_the_tag(cell) and reaches(cell_id, cell, "release", calls))


def test_no_release_entry_runs_a_cell_that_needs_the_tag_on_github():
    assert tag_bound_in_release() == []


def _with_release(name):
    entry = MAP["jobs"][name]
    return {**MAP["jobs"], name: {**entry, "when": sorted({*entry["when"], "release"})}}


def test_an_online_entry_back_in_the_release_cadence_is_caught():
    tagged = ["lin-online-marketplaces", "lin-online-pipgit", "lin-online-pypi", "published-precommit",
              "published-registry"]

    assert tag_bound_in_release(_with_release("weekly-online")) == tagged
    assert tag_bound_in_release(_with_release("published-online")) == tagged


def test_latest_harnesses_without_its_packet_is_caught():
    entry = MAP["jobs"]["latest-harnesses"]
    unnarrowed = [args.replace(" --packet deploy-harnesses", "") for args in entry["runs"]]
    jobs = {**MAP["jobs"], "latest-harnesses": {**entry, "runs": unnarrowed}}

    assert "release" in entry["when"]
    assert "lin-online-pipgit" in tag_bound_in_release(jobs)


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
    """What run.py selects a cell on, and the every-harness row a nightly run drops it by. The image
    counts only where the cell runs in a container: on Linux."""
    image = fields.get("image") if "linux" in _parts(fields.get("os")) else "native"
    return {"cadence": _parts(fields.get("cadence")), "os": _parts(fields.get("os")), "image": image,
            "packet": fields.get("packet"), "row": fields.get("row"),
            **{flag: bool(fields.get(flag, default)) for flag, default in FLAGS.items()}}


def _shown(value):
    return sorted(value) if isinstance(value, frozenset) else value


def _agrees(field, actual, wanted):
    """A cell's tests may split its cadences (lin-up-pip-0.7.6 runs 3.12 on push and the other
    Pythons nightly), so each runs on cadences the map gives; every other field matches exactly."""
    return actual <= wanted if field == "cadence" else actual == wanted


def disagreements(meta, data=MAP):
    """Each field where a @cell and its map entry differ."""
    mapped = data["cell"].get(cell_id(meta))
    if mapped is None:
        return []
    actual = _selection({**meta, "packet": meta.get("packet") or meta.get("module_packet")})
    wanted = _selection(mapped)
    return [f"{cell_id(meta)} {field}: @cell {_shown(actual[field])!r}, map {_shown(wanted[field])!r}"
            for field in wanted if not _agrees(field, actual[field], wanted[field])]


def uncovered_cadences(metas, data=MAP):
    """Each cadence the map gives a cell that none of the cell's tests runs on."""
    runs = {}
    for meta in metas:
        runs.setdefault(cell_id(meta), set()).update(_parts(meta.get("cadence")))
    return [f"{cell} never runs on {cadence}" for cell, seen in sorted(runs.items()) if cell in data["cell"]
            for cadence in sorted(_parts(data["cell"][cell]["cadence"]) - seen)]


def test_each_cell_in_the_tree_carries_the_fields_the_map_gives(tree_cells):
    assert [problem for meta in tree_cells["cells"] for problem in disagreements(meta)] == []


def test_each_cadence_the_map_gives_a_cell_is_one_its_tests_run_on(tree_cells):
    assert uncovered_cadences(tree_cells["cells"]) == []


def test_a_native_linux_cell_left_on_the_default_image_is_caught():
    meta = {"id": "lin-native-start", "cadence": "push", "os": "linux", "image": "core", "packet": "deploy-channels"}

    assert disagreements(meta) == ["lin-native-start image: @cell 'core', map None"]


def test_a_windows_cell_matches_whatever_image_its_decorator_defaulted_to():
    meta = {"id": "win-pip-start", "cadence": "push", "os": "windows", "image": "core", "packet": "deploy-channels"}

    assert disagreements(meta) == []


ROW_CELL = {"cell": {"lin-row-x": {"packet": "deploy-harnesses", "cadence": "nightly", "os": "linux", "image": "full",
                                   "row": "lanes-visible-04"}}}


def _row_meta(**row):
    return {"id": "lin-row-x", "cadence": "nightly", "os": "linux", "image": "full", "packet": "deploy-harnesses",
            **row}


def test_a_test_that_drops_the_row_its_map_entry_gives_is_caught_and_so_is_the_reverse():
    untagged = {"cell": {"lin-row-x": {key: value for key, value in ROW_CELL["cell"]["lin-row-x"].items()
                                       if key != "row"}}}

    assert disagreements(_row_meta(), ROW_CELL) == ["lin-row-x row: @cell None, map 'lanes-visible-04'"]
    assert disagreements(_row_meta(row="lanes-visible-04"), untagged) == [
        "lin-row-x row: @cell 'lanes-visible-04', map None"]
    assert disagreements(_row_meta(row="lanes-visible-04"), ROW_CELL) == []
    assert disagreements(_row_meta(row=None), untagged) == []


TWO_OS = {"cell": {"docs-x": {"packet": "deploy-docs", "cadence": "push", "os": ["linux", "windows"], "image": "core"}}}


def test_a_cell_on_linux_and_windows_is_held_to_the_image_its_linux_half_runs_in():
    meta = {"id": "docs-x", "cadence": "push", "os": ("linux", "windows"), "image": "cells", "packet": "deploy-docs"}

    assert disagreements(meta, TWO_OS) == ["docs-x image: @cell 'cells', map 'core'"]
    assert disagreements({**meta, "image": "core"}, TWO_OS) == []


SPLIT = {"cell": {"lin-up-x": {"packet": "deploy-upgrade", "cadence": "push+nightly", "os": "linux", "image": "core"}}}


def _split_meta(cadence):
    return {"id": "lin-up-x", "cadence": cadence, "os": "linux", "image": "core", "packet": "deploy-upgrade"}


def test_a_cell_whose_tests_split_its_cadences_runs_each_on_one_the_map_gives():
    assert disagreements(_split_meta("push"), SPLIT) == disagreements(_split_meta("nightly"), SPLIT) == []
    assert disagreements(_split_meta("weekly"), SPLIT) == [
        "lin-up-x cadence: @cell ['weekly'], map ['nightly', 'push']"]


def test_a_cadence_the_map_gives_that_no_test_of_the_cell_runs_on_is_caught():
    assert uncovered_cadences([_split_meta("push"), _split_meta("nightly")], SPLIT) == []
    assert uncovered_cadences([_split_meta("push")], SPLIT) == ["lin-up-x never runs on nightly"]
