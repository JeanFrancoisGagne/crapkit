"""The runner never replaces a selected old installation with candidate source."""
import json
import shutil
import subprocess
import sys

from test_ci_verdict import driver
from test_suite_schedule import SCRIPT, fixture_env


def test_old_selected_package_refuses_before_loading_candidate_helpers(tmp_path):
    package = tmp_path / "src/crapkit"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path)],
                            env=fixture_env(tmp_path), capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert "selected Crapkit installation lacks" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".crapkit/test-runs").exists()


def test_wheel_measurement_uses_the_runner_from_that_revision(tmp_path, dependency_venv):
    ci = driver()
    root = tmp_path / "checkout"
    runner = root / "tools/testing/run.py"
    runner.parent.mkdir(parents=True)
    package = root / "src/crapkit"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    python, site = dependency_venv(tmp_path / "venv")
    shutil.copytree(package, site / "crapkit")
    runner.write_text(
        "import crapkit, json\nfrom pathlib import Path\n"
        "Path('runner-proof.json').write_text(json.dumps({'version': crapkit.__version__, "
        "'package': crapkit.__file__, 'runner': __file__}))\n", encoding="utf-8")
    env = ci._environment(python)
    proof = ci.installed_source(root, python, env)
    assert ci._measure(root, python, env, proof) == 0
    report = json.loads((root / "runner-proof.json").read_text(encoding="utf-8"))
    assert report["version"] == "0.7.0"
    assert report["package"] == str(site / "crapkit/__init__.py")
    assert report["runner"] == str(runner)
    assert proof["runner"]["path"] == str(runner)
