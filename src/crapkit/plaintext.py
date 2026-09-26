"""Plain text out of what another program printed.

A lane runs with crapkit's environment, so FORCE_COLOR and PY_COLORS colour
what pytest writes to the lane log and the junit report, and on Python 3.14
PYTHON_COLORS colours an argparse usage error too. crapkit reads that text as
data: a regex anchored at the line start hoists the cause, a substring names a
missing plugin, and a refusal quotes the tail to stderr, `--json` and the
pull-request comment. The override refusal quotes what the alert command
printed the same way. Each of those readers takes the text from here. The file
on disk keeps its colour for whoever tails it.
"""
from __future__ import annotations

import re


def _escapes(esc: str, bel: str) -> re.Pattern[str]:
    """ESC and what follows it in an ECMA-48 sequence: a CSI (`[`, parameter
    bytes, intermediate bytes, one final byte), an OSC (`]` up to BEL or ST, or
    to the end of the line when the child cut it off), or a two-byte pair such
    as `ESC M`, with the intermediates a charset selection like `ESC ( B` puts
    before its final. An ESC that none of these follows goes on its own, so no
    ESC survives whatever the child wrote after it."""
    osc = rf"\](?:(?!{bel}|{esc}).)*(?:{bel}|{esc}\\)?"
    return re.compile(rf"{esc}(?:\[[0-?]*[ -/]*[@-~]|{osc}|[ -/]*[0-~])?")


_ESCAPES = _escapes("\x1b", "\x07")
# pytest's junitxml spells a character XML 1.0 cannot hold as `#x` and its hex
# code, so a coloured traceback reaches the report as `#x1B[31m`.
_JUNIT_ESCAPES = _escapes("#x1B", "#x07")


def strip_escapes(text: str) -> str:
    """`text` with every escape sequence removed and nothing else changed."""
    return _ESCAPES.sub("", text)


def strip_junit_escapes(text: str) -> str:
    """`text` read out of a junit report, with the escape sequences pytest
    wrote there as `#x1B` text removed."""
    return _JUNIT_ESCAPES.sub("", text)


def printed_text(data: bytes) -> str:
    """What a child wrote to a pipe crapkit read as bytes, as a message quotes
    it: UTF-8 with each byte that is not UTF-8 as U+FFFD, a CR LF or a lone CR
    read as LF the way a text-mode pipe reads them, and no escape sequence. A
    child on Windows ends each line in CR LF, and a quote that kept the CR put
    one inside the refusal a reader sees."""
    text = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return strip_escapes(text)
