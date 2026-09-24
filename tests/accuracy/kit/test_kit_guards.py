"""kit.guards and their conftest wiring: no skip, no xfail outside a rulings row,
no write under tests/accuracy."""
import os
from pathlib import Path
import sys

import pytest

import hang_guard
from accuracy.kit import guards, rulings, tiers

HERE = Path(__file__).resolve().parent
PROBES = HERE / "test_kit_guard_probes.py"
WRITTEN = HERE / "guard-probe-written.txt"


@pytest.fixture(scope="module")
def probe_session():
    env = {**os.environ, tiers.GUARD_PROBES_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, "-m", "pytest", "-rA", "-p", "no:randomly", "-p", "no:cacheprovider",
            str(PROBES)]
    try:
        done = hang_guard.run(argv, cwd=HERE.parents[2], env=env, text=True, encoding="utf-8",
                              errors="replace")
    finally:
        WRITTEN.unlink(missing_ok=True)
    return done


@pytest.mark.process
def test_a_skip_fails(probe_session):
    assert "FAILED tests/accuracy/kit/test_kit_guard_probes.py::test_probe_skips" in (
        probe_session.stdout)
    assert "tests/accuracy never skips" in probe_session.stdout


@pytest.mark.process
def test_an_xfail_outside_a_ruling_fails(probe_session):
    assert ("FAILED tests/accuracy/kit/test_kit_guard_probes.py::"
            "test_probe_xfails_without_a_ruling") in probe_session.stdout


@pytest.mark.process
def test_a_write_under_tests_accuracy_fails_the_session(probe_session):
    assert probe_session.returncode == 1
    assert ("tests/accuracy changed during the session: kit/guard-probe-written.txt"
            in probe_session.stdout)
    assert "PASSED tests/accuracy/kit/test_kit_guard_probes.py::test_probe_passes" in (
        probe_session.stdout)


def test_the_probes_are_deselected_without_the_switch():
    assert not tiers.selected(["guard_probe"], "push", environ={})
    assert tiers.selected(["guard_probe"], "push", environ={tiers.GUARD_PROBES_ENV: "1"})


def test_collect_all_selects_every_tier_and_platform():
    everything = {tiers.COLLECT_ALL_ENV: "1"}

    assert tiers.selected(["weekly"], "push", ["no-such-platform"], environ=everything)
    assert not tiers.selected(["weekly"], "push", environ={})


def test_snapshots_name_what_appeared_vanished_or_changed(tmp_path):
    (tmp_path / "kept.tsv").write_text("a\n", encoding="utf-8")
    (tmp_path / "gone.tsv").write_text("a\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\0")
    before = guards.snapshot(tmp_path)
    (tmp_path / "gone.tsv").unlink()
    (tmp_path / "new.tsv").write_text("b\n", encoding="utf-8")
    (tmp_path / "kept.tsv").write_text("a longer row\n", encoding="utf-8")
    (tmp_path / "__pycache__" / "y.pyc").write_bytes(b"\0")

    assert guards.changed(before, guards.snapshot(tmp_path)) == ["gone.tsv", "kept.tsv",
                                                                  "new.tsv"]


def test_a_rulings_xfail_is_the_one_kind_allowed():
    ruled = pytest.mark.xfail(strict=True, raises=rulings.RulingDefect).mark
    loose = pytest.mark.xfail(strict=True).mark

    assert guards.xfail_problem([ruled]) is None
    assert guards.xfail_problem([]) is None
    assert "kit.rulings.applies" in guards.xfail_problem([ruled, loose])


def test_a_skip_is_a_problem_and_an_xfail_is_not():
    assert "never skips" in guards.skip_problem(skipped=True, wasxfail=False)
    assert guards.skip_problem(skipped=True, wasxfail=True) is None
    assert guards.skip_problem(skipped=False, wasxfail=False) is None
