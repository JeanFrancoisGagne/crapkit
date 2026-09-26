"""CI tests every Python version pyproject.toml's classifiers name, on Ubuntu and Windows.

requires-python said >=3.11 and README said "Requires Python 3.11 or newer",
while the classifiers and CI's matrix stopped at 3.13. So a 3.14 user ran code
no CI job had run, including the argparse colour path that only 3.14 takes.
The classifiers are what a package index shows as supported; CI has to run
each of them, starting at requires-python's floor.
"""
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_VERSION = re.compile(r"Programming Language :: Python :: (3\.\d+)$")


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _classified() -> list[str]:
    found = (_VERSION.match(entry) for entry in _project()["classifiers"])
    return [match.group(1) for match in found if match]


def _ci_versions() -> list[str]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["test"]["strategy"]["matrix"]["python"]


def test_ci_tests_every_classified_version_and_no_other():
    assert _ci_versions() == _classified()


def test_the_classified_versions_start_at_the_requires_python_floor():
    assert _project()["requires-python"] == ">=" + _classified()[0]


def test_python_3_14_is_classified_and_tested():
    """The version whose argparse colours help: the colour tests skip below it."""
    assert "3.14" in _ci_versions()
