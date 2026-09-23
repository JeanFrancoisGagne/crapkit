"""A measured fixture repo is built once per worker and copied into each test.

The copy has to be the build: same files, same `.git`, same `.crapkit`, and
private to the test that asked. These tests pin that contract on a small real
repo, and pin the two ways a stored build must not be reused: after a build
that failed, and on a later UTC day than the one it was built on. The last test
pins what a copy does not carry over: a lane artifact keyed by the build's
deleted staging dir.
"""
import json
import subprocess
from pathlib import Path

import pytest

import repo_templates
from conftest import git_commit_all, git_init_repo


def _worker(tmp_path: Path, *names: str) -> list[Path]:
    """Sibling test dirs under one private basetemp, the shape pytest gives a
    worker's tests, so the template these tests build is their own."""
    dirs = [tmp_path / "worker" / name for name in names]
    for path in dirs:
        path.mkdir(parents=True)
    return dirs


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


class Builds:
    """A build that commits one file and a measurement, counting its calls."""

    def __init__(self):
        self.calls = 0

    def __call__(self, repo: Path) -> None:
        self.calls += 1
        (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        git_commit_all(git_init_repo(repo), "init")
        (repo / ".crapkit").mkdir()
        (repo / ".crapkit" / "crap.sqlite").write_bytes(b"measured")


def test_two_tests_share_one_build_and_each_gets_the_whole_tree(tmp_path):
    build = Builds()
    first, second = _worker(tmp_path, "one", "two")

    a = repo_templates.copy_of(repo_templates.template(first, "demo", build), first / "repo")
    b = repo_templates.copy_of(repo_templates.template(second, "demo", build), second / "repo")

    assert build.calls == 1
    for repo in (a, b):
        assert (repo / "app.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"
        assert (repo / ".crapkit" / "crap.sqlite").read_bytes() == b"measured"
        assert (repo / ".git" / "HEAD").is_file()
    assert _head(a) == _head(b)


def test_a_copy_is_private_to_its_test(tmp_path):
    first, second = _worker(tmp_path, "one", "two")
    built = repo_templates.template(first, "demo", Builds())
    a = repo_templates.copy_of(built, first / "repo")
    b = repo_templates.copy_of(built, second / "repo")
    before = _head(b)

    (a / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    git_commit_all(a, "edit")

    assert _head(b) == before and _head(built) == before
    assert (b / "app.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"


def test_a_copy_can_land_in_the_empty_dir_pytest_made(tmp_path):
    (here,) = _worker(tmp_path, "one")

    repo = repo_templates.copy_of(repo_templates.template(here, "demo", Builds()), here)

    assert repo == here and (here / "app.py").is_file()


def test_a_build_that_raised_leaves_nothing_behind_to_copy(tmp_path):
    first, second = _worker(tmp_path, "one", "two")
    build = Builds()

    def broken(repo: Path) -> None:
        build(repo)
        raise RuntimeError("the fixture's coverage run failed")

    with pytest.raises(RuntimeError):
        repo_templates.template(first, "demo", broken)
    built = repo_templates.template(second, "demo", build)

    assert build.calls == 2, "the second test builds again instead of copying a half-built tree"
    assert (built / ".crapkit" / "crap.sqlite").is_file()


def test_a_build_from_an_earlier_utc_day_is_not_reused(tmp_path, monkeypatch):
    """crapkit keys its churn caches on the UTC date, so a tree built before
    midnight hands a test after it caches the CLI discards."""
    build = Builds()
    first, second = _worker(tmp_path, "one", "two")
    monkeypatch.setattr(repo_templates, "_utc_day", lambda: "2026-09-21")
    repo_templates.template(first, "demo", build)

    monkeypatch.setattr(repo_templates, "_utc_day", lambda: "2026-09-22")
    repo_templates.template(second, "demo", build)

    assert build.calls == 2


def test_unchanged_holds_until_the_test_writes_to_its_copy(tmp_path):
    (here,) = _worker(tmp_path, "one")
    built = repo_templates.template(here, "demo", Builds())
    repo = repo_templates.copy_of(built, here / "repo")
    assert repo_templates.unchanged(repo, built)

    (repo / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    assert not repo_templates.unchanged(repo, built)


def test_a_new_file_makes_a_copy_changed(tmp_path):
    (here,) = _worker(tmp_path, "one")
    built = repo_templates.template(here, "demo", Builds())
    repo = repo_templates.copy_of(built, here / "repo")

    (repo / "cov_plan.json").write_text("{}", encoding="utf-8")

    assert not repo_templates.unchanged(repo, built)


def test_a_later_build_lays_over_an_unchanged_copy(tmp_path):
    """Git writes its objects read-only, and Windows refuses to overwrite a
    read-only file, so laying a scored tree over the copy it grew from must
    leave the files the two share alone."""
    (here,) = _worker(tmp_path, "one")
    built = repo_templates.template(here, "demo", Builds())

    def measured(repo: Path) -> None:
        repo_templates.copy_of(built, repo)
        (repo / ".crapkit" / "crap.sqlite").write_bytes(b"scored")

    repo = repo_templates.copy_of(built, here / "repo")
    repo_templates.copy_of(repo_templates.template(here, "demo-scored", measured), repo)

    assert (repo / ".crapkit" / "crap.sqlite").read_bytes() == b"scored"
    assert _head(repo) == _head(built)


def _dark_lines(repo: Path) -> tuple:
    import test_report_e2e

    done = test_report_e2e.run_cli(repo, "brief", "src/app.ts", "plain", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    brief = json.loads(done.stdout)
    return brief["uncovered_lines"], brief.get("uncovered_lines_note")


def test_a_copys_lane_artifact_names_the_deleted_build_until_coverage_runs(tmp_path):
    """The report fixture's lane keys cov.json by the absolute path it ran in,
    the staging dir template() deletes after the rename. So a copy reads its
    dark lines as unmeasured, and one `coverage` run in the copy rewrites the
    artifact and answers what a fresh build does."""
    import test_report_e2e

    (here,) = _worker(tmp_path, "one")
    copy = test_report_e2e.scored_repo.__wrapped__(here)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    test_report_e2e._build_scored_repo(fresh)

    assert _dark_lines(copy) == (None, "no lane artifact measured src/app.ts")
    assert _dark_lines(fresh) == ([], None)
    assert test_report_e2e.run_cli(copy, "coverage", "--json").returncode == 0
    assert _dark_lines(copy) == _dark_lines(fresh)
