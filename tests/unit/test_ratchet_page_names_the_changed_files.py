"""docs/ratchet.md prints verify's verdict the way verify prints it.

Since 0.8.1 a verdict with a non-empty diff is followed by a `changed files:`
line naming the files behind the count, and an untracked source file inside a
scope draws a stderr warning. The page's transcripts carry both, taken from
the code that prints them.
"""
import re
from pathlib import Path

from crapkit.cli import verifying

ROOT = Path(__file__).resolve().parents[2]
VERDICT = re.compile(r"^verify (OK|FAILED) @ .*\((\d+) changed files\)")


def _page() -> list[str]:
    return (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8").splitlines()


def _verdicts() -> list[tuple[int, str]]:
    """(count, the line under it) for each verdict line on the page."""
    lines = _page()
    return [(int(m.group(2)), lines[i + 1]) for i, line in enumerate(lines)
            if (m := VERDICT.match(line))]


def test_every_verdict_with_a_diff_names_its_files_on_the_next_line():
    verdicts = [(n, under) for n, under in _verdicts() if n]

    assert verdicts, "the page quotes no verdict with a changed file"
    for count, under in verdicts:
        assert under.startswith("  changed files: "), (count, under)
        names = under.removeprefix("  changed files: ").split(", ")
        assert len(names) == min(count, 3), (count, under)


def test_an_empty_diff_prints_no_changed_files_line():
    assert all(not under.startswith("  changed files:") for n, under in _verdicts() if not n)


def test_the_page_quotes_the_untracked_warning_verify_prints(capsys):
    verifying._warn_untracked_in_scope(["src/added.ts"])
    printed = capsys.readouterr().err.strip()

    assert printed in _page(), printed


def test_the_page_names_every_reader_the_same_size_limit_reaches():
    """tests/e2e/test_git_view_readers_e2e.py pins the limit at each of these
    readers; the page names the same list."""
    text = " ".join(" ".join(_page()).split())
    start = text.index("a same-size edit whose old modification time was put back")
    sentence = text[start:text.index(".", start)]

    for reader in ("committed/dirty split", "`rescore --gate`", "re-stage note", "`mutate`"):
        assert reader in sentence, reader
