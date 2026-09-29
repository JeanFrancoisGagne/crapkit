"""Tier selection, the session guards, and the fixtures every packet shares.

The hooks act on tests under tests/accuracy only: a session that also collects
tests/unit leaves those items as it found them. The guards (kit/guards.py) fail
a test that skips or xfails outside a rulings row, and fail the session when a
test wrote under tests/accuracy.
"""
import itertools
import os
from pathlib import Path
import sys

import pytest

from accuracy.kit import corpus_run, drive, guards, oracles, repos, runlog, tiers

HERE = Path(__file__).resolve().parent
_SNAPSHOT = pytest.StashKey[dict]()
# Corpus files, probes and recordings are data: a test_*.py among them is a
# fixture's own test, never one of ours.
collect_ignore_glob = ["*/fixtures/*", "*/small/*", "*/recorded/*", "*/probes/*"]


def pytest_configure(config):
    for name, text in tiers.MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {text}")


def _ours(item) -> bool:
    return HERE in Path(str(item.path)).resolve().parents


def _platforms(item) -> list[str]:
    return [name for mark in item.iter_markers("platform") for name in mark.args]


def _pythons(item) -> list[tuple]:
    return [tuple(mark.args) for mark in item.iter_markers("python")]


def _runs(item, tier: str) -> bool:
    names = [mark.name for mark in item.iter_markers()]
    return tiers.selected(names, tier, _platforms(item), pythons=_pythons(item))


def _dropped(items, tier: str) -> list:
    return [item for item in items if _ours(item) and not _runs(item, tier)]


def pytest_collection_modifyitems(config, items):
    dropped = _dropped(items, tiers.current_tier())
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        gone = set(map(id, dropped))
        items[:] = [item for item in items if id(item) not in gone]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _ours(item):
        tiers.enter(mark.name for mark in item.iter_markers())
    try:
        yield
    finally:
        tiers.leave()


def _report_problem(item, report) -> str | None:
    if not _ours(item):
        return None
    if hasattr(report, "wasxfail"):
        return guards.xfail_problem(item.iter_markers("xfail"))
    return guards.skip_problem(report.skipped, wasxfail=False)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    problem = _report_problem(item, report)
    if problem:
        report.outcome, report.longrepr = "failed", problem
        report.__dict__.pop("wasxfail", None)


def _controller(config) -> bool:
    return not hasattr(config, "workerinput")


def pytest_sessionstart(session):
    if _controller(session.config):
        session.config.stash[_SNAPSHOT] = guards.snapshot(guards.guarded_root(HERE))



@pytest.fixture(scope="session")
def oracle():
    """oracle(name) is the installed, pin-checked tool a test reads its expected
    value from. A missing one fails the test; see kit/oracles.py."""
    tier = tiers.current_tier()
    return lambda name: oracles.require(name, tier)


@pytest.fixture(scope="session", autouse=True)
def in_process_hang_log(tmp_path_factory):
    """Where an in-process crapkit call stuck in C code past its bound leaves every
    thread's stack before the process ends (tests/e2e/cli_in_process.py):
    in-process-hangs.log under the session's basetemp, outside pytest's capture
    and the call's own descriptor 2, as tests/e2e/conftest.py names it."""
    runner = drive._in_process_runner()
    path = tmp_path_factory.getbasetemp() / "in-process-hangs.log"
    with open(path, "a", encoding="utf-8") as log:
        runner.log_hangs_to(log)
        yield path
    runner.log_hangs_to(sys.__stderr__)


@pytest.fixture(scope="session")
def repo_templates(tmp_path_factory):
    return repos.Templates(tmp_path_factory.mktemp("repo-templates"))


@pytest.fixture
def make_repo(repo_templates, tmp_path):
    """make_repo(spec) is a fresh copy of the spec's repo under this test's tmp_path."""
    numbers = itertools.count()
    return lambda spec: repo_templates.copy(spec, tmp_path / f"repo{next(numbers)}")


def _note_events() -> None:
    """Hand run.py the shape events the strategies emitted this session."""
    drawn = sys.modules.get("accuracy.kit.strategies")
    if drawn is not None and drawn.EVENTS:
        runlog.note("events", counts=dict(drawn.EVENTS))


def _written(config) -> list[str]:
    """What changed under tests/accuracy since the controller's snapshot. An
    xdist worker took no snapshot, so it reports nothing."""
    before = config.stash.get(_SNAPSHOT, None)
    if before is None:
        return []
    return guards.changed(before, guards.snapshot(guards.guarded_root(HERE)))


def pytest_sessionfinish(session, exitstatus):
    """Note the events, and fail the session when a test wrote under tests/accuracy."""
    _note_events()
    written = _written(session.config)
    if written:
        session.config.get_terminal_writer().line(
            f"tests/accuracy changed during the session: {', '.join(written)}; a test writes "
            "under tmp_path, never under tests/accuracy", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _shared_base(tmp_path_factory) -> Path:
    """The session's temp root every xdist worker shares."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


@pytest.fixture(scope="session")
def small_corpus(tmp_path_factory):
    """The small corpus, measured once per session: read its outputs, and take
    private_copy() before changing anything. See kit/corpus_run.py."""
    return corpus_run.measure(corpus_run.SMALL, _shared_base(tmp_path_factory),
                              corpus_run.date_now())
