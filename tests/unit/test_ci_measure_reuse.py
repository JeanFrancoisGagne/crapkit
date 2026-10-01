"""The base side reuses the measurement of an equal tree under equal packages.

A push's base is the commit the previous push measured as its candidate, so the
base job measured the same tree twice: 16.4 machine-min per push for a result
CI already held (gate audit 2026-10-01). The measurement is keyed by the side's
git tree id (every tracked path, mode and blob), the sha256 of ci.py and
_ci_linux.py in the checkout that runs them, the Python and the measuring
venv's pip freeze. On an equal key the base hands off the stored measurement
and the runner does not run. A reused hand-off carries no suite result: the
base's suite exit is the earlier tree's, and the join judges only the
candidate's.
"""
import json
from pathlib import Path
import shlex
import shutil

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


def test_a_push_whose_parent_was_measured_as_its_candidate_calls_the_base_runner_zero_times(
        tmp_path, monkeypatch):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch)
    cache = tmp_path / "cache"
    parent = git(repo, "rev-parse", "HEAD")
    assert measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache) == 0
    commit(repo, "the next push", changes={"src/app.py": "def f(value):\n    return 5\n"})

    assert measure(ci, repo, parent, "base", tmp_path / "push-2", cache) == 0

    assert runs.calls == ["candidate"]
    assert proof(tmp_path / "push-2", "base")["commit"] == parent


def _chmod(repo):
    """The file's own mode as well, or `git add` on a POSIX checkout puts 644 back."""
    (repo / "src/app.py").chmod(0o755)
    git(repo, "update-index", "--chmod=+x", "src/app.py")


def _rename(repo):
    git(repo, "mv", "src/app.py", "src/moved.py")


@pytest.mark.parametrize("change", [
    {"src/app.py": "def f(value):\n    return 7\n"},
    {"crapkit.toml": None},
    {"pyproject.toml": "[project]\nname = 'app'\n"},
    {"tools/helper.py": "VALUE = 1\n"},
    {"tests/fixtures/sample/data.txt": "one byte more\n"},
    {"docs/x.md": "prose only\n"},
    {"README.md": "prose only\n"},
    {".github/workflows/ci.yml": "name: ci\n"},
    {"tests/goldens/claude_hook/case.json": "{}\n"},
    {"plugin/hooks/hooks.json": "{}\n"},
    {"crapkit-ratchet.tsv": "path\n"},
    _chmod,
    _rename,
], ids=["src", "crapkit.toml", "pyproject", "tools-py", "fixture", "docs", "readme", "workflow",
        "golden", "plugin", "ratchet", "mode", "path"])
def test_any_tracked_byte_mode_or_path_calls_the_base_runner_once(tmp_path, monkeypatch, change):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch)
    cache = tmp_path / "cache"
    assert measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache) == 0
    if callable(change):
        change(repo)
        changed = commit(repo, "one changed entry")
    else:
        texts = {name: text if text is not None else (repo / name).read_text(encoding="utf-8") + "\n"
                 for name, text in change.items()}
        changed = commit(repo, "one changed file", changes=texts)

    assert measure(ci, repo, changed, "base", tmp_path / "push-2", cache) == 0

    assert runs.calls == ["candidate", "base"]
    assert proof(tmp_path / "push-2", "base")["suite_exit"] == 0


def test_the_driver_inputs_are_the_two_files_of_the_checkout_that_runs_them():
    ci = driver()
    here = Path(ci.__file__).parent

    assert ci.DRIVER_FILES == (here / "ci.py", here / "_ci_linux.py")


def _edited_driver(ci, tmp_path, monkeypatch, name):
    copies = tmp_path / "driver"
    copies.mkdir()
    for source in ci.DRIVER_FILES:
        shutil.copyfile(source, copies / source.name)
    (copies / name).write_bytes((copies / name).read_bytes() + b"\n")
    monkeypatch.setattr(ci, "DRIVER_FILES", tuple(copies / source.name for source in ci.DRIVER_FILES))


@pytest.mark.parametrize("moved", ["packages", "ci.py", "_ci_linux.py", "python"])
def test_a_different_freeze_driver_or_python_calls_the_runner_once(tmp_path, monkeypatch, moved):
    ci = driver()
    repo, _ = repository(tmp_path)
    runs = Runs(ci, monkeypatch)
    cache = tmp_path / "cache"
    assert measure(ci, repo, "HEAD~1", "candidate", tmp_path / "push-1", cache) == 0
    if moved == "packages":
        runs.packages = ["coverage==7.13.2", "pytest==8.4.2"]
    elif moved == "python":
        monkeypatch.setattr(ci.sys, "version", ci.sys.version + " (another build)")
    else:
        _edited_driver(ci, tmp_path, monkeypatch, moved)

    assert measure(ci, repo, "HEAD", "base", tmp_path / "push-2", cache) == 0

    assert runs.calls == ["candidate", "base"]
    stored = json.loads((cache / "proof.json").read_text(encoding="utf-8"))
    assert stored["commit"] == git(repo, "rev-parse", "HEAD") and stored["suite_exit"] == 0
    assert stored["inputs_key"] == proof(tmp_path / "push-2", "base")["inputs_key"],         "the new measurement replaced the stored one"


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
    assert capsys.readouterr().out == f"key={git(repo, 'rev-parse', base + '^{tree}')}\n"
    assert ci.main(["--repo", str(repo), "--base", base, "--tree-key", "candidate"]) == 0
    assert capsys.readouterr().out == f"key={git(repo, 'rev-parse', 'HEAD^{tree}')}\n"


UNFETCHED = "0" * 40  # github.event.before after a force push: a commit this clone never fetched


def test_a_base_this_clone_cannot_resolve_gets_an_empty_key_and_its_measurement_says_why(tmp_path, capsys):
    """The tree key step ran before the measure step and exited 1 on a base the
    checkout never fetched, so the measure step skipped and wrote no
    failure.json, and the verdict job died at download-artifact without the
    phase and error the hand-off names. The key is now empty (a cache miss) and
    the measure step records the failure."""
    ci = driver()
    repo, _ = repository(tmp_path)

    assert ci.main(["--repo", str(repo), "--base", UNFETCHED, "--tree-key", "base"]) == 0
    printed = capsys.readouterr()
    assert printed.out == "key=\n" and UNFETCHED in printed.err
    assert ci.main(["--repo", str(repo), "--base", UNFETCHED, "--measure", "base",
                    "--measured", str(tmp_path / "measured")]) == 1
    failure = json.loads((tmp_path / "measured" / "base" / "failure.json").read_text(encoding="utf-8"))
    assert UNFETCHED in json.dumps(failure)


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
    assert measure_step.get("if") == "${{ !cancelled() }}", "a failed key step must not skip the hand-off"
