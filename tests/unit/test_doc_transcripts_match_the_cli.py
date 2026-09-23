"""Transcripts the pages print, held to what the CLI prints for the same step.

A reader following the quickstarts diffs each printed block against their
terminal. `doctor` gained a first line naming its resource policy, `verify OK`
gained the ratchet tail those exact steps write, `rescore --gate` a closing
verdict line, and none of the pages followed. Each check here takes the line
from the code that prints it, then looks for it on the page.
"""
import json
import re
from pathlib import Path

import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.cli.verifying import _ratchet_suffix
from crapkit.config import Lane
from crapkit.errors import ToolError

ROOT = Path(__file__).resolve().parents[2]
MARKS = "crapkit-ratchet.tsv"


def _page(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _output_under(lines: list[str]) -> list[str]:
    """Lines up to the next prompt, blank line or fence."""
    out = []
    for line in lines:
        if not line.strip() or line.startswith(("$ ", "```")):
            break
        out.append(line)
    return out


def _blocks(text: str, command: str) -> list[list[str]]:
    """The output lines under each `$ <command>` line inside a fenced block."""
    lines = text.splitlines()
    return [_output_under(lines[i + 1:]) for i, line in enumerate(lines)
            if line.strip() == f"$ {command}"]


def _doctor_first_line(root: Path, capsys) -> str:
    main(["doctor", "--repo", str(root)])
    return capsys.readouterr().out.splitlines()[0]


FINDINGS = ("ok ", "WARN", "FAIL", "note")
DOCTOR_PAGES = ("README.md", "docs/lanes.md", "docs/configuration.md")


@pytest.mark.parametrize("page", DOCTOR_PAGES)
def test_each_doctor_report_opens_with_the_resources_line(page, repo, capsys):
    """Printed first since 0.8.0's resource policy; a report shown without it
    starts at a line the reader's terminal shows second."""
    shape = re.escape(_doctor_first_line(repo, capsys))
    shape = re.sub(r"\d+", r"\\d+", shape)
    reports = [b for b in _blocks(_page(page), "crapkit doctor")
               if any(line.startswith(FINDINGS) for line in b)]

    assert reports, f"{page} prints no doctor report"
    for report in reports:
        assert re.fullmatch(shape, report[0]), (page, report[:2])


def test_the_quickstarts_verify_ok_lines_carry_the_ratchet_tail_their_steps_write():
    """Step 6 (Python) and step 7 (TypeScript) repay the one seeded mark, so
    the tighten drops it and the OK line asks for the `git add`."""
    tail = _ratchet_suffix({"dropped": 1, "tightened": 0}, [], MARKS)
    ok_lines = [line for block in _blocks(_page("README.md"), "crapkit verify")
                for line in block if line.startswith("verify OK")]

    assert len(ok_lines) == 2, ok_lines
    assert all(line.endswith(f"changed files){tail}") for line in ok_lines), ok_lines


def test_the_restamp_transcript_carries_the_tail_the_restamp_prints():
    tail = _ratchet_suffix({"dropped": 0, "tightened": 0}, [], MARKS)
    text = _page("docs/ratchet.md")
    block = text[text.index("warning: crapkit-ratchet.tsv carries no metric stamp"):]
    ok = next(line for line in block.splitlines() if line.startswith("verify OK"))

    assert ok.endswith(tail), ok


def test_the_typescript_rescore_gate_block_ends_with_the_gate_line():
    """A passing `rescore --gate` prints one stdout line after the table, so
    the exit code is not the only signal; the page stopped at the table."""
    [block] = _blocks(_page("README.md"), "crapkit rescore src/grade.ts --gate")

    assert block[-1] == "gate: 4 changed function(s) judged, 0 over ceiling 6", block


def test_the_typescript_quickstart_says_why_the_first_doctor_warns():
    """init writes the vitest scoped_tests line commented, so the first doctor
    WARNs; the quickstart named no doctor step and no reason for the WARN."""
    text = " ".join(_page("README.md").split())
    quickstart = text[text.index("## Quickstart: TypeScript"):
                      text.index("### 2. Install a coverage provider")]

    assert "has a lane but no [crapkit.scoped_tests] template" in quickstart


def test_the_crapkit_directory_listing_names_the_measurement_lock():
    """lanes.py holds `.crapkit/measurement.lock` while lanes run and leaves it
    behind; the listing showed a tree without it."""
    text = _page("docs/lanes.md")
    names = _listing_names(text)
    table = text[text.index("| Path | What it holds | Key |"):]
    rows = [name.replace("lane-py.log", "lane-<name>.log") for name in names
            if name not in ("junit-py.xml", "py.json", "cov")]

    assert "measurement.lock" in names, names
    assert [row for row in rows if f"`{row}`" not in table] == [], "listed but never explained"


def _listing_names(text: str) -> list[str]:
    listing = text[text.index("$ ls .crapkit .crapkit/cov"):]
    listing = listing[:listing.index("```")]
    return [line for line in listing.splitlines()[2:] if line and not line.endswith(":")]


def test_the_lanes_page_quotes_the_refusal_an_fnmap_entry_without_decl_draws(tmp_path):
    from crapkit import coverage_istanbul

    source = str(tmp_path / "src" / "a.ts")
    artifact = tmp_path / "coverage-final.json"
    artifact.write_text(json.dumps({source: {
        "path": source, "fnMap": {"0": {"name": "f", "loc": {"start": {"line": 1},
                                                             "end": {"line": 2}}}},
        "f": {"0": 1}, "branchMap": {}, "b": {}, "statementMap": {}, "s": {}}}),
        encoding="utf-8")
    lane = Lane("js", "x", "coverage-final.json", "istanbul", ("src",))
    with pytest.raises(ToolError) as raised:
        coverage_istanbul.read(lane, tmp_path, artifact)
    printed = str(raised.value).replace(str(artifact),
                                        "/repo/.crapkit/cov/js/coverage-final.json")

    assert f"crapkit: lane 'js' FAILED: {printed}\n" in _page("docs/lanes.md"), printed
