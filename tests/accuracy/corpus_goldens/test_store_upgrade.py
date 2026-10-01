"""The last five releases each write a small history; the crapkit under test reads it.

upgrade_runs writes the history with each release's own wheel: coverage
(run 1), worklist and coupling, a ccn-7 edit, a verify that fails (run 2) and
another coverage (run 3). The releases are the newest five on PyPI at or
below the version under test, so a replay of an old commit reads the
releases that came before it, less any that cannot run a lane on this Python
(NEED_WAITID): nobody there has a history from them. Each test checks every
release and names the ones that fail. The expected values come from outside
crapkit's code:

- hand and model: the README's taint rule picks run 1, and model_baseline
  over the runs table (read with sqlite3 after the candidate opened the
  store) picks the run `runs list --json` marks and `verify` compares against.
- metamorphic: the candidate's worklist and coupling from the release's warm
  caches equal a cold rebuild with the cache files deleted.
- R100: two installs sharing one tree never rewrite each other's cache: once
  each has read, another read by either leaves every churn and coupling
  cache file's bytes as they were.
- self-diff: the release's answers and the candidate's on one .crapkit/
  differ only in calcs a CHANGES row declared on or after the release's date.
- CG5 (fixed): 0.6.0, the last release of the path format 0.7.0 retired
  without renaming the cache files, and 0.7.0 to 0.8.0 rewrote each other's
  churn-cache-v2.json and coupling-cache-v1.json on every command. This
  crapkit writes churn-cache-v3.json and coupling-cache-v2.json, so neither
  install touches the other's.
"""
from pathlib import Path

from packaging.specifiers import SpecifierSet
import pytest

import hang_guard
from accuracy.corpus_goldens import model_baseline, releases, upgrade_diff, upgrade_runs
from accuracy.kit import drive, rulings, surfaces

pytestmark = [pytest.mark.nightly, pytest.mark.release, pytest.mark.process]
RELEASES = 5
WROTE = (("coverage --json", 0), ("worklist --json", 0), ("coupling --json", 0),
         ("verify --json", 6), ("coverage --json", 0))
# README "The trusted baseline": run 2 is a failed verify no verify cleared.
HAND_RUNS = [(1, "coverage", None), (2, "verify", 0), (3, "coverage", None)]
# These releases wait on every POSIX lane with os.waitid, which python.org's
# macOS builds of Python 3.11 and 3.12 lack: there every lane they start dies
# with AttributeError, so they never wrote a history on that Python (0.8.1
# fixed it, 9b214486).
NEED_WAITID = SpecifierSet(">=0.7.0,<0.8.1")


def _lanes_wait(driver: drive.Driver) -> bool:
    """Whether NEED_WAITID releases can wait on a lane under the driver's Python:
    on Windows they never call os.waitid."""
    probe = "import os; print(os.name == 'nt' or hasattr(os, 'waitid'))"
    done = hang_guard.run([driver.python, "-c", probe], env=driver.env, text=True, encoding="utf-8")
    return done.stdout.strip() == "True"


@pytest.fixture(scope="module")
def versions(tmp_path_factory):
    """The newest five releases, less those that cannot write a history on this Python."""
    probe = drive.Driver(tmp_path_factory.mktemp("version"), spawn=True)
    newest = releases.last(RELEASES, at_most=upgrade_runs.version_of(probe))
    kept = newest if _lanes_wait(probe) else [name for name in newest if name not in NEED_WAITID]
    assert kept, f"none of {newest} can write a history under {probe.python}"
    return kept


@pytest.fixture(scope="module")
def histories(versions, tmp_path_factory):
    return [upgrade_runs.write_history(version, tmp_path_factory.mktemp(f"upgrade-{version}"))
            for version in versions]


def _runs(driver: drive.Driver) -> list[dict]:
    return driver.store("select id, kind, verdict_ok from runs order by id")


def _hand_runs(history: upgrade_runs.History) -> list[tuple]:
    return [tuple(run.values()) for run in _runs(history.driver(history.root, history.site))]


def test_the_release_wrote_the_history_the_readme_describes(versions, histories):
    assert {history.version: history.codes for history in histories} == dict.fromkeys(versions, WROTE)
    assert {history.version: _hand_runs(history) for history in histories} == \
        dict.fromkeys(versions, HAND_RUNS)


def _baseline(history: upgrade_runs.History, where: Path) -> tuple:
    """The runs `runs list` marks, the model's pick over the runs table, and verify's baseline."""
    new = history.driver(history.copy(where / "repo"))
    listed = new.json("runs", "list")["runs"]
    verdict = new.json("verify")
    return ([run["id"] for run in listed if run["baseline"]],
            [model_baseline.baseline(_runs(new))], verdict["baseline_run"])


def test_the_candidate_marks_the_baseline_the_readme_picks(versions, histories, tmp_path):
    found = {history.version: _baseline(history, tmp_path / history.version) for history in histories}
    picked = upgrade_runs.BASELINE

    assert found == dict.fromkeys(versions, ([picked], [picked], picked))


