"""The small corpus's own recording suite: run by tools/accuracy/regenerate.py
under coverage.py to write recorded/py.json, never by the accuracy suite."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def load():
    """Import a corpus module by path, for file names no import statement spells."""
    def _load(relative):
        path = ROOT / relative
        spec = importlib.util.spec_from_file_location(path.stem.replace(" ", "_"), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return _load
