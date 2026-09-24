"""Install lines, read verbatim from the docs a user reads.

A cell never retypes a command. It asks for the fence under a heading of a
page in the stamped candidate tree (candidate.py's staged/, where README and
the docs already name the candidate version) and runs what that fence says.
When a fence moves or its heading is renamed, the lookup fails naming the page
and the heading, which is the doc edit that broke the cell.

    block = docsnip.fence("README.md", "The 60-second start")
    for line in docsnip.commands(block): ...

Markdown fences (``` and ~~~) and indented blocks are read from .md pages,
<pre> blocks from .html pages. A heading is the nearest one above the fence;
`heading` matches its text exactly, or `contains` narrows by the fence's text.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

FENCE = re.compile(r"^(?P<indent> {0,3})(?P<mark>`{3,}|~{3,})(?P<info>.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


class DocSnipError(AssertionError):
    """A fence the cell expects is not where the cell expects it."""


@dataclass(frozen=True)
class Fence:
    page: str
    heading: str
    lang: str
    text: str
    line: int


def root() -> Path:
    """The stamped tree: CRAPKIT_DEPLOY_CANDIDATE/staged."""
    return Path(os.environ["CRAPKIT_DEPLOY_CANDIDATE"]) / "staged"


# --- markdown -------------------------------------------------------------------

INDENT = "    "


class _Markdown:
    """One pass over a page: fenced blocks, indented blocks (after a blank
    line, four spaces in) and the heading each sits under."""

    def __init__(self, page: str):
        self.page, self.heading, self.fences = page, "", []
        self.open: tuple[str, str, int, list[str]] | None = None
        self.indented: tuple[int, list[str]] | None = None
        self.blank = True

    def feed(self, number: int, line: str) -> None:
        if self.open:
            self._inside(line)
        elif self._indented(number, line):
            pass
        else:
            self._outside(number, line)
        self.blank = not line.strip()

    def _outside(self, number: int, line: str) -> None:
        match = FENCE.match(line)
        if match:
            self.open = (match["mark"], match["info"].strip().split(" ")[0], number, [])
        elif HEADING.match(line):
            self.heading = HEADING.match(line)[2].strip()

    def _indented(self, number: int, line: str) -> bool:
        """Consume a line of an indented block; False hands it back."""
        if self._code_line(line):
            self._extend(number, line[len(INDENT):])
            return True
        if self.indented and not line.strip():
            self.indented[1].append("")
            return True
        self.close_indented()
        return False

    def _code_line(self, line: str) -> bool:
        """Four spaces in, after a blank line or inside a block already open."""
        return line.startswith(INDENT) and bool(self.indented or self.blank)

    def _extend(self, number: int, text: str) -> None:
        self.indented = self.indented or (number, [])
        self.indented[1].append(text)

    def close_indented(self) -> None:
        if self.indented:
            number, body = self.indented
            self.fences.append(Fence(self.page, self.heading, "indented", "\n".join(body).strip("\n"), number))
            self.indented = None

    def _inside(self, line: str) -> None:
        mark, lang, number, body = self.open
        if line.strip().startswith(mark[0] * len(mark)) and not line.strip().strip(mark[0]):
            self.fences.append(Fence(self.page, self.heading, lang, "\n".join(body), number))
            self.open = None
        else:
            body.append(line)


def markdown_fences(page: str, text: str) -> list[Fence]:
    reader = _Markdown(page)
    for number, line in enumerate(text.splitlines(), 1):
        reader.feed(number, line)
    reader.close_indented()
    return sorted(reader.fences, key=lambda block: block.line)


# --- html -----------------------------------------------------------------------

class _Html(HTMLParser):
    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.page, self.heading, self.fences = page, "", []
        self.capture: list[str] | None = None
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.in_heading, self.heading = True, ""
        elif tag == "pre":
            self.capture = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4"):
            self.in_heading = False
        elif tag == "pre" and self.capture is not None:
            text = html.unescape("".join(self.capture)).strip("\n")
            self.fences.append(Fence(self.page, self.heading, "", text, self.getpos()[0]))
            self.capture = None

    def handle_data(self, data):
        if self.capture is not None:
            self.capture.append(data)
        elif self.in_heading:
            self.heading = " ".join(f"{self.heading} {data}".split())


def html_fences(page: str, text: str) -> list[Fence]:
    reader = _Html(page)
    reader.feed(text)
    return reader.fences


# --- lookups --------------------------------------------------------------------

def fences(page: str, base: Path | None = None) -> list[Fence]:
    path = (base or root()) / page
    if not path.exists():
        raise DocSnipError(f"{page}: no such page in the stamped tree {base or root()}")
    text = path.read_text(encoding="utf-8")
    return html_fences(page, text) if page.endswith(".html") else markdown_fences(page, text)


def fence(page: str, heading: str, *, index: int = 0, contains: str | None = None,
          base: Path | None = None) -> Fence:
    """The index-th fence under `heading` whose text holds `contains`."""
    under = [block for block in fences(page, base) if block.heading == heading]
    matching = _holding(under, contains)
    if index >= len(matching):
        raise _missing(page, heading, index, contains, under)
    return matching[index]


def _holding(blocks: list[Fence], contains: str | None) -> list[Fence]:
    return [block for block in blocks if contains is None or contains in block.text]


def _missing(page: str, heading: str, index: int, contains: str | None, under: list[Fence]) -> DocSnipError:
    found = f"{len(under)} fence(s) under it" if under else "no such heading"
    wanted = f" holding {contains!r}" if contains else ""
    return DocSnipError(f"{page} > {heading}: no fence #{index}{wanted} ({found})")


# --- reading a fence ---------------------------------------------------------------

POWERSHELL = ("powershell", "ps1", "pwsh")


def _joined(lines: list[str], mark: str) -> list[str]:
    """Continuation lines (ending in `mark`: a backslash, or PowerShell's
    backtick) folded into one line each."""
    joined, pending = [], ""
    for line in lines:
        pending += line
        if pending.endswith(mark):
            pending = pending[:-1] + " "
            continue
        joined.append(pending)
        pending = ""
    return joined + ([pending] if pending else [])


def _uncommented(line: str) -> str:
    """The command with a trailing `  # comment` dropped."""
    return re.split(r"\s{2,}#\s", line, maxsplit=1)[0].rstrip()


def commands(block: Fence) -> list[str]:
    """What a user types: the `$ ` lines of a transcript fence, or every
    non-comment line of a command fence."""
    lines = block.text.splitlines()
    mark = "`" if block.lang in POWERSHELL else "\\"
    return _joined(_prompted(lines) or _typed(lines), mark)


def _prompted(lines: list[str]) -> list[str]:
    return [line[2:] for line in lines if line.startswith("$ ")]


def _typed(lines: list[str]) -> list[str]:
    return [_uncommented(line) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def outputs(block: Fence) -> list[tuple[str, str]]:
    """A transcript fence as (command, the output printed under it) pairs."""
    pairs: list[tuple[str, list[str]]] = []
    for line in block.text.splitlines():
        if line.startswith("$ "):
            pairs.append((line[2:], []))
        elif pairs:
            pairs[-1][1].append(line)
    return [(command, "\n".join(body).strip("\n")) for command, body in pairs]
