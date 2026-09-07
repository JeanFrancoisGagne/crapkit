"""Unit scheduling preserves the full suite, child coverage and a serial mode."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from test_suite_schedule import SCRIPT, fixture_env, fixture_repo


@pytest.mark.parametrize("workers", [1, 2])
def test_unit_worker_selection_keeps_case_identity_and_subprocess_coverage(tmp_path, workers):
    fixture_repo(tmp_path, "", contexts=False)
    (tmp_path / "tests/unit/test_workers.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\nfrom crapkit import choose\n"
        "@pytest.mark.parametrize('sample', range(8))\ndef test_worker(sample):\n"
        "    Path(f'unit-pid-{os.getpid()}').touch()\n    assert choose(True) == 11\n")
    command = [sys.executable, str(SCRIPT), "--repo", str(tmp_path), "--coverage",
               "--workers", "2", "--unit-workers", str(workers), "--output", ".crapkit/cov"]
    result = subprocess.run(command, env=fixture_env(tmp_path), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / ".crapkit/cov"
    report = ET.parse(output / "junit.xml")
    names = sorted(case.attrib["name"] for case in report.findall(".//testcase"))
    assert names == sorted(["test_child", "test_unit", *[f"test_worker[{n}]" for n in range(8)]])
    assert len(list(tmp_path.glob("unit-pid-*"))) == workers
    coverage = json.loads((output / "py.json").read_text())
    files = {name.replace("\\", "/"): data for name, data in coverage["files"].items()}
    measured = files["src/crapkit/__init__.py"]
    assert measured["executed_branches"] == [[2, 3], [2, 4]]
    assert {context for contexts in measured["contexts"].values() for context in contexts} == {""}
    assert len(ET.parse(output / "unit.xml").findall(".//testcase")) == 9
    assert len(ET.parse(output / "e2e.xml").findall(".//testcase")) == 1
    assert (output / "unit.coverage").is_file()
    assert (output / "e2e.coverage").is_file()
