"""@cell: one deploy scenario, the markers run.py selects on, and the JUnit
properties a reader of the run needs.

    @cell("lin-pip-start-py311", channel="pip venv", harness="none",
          scenario="fresh: README 60-second start", use_cases="init, coverage",
          os="linux", image="core", cadence="push")
    def test_readme_start(box): ...

Markers: the cadence (push, nightly, weekly, published; "weekly+published"
gives both), each OS, image_<image>, and real_cli, nonblocking, docker_host
and online when set. run.py builds its -m expression from these, so a cell
reaches exactly the jobs its markers name. The kit's own tests carry `kit`
and run in every job; `--cell` or `--packet` narrows a run to the cells it
names, and `--packet deploy-kit` runs the kit's tests alone. `--shard K/N`
then keeps part K of N of what is left, kit tests included.

A test of an every-harness row (tests/deploy/MAP.toml [every_harness]) names
its row with `row="<entry key>"`, and its [cell] entry carries the same `row`.
A nightly run drops the items of a `core` row on the full image and the images
built on it (`nightly_keeps`); every other cadence keeps them.

JUnit properties: every field above plus packet and row when set, the image
digest and the toolchain hash, written by the autouse fixture in
tests/deploy/conftest.py.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import os
import re
import tomllib
from pathlib import Path

import pytest

CADENCES = ("push", "nightly", "weekly", "published")
OSES = ("linux", "windows", "macos")
IMAGES = ("cells", "core", "full", "ci", "gui")
FLAGS = ("real_cli", "nonblocking", "docker_host", "online")
MARKERS = {
    "kit": "the deploy kit's own tests; every job runs them",
    "deploy_cell": "a deploy cell's metadata (id, channel, harness, scenario ...)",
    **{name: f"a {name}-cadence deploy cell" for name in CADENCES},
    **{name: f"a deploy cell that runs on {name}" for name in OSES},
    **{f"image_{name}": f"a deploy cell that needs the :{name} image or a larger one" for name in IMAGES},
    "real_cli": "the released binary of the cell's harness drives it",
    "nonblocking": "reported, never fails the job",
    "docker_host": "needs the host's Docker daemon, not a container",
    "online": "needs the network",
}
# tests/deploy/conftest.py keeps every harness binary's stamp here at session
# start (kit/sandbox.py harness_stamps); test_kit_isolation reads it back.
HARNESSES = pytest.StashKey[dict]()


def excused(report, item, call) -> None:
    """A nonblocking cell whose call failed reads as an xfail naming its error,
    so it is reported and never fails the run, whichever job selected it: the
    release cadence runs the weekly prerelease and arm64 cells inside jobs
    that block. A failed setup or teardown still fails, since the kit broke."""
    if report.when == "call" and report.failed and item.get_closest_marker("nonblocking"):
        report.outcome = "skipped"
        report.wasxfail = "nonblocking cell failed: " + call.excinfo.exconly().splitlines()[0][:300]


def _split(value: str | tuple | list) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace("+", ",").split(",") if part.strip()]
    return list(value)


def _unknown(values: list[str], allowed: tuple, field: str) -> None:
    wrong = [value for value in values if value not in allowed]
    if wrong:
        raise ValueError(f"@cell {field} {wrong} is not one of {allowed}")


def markers(meta: dict) -> list:
    """The pytest marks one cell carries."""
    cadences, oses = _split(meta["cadence"]), _split(meta["os"])
    _unknown(cadences, CADENCES, "cadence")
    _unknown(oses, OSES, "os")
    names = [*cadences, *oses, *(name for name in FLAGS if meta.get(name))]
    names += [f"image_{meta['image']}"] if meta.get("image") else []
    return [pytest.mark.deploy_cell(**meta)] + [getattr(pytest.mark, name) for name in names]


def cell(cell_id: str, *, channel: str, harness: str, scenario: str, use_cases: str, os: str | tuple,
         image: str | None = "core", cadence: str = "push", real_cli: bool = True, packet: str | None = None,
         nonblocking: bool = False, docker_host: bool = False, online: bool = False, row: str | None = None):
    """Mark a test as the deploy cell `cell_id`; `row` is its every-harness row's key in MAP.toml."""
    if image is not None:
        _unknown([image], IMAGES, "image")
    meta = {"id": cell_id, "channel": channel, "harness": harness, "scenario": scenario,
            "use_cases": use_cases, "os": os, "image": image, "cadence": cadence, "real_cli": real_cli,
            "packet": packet, "nonblocking": nonblocking, "docker_host": docker_host, "online": online, "row": row}

    def apply(function):
        for mark in markers(meta):
            function = mark(function)
        return function
    return apply


def cell_meta(item) -> dict | None:
    mark = item.get_closest_marker("deploy_cell")
    if mark is None:
        return None
    meta = dict(mark.kwargs)
    meta["packet"] = meta.get("packet") or getattr(item.module, "PACKET", None)
    return meta


def toolchain_hash() -> str:
    path = os.environ.get("CRAPKIT_DEPLOY_TOOLCHAIN")
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).exists() else ""


# Written for every cell, false or empty included: a reader of the JUnit
# tells a simulated cell (real_cli False) from a real one without guessing.
ALWAYS = ("id", "channel", "harness", "scenario", "use_cases", "os", "image", "cadence", "real_cli")


def _text(value) -> str:
    """A property value: lists joined with commas, None as "none"."""
    if isinstance(value, (list, tuple)):
        return ",".join(map(str, value))
    return "none" if value is None else str(value)


