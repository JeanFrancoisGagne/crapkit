"""The last five releases each write a small history; the crapkit under test reads it.

upgrade_runs writes the history with each release's own wheel: coverage
(run 1), worklist and coupling, a ccn-7 edit, a verify that fails (run 2) and
another coverage (run 3). The releases are the newest five on PyPI at or
below the version under test, so a replay of an old commit reads the
releases that came before it. The expected values come from outside
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
"""
from pathlib import Path

import pytest

from accuracy.corpus_goldens import model_baseline, releases, upgrade_diff, upgrade_runs
from accuracy.kit import drive, surfaces

pytestmark = [pytest.mark.nightly, pytest.mark.release, pytest.mark.process]
RELEASES = 5
# README "The trusted baseline": run 2 is a failed verify no verify cleared.
HAND_RUNS = [(1, "coverage", None), (2, "verify", 0), (3, "coverage", None)]


@pytest.fixture(scope="module")
def versions(tmp_path_factory):
    probe = drive.Driver(tmp_path_factory.mktemp("version"), spawn=True)
    return releases.last(RELEASES, at_most=upgrade_runs.version_of(probe))


@pytest.fixture(scope="module", params=range(RELEASES), ids=[f"release-{n}" for n in range(RELEASES)])
def history(request, versions, tmp_path_factory):
    version = versions[request.param]
    return upgrade_runs.write_history(version, tmp_path_factory.mktemp(f"upgrade-{version}"))


def _runs(driver: drive.Driver) -> list[dict]:
    return driver.store("select id, kind, verdict_ok from runs order by id")


def test_the_release_wrote_the_history_the_readme_describes(history):
    old = history.driver(history.root, history.site)

    assert history.codes == (("coverage --json", 0), ("worklist --json", 0),
                             ("coupling --json", 0), ("verify --json", 6), ("coverage --json", 0))
    assert [tuple(run.values()) for run in _runs(old)] == HAND_RUNS


def test_the_candidate_marks_the_baseline_the_readme_picks(history, tmp_path):
    new = history.driver(history.copy(tmp_path / "repo"))
    listed = new.json("runs", "list")["runs"]
    marked = [run["id"] for run in listed if run["baseline"]]
    verdict = new.json("verify")

    assert marked == [model_baseline.baseline(_runs(new))] == [upgrade_runs.BASELINE]
    assert verdict["baseline_run"] == upgrade_runs.BASELINE


def _normalized(results: dict, root: Path) -> dict:
    volatile = surfaces.Volatile(roots=surfaces.spellings(root))
    return {command: surfaces.normalize(result.json(), volatile)
            for command, result in results.items()}


def test_warm_churn_cache_equals_cold_after_upgrade(history, tmp_path):
    """R100: the candidate answers from the release's caches as it does from none."""
    warm = history.copy(tmp_path / "warm")
    cold = history.copy(tmp_path / "cold")
    upgrade_runs.drop_caches(cold)

    found = {name: _normalized(upgrade_runs.read(history.driver(root)), root)
             for name, root in (("warm", warm), ("cold", cold))}

    assert found["warm"] == found["cold"]
    assert found["warm"]["coupling --json"]["pairs"], "the history must couple a.py and b.py"


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


def test_two_installs_never_rewrite_each_others_caches(history, tmp_path):
    """R100 (b3e14d6): 0.4.3 and 0.4.4 read each other's churn cache as cold and
    rewrote it on every command."""
    root = history.copy(tmp_path / "repo")
    old, new = history.driver(root, history.site), history.driver(root)
    _rewrites(root, {}, [("candidate", new)])

    assert _rewrites(root, upgrade_runs.caches(root), [("release", old), ("candidate", new)]) == []


def test_the_release_and_the_candidate_answer_alike_or_declare_it(history, tmp_path):
    root = history.copy(tmp_path / "repo")
    old = {command: result.json() for command, result in
           upgrade_runs.read(history.driver(root, history.site)).items()}
    new = {command: result.json() for command, result in upgrade_runs.read(history.driver(root)).items()}
    found = [difference for command in upgrade_runs.COMMANDS
             for difference in upgrade_diff.differences(command, *_pair(old, new, command, root))]

    assert upgrade_diff.undeclared(found, upgrade_diff.read_changes(),
                                   releases.upload_date(history.version)) == []


def _pair(old: dict, new: dict, command: str, root: Path) -> tuple:
    volatile = surfaces.Volatile(roots=surfaces.spellings(root))
    return surfaces.normalize(old[command], volatile), surfaces.normalize(new[command], volatile)
