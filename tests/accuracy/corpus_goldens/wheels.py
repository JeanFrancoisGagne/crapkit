"""This checkout's crapkit package zipped as a wheel, with an optional plant.

A plant is (file name, text, replacement): the one change a test wants the
candidate side to carry. zipped() refuses a plant whose text the file does not
hold, so a source edit cannot turn a planted test into a no-op.
"""
from __future__ import annotations

from pathlib import Path
import zipfile

SOURCE = Path(__file__).resolve().parents[3] / "src" / "crapkit"
CRAP_LINE = "    return ccn * ccn * (uncovered * uncovered * uncovered) + ccn\n"
COGNITIVE_LINE = '        cognitive=getattr(fn, "cognitive_complexity", 0) or 0,\n'
# Cognitive complexity gains 1 at ccn 3, and nowhere else. The runtime guards
# pin CRAP at coverage 0 and 1 and bound it in between, so a planted CRAP move
# on these trees stops the run with exit 5 before any export; cognitive has
# only a floor of 0, so this wrong number reaches the exports and only a
# diff can catch it.
COGNITIVE_PLANT = ("analyze.py", COGNITIVE_LINE,
                   '        cognitive=(getattr(fn, "cognitive_complexity", 0) or 0)'
                   " + (1 if std == 3 else 0),\n")
# Every CRAP computation stops the process with exit 5, as an internal-check stop does.
STOP_PLANT = ("score.py", CRAP_LINE, "    raise SystemExit(5)\n")
PLANTS = {"cognitive": COGNITIVE_PLANT, "stop": STOP_PLANT}


def _text(path: Path, plant: tuple | None) -> str:
    text = path.read_bytes().decode("utf-8")
    if plant is None or path.name != plant[0]:
        return text
    assert plant[1] in text, f"{plant[0]} no longer holds the planted line {plant[1]!r}"
    return text.replace(plant[1], plant[2])


def zipped(dest: Path, plant: tuple | None = None) -> Path:
    with zipfile.ZipFile(dest, "w") as archive:
        for path in sorted(SOURCE.rglob("*.py")):
            archive.writestr("crapkit/" + path.relative_to(SOURCE).as_posix(), _text(path, plant))
    return dest
