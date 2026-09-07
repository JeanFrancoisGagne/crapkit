"""A refused CI verdict must retain the inputs needed to diagnose it."""
import json
import os
from pathlib import Path
import sqlite3
import sys

from test_ci_verdict import ROOT, driver, git, prepared


def test_ci_preserves_failed_measurement_and_copied_ledger(tmp_path, monkeypatch):
    ci = driver()
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
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    monkeypatch.setattr(ci, "install_revision", lambda root, destination:
                        (Path(sys.executable), environment, {"fixture": root.name}))

    def measure(root, *args):
        prepared(root, covered=True)
        for suite in ("unit", "e2e"):
            (root / ".crapkit/cov" / (suite + ".xml")).write_text('<testsuite tests="1"/>')
            (root / ".crapkit/cov" / (suite + ".coverage")).write_bytes(b"coverage fixture")
        if root.name == "candidate":
            (root / ".crapkit/cov/py.json").write_text("{broken")
            return 1
        return 0

    monkeypatch.setattr(ci, "_measure", measure)
    output = tmp_path / "evidence"

    assert ci.main(["--repo", str(repo), "--base", base, "--output", str(output)]) == 1

    saved = json.loads((output / "verdict.json").read_text())
    assert saved["phase"] == "verify" and saved["suite_exits"] == [0, 1]
    assert "candidate verification refused" in saved["error"]
    assert saved["base"] == {"fixture": "base"}
    evidence = output / saved["evidence_dir"]
    for name in ("base", "candidate"):
        assert (evidence / name / "cov/unit.xml").is_file()
        assert (evidence / name / "cov/e2e.coverage").read_bytes() == b"coverage fixture"
        with sqlite3.connect(evidence / name / "crap.sqlite") as db:
            assert db.execute("SELECT commit_sha FROM runs").fetchall() == [(base,)]
    assert (evidence / "candidate/cov/py.json").read_text() == "{broken"
    assert json.loads((evidence / "verdict.json").read_text()) == saved


def test_checkout_refusal_replaces_latest_status_and_keeps_the_earlier_attempt(tmp_path):
    ci = driver()
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    output = tmp_path / "evidence"
    args = ["--repo", str(repo), "--base", "missing-ref", "--output", str(output)]

    assert ci.main(args) == 1
    first = json.loads((output / "verdict.json").read_text())
    assert ci.main(args) == 1
    latest = json.loads((output / "verdict.json").read_text())

    assert latest["phase"] == "checkout" and "error" in latest
    assert latest["evidence_dir"] != first["evidence_dir"]
    assert json.loads((output / first["evidence_dir"] / "verdict.json").read_text()) == first
