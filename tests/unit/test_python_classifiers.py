"""pyproject's Python classifiers name the CPythons the deploy suite starts crapkit on.

A classifier is a claim a user reads on PyPI before installing. tools/deploy/
pins.toml pins every CPython the deploy cells install crapkit into and run the
60-second start on (lin-pip-start-py311 through lin-pip-start-py314), so that
list is the evidence, and the classifiers name exactly it. 3.14 joined when
its start cell passed.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_MINOR = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _classified() -> list[str]:
    found = (_MINOR.match(text) for text in _pyproject()["project"]["classifiers"])
    return [match.group(1) for match in found if match]


def _started() -> list[str]:
    pins = tomllib.loads((ROOT / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))
    return [".".join(version.split(".")[:2]) for version in pins["python"]["versions"]]


def test_the_classifiers_name_every_cpython_the_deploy_cells_start_on():
    assert _classified() == _started() == ["3.11", "3.12", "3.13", "3.14"]


def test_the_oldest_classifier_is_the_floor_requires_python_sets():
    assert _pyproject()["project"]["requires-python"] == f">={_classified()[0]}"
