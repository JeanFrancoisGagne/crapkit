"""Nested pytest owns its data while sibling CLI coverage remains measured."""
import json
import os
from pathlib import Path
import subprocess
import sys

from test_suite_schedule import SCRIPT, fixture_repo


NESTED = '''import subprocess, sys, time
from pathlib import Path

def test_nested(tmp_path):
    root = Path(__file__).resolve().parents[2]
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "inner.py").write_text("def result():\\n    return 3\\n")
    (inner / "pyproject.toml").write_text('[tool.pytest.ini_options]\\n')
    (inner / "test_inner.py").write_text(
        'from pathlib import Path\\nimport coverage\\nfrom inner import result\\n'
        'def test_result():\\n'
        '    data = Path(coverage.Coverage.current().get_data().data_filename())\\n'
        '    assert data.is_relative_to(Path.cwd()), str(data)\\n'
        '    assert result() == 3\\n')
    (root / "nested-ready").touch()
    deadline = time.monotonic() + 15
    while not (root / "sibling-ready").exists():
        assert time.monotonic() < deadline
        time.sleep(.01)
    try:
        child = subprocess.run([sys.executable, '-m', 'pytest', 'test_inner.py',
            '--cov=inner', '--cov-report=json:inner.json', '-q', '-p', 'no:randomly'],
            cwd=inner, capture_output=True, text=True)
    finally:
        (root / "nested-done").touch()
    assert child.returncode == 0, child.stdout + child.stderr
    assert (inner / "inner.json").is_file()
'''

SIBLING = '''import subprocess, sys, time
from pathlib import Path

def test_sibling(tmp_path):
    root = Path(__file__).resolve().parents[2]
    deadline = time.monotonic() + 15
    while not (root / "nested-ready").exists():
        assert time.monotonic() < deadline
        time.sleep(.01)
    code = (
        'from crapkit import choose\\nfrom pathlib import Path\\nimport sys,time\\n'
        'root=Path(sys.argv[1])\\n(root/"sibling-ready").touch()\\n'
        'deadline=time.monotonic()+15\\n'
        'while not (root/"nested-done").exists():\\n'
        '    assert time.monotonic()<deadline\\n'
        '    choose(False)\\n    time.sleep(.01)\\n'
        'print(choose(False))\\n')
    child = subprocess.run([sys.executable, '-c', code, str(root)],
                           cwd=tmp_path, capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == '22'
'''


def test_real_nested_pytest_and_parallel_cli_sibling_keep_separate_data(tmp_path):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/e2e/test_two.py").write_text(NESTED)
    (tmp_path / "tests/e2e/test_sibling.py").write_text(SIBLING)
    environment = dict(os.environ, PYTHONPATH=str(tmp_path / "src"))
    for key in tuple(environment):
        if key.startswith(("COVERAGE_", "COV_CORE_")):
            environment.pop(key)

    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2"], env=environment,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((tmp_path / ".crapkit/cov/py.json").read_text())
    files = {name.replace("\\", "/"): measured for name, measured in data["files"].items()}
    assert files["src/crapkit/__init__.py"]["executed_branches"] == [[2, 3], [2, 4]]
