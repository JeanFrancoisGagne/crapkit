"""The committed demo shows the next step `crapkit worklist` prints today.

README.md shows docs/demo.gif on GitHub and PyPI, and the handbook embeds it.
The frames were last generated at 0.5.0, and since then worklist ends a repo
with no marks file with two more lines: why to seed, and `-> next: crapkit
ratchet seed`. The demo's worklist step runs on exactly that repo, yet its
frames stopped at the table. Nothing compared the committed frames with what
the printer says.

This reads the lines from the printer itself, wraps them at the demo's width
the way its renderer does, and looks for each row in the committed SVG. A
wording change to the hint fails here until `python tools/demo/generate.py`
runs again.
"""
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "demo"))

demo_render = pytest.importorskip("demo_render")
demo_run = pytest.importorskip("demo_run")

from crapkit.cli import queue  # noqa: E402

SVG = ROOT / "docs" / "demo.svg"


def _committed_rows() -> set[str]:
    root = ElementTree.parse(SVG).getroot()
    return {element.text or "" for element in root.iter() if element.tag.endswith("text")}


def _next_step(tmp_path, monkeypatch) -> list[str]:
    """What worklist prints after its table on the demo's repo: a trusted coverage
    run and no marks file, under the console-script spelling the frames show."""
    monkeypatch.setattr(queue, "_self", lambda: "crapkit")
    config = SimpleNamespace(ratchet_file="crapkit-ratchet.tsv")
    return queue._worklist_next(tmp_path, config, {"id": 3, "kind": "coverage", "lanes": {}})


def test_the_demo_shows_the_next_step_worklist_prints_on_a_repo_with_no_marks(tmp_path, monkeypatch):
    lines = _next_step(tmp_path, monkeypatch)
    rows = [row for line in lines for row in demo_render._wrap(line, demo_run.COLUMNS)]

    assert lines[-1] == "-> next: crapkit ratchet seed"
    assert [row for row in rows if row not in _committed_rows()] == []
