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
and run in every job.

JUnit properties: every field above plus packet, the image digest and the
toolchain hash, written by the autouse fixture in tests/deploy/conftest.py.
"""
from __future__ import annotations

import hashlib
import os
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
         nonblocking: bool = False, docker_host: bool = False, online: bool = False):
    """Mark a test as the deploy cell `cell_id`."""
    if image is not None:
        _unknown([image], IMAGES, "image")
    meta = {"id": cell_id, "channel": channel, "harness": harness, "scenario": scenario,
            "use_cases": use_cases, "os": os, "image": image, "cadence": cadence, "real_cli": real_cli,
            "packet": packet, "nonblocking": nonblocking, "docker_host": docker_host, "online": online}

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


def properties(meta: dict) -> list[tuple[str, str]]:
    """The JUnit <property> pairs for one cell."""
    pairs = [(f"cell_{key}", str(value)) for key, value in meta.items() if value not in (None, False, "")]
    pairs.append(("image_digest", os.environ.get("CRAPKIT_DEPLOY_IMAGE_DIGEST", "native")))
    pairs.append(("toolchain_hash", toolchain_hash()))
    return pairs


def partition(items: list, keep_item) -> tuple[list, list]:
    keep, dropped = [], []
    for item in items:
        (keep if keep_item(item) else dropped).append(item)
    return keep, dropped


def selected(meta: dict | None, cells: list[str], packet: str | None) -> bool:
    """Whether --deploy-cell / --deploy-packet keep an item. Kit tests always stay."""
    if meta is None:
        return True
    if cells and meta["id"] not in cells:
        return False
    return packet is None or meta.get("packet") == packet
