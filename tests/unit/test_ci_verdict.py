"""The CI comparison must reject lost coverage even when complexity is unchanged."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_hosted_ci_invokes_the_full_isolated_verdict_and_saves_evidence():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python tools/testing/ci.py --base "$BASE_REF"' in workflow
    assert "path: .crapkit/ci-verdict" in workflow
    assert "include-hidden-files: true" in workflow
    assert "run: python tools/testing/run.py" in workflow


def driver():
    spec = importlib.util.spec_from_file_location("ci_verdict", ROOT / "tools/testing/ci.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()


def prepared(root, *, covered):
    output = root / ".crapkit/cov"
    output.mkdir(parents=True)
    output.joinpath("py.json").write_text(json.dumps({
        "meta": {"branch_coverage": True}, "files": {
            "src/app.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": [],
                           "executed_branches": [[2, 3], [2, 4]] if covered else [],
                           "missing_branches": [] if covered else [[2, 3], [2, 4]],
                           "functions": {"f": {"start_line": 1, "executed_lines": [1, 2, 3, 4],
                                               "summary": {"covered_lines": 4, "num_statements": 4,
                                                           "num_branches": 2,
                                                           "covered_branches": 2 if covered else 0}}}}}}))
    output.joinpath("junit.xml").write_text(
        '<testsuite tests="1" failures="0"><testcase classname="tests" name="test_app"/></testsuite>')


@pytest.mark.parametrize("lost_coverage", [False, True])
def test_real_verdict_compares_baseline_coverage_and_preserves_ledger(tmp_path, lost_coverage):
    ci = driver()
    base = tmp_path / "base"
    base.mkdir()
    (base / "src").mkdir()
    (base / "src/app.py").write_text("def f(value):\n    if value:\n        return 1\n    return 2\n")
    (base / ".gitignore").write_text(".crapkit/\n")
    (base / "crapkit.toml").write_text(
        '[crapkit]\ntarget=5\n[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
        '[[lane]]\nname="py"\ncommand="run"\nartifact=".crapkit/cov/py.json"\n'
        'parser="coveragepy"\nscopes=["src"]\nresults_artifact=".crapkit/cov/junit.xml"\n')
    git(base, "init", "-b", "main")
    git(base, "-c", "user.name=CI Test", "-c", "user.email=ci@example.test", "add", ".")
    git(base, "-c", "user.name=CI Test", "-c", "user.email=ci@example.test", "commit", "-qm", "base")
    candidate = tmp_path / "candidate"
    git(tmp_path, "clone", "--quiet", str(base), str(candidate))
    (candidate / "src/app.py").write_text("def f(value):\n    if value:\n        return 11\n    return 22\n")
    git(candidate, "add", ".")
    git(candidate, "-c", "user.name=CI Test", "-c", "user.email=ci@example.test", "commit", "-qm", "candidate")
    prepared(base, covered=True)
    prepared(candidate, covered=not lost_coverage)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    code, verdict = ci.verify_pair(base, candidate, Path(sys.executable), Path(sys.executable), env, env)
    assert code == (6 if lost_coverage else 0), verdict
    assert verdict["ok"] is not lost_coverage
    assert (candidate / ".crapkit/crap.sqlite").is_file()


def test_installed_source_is_verified_before_coverage_paths_are_mapped(tmp_path):
    from test_suite_schedule import fixture_repo

    ci = driver()
    root = tmp_path / "checkout"
    root.mkdir()
    fixture_repo(root, "")
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = ci._environment(python)
    site = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                          env=env, capture_output=True, text=True, check=True).stdout.strip()
    package = Path(site) / "crapkit"
    shutil.copytree(root / "src/crapkit", package)
    proof = ci.installed_source(root, python, env)
    assert ci._measure(root, python, env, proof) == 0
    coverage = json.loads((root / ".crapkit/cov/py.json").read_text())
    files = {name.replace("\\", "/"): value for name, value in coverage["files"].items()}
    assert files["src/crapkit/__init__.py"]["executed_branches"] == [[2, 3], [2, 4]]
    package.joinpath("__init__.py").write_text("def choose(value):\n    return 99\n")
    with pytest.raises(ValueError, match="differs"):
        ci.installed_source(root, python, env)
