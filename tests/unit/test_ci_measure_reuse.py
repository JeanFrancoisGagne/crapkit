"""The base side reuses the measurement of an equal tree under equal packages.

A push's base is the commit the previous push measured as its candidate, so the
base job measured the same tree twice: 16.4 machine-min per push for a result
CI already held (gate audit 2026-10-01). The measurement is keyed by the blob of
every tracked *.py file, src/**, pyproject.toml, crapkit.toml and the test
fixture trees, the driver's own bytes, the Python and the measuring venv's pip
freeze. On an equal key the base hands off the stored measurement and the
runner does not run. A reused hand-off carries no suite result: the base's
suite exit is the earlier tree's, and the join judges only the candidate's.
"""
import json
from pathlib import Path
import shlex

import pytest

from test_ci_baseline_admission import repository
from test_ci_parallel_jobs import arguments, step, workflow
from test_ci_split_verdict import WHEEL, measuring, reinstalling
from test_ci_verdict import driver, git

PACKAGES = ["coverage==7.13.1", "pytest==8.4.2"]
COMMIT = ("-c", "user.name=CI Test", "-c", "user.email=ci@example.test", "commit", "-q")


class Runs:
    """Count the suite runs, the one cost a reuse saves, and set the venv's freeze."""

    def __init__(self, ci, monkeypatch, **options):
        measuring(ci, monkeypatch, **options)
        self.calls, self.packages = [], list(PACKAGES)
        measured = ci._measure

        def counted(root, *args):
            self.calls.append(root.name)
            return measured(root, *args)

        monkeypatch.setattr(ci, "_measure", counted)
        monkeypatch.setattr(ci, "_packages", lambda python, environment: list(self.packages))


def commit(repo, message, *, changes=None):
    for name, text in (changes or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, *COMMIT, "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def measure(ci, repo, base, side, measured, cache):
    options = ["--cache", str(cache)] if cache else []
    return ci.main(["--repo", str(repo), "--base", base, "--measure", side,
                    "--measured", str(measured), *options])


def proof(measured, side):
    return json.loads((measured / side / "proof.json").read_text(encoding="utf-8"))


def judged(ci, repo, base, measured, output):
    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])
    return code, json.loads((output / "verdict.json").read_text(encoding="utf-8"))


def test_an_empty_commit_as_base_calls_the_runner_zero_times_and_judges_as_a_fresh_run(
        tmp_path, monkeypatch):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch)
    cache = tmp_path / "cache"
    measured_first = commit(repo, "the previous push's candidate", changes={"docs/x.md": "a\n"})
    assert measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache) == 0
    assert runs.calls == ["candidate"]
    empty = commit(repo, "an empty commit")
    commit(repo, "this push", changes={"src/app.py": "def f(value):\n    return 3\n"})
    reused, fresh = tmp_path / "reused", tmp_path / "fresh"

    assert measure(ci, repo, empty, "base", reused, cache) == 0

    assert runs.calls == ["candidate"], "the base reused the stored measurement"
    handed = proof(reused, "base")
    assert handed["suite_exit"] is None, "a reused hand-off carries no suite result"
    assert handed["commit"] == measured_first
    assert (reused / "base/cov/py.json").read_bytes() == (tmp_path / "push-1/candidate/cov/py.json").read_bytes()
    assert (reused / "base" / WHEEL).read_bytes() == (tmp_path / "push-1/candidate" / WHEEL).read_bytes()

    for side, directory in (("candidate", reused), ("base", fresh), ("candidate", fresh)):
        measure(ci, repo, empty, side, directory, None)
    reinstalling(ci, monkeypatch)
    reuse_code, reuse_verdict = judged(ci, repo, empty, reused, tmp_path / "verdict-reused")
    fresh_code, fresh_verdict = judged(ci, repo, empty, fresh, tmp_path / "verdict-fresh")

    assert (reuse_code, fresh_code) == (0, 0), (reuse_verdict, fresh_verdict)
    assert reuse_verdict["suite_exits"] == [None, 0]
    assert fresh_verdict["suite_exits"] == [0, 0]
    assert reuse_verdict["verdict"] == fresh_verdict["verdict"]


@pytest.mark.parametrize(("change", "reruns"), [
    ({"src/app.py": "def f(value):\n    return 7\n"}, True),
    ({"crapkit.toml": None}, True),
    ({"pyproject.toml": "[project]\nname = 'app'\n"}, True),
    ({"tools/helper.py": "VALUE = 1\n"}, True),
    ({"tests/fixtures/sample/data.txt": "one byte more\n"}, True),
    ({"docs/x.md": "prose only\n"}, False),
    ({"README.md": "prose only\n"}, False),
    ({".github/workflows/ci.yml": "name: ci\n"}, False),
], ids=["src", "crapkit.toml", "pyproject", "tools-py", "fixture", "docs", "readme", "workflow"])
def test_a_keyed_byte_calls_the_runner_once_and_any_other_byte_calls_it_zero_times(
        tmp_path, monkeypatch, change, reruns):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch)
    cache = tmp_path / "cache"
    assert measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache) == 0
    texts = {name: text if text is not None else (repo / name).read_text(encoding="utf-8") + "\n"
             for name, text in change.items()}
    changed = commit(repo, "one changed file", changes=texts)

    assert measure(ci, repo, changed, "base", tmp_path / "push-2", cache) == 0

    assert runs.calls == ["candidate"] + ["base"] * reruns
    assert (proof(tmp_path / "push-2", "base")["suite_exit"] is None) is not reruns


