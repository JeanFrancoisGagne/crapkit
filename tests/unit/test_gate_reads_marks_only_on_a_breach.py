"""`rescore --gate` reads the marks file only when a changed function breached.

A mark can only pardon a breach, so a gate with none has no question for the
marks file. Reading it anyway cost a clean check_gate several seconds on a large
consumer repo, where proving legacy ratchet keys scans every stored run. The
pre-commit hook already skips the read on a clean commit, and this is the same
rule.

The trade is the hook's too: a clean gate no longer reports a marks file it
cannot parse. The next gate that breaches still reads the file and refuses it.
"""
from cli_inproc_repo import add_knotty, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit.cli import main
from crapkit.mcp_server import tool_listing

HEADER = "path\tlong_name\tcrap\n"

# A key stamp from a future format: identity checking cannot compare it.
UNSUPPORTED_KEYS = "# crapkit-keys=99\n" + HEADER
# One hand-edited line with no mark on it: the lenient read skips it and says so.
SHORT_LINE = HEADER + "src/app.ts\tknotty ( n )\n"


@pytest.fixture()
def scored(repo, capsys):
    """A repo carrying one trusted coverage run, the way `rescore` needs it."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def gate(repo, capsys) -> tuple[int, str]:
    code = main(["rescore", "src/app.ts", "--gate", "--repo", str(repo)])
    return code, capsys.readouterr().err


def write_marks(repo, text: str) -> None:
    (repo / "crapkit-ratchet.tsv").write_text(text, encoding="utf-8", newline="\n")


@pytest.mark.parametrize("marks", [UNSUPPORTED_KEYS, SHORT_LINE], ids=["unsupported-keys", "short-line"])
def test_a_clean_gate_never_opens_the_marks_file(scored, capsys, marks):
    write_marks(scored, marks)

    assert gate(scored, capsys) == (0, "")


def test_a_breaching_gate_still_refuses_a_marks_file_it_cannot_compare(scored, capsys):
    write_marks(scored, UNSUPPORTED_KEYS)
    add_knotty(scored)

    code, err = gate(scored, capsys)

    assert code == 3
    assert "crapkit-ratchet.tsv" in err and "unsupported ratchet key identity version" in err, err


def test_a_breaching_gate_still_names_a_mark_line_it_skipped(scored, capsys):
    write_marks(scored, SHORT_LINE)
    add_knotty(scored)

    code, err = gate(scored, capsys)

    assert code == 6
    assert "skipped an unreadable mark in crapkit-ratchet.tsv" in err, err
    assert "knotty" in err, err


def test_check_gate_tells_an_agent_what_a_clean_gate_no_longer_reports():
    (entry,) = [t for t in tool_listing() if t["name"] == "check_gate"]

    assert "read only on a breach" in entry["description"], entry["description"]
    assert "marks file" in entry["description"], entry["description"]
