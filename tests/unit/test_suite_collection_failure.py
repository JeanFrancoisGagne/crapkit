"""Collection errors retain partial evidence without publishing full coverage."""
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from test_suite_schedule import SCRIPT, fixture_repo


def test_collection_error_keeps_other_suite_and_refuses_final_coverage(tmp_path):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/unit/test_before.py").write_text(
        "from crapkit import choose\nchoose(True)\n")
    (tmp_path / "tests/unit/test_one.py").write_text("def test_broken(:\n")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COVERAGE_", "COV_CORE_"))}
    env["PYTHONPATH"] = str(tmp_path / "src")

    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2", "--output", ".crapkit/cov"],
                            env=env, capture_output=True, text=True)

    assert result.returncode == 1, result.stdout + result.stderr
    output = tmp_path / ".crapkit/cov"
    junit = ET.parse(output / "junit.xml")
    assert "test_child" in [case.attrib["name"] for case in junit.findall(".//testcase")]
    assert any("SyntaxError" in (error.text or "") for error in junit.findall(".//error"))
    assert not (output / "py.json").exists(), "collection failure is an incomplete measurement"
    assert any("unit exited 2" in error.attrib.get("message", "")
               for error in junit.findall(".//error")), result.stdout + result.stderr
    assert list(output.glob("incomplete/*/unit.xml"))
    assert list(output.glob("incomplete/*/e2e.xml"))