def _normalized(results: dict, root: Path) -> dict:
    volatile = surfaces.Volatile(roots=surfaces.spellings(root))
    return {command: surfaces.normalize(result.json(), volatile)
            for command, result in results.items()}


def _warm_and_cold(history: upgrade_runs.History, where: Path) -> dict:
    warm = history.copy(where / "warm")
    cold = history.copy(where / "cold")
    upgrade_runs.drop_caches(cold)
    return {name: _normalized(upgrade_runs.read(history.driver(root)), root)
            for name, root in (("warm", warm), ("cold", cold))}


def _uncoupled(answers: dict) -> list[str]:
    """The releases whose history couples no pair of files."""
    return [version for version, read in answers.items() if not read["coupling --json"]["pairs"]]


def test_warm_churn_cache_equals_cold_after_upgrade(histories, tmp_path):
    """R100: the candidate answers from the release's caches as it does from none."""
    found = {history.version: _warm_and_cold(history, tmp_path / history.version)
             for history in histories}
    warm = {version: both["warm"] for version, both in found.items()}

    assert warm == {version: both["cold"] for version, both in found.items()}
    assert _uncoupled(warm) == [], "the history must couple a.py and b.py"


def _rewrites(root: Path, before: dict, readers: list) -> list[str]:
    changed = []
    for label, driver in readers:
        for argv in (("worklist", "--json"), ("coupling", "--json")):
            driver.run(*argv)
            now = upgrade_runs.caches(root)
            changed += [f"{label} {' '.join(argv)} rewrote {name}" for name in sorted(set(before) | set(now))
                        if before.get(name) != now.get(name)]
            before = now
    return changed


def _shared_tree_rewrites(history: upgrade_runs.History, where: Path) -> list[str]:
    """Once the candidate has read, what a read by the release and then the candidate rewrote."""
    root = history.copy(where / "repo")
    old, new = history.driver(root, history.site), history.driver(root)
    _rewrites(root, {}, [("candidate", new)])
    return _rewrites(root, upgrade_runs.caches(root), [("release", old), ("candidate", new)])


def test_two_installs_never_rewrite_each_others_caches(versions, histories, tmp_path):
    """R100 (b3e14d6): 0.4.3 and 0.4.4 read each other's churn cache as cold and
    rewrote it on every command."""
    found = {history.version: _shared_tree_rewrites(history, tmp_path / history.version)
             for history in histories}

    assert found == dict.fromkeys(versions, [])


# The last release of each retired cache format that shares a file name with
# this crapkit's: 0.6.0 keys churn-cache-v2.json and coupling-cache-v1.json with
# the path format "root-relative", which 0.7.0 changed without renaming them.
SHARED_NAME_RELEASES = ("0.6.0",)


@pytest.fixture(scope="module", params=SHARED_NAME_RELEASES)
def retired(request, tmp_path_factory):
    return upgrade_runs.write_history(request.param,
                                      tmp_path_factory.mktemp(f"retired-{request.param}"))


def _rewritten(lines: list[str]) -> str:
    return ", ".join(sorted({line.split(" rewrote ")[1] for line in lines})) or "none"


@rulings.applies("CG5")
def test_a_retired_format_under_a_shared_name_is_never_rewritten(retired, tmp_path):
    """R100's class: README names each cache by its format (coupling-cache-v2.json),
    so an older install on the same tree keeps its own file warm."""
    root = retired.copy(tmp_path / "repo")
    old, new = retired.driver(root, retired.site), retired.driver(root)
    _rewrites(root, {}, [("candidate", new)])
    found = _rewrites(root, upgrade_runs.caches(root), [("release", old), ("candidate", new)])

    rulings.pin_ruling("CG5", crapkit=_rewritten(found), oracle="none")


def _undeclared(history: upgrade_runs.History, where: Path) -> list[str]:
    root = history.copy(where / "repo")
    old = {command: result.json() for command, result in
           upgrade_runs.read(history.driver(root, history.site)).items()}
    new = {command: result.json() for command, result in upgrade_runs.read(history.driver(root)).items()}
    found = [difference for command in upgrade_runs.COMMANDS
             for difference in upgrade_diff.differences(command, *_pair(old, new, command, root))]
    return upgrade_diff.undeclared(found, upgrade_diff.read_changes(),
                                   releases.upload_date(history.version))


def test_the_release_and_the_candidate_answer_alike_or_declare_it(versions, histories, tmp_path):
    found = {history.version: _undeclared(history, tmp_path / history.version) for history in histories}

    assert found == dict.fromkeys(versions, [])


def _pair(old: dict, new: dict, command: str, root: Path) -> tuple:
    volatile = surfaces.Volatile(roots=surfaces.spellings(root))
    return surfaces.normalize(old[command], volatile), surfaces.normalize(new[command], volatile)