@pytest.mark.parametrize("moved", ["packages", "driver", "python"])
def test_a_different_freeze_driver_or_python_calls_the_runner_once(tmp_path, monkeypatch, moved):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch)
    cache = tmp_path / "cache"
    assert measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache) == 0
    if moved == "packages":
        runs.packages = ["coverage==7.13.2", "pytest==8.4.2"]
    elif moved == "driver":
        edited = tmp_path / "ci.py"
        edited.write_bytes(ci.DRIVER.read_bytes() + b"\n")
        monkeypatch.setattr(ci, "DRIVER", edited)
    else:
        monkeypatch.setattr(ci.sys, "version", ci.sys.version + " (another build)")

    assert measure(ci, repo, "HEAD", "base", tmp_path / "push-2", cache) == 0

    assert runs.calls == ["candidate", "base"]
    stored = json.loads((cache / "proof.json").read_text(encoding="utf-8"))
    assert stored["commit"] == git(repo, "rev-parse", "HEAD") and stored["suite_exit"] == 0
    assert stored["inputs_key"] == proof(tmp_path / "push-2", "base")["inputs_key"], \
        "the new measurement replaced the stored one"


def test_the_candidate_always_measures_and_a_failing_suite_is_never_stored(tmp_path, monkeypatch):
    """The join judges the candidate's own suite exit, so the candidate never reuses.
    A suite that failed is not a result to carry to the next push."""
    ci = driver()
    repo, base = repository(tmp_path)
    runs = Runs(ci, monkeypatch, failing={"candidate"})
    cache = tmp_path / "cache"

    for attempt in ("first", "second"):
        assert measure(ci, repo, base, "candidate", tmp_path / attempt, cache) == 0
        assert proof(tmp_path / attempt, "candidate")["suite_exit"] == 1
    assert runs.calls == ["candidate", "candidate"]
    assert not (cache / "proof.json").exists()

    assert measure(ci, repo, "HEAD", "base", tmp_path / "third", cache) == 0
    assert runs.calls == ["candidate", "candidate", "base"]


def test_a_hand_off_with_no_coverage_report_is_never_stored(tmp_path, monkeypatch):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch, lost_coverage=False)
    original = ci._measure

    def no_report(root, *args):
        code = original(root, *args)
        (root / ".crapkit/cov/py.json").unlink()
        return code

    monkeypatch.setattr(ci, "_measure", no_report)
    cache = tmp_path / "cache"
    measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache)

    assert not (cache / "proof.json").exists()
    assert runs.calls == ["candidate"]


def test_the_join_refuses_a_candidate_hand_off_with_no_suite_result(tmp_path, monkeypatch):
    ci = driver()
    repo, base = repository(tmp_path)
    Runs(ci, monkeypatch)
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    for side in ("base", "candidate"):
        measure(ci, repo, base, side, measured, None)
    handed = measured / "candidate/proof.json"
    handed.write_text(json.dumps(dict(proof(measured, "candidate"), suite_exit=None)))
    reinstalling(ci, monkeypatch)

    code, saved = judged(ci, repo, base, measured, output)

    assert code == 1
    assert "candidate hand-off holds no suite result" in saved["error"]


def test_the_freeze_leaves_out_the_measured_wheel_whose_temp_path_changes_every_run():
    ci = driver()
    frozen = ("coverage==7.13.1\ncrapkit @ file:///tmp/crapkit-ci-x1/install/dist/crapkit-0.8.1-py3-none-any.whl\n"
              "Crapkit==0.8.1\npytest-cov==7.0.0\n")

    assert ci._frozen_lines(frozen) == ["coverage==7.13.1", "pytest-cov==7.0.0"]


def test_the_tree_key_reads_the_side_the_workflow_names(tmp_path, capsys):
    ci = driver()
    repo, base = repository(tmp_path)

    assert ci.main(["--repo", str(repo), "--base", base, "--tree-key", "base"]) == 0
    assert capsys.readouterr().out == f"key={ci.tree_key(repo, base)}\n"
    assert ci.tree_key(repo, base) != ci.tree_key(repo, "HEAD"), "src/app.py differs"


def _rendered(text, side):
    return text.replace("${{ matrix.side }}", side)


def test_the_base_job_restores_the_cache_the_measure_step_reads_and_the_save_step_writes():
    job = workflow()["jobs"]["verdict-measure"]
    keyed = step(job, "name", "the tree key")
    restore = step(job, "uses", "actions/cache/restore@")
    save = step(job, "uses", "actions/cache/save@")
    measure_step = step(job, "run", "python tools/testing/ci.py --base \"$BASE_REF\" --measure")
    names = [item.get("name") or item.get("uses") or item.get("run") for item in job["steps"]]

    for side in ("base", "candidate"):
        args = arguments(driver().parse_arguments, _rendered(measure_step["run"], side), "tools/testing/ci.py")
        assert args.cache == Path(restore["with"]["path"]) == Path(save["with"]["path"])
        tree = arguments(driver().parse_arguments, _rendered(keyed["run"].split(" >>")[0], side),
                         "tools/testing/ci.py")
        assert tree.tree_key == side
    assert shlex.split(keyed["run"])[-2:] == [">>", "$GITHUB_OUTPUT"]
    assert restore["if"] == "matrix.side == 'base'", "only the base reuses"
    assert restore["with"]["key"] == "verdict-measure-${{ steps.tree.outputs.key }}"
    assert restore["with"]["restore-keys"] == "verdict-measure-${{ steps.tree.outputs.key }}-"
    assert save["with"]["key"].startswith("verdict-measure-${{ steps.tree.outputs.key }}-")
    assert names.index(restore["uses"]) < names.index(measure_step["name"]) < names.index(save["uses"])
