"""Parallel scheduling preserves every case and subprocess coverage."""
import json
import runpy
import sys
import time
import tomllib
import xml.etree.ElementTree as ET

from packaging.requirements import Requirement

from crapkit.procs import run_owned
from test_suite_schedule import ROOT, SCRIPT, fixture_env, fixture_repo


def sample_repo(root):
    fixture_repo(root, "", contexts=False)
    root.joinpath("tests/unit/test_000_samples.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\nfrom crapkit import choose\n"
        "@pytest.mark.parametrize('sample', range(128))\ndef test_sample(sample):\n"
        "    if sample < 8:\n"
        "        Path(f'sample-worker-{sample}').write_text(str(os.getpid()))\n"
        "    assert choose(sample % 2) == (11 if sample % 2 else 22)\n",
        encoding="utf-8")


def suite_evidence(root):
    output = root / ".crapkit/cov"
    report = ET.parse(output / "junit.xml")
    cases = sorted((case.get("classname"), case.get("name")) for case in report.iter("testcase"))
    coverage = json.loads((output / "py.json").read_text())
    files = {name.replace("\\", "/"): data for name, data in coverage["files"].items()}
    measured = files["src/crapkit/__init__.py"]
    return {"cases": cases, "failures": len(report.findall(".//failure")),
            "errors": len(report.findall(".//error")), "skipped": len(report.findall(".//skipped")),
            "branches": measured["executed_branches"], "lines": measured["executed_lines"],
            "sample_workers": sorted({path.read_text() for path in root.glob("sample-worker-*")})}


def test_default_runner_keeps_all_cases_and_subprocess_coverage(tmp_path):
    sample_repo(tmp_path)
    command = [sys.executable, str(SCRIPT), "--repo", str(tmp_path),
               "--coverage", "--workers", "2", "--output", ".crapkit/cov"]
    started = time.perf_counter()
    done = run_owned(command, timeout=120, cwd=ROOT, env=fixture_env(tmp_path), capture_output=True)
    seconds = time.perf_counter() - started
    tmp_path.joinpath("runner.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr
    evidence = suite_evidence(tmp_path)
    tmp_path.joinpath("result.json").write_text(json.dumps(dict(evidence, seconds=seconds)), encoding="utf-8")
    expected = [("tests.unit.test_000_samples", f"test_sample[{sample}]") for sample in range(128)]
    expected += [("tests.unit.test_one", "test_unit"), ("tests.e2e.test_two", "test_child")]
    assert evidence["cases"] == sorted(expected)
    assert evidence["failures"] == 0
    assert evidence["errors"] == evidence["skipped"] == 0
    assert evidence["branches"] == [[2, 3], [2, 4]]
    assert evidence["lines"] == [1, 2, 3, 4]


def test_schedule_keeps_worker_counts_and_serial_override():
    schedule = runpy.run_path(str(SCRIPT))
    unit, e2e = schedule["test_commands"]()
    assert unit[-4:] == ["-n", "4", "--dist", "worksteal"]
    assert e2e[-2:] == ["--dist", "worksteal"] and e2e[e2e.index("-n") + 1] == "8"
    serial = schedule["test_commands"](unit_workers=1)[0]
    assert "-n" not in serial and "--dist" not in serial


def test_dev_extra_supports_work_stealing_on_python_313():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = map(Requirement, project["project"]["optional-dependencies"]["dev"])
    xdist = next(item for item in requirements if item.name == "pytest-xdist")
    assert "3.7.0" in xdist.specifier and "3.6.1" not in xdist.specifier
