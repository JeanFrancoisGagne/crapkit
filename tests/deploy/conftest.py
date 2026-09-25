"""Fixtures every deploy cell shares. Collected only under CRAPKIT_DEPLOY=1
(tests/conftest.py); tools/deploy/run.py sets it and the rest of the
environment the kit reads.

  transcript   this test's Transcript, written to <out>/transcripts/ at teardown
  toolchain    the pinned toolchain run.py or the image named
  box          a fresh sandbox (kit/sandbox.py) under this test's tmp_path
  candidate    the candidate build: version, wheel, sdist, stamped tree
  templates    the session's fixture-repo cache for kit/repos.py

The session also holds every harness binary to what it was when the session
started (kit/sandbox.py harness_stamps): a harness that updates itself
mid-run would make two cells of one run test different releases. And it
records every deploy test that is neither a kit test nor a cell, which no
run.py job selects (test_kit_isolation fails on any).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from kit import cells, sandbox, wheels
from kit.transcript import Transcript


def pytest_addoption(parser):
    group = parser.getgroup("deploy")
    group.addoption("--deploy-cell", action="append", default=[], help="run only this cell id (repeatable)")
    group.addoption("--deploy-packet", default=None, help="run only the cells of this packet")


def pytest_configure(config):
    for name, text in cells.MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {text}")


def _harness_stamps() -> dict[str, str]:
    path = os.environ.get("CRAPKIT_DEPLOY_TOOLCHAIN")
    return sandbox.harness_stamps(sandbox.Toolchain.load(path)) if path else {}


def pytest_sessionstart(session):
    """Every harness binary as the session found it: test_kit_isolation and
    pytest_sessionfinish hold the session to it."""
    session.config.stash[cells.HARNESSES] = _harness_stamps()


def pytest_sessionfinish(session, exitstatus):
    """A harness that updated itself during the run fails the run. The xdist
    controller checks once, after every worker is done."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    changed = sandbox.changed_stamps(session.config.stash.get(cells.HARNESSES, {}), _harness_stamps())
    for line in changed:
        print(f"deploy: a harness changed during the session: {line}", file=sys.stderr)
    if changed:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Runs before -m deselects anything, so the loose-test record sees every
    collected deploy test."""
    config.stash[cells.LOOSE] = cells.loose(items, Path(__file__).resolve().parent)
    wanted, packet = config.getoption("--deploy-cell"), config.getoption("--deploy-packet")
    keep, dropped = cells.partition(items, lambda item: cells.selected(cells.cell_meta(item), wanted, packet,
                                                                        cells.module_packet(item)))
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep


def out_dir() -> Path:
    return Path(os.environ.get("CRAPKIT_DEPLOY_OUT", Path.cwd() / ".crapkit" / "deploy-out"))


@pytest.fixture(autouse=True)
def _cell_properties(request, record_property):
    meta = cells.cell_meta(request.node)
    for name, value in cells.properties(meta) if meta else []:
        record_property(name, value)


@pytest.fixture
def transcript(request):
    record = Transcript(request.node.nodeid)
    yield record
    record.write(out_dir() / "transcripts")


@pytest.fixture(scope="session")
def toolchain():
    return sandbox.Toolchain.load()


@pytest.fixture
def box(tmp_path, transcript, toolchain):
    return sandbox.make(tmp_path / "box", transcript, toolchain=toolchain)


@pytest.fixture(scope="session")
def candidate():
    return wheels.Candidate.load()


@pytest.fixture(scope="session")
def templates(tmp_path_factory):
    """Where kit/repos.py builds each template once. xdist workers share the
    parent of their own basetemp, so one build serves every worker."""
    base = tmp_path_factory.getbasetemp()
    return (base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base) / "deploy-templates"
