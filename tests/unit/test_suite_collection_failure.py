"""Collection errors retain partial evidence without publishing full coverage."""
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from test_suite_schedule import SCRIPT, fixture_env, fixture_repo


@pytest.mark.parametrize(("suite", "workers", "exit_code"), [
    ("unit", 1, 2), ("unit", 4, 1), ("e2e", 4, 1),
])
def test_collection_error_keeps_other_suite_and_refuses_final_coverage(tmp_path, suite, workers, exit_code):
    fixture_repo(tmp_path, "")
    (tmp_path / f"tests/{suite}/test_before.py").write_text(
        "from crapkit import choose\nchoose(True)\n")
    (tmp_path / f"tests/{suite}/test_broken.py").write_text("def test_broken(:\n")
    env = fixture_env(tmp_path)

    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2", "--unit-workers", str(workers),
                             "--output", ".crapkit/cov"],
                            env=env, capture_output=True, text=True)

    assert result.returncode == 1, result.stdout + result.stderr
    output = tmp_path / ".crapkit/cov"
    junit = ET.parse(output / "junit.xml")
    healthy = "test_child" if suite == "unit" else "test_unit"
    assert healthy in [case.attrib["name"] for case in junit.findall(".//testcase")]
    assert any("SyntaxError" in (error.text or "") for error in junit.findall(".//error"))
    assert not (output / "py.json").exists(), "collection failure is an incomplete measurement"
    assert any(f"{suite} exited {exit_code}" in error.attrib.get("message", "")
               for error in junit.findall(".//error")), result.stdout + result.stderr
    assert list(output.glob("incomplete/*/unit.xml"))
    assert list(output.glob("incomplete/*/e2e.xml"))


@pytest.mark.parametrize("suite", ["unit", "e2e"])
def test_crashed_worker_keeps_other_suite_without_publishing_full_coverage(tmp_path, suite):
    fixture_repo(tmp_path, "")
    (tmp_path / f"tests/{suite}/test_crash.py").write_text(
        "import os\ndef test_worker_dies():\n    os._exit(13)\n")
    environment = fixture_env(tmp_path)
    environment["PYTEST_ADDOPTS"] = "--max-worker-restart=0"
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2", "--output", ".crapkit/cov"],
                            env=environment, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    output = tmp_path / ".crapkit/cov"
    junit = ET.parse(output / "junit.xml")
    healthy = "test_child" if suite == "unit" else "test_unit"
    assert healthy in [case.attrib["name"] for case in junit.findall(".//testcase")]
    assert any("crashed while running" in error.get("message", "") for error in junit.findall(".//error"))
    assert not (output / "py.json").exists()
    assert list(output.glob("incomplete/*/unit.xml"))
    assert list(output.glob("incomplete/*/e2e.xml"))


def test_completed_fixture_error_still_publishes_its_full_failed_measurement(tmp_path):
    fixture_repo(tmp_path, "")
    (tmp_path / "tests/unit/conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\ndef broken_setup():\n"
        "    raise RuntimeError('ordinary fixture failure')\n")
    environment = fixture_env(tmp_path)
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(tmp_path),
                             "--coverage", "--workers", "2", "--output", ".crapkit/cov"],
                            env=environment, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    output = tmp_path / ".crapkit/cov"
    junit = ET.parse(output / "junit.xml")
    assert {case.attrib["name"] for case in junit.findall(".//testcase")} == {"test_unit", "test_child"}
    assert "ordinary fixture failure" in ET.tostring(junit.getroot(), encoding="unicode")
    assert (output / "py.json").is_file()
    assert not list(output.glob("incomplete/*"))
