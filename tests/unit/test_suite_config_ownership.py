"""An outer coverage config must not select a nested project's source."""
import json
import os
import subprocess
import sys

from test_suite_nested_coverage import NESTED, SIBLING
from test_suite_schedule import SCRIPT, fixture_repo


def test_explicit_outer_config_stays_out_of_nested_pytest(tmp_path):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/e2e/test_two.py").write_text(NESTED.replace("'--cov=inner'", "'--cov'"))
    (tmp_path / "tests/e2e/test_sibling.py").write_text(SIBLING)
    config = tmp_path / "outer.coveragerc"
    config.write_text("[run]\nsource=crapkit\npatch=subprocess\n")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COVERAGE_", "COV_CORE_"))}
    env.update(PYTHONPATH=str(tmp_path / "src"), COVERAGE_RCFILE=str(config))

    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2", "--output", ".crapkit/cov"], env=env,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((tmp_path / ".crapkit/cov/py.json").read_text())
    files = {name.replace("\\", "/"): value for name, value in data["files"].items()}
    assert files["src/crapkit/__init__.py"]["executed_branches"] == [[2, 3], [2, 4]]
