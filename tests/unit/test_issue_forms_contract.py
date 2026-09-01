"""A version literal in an issue form is a promise the next release breaks.

`.github/ISSUE_TEMPLATE/*.yml` shows a reporter what a good answer looks like,
and a placeholder is the line they pattern-match against. One reading
`crapkit 0.4.0` teaches a number three releases old as the normal answer, and
it goes stale again at every release without failing anything. So the rule is
narrow and mechanical: a placeholder either names the version this tree ships,
or it names no version at all.

The forms are read as text rather than through a YAML parser: PyYAML is not a
test dependency, and the only thing this pin needs out of the file is the value
of every `placeholder` key.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from crapkit import __version__

ROOT = Path(__file__).resolve().parents[2]
FORMS_DIR = ROOT / ".github" / "ISSUE_TEMPLATE"
FORMS = sorted(FORMS_DIR.glob("*.yml"))

_PLACEHOLDER = re.compile(r"^([ \t]*)placeholder:[ \t]*(.*?)[ \t]*$")
_BLOCK_SCALAR = ("|", "|-", "|+", ">", ">-", ">+")
_VERSION = re.compile(r"\bcrapkit (\d+\.\d+\.\d+)\b")


def _block_body(lines: list[str], start: int, indent: int) -> str:
    """The body of a `placeholder: |` block: the lines indented past its key."""
    body = []
    for line in lines[start:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return "\n".join(body)


def placeholder_values(text: str) -> list[str]:
    """Every `placeholder` value in one form, inline and block scalars alike."""
    lines = text.splitlines()
    values = []
    for number, line in enumerate(lines, start=1):
        found = _PLACEHOLDER.match(line)
        if found is None:
            continue
        value = found.group(2)
        if value in _BLOCK_SCALAR:
            value = _block_body(lines, number, len(found.group(1)))
        values.append(value)
    return values


def test_the_forms_this_pin_reads_are_the_forms_on_disk():
    """An empty glob parametrizes zero cases and passes, which is the way this
    pin would go quiet if the directory ever moved."""
    assert [form.name for form in FORMS] == [
        "bug_report.yml",
        "config.yml",
        "feature_request.yml",
        "field_report.yml",
        "language_request.yml",
    ]


@pytest.mark.parametrize("form", FORMS, ids=lambda form: form.name)
def test_no_placeholder_names_a_version_other_than_the_one_this_tree_ships(form):
    named = {
        version
        for value in placeholder_values(form.read_text(encoding="utf-8"))
        for version in _VERSION.findall(value)
    }

    assert named <= {__version__}, (
        f"{form.name} shows the reporter {sorted(named)}, this tree ships "
        f"{__version__}: name the live version or none"
    )
