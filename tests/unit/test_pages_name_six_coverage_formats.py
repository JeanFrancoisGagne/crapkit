"""Every page says crapkit reads the formats `coverage_format._FORMATS` holds, never two.

crapkit read two coverage formats through 0.8.1, and the pages, the templates and
src's docstrings said so in a dozen phrasings. These checks read README.md,
AGENTS.md, CONTRIBUTING.md, the pages under docs/ (less docs/specs/ and
docs/architecture/), plugin/, .github/, the string constants under src/ and
CHANGELOG.md's unreleased section, and fail naming the file and phrase. The
count lines and the README intro follow the registry, so a seventh reader fails
here until they name it.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from crapkit import coverage_format

ROOT = Path(__file__).resolve().parents[2]
PAGE_SUFFIXES = {".md", ".html", ".yml", ".yaml", ".json", ".toml", ".txt"}
SKIPPED_DOCS = ("docs/specs/", "docs/architecture/")
# The claims 0.8.1's pages made about the two formats it read.
OLD_CLAIMS = ("two coverage parsers", "two coverage formats", "two parsers and no others",
              "only Python and JS/TS have coverage parsers", "crapkit reads none of them",
              "Nothing else exists", "no coverage parser at all", "neither parser reads",
              "the istanbul or coverage.py artifact")
# How the README intro names each reader's format: a key of _FORMATS to its words.
DISPLAY = {"coveragepy": "coverage.py JSON", "istanbul": "istanbul JSON", "lcov": "lcov",
           "cobertura": "Cobertura XML", "jacoco": "JaCoCo XML", "coverprofile": "Go coverprofile"}
NUMBERS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
           "ten", "eleven", "twelve")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _pages() -> list[str]:
    tops = ["README.md", "AGENTS.md", "CONTRIBUTING.md"]
    trees = [path for top in ("docs", "plugin", ".github") for path in (ROOT / top).rglob("*")
             if path.is_file() and path.suffix in PAGE_SUFFIXES]
    found = [path.relative_to(ROOT).as_posix() for path in trees]
    return tops + sorted(rel for rel in found if not rel.startswith(SKIPPED_DOCS))


def _unreleased_changelog() -> str:
    """CHANGELOG.md down to the first released heading: released notes keep their words."""
    text = _read("CHANGELOG.md")
    released = re.search(r"^## \d+\.\d+\.\d+ — \d{4}-", text, re.MULTILINE)
    return text[:released.start()] if released else text


def _src_strings() -> dict[str, str]:
    """Each src module's docstrings and messages, the string constants it holds."""
    found = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = [node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        found[path.relative_to(ROOT).as_posix()] = "\n".join(constants)
    return found


def _texts() -> dict[str, str]:
    return {**{rel: _read(rel) for rel in _pages()},
            "CHANGELOG.md (unreleased)": _unreleased_changelog(), **_src_strings()}


def _claims(texts: dict[str, str]) -> list[str]:
    flat = {place: re.sub(r"\s+", " ", text).lower() for place, text in texts.items()}
    return [f"{place}: {claim!r}" for place, text in flat.items() for claim in OLD_CLAIMS
            if claim.lower() in text]


def test_no_page_template_or_message_says_crapkit_reads_two_formats():
    assert _claims(_texts()) == []


@pytest.mark.parametrize("claim", OLD_CLAIMS)
def test_each_old_claim_is_caught_across_a_line_break_in_any_case(claim):
    words = claim.upper().split(" ")
    planted = " ".join(words[:1]) + "\n" + " ".join(words[1:])
    assert _claims({"README.md": f"crapkit {planted}."}) == [f"README.md: {claim!r}"]


def _count_words() -> str:
    return f"{NUMBERS[len(coverage_format._FORMATS)]} coverage formats"


def _paragraph(text: str, start: str) -> str:
    """The paragraph that opens with `start`, its lines joined."""
    found = re.search(rf"^{re.escape(start)}.*?(?=\n\s*\n)", text, re.MULTILINE | re.DOTALL)
    assert found, f"no paragraph opens with {start!r}"
    return re.sub(r"\s+", " ", found.group(0))


COUNT_LINES = [("README.md", "14 languages,"), ("docs/configuration.md", "Fourteen languages,"),
               ("docs/handbook.html", "<p>Fourteen languages,")]


@pytest.mark.parametrize(("rel", "start"), COUNT_LINES, ids=[rel for rel, _ in COUNT_LINES])
def test_the_count_lines_name_the_registry_count(rel, start):
    assert _count_words() in _paragraph(_read(rel), start).lower()


def test_the_readme_intro_names_each_format_the_registry_reads():
    intro = _paragraph(_read("README.md"), "crapkit scores every function")
    assert set(DISPLAY) == set(coverage_format._FORMATS), "DISPLAY names each _FORMATS key"
    assert [key for key, words in DISPLAY.items() if words not in intro] == []


def test_a_seventh_reader_fails_the_count_and_the_intro(monkeypatch):
    monkeypatch.setitem(coverage_format._FORMATS, "no-such-format", object())

    with pytest.raises(AssertionError):
        test_the_count_lines_name_the_registry_count("README.md", "14 languages,")
    with pytest.raises(AssertionError, match="DISPLAY names each _FORMATS key"):
        test_the_readme_intro_names_each_format_the_registry_reads()
