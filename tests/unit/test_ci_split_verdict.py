"""The verdict measures base and candidate as two jobs, and a join judges them.

Each measurement job hands off its coverage evidence, the wheel it measured and
a proof of that wheel. The join runs on a third runner: it installs each uploaded
wheel into a fresh venv, proves it is the checkout's source again, and only then
lets the uploaded evidence stand for that revision.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from test_ci_baseline_admission import FAILURE, repository
from test_ci_verdict import ROOT, driver, git, prepared

ENVIRONMENT = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
WHEEL = "crapkit-0.0.0-py3-none-any.whl"


def measuring(ci, monkeypatch, *, lost_coverage=False, failing=()):
    """Stand in for the wheel build and the suite; the hand-off itself is real."""
    def install(root, destination):
        dist = destination / "dist"
        dist.mkdir(parents=True)
        commit = git(root, "rev-parse", "HEAD")
        (dist / WHEEL).write_text("wheel built from " + commit, encoding="utf-8")
        return Path(sys.executable), ENVIRONMENT, {
            "wheel": WHEEL, "wheel_sha256": hashlib.sha256((dist / WHEEL).read_bytes()).hexdigest(),
            "commit": commit}

    def measure(root, python, environment, proof):
        prepared(root, covered=not (root.name == "candidate" and lost_coverage))
        if root.name in failing:
            (root / ".crapkit/cov/junit.xml").write_text(FAILURE)
        return int(root.name in failing)

    monkeypatch.setattr(ci, "install_revision", install)
    monkeypatch.setattr(ci, "_measure", measure)


def reinstalling(ci, monkeypatch):
    """Record each venv the join builds and each provenance proof it runs."""
    calls = []

    def install_wheel(destination, requirement):
        calls.append(("install", destination.name, Path(requirement).name))
        return Path(sys.executable), ENVIRONMENT

    def installed_source(root, python, environment):
        calls.append(("prove", root.name, python))
        return {"package": f"site-packages of {root.name}"}

    monkeypatch.setattr(ci, "_install_wheel", install_wheel)
    monkeypatch.setattr(ci, "installed_source", installed_source)
    return calls


def hand_offs(ci, repo, base, measured):
    return [ci.main(["--repo", str(repo), "--base", base, "--measure", side,
                     "--measured", str(measured)]) for side in ("base", "candidate")]


def test_each_side_hands_off_its_evidence_wheel_and_proof(tmp_path, monkeypatch):
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch, failing={"candidate"})
    measured = tmp_path / "measured"

    assert hand_offs(ci, repo, base, measured) == [0, 0], "a failing suite is the join's to judge"

    commits = {"base": base, "candidate": git(repo, "rev-parse", "HEAD")}
    for side, suite_exit in (("base", 0), ("candidate", 1)):
        proof = json.loads((measured / side / "proof.json").read_text(encoding="utf-8"))
        assert proof["commit"] == commits[side]
        assert proof["suite_exit"] == suite_exit
        wheel = (measured / side / WHEEL).read_bytes()
        assert wheel == f"wheel built from {commits[side]}".encode()
        assert hashlib.sha256(wheel).hexdigest() == proof["wheel_sha256"]
    assert "<failure" not in (measured / "base/cov/junit.xml").read_text()
    assert (measured / "candidate/cov/junit.xml").read_text() == FAILURE


def test_a_second_hand_off_replaces_the_first(tmp_path, monkeypatch):
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch)
    measured = tmp_path / "measured"
    stale = measured / "base"
    (stale / "cov").mkdir(parents=True)
    (stale / "cov/stale.xml").write_text("an earlier attempt")
    (stale / "crapkit-9.9.9-py3-none-any.whl").write_text("an earlier wheel")
    (stale / "failure.json").write_text('{"phase": "install", "error": "an earlier failure"}')

    assert hand_offs(ci, repo, base, measured)[0] == 0

    assert sorted(path.name for path in stale.iterdir()) == sorted(["cov", "proof.json", WHEEL])
    assert not (stale / "cov/stale.xml").exists()


def raising(error):
    def fail(*args):
        raise error
    return fail


PIP_FAILED = subprocess.CalledProcessError(1, ["pip", "install", WHEEL])
NO_RUNNER = ValueError("revision has no tools/testing/run.py; select a baseline with the shared runner")
DISK_FULL = OSError(28, "No space left on device")


@pytest.mark.parametrize(("step", "error", "phase"), [
    ("install_revision", PIP_FAILED, "install"),
    ("_measure", NO_RUNNER, "measure"),
    ("_hand_off", DISK_FULL, "hand-off"),
], ids=["install", "measure", "hand-off"])
def test_a_measurement_that_stops_hands_off_why_instead_of_nothing(tmp_path, monkeypatch, step, error, phase):
    """The join never runs after a failed measurement job, so the upload carries the diagnosis."""
    ci = driver()
    repo, base = repository(tmp_path)
    measured = tmp_path / "measured"
    earlier = measured / "candidate"
    earlier.mkdir(parents=True)
    (earlier / "proof.json").write_text('{"commit": "an earlier attempt"}')
    measuring(ci, monkeypatch)
    monkeypatch.setattr(ci, step, raising(error))

    code = ci.main(["--repo", str(repo), "--base", base, "--measure", "candidate",
                    "--measured", str(measured)])

    assert code == 1
    assert sorted(path.name for path in earlier.iterdir()) == ["failure.json"]
    expected = {
        "install": "Command '['pip', 'install', 'crapkit-0.0.0-py3-none-any.whl']' returned non-zero exit status 1.",
        "measure": "revision has no tools/testing/run.py; select a baseline with the shared runner",
        "hand-off": "[Errno 28] No space left on device",
    }[phase]
    assert json.loads((earlier / "failure.json").read_text(encoding="utf-8")) == {"phase": phase, "error": expected}


def test_a_measurement_of_a_missing_base_records_its_checkout_failure(tmp_path):
    ci = driver()
    repo, _ = repository(tmp_path)
    measured = tmp_path / "measured"

    code = ci.main(["--repo", str(repo), "--base", "missing-ref", "--measure", "base",
                    "--measured", str(measured)])

    failure = json.loads((measured / "base/failure.json").read_text(encoding="utf-8"))
    assert code == 1
    assert failure["phase"] == "checkout"
    assert "missing-ref" in failure["error"], failure


@pytest.mark.parametrize("lost_coverage", [False, True])
def test_the_join_judges_the_hand_offs_after_proving_each_wheel_again(tmp_path, monkeypatch, lost_coverage):
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch, lost_coverage=lost_coverage)
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    assert hand_offs(ci, repo, base, measured) == [0, 0]
    calls = reinstalling(ci, monkeypatch)

    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])

    assert code == (6 if lost_coverage else 0)
    assert calls == [("install", "base-install", WHEEL), ("prove", "base", Path(sys.executable)),
                     ("install", "candidate-install", WHEEL), ("prove", "candidate", Path(sys.executable))]
    saved = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    assert saved["phase"] == "complete"
    assert saved["suite_exits"] == [0, 0]
    assert saved["verdict"]["ok"] is not lost_coverage
    assert saved["candidate"]["reinstalled"] == "site-packages of candidate"
    retained = output / saved["evidence_dir"]
    assert (retained / "candidate/cov/py.json").read_bytes() == (measured / "candidate/cov/py.json").read_bytes()
    assert (retained / "base/crap.sqlite").is_file()


def test_the_join_fails_a_candidate_whose_suite_failed_even_when_the_verdict_holds(tmp_path, monkeypatch):
    """The baseline failed the same test, so verify holds; the failing suite does not."""
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch, failing={"base", "candidate"})
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    hand_offs(ci, repo, base, measured)
    reinstalling(ci, monkeypatch)

    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])

    saved = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    assert code == 1
    assert saved["suite_exits"] == [1, 1]
    assert saved["verdict"]["ok"] is True


def test_the_join_refuses_a_wheel_other_than_the_one_measured(tmp_path, monkeypatch):
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch)
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    hand_offs(ci, repo, base, measured)
    (measured / "candidate" / WHEEL).write_text("a different build", encoding="utf-8")
    calls = reinstalling(ci, monkeypatch)

    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])

    saved = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    assert code == 1
    assert saved["phase"] == "candidate-install"
    assert WHEEL in saved["error"] and "not the wheel its measurement recorded" in saved["error"]
    assert ("install", "candidate-install", WHEEL) not in calls


def test_the_join_refuses_a_measurement_of_another_commit(tmp_path, monkeypatch):
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch)
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    hand_offs(ci, repo, base, measured)
    candidate = git(repo, "rev-parse", "HEAD")
    proof = measured / "base/proof.json"
    proof.write_text(json.dumps(dict(json.loads(proof.read_text()), commit=candidate)))
    reinstalling(ci, monkeypatch)

    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])

    saved = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    assert code == 1
    assert saved["phase"] == "base-install"
    assert f"measurement of {candidate} cannot stand for checkout {base}" in saved["error"]


def test_the_join_refuses_a_hand_off_missing_its_proof_fields(tmp_path, monkeypatch):
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch)
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    hand_offs(ci, repo, base, measured)
    proof = measured / "base/proof.json"
    fields = json.loads(proof.read_text())
    del fields["suite_exit"]
    proof.write_text(json.dumps(fields))
    reinstalling(ci, monkeypatch)

    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])

    saved = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    assert code == 1
    assert "proof lacks suite_exit" in saved["error"]


@pytest.mark.parametrize(("side", "suite_exit"), [("base", 0), ("candidate", 1)])
def test_the_join_refuses_a_hand_off_that_holds_no_coverage_report(tmp_path, monkeypatch, capsys,
                                                                    side, suite_exit):
    """A measured suite whose coverage report stopped still hands off, without
    py.json. The join names the side, the suite exit its proof recorded and the
    job whose log holds the report's error, where verify said only that a lane
    produced no artifact."""
    ci = driver()
    repo, base = repository(tmp_path)
    measuring(ci, monkeypatch, failing={"candidate"})
    measured, output = tmp_path / "measured", tmp_path / "verdict"
    hand_offs(ci, repo, base, measured)
    (measured / side / "cov/py.json").unlink()
    calls = reinstalling(ci, monkeypatch)

    code = ci.main(["--repo", str(repo), "--base", base, "--join",
                    "--measured", str(measured), "--output", str(output)])

    saved = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    refusal = (f"{side} hand-off holds no cov/py.json; its suite exited {suite_exit}, "
               f"and the log of CI job verdict-measure ({side}) says why")
    assert code == 1
    assert (saved["phase"], saved["error"]) == (side + "-install", refusal)
    assert refusal in capsys.readouterr().err
    assert ("install", side + "-install", WHEEL) not in calls
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    assert "verdict-measure" in jobs, "the refusal names a job CI runs"


def test_measure_and_join_are_separate_steps(capsys):
    ci = driver()
    with pytest.raises(SystemExit) as refused:
        ci.main(["--base", "HEAD", "--measure", "base", "--join"])
    assert refused.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
