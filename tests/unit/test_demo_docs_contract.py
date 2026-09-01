"""The demo images are referenced by two pages and shipped in the repo.

A README that points at an image nobody committed renders a broken box on
GitHub and on PyPI, and a demo that grew past the size cap costs every reader
the download before they read a word. Both failures are invisible to the
generator, which is happy to write files nobody links.
"""
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(ROOT / "tools" / "demo"))

demo_render = pytest.importorskip("demo_render")

GIF = ROOT / "docs" / "demo.gif"
SVG = ROOT / "docs" / "demo.svg"
NOTE = "notes/pytest-cov-7-subprocess-coverage.html"


def _page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_the_readme_embeds_the_demo_under_its_badges():
    lines = _page("README.md").splitlines()
    embed = [i for i, line in enumerate(lines) if "docs/demo.gif" in line]

    assert embed, "the README lost its demo"
    assert embed[0] < 12, "the demo sits below the first screen"
    assert lines[embed[0]].startswith("!["), "the demo is a link, not an image"


def test_the_readme_alt_text_says_what_the_demo_shows():
    line = next(l for l in _page("README.md").splitlines() if "docs/demo.gif" in l)
    alt = line[2:line.index("](")]

    for word in ("worklist", "advisory", "gate", "exit 6"):
        assert word in alt, f"the alt text never mentions {word!r}"


def test_the_readme_fetches_the_demo_by_an_absolute_url():
    """PyPI renders the README as the project page and resolves no relative
    image path, so a `docs/demo.gif` reference shows a broken image there. The
    raw GitHub URL renders on GitHub and on PyPI alike; the handbook, which has
    to work from file:// on a clone, keeps its relative src instead."""
    line = next(l for l in _page("README.md").splitlines() if "docs/demo.gif" in l)
    target = line[line.index("](") + 2:line.rindex(")")]

    assert target.startswith("https://raw.githubusercontent.com/JeanFrancoisGagne/crapkit/"), target
    assert target.endswith("/docs/demo.gif"), target


def test_the_handbook_shows_the_demo_on_its_first_screen():
    page = _page("docs/handbook.html")
    head = page[:page.index('<h2 id="intro"')]

    assert 'src="demo.gif"' in head, "the demo is not on the handbook's first screen"


def test_the_handbook_links_the_subprocess_coverage_note_from_its_lanes_section():
    page = _page("docs/handbook.html")
    lanes = page[page.index('<h2 id="lanes"'):page.index('<h2 id="queue"')]

    assert f'href="{NOTE}"' in lanes
    assert "pytest-cov 7 and subprocess coverage</a>" in lanes


def test_both_demo_images_are_committed():
    assert GIF.is_file() and SVG.is_file()


def test_the_gif_stays_under_the_size_cap():
    assert GIF.stat().st_size <= demo_render.GIF_CEILING


def test_the_committed_svg_parses_as_xml():
    assert ElementTree.parse(SVG).getroot().tag.endswith("svg")


def test_the_regeneration_command_is_written_down():
    assert "python tools/demo/generate.py" in _page("tools/demo/README.md")
