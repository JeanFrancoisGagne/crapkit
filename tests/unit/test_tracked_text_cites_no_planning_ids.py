"""No tracked text cites an id from a planning list or a bug hunt this repository does not hold.

A comment that says "judged by claim (Q17)" or "the same shape-5 row" points
the reader at a ruling list or a hunt's bug folder kept outside this
repository, so the reader learns nothing about the rule the code follows. The
text states the rule instead. The ids come in two shapes: a question or
section number (Q17, S10, U29, PT1, PC8, PL7, arch-13, signal-1, and the
planning document's own initials) and a hunt's loop number (shape-5, boundary-10,
history-3, c26).

The id is matched only where it stands alone: `-U0`, `CORE-S01`, a base64
digest and an `<h2>` tag hold the same letters and are no citation.
"""
import re
from pathlib import Path

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
PLANNING_ID = re.compile(
    r"(?<![\w/+=.\\-])(?:Q\d{1,3}|arch-\d{1,2}|P[CLT]\d{1,2}|S\d{1,2}|U\d{1,2}|c\d{2}|signal-\d{1,2}|PRD)"
    r"(?![\w+/=-])"
    r"|\b(?:shape|boundary|history)-\d{1,2}\b")


def _tracked() -> list[str]:
    listed = hang_guard.run(["git", "ls-files", "-z"], cwd=ROOT)
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    return [path.decode("utf-8") for path in listed.stdout.split(b"\0") if path]


def _citations(path: str) -> list[str]:
    data = (ROOT / path).read_bytes()
    if b"\0" in data:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return [f"{path}:{number}: {match.group()}" for number, line in enumerate(lines, 1)
            for match in PLANNING_ID.finditer(line)]


def test_the_pattern_finds_each_id_shape_and_passes_the_look_alikes():
    cited = "(Q17) S10, U29: PT1 PC8 (PL7) arch-13 signal-1 PRD a-shape-5 -boundary-10 history-3 c26"
    alike = "git diff -U0 CORE-S01 sha512-B5l+U7/Zs= <h2> S3Q12 c264 history-3x PS1"

    assert [match.group() for match in PLANNING_ID.finditer(cited)] == [
        "Q17", "S10", "U29", "PT1", "PC8", "PL7", "arch-13", "signal-1", "PRD", "shape-5",
        "boundary-10", "history-3", "c26"]
    assert [match.group() for match in PLANNING_ID.finditer(alike)] == []


def test_no_tracked_text_cites_a_planning_id():
    paths = [path for path in _tracked() if path != SELF]
    found = [citation for path in paths for citation in _citations(path)]

    assert paths, "git ls-files listed nothing, so the scan checked nothing"
    assert found == [], f"{len(found)} citation(s) of an id kept outside the repository; " \
                        f"state the rule it names instead: {found[:10]}"
