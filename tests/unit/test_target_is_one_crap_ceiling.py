"""`target` is one CRAP ceiling on every surface that names it.

A staged blob has no coverage, so the commit hook knows only the low end of a
function's CRAP, which is its ccn, and refuses when that low end passes the
ceiling. These tests hold the words to that one rule: no page, docstring or
field calls `target` a complexity or ccn ceiling, and the hook's refusal says
why ccn decides.
"""
from __future__ import annotations

import re
from pathlib import Path

from cli_inproc_repo import git, repo, template_repo  # noqa: F401
from crapkit.agent_fields import PAYLOADS
from crapkit.cli import main

ROOT = Path(__file__).resolve().parent.parent.parent

# The surfaces the wording check reads. It leaves out, each for its reason:
# - CHANGELOG.md: its past sections quote what each release printed.
# - docs/releases/ and docs/architecture/: dated records of past releases and designs.
# - docs/demo.svg: a recording that tools/demo/demo_run.py regenerates.
# - the retro TSVs under tests/accuracy/: failures quoted as they were printed.
# - README.md's opening section, up to its first `##` heading: the first-run
#   text there is rewritten on its own, and that rewrite applies this rule.
OLD_NAMES = re.compile(r"\b(?:complexity|ccn)(?:\s|#)+ceilings?\b", re.IGNORECASE)

HEAD = ("crapkit gate: 1 staged function(s) exceed the CRAP ceiling of 6 "
        "(with no coverage known, CRAP is at least ccn):")

NINE = """
export function nine(n: number): number {
  if (n > 1) { return 1; }
  if (n > 2) { return 2; }
  if (n > 3) { return 3; }
  if (n > 4) { return 4; }
  if (n > 5) { return 5; }
  if (n > 6) { return 6; }
  if (n > 7) { return 7; }
  if (n > 8) { return 8; }
  return 0;
}
"""

CHANGELOG_LINE = ("The commit hook and `rescore --gate` call `target` the CRAP ceiling, as verify "
                  "does, and say that with no coverage known a function's CRAP is at least its ccn.")


def _surfaces() -> list[Path]:
    return [*sorted((ROOT / "src" / "crapkit").rglob("*.py")), *sorted((ROOT / "docs").glob("*.md")),
            ROOT / "docs" / "handbook.html", *sorted((ROOT / "plugin" / "skills").glob("*/SKILL.md")),
            ROOT / "README.md", ROOT / "AGENTS.md"]


def _checked(path: Path) -> tuple[str, int]:
    """The text the check reads and the line number it starts on: README.md
    from its first `##` heading on, every other surface whole."""
    text = path.read_text(encoding="utf-8")
    if path != ROOT / "README.md":
        return text, 1
    start = re.search(r"^## ", text, re.MULTILINE).start()
    return text[start:], text.count("\n", 0, start) + 1


def _old_names(path: Path) -> list[str]:
    text, first = _checked(path)
    rel = path.relative_to(ROOT).as_posix()
    return [f"{rel}:{first + text.count(chr(10), 0, m.start())}: {m.group(0)!r}"
            for m in OLD_NAMES.finditer(text)]


def test_no_surface_calls_target_a_complexity_or_ccn_ceiling():
    assert [hit for path in _surfaces() for hit in _old_names(path)] == []


def test_the_wording_check_reads_each_kind_of_surface():
    """A glob that matched nothing would pass the check above by reading nothing."""
    rels = {path.relative_to(ROOT).as_posix() for path in _surfaces()}

    assert {"src/crapkit/hook.py", "src/crapkit/cli/verifying.py", "docs/configuration.md",
            "docs/handbook.html", "plugin/skills/crapkit/SKILL.md", "README.md", "AGENTS.md"} <= rels


def test_the_readme_check_starts_at_its_first_section_heading():
    text, first = _checked(ROOT / "README.md")

    assert text.startswith("## ")
    assert first > 1


def test_the_hook_refusal_names_the_crap_ceiling_and_why_ccn_decides(repo, capsys):  # noqa: F811
    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(NINE)
    git(repo, "add", "-A")

    code = main(["hook-precommit", "--repo", str(repo)])
    out = capsys.readouterr().out.splitlines()

    assert code == 6, out
    assert out[:2] == [HEAD, "  ccn   9  src/app.ts:20  nine ( n )"], out


def test_the_rescore_gate_ceilings_field_names_the_crap_ceiling():
    description = PAYLOADS["rescore --gate --json"]["gate"]["properties"]["ceilings"]["description"]

    assert "CRAP ceiling" in description, description


def _lines(page: str) -> list[str]:
    return (ROOT / page).read_text(encoding="utf-8").splitlines()


def test_the_configuration_page_says_once_what_target_caps():
    lines = _lines("docs/configuration.md")
    dial = [line for line in lines if line.startswith("| the CRAP ceiling | `[crapkit] target` |")]
    rows = [line for line in lines if line.startswith("| `target` | int >= 1 |")]
    why = "because coverage can at best bring CRAP down to ccn"

    assert len(dial) == 1 and len(rows) == 2, (dial, rows)
    assert all("CRAP ceiling" in row for row in [*dial, *rows]), (dial, rows)
    assert "the highest CRAP a function may score" in dial[0]
    assert ("The repo-wide CRAP ceiling: the highest CRAP a function may score. At 6 it also caps "
            f"ccn at 6, {why}; the commit hook, which has no coverage, refuses a function whose "
            "ccn alone exceeds it.") in rows[0]
    assert sum(why in line for line in lines) == 1


def test_the_handbook_dial_says_the_crap_ceiling_also_caps_ccn():
    dial = [line for line in _lines("docs/handbook.html") if line.startswith("target = 6 ")]

    assert len(dial) == 1, dial
    assert "the CRAP ceiling, which also caps ccn" in dial[0], dial


def test_the_changelog_says_it_inside_the_slot_block():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    block = text.split("<!-- 0.9.0:mission-4 -->", 1)[1].split("<!-- /0.9.0:mission-4 -->", 1)[0]

    assert [line for line in block.splitlines() if line.startswith(f"- {CHANGELOG_LINE} [")], block
