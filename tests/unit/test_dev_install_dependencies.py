"""A development install must run the action, workflow and rendering contracts."""
from pathlib import Path
import re
import tomllib


def test_dev_extra_installs_yaml_and_pillow_for_committed_contracts():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = project["project"]["optional-dependencies"]["dev"]
    packages = {re.split(r"[<>=!~\[ ]", item, maxsplit=1)[0].lower()
                for item in requirements}

    assert {"pyyaml", "pillow"} <= packages, "a development install must collect every contract"
