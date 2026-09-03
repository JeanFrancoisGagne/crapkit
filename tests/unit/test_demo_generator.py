"""The demo generator renders the same bytes twice, and names no machine path.

docs/demo.gif and docs/demo.svg are committed, so a render that moved with the
clock, the temp directory or the run would show up as a diff on every
regeneration and nobody would trust `git status` again. These drive the renderer
over a fixture transcript rather than running the five real commands: the
commands are what tools/demo/generate.py does, and they take a git repo and a
pytest run to answer.
"""
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

# Rendering needs Pillow, which the dev extra does not ship: CI installs the
# suite's needs, and regenerating the demo is a maintainer step. Without it these
# tests skip; the docs contract on the committed GIF still runs everywhere.
pytest.importorskip("PIL")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(ROOT / "tools" / "demo"))

demo_render = pytest.importorskip("demo_render")
demo_run = pytest.importorskip("demo_run")

# Frames: 5 typed (14 characters of "$ crapkit init" at 3 per frame) + 1 settle
# + 1 output for the first step, 7 typed + 2 continuation lines + 1 settle
# + 1 output for the second, and 1 closing frame.
EXPECTED_FRAMES = 19


def _transcript() -> dict:
    return {"columns": 40, "rows": 8, "steps": [
        {"shown": ["crapkit init"], "exit": 0, "hold": 1.0,
         "output": [["out", "wrote crapkit.toml with 1 scope(s): calc"]]},
        {"shown": ["cat >> a.py <<'PY'", "def f():", "PY"], "exit": 2, "hold": 1.0,
         "output": [["err", "crapkit advisory: 1 function(s) over ceiling 6"]]},
    ]}


@pytest.fixture
def frames() -> list:
    return demo_render.frames(_transcript())


def test_the_frame_count_is_the_typing_plus_one_frame_per_output(frames):
    assert len(frames) == EXPECTED_FRAMES


def test_every_frame_holds_exactly_one_screen_of_rows(frames):
    assert {len(rows) for rows, _ in frames} == {_transcript()["rows"]}


def test_the_last_frame_shows_the_gate_verdict_and_its_exit_code(frames):
    shown = [text for _, text in frames[-1][0]]

    assert "[exit 2]" in shown


def test_two_renders_of_one_transcript_write_the_same_gif(tmp_path, frames):
    canvas = demo_render.size(_transcript())
    first, second = tmp_path / "a.gif", tmp_path / "b.gif"
    demo_render.render_gif(frames, first, canvas)
    demo_render.render_gif(frames, second, canvas)

    assert first.read_bytes() == second.read_bytes()


def test_two_renders_of_one_transcript_write_the_same_svg(tmp_path, frames):
    canvas = demo_render.size(_transcript())
    first, second = tmp_path / "a.svg", tmp_path / "b.svg"
    demo_render.render_svg(frames, first, canvas)
    demo_render.render_svg(frames, second, canvas)

    assert first.read_bytes() == second.read_bytes()


def test_the_svg_parses_as_xml(tmp_path, frames):
    path = tmp_path / "demo.svg"
    demo_render.render_svg(frames, path, demo_render.size(_transcript()))

    root = ElementTree.parse(path).getroot()

    assert root.tag.endswith("svg")


def test_no_frame_carries_an_absolute_path(frames):
    assert demo_run.absolute_paths(demo_render.frame_text(frames)) == []


@pytest.mark.parametrize("line", [r"C:\Users\dana\ledger", "wrote /home/dana/x.py",
                                  "artifact at /tmp/run/py.json"])
def test_the_absolute_path_check_catches_what_redaction_missed(line):
    """The positive control. A check that answers "clean" to everything would
    pass the test above on a transcript full of somebody's home directory."""
    assert demo_run.absolute_paths([line]) == [line]


def test_redaction_removes_the_repo_path_in_every_spelling(tmp_path):
    repo = tmp_path / "ledger"
    repo.mkdir()
    slashed = str(repo).replace("\\", "/")

    text = demo_run.redact(f"lane wrote {repo} and {slashed}/py.json", repo)

    assert demo_run.absolute_paths([text]) == []


def test_redaction_removes_a_wall_clock_stamp(tmp_path):
    text = demo_run.redact("artifact stamped 2026-08-24T16:20:00 in 12.4s", tmp_path)

    assert "2026" not in text and "12.4s" not in text


def test_redaction_spells_the_module_run_as_the_console_script(tmp_path):
    """The generator runs `python -m crapkit`, so every next-step crapkit prints
    names the interpreter by its absolute path (`invocation._self`). The frames
    show the spelling a reader installs, and the path check would otherwise
    refuse the whole render."""
    import sys

    quoted = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    line = f"detected 1 lane(s): py - next: run `{quoted} -m crapkit coverage`"

    text = demo_run.redact(line, tmp_path)

    assert text == "detected 1 lane(s): py - next: run `crapkit coverage`"
    assert demo_run.absolute_paths([text]) == []