def properties(meta: dict) -> list[tuple[str, str]]:
    """The JUnit <property> pairs for one cell: every ALWAYS field, then any
    other field that is set (packet, nonblocking, docker_host, online, row)."""
    pairs = [(f"cell_{key}", _text(meta.get(key))) for key in ALWAYS]
    pairs += [(f"cell_{key}", _text(value)) for key, value in meta.items()
              if key not in ALWAYS and value not in (None, False, "")]
    pairs.append(("image_digest", os.environ.get("CRAPKIT_DEPLOY_IMAGE_DIGEST", "native")))
    pairs.append(("toolchain_hash", toolchain_hash()))
    return pairs


# --- the nightly run's every-harness rows ---------------------------------------

MAP_TOML = Path(__file__).resolve().parents[1] / "MAP.toml"
PINS_PY = Path(__file__).resolve().parents[3] / "tools" / "deploy" / "pins.py"


def every_harness(path: Path = MAP_TOML) -> dict[str, dict]:
    """MAP.toml's [every_harness]: each row's key and its nightly tag."""
    return tomllib.loads(path.read_text(encoding="utf-8")).get("every_harness", {})


@functools.cache
def image_chain() -> dict[str, list[str]]:
    """tools/deploy/pins.py's IMAGE_CHAIN: each image and the images it is built on."""
    spec = importlib.util.spec_from_file_location("deploy_cells_pins", PINS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.IMAGE_CHAIN


def _row_tag(meta: dict, every_harness: dict) -> str | None:
    """The nightly tag of the item's row, None for an item with no row. A row
    MAP.toml lacks is refused: the ticket that adds the row adds its entry."""
    row = meta.get("row")
    if row is None:
        return None
    if row not in every_harness:
        raise ValueError(f"@cell {meta.get('id')} names row {row!r}, which tests/deploy/MAP.toml's [every_harness] "
                         f"lacks: add the row's entry, nightly = \"all\" or \"core\", in the change that adds its cells")
    return every_harness[row]["nightly"]


def _on_full(image: str | None) -> bool:
    """The item runs on the full image or one built on it, where the 14 non-core harnesses are."""
    return image is not None and "full" in image_chain()[image]


def nightly_keeps(meta: dict | None, cadence: str | None, every_harness: dict) -> bool:
    """Whether a run of this cadence keeps the item. A nightly run drops a
    `core` row's items on the full image and those built on it; it keeps an
    item with no row, an `all` row's items, core-image and native items, and
    every other cadence keeps every item, so the release run takes every row
    on every harness. An unknown row raises at every cadence."""
    tag = _row_tag(meta or {}, every_harness)
    return not (cadence == "nightly" and tag == "core" and _on_full(meta.get("image")))


def partition(items: list, keep_item) -> tuple[list, list]:
    keep, dropped = [], []
    for item in items:
        (keep if keep_item(item) else dropped).append(item)
    return keep, dropped


def shard(text: str) -> tuple[int, int]:
    """--deploy-shard PART/PARTS as (PART, PARTS), 1 <= PART <= PARTS."""
    found = re.fullmatch(r"(\d+)/(\d+)", text)
    if not found or not 1 <= int(found[1]) <= int(found[2]):
        raise argparse.ArgumentTypeError(f"{text!r} is not PART/PARTS, 1 <= PART <= PARTS, such as 1/2")
    return int(found[1]), int(found[2])


def in_shard(items: list, part: int, parts: int) -> tuple[list, list]:
    """(kept, dropped) for part PART of PARTS: in test-id order, every PARTS-th
    test from the PART-th. Neighbours in that order are tests of one module
    and cost about the same, so the parts take about the same time, and the
    order is the same in every xdist worker and every part's container."""
    rank = {item.nodeid: index for index, item in enumerate(sorted(items, key=lambda item: item.nodeid))}
    return partition(items, lambda item: rank[item.nodeid] % parts == part - 1)


KIT_PACKET = "deploy-kit"


def module_packet(item) -> str | None:
    """The PACKET a test's module names, or None."""
    return getattr(getattr(item, "module", None), "PACKET", None)


def _kit_selected(cells: list[str], packet: str | None, home: str | None) -> bool:
    return not cells and packet in (None, KIT_PACKET, home)


def selected(meta: dict | None, cells: list[str], packet: str | None, home: str | None = None) -> bool:
    """Whether --deploy-cell / --deploy-packet keep an item. A kit test runs
    when neither narrows the run, under --deploy-packet deploy-kit, and under
    the packet its module names (`home`), so a packet's own helper tests run
    with its cells."""
    if meta is None:
        return _kit_selected(cells, packet, home)
    if cells and meta["id"] not in cells:
        return False
    return packet is None or meta.get("packet") == packet


# tests/deploy/conftest.py keeps here the deploy tests no run selects;
# test_kit_isolation fails while it holds any.
LOOSE = pytest.StashKey[list]()


def _under(item, root: Path) -> bool:
    return Path(str(getattr(item, "path", ""))).is_relative_to(root)


def loose(items: list, root: Path) -> list[str]:
    """The tests under `root` that carry neither `kit` nor @cell. run.py
    selects on `kit or (<cadence> and ...)`, so no job ever runs them."""
    return [item.nodeid for item in items
            if _under(item, root) and item.get_closest_marker("kit") is None and cell_meta(item) is None]
