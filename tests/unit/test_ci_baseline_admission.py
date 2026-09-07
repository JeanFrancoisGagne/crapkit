"""A complete failing baseline remains evidence; interrupted suites do not."""
import json
import os
from pathlib import Path
import sys

import pytest

from test_ci_verdict import ROOT, driver, git, prepared


def repository(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/app.py").write_text("def f(value):\n    if value:\n        return 1\n    return 2\n")
    (repo / ".gitignore").write_text(".crapkit/\n")
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget=5\n[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
        '[[lane]]\nname="py"\ncommand="run"\nartifact=".crapkit/cov/py.json"\n'
        'parser="coveragepy"\nscopes=["src"]\nresults_artifact=".crapkit/cov/junit.xml"\n')
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=CI Test", "-c", "user.email=ci@example.test", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src/app.py").write_text("def f(value):\n    if value:\n        return 11\n    return 22\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=CI Test", "-c", "user.email=ci@example.test", "commit", "-qm", "candidate")
    return repo, base


FAILURE = '<testsuite tests="1"><testcase classname="tests" name="known"><failure/></testcase></testsuite>'
CRASH = ('<testsuite tests="1"><testcase classname="tests" name="known">'
         '<error message="worker &apos;gw0&apos; crashed while running &apos;tests/test_app.py&apos;"/>'
         '</testcase></testsuite>')


def comparison(tmp_path, monkeypatch, baseline_xml, *, candidate_fails=False):
    ci = driver()
    repo, base = repository(tmp_path)
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    monkeypatch.setattr(ci, "install_revision", lambda root, destination:
                        (Path(sys.executable), environment, {"fixture": root.name}))

    def measure(root, *args):
        prepared(root, covered=True)
        junit = root / ".crapkit/cov/junit.xml"
        if root.name == "base":
            if baseline_xml is None:
                junit.unlink()
            else:
                junit.write_text(baseline_xml)
            return 1
        if candidate_fails:
            junit.write_text(FAILURE)
        return int(candidate_fails)

    monkeypatch.setattr(ci, "_measure", measure)
    output = tmp_path / "evidence"
    code = ci.main(["--repo", str(repo), "--base", base, "--output", str(output)])
    return code, json.loads((output / "verdict.json").read_text()), output


def test_complete_baseline_failures_are_recorded_without_rejecting_a_passing_candidate(tmp_path, monkeypatch):
    code, saved, output = comparison(tmp_path, monkeypatch, FAILURE)

    assert code == 0, saved
    assert saved["suite_exits"] == [1, 0]
    assert saved["verdict"]["ok"] is True
    assert saved["verdict"]["tests"]["base"] == {"tests": 1, "skipped": 0, "failures": ["tests::known"]}
    assert saved["verdict"]["tests"]["candidate"]["failures"] == []
    retained = output / saved["evidence_dir"] / "base/cov/junit.xml"
    assert retained.read_text() == FAILURE


@pytest.mark.parametrize("baseline_xml", [None, CRASH, "<testsuite tests='0'/>"])
def test_missing_or_unfinished_baseline_cannot_be_scored(tmp_path, monkeypatch, baseline_xml):
    code, saved, output = comparison(tmp_path, monkeypatch, baseline_xml)

    assert code == 1
    assert saved["phase"] == "verify"
    assert "error" in saved, saved
    assert not (output / saved["evidence_dir"] / "base/crap.sqlite").exists()


def test_candidate_suite_failure_still_fails_when_its_failure_existed_in_baseline(tmp_path, monkeypatch):
    code, saved, _ = comparison(tmp_path, monkeypatch, FAILURE, candidate_fails=True)

    assert code == 1
    assert saved["suite_exits"] == [1, 1]
    assert saved["verdict"]["ok"] is True
