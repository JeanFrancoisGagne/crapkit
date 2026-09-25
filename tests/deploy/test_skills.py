"""The plugin's skills, followed as an agent or a user would follow them.

    lin-onboard-skill   every fence of crapkit-onboard/SKILL.md in page order: the install lines run,
                        each printed line is reproduced and compared, the Bash entry parses
    lin-recover-skill   each row of crapkit-recover's exit-code table: the refusal is produced, the
                        row's first command runs, the fix is applied, the refused command succeeds
    lin-skills-copy     plugin/skills/* copied where the docs say, parsed by Claude Code and Codex;
                        crapkit-onboard stays out of the model's reach in both
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from kit import docsnip, repos
from kit.cells import cell
from test_claude_hook_stub import bash_entry
from test_claude_plugin import BREACH, cli_venv, crapkit, github, harness_on_path, measured_repo

PACKET = "deploy-plugins"
ONBOARD = "plugin/skills/crapkit-onboard/SKILL.md"
RECOVER = "plugin/skills/crapkit-recover/SKILL.md"


def commit(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


# --- crapkit-onboard, fence by fence ---------------------------------------------------

def normalized(text: str, names: dict[str, str]) -> list[str]:
    """Lines with each placeholder filled and every path separator a slash."""
    for placeholder, value in names.items():
        text = text.replace(placeholder, value)
    return [line.rstrip().replace("\\", "/") for line in text.strip().splitlines()]


def printed(box, repo: Path, block: docsnip.Fence, names: dict) -> list[tuple[list[str], list[str]]]:
    """A transcript fence: each `$` command run, its output beside the page's."""
    pairs = []
    for command, output in docsnip.outputs(block):
        step = box.script(command, cwd=repo)
        pairs.append((normalized(step.stdout + step.stderr, names), normalized(output, names)))
    return pairs


def no_install_line(box, repo: Path) -> tuple[str, dict]:
    """doctor --plugin-root with no path, where Claude Code holds no plugin."""
    empty = box.root / "fresh-claude-config"
    empty.mkdir(exist_ok=True)
    step = box.run(["crapkit", "doctor", "--plugin-root"], cwd=repo, env={"CLAUDE_CONFIG_DIR": str(empty)}, expect=1)
    return step.stdout, {"DIR": str(empty / "plugins")}


def no_manifest_line(box, repo: Path) -> tuple[str, dict]:
    """doctor --plugin-root on a path that holds no plugin."""
    empty = box.root / "not-a-plugin"
    empty.mkdir(exist_ok=True)
    return crapkit(box, repo, "doctor", "--plugin-root", str(empty), expect=1).stdout, {"PATH": str(empty)}


# The page's lines that are output with no command above them, and how the cell
# produces the condition each one describes.
CONDITIONS: dict[str, Callable] = {"no installed crapkit plugin under": no_install_line,
                                   "has no .claude-plugin/plugin.json": no_manifest_line}


def reproduced(box, repo: Path, block: docsnip.Fence, names: dict) -> list[tuple[list[str], list[str]]]:
    """An output-only fence, compared with what the condition it names prints."""
    produce = next((make for key, make in CONDITIONS.items() if key in block.text), None)
    assert produce, f"{block.page}:{block.line}: the cell does not know what prints {block.text!r}"
    out, placeholders = produce(box, repo)
    return [(normalized(out, placeholders), normalized(block.text, placeholders))]


def fragment(block: docsnip.Fence) -> list[tuple[list[str], list[str]]]:
    """The settings fragment, parsed, beside the README's Bash entry."""
    entries = json.loads("{" + block.text + "}")["PostToolUse"]
    return [([json.dumps(entries)], [json.dumps([bash_entry()])])]


def ran(box, repo: Path, block: docsnip.Fence) -> list[tuple[list[str], list[str]]]:
    """A command fence: each line's exit code beside the 0 the page expects."""
    steps = [box.script(line, cwd=repo) for line in docsnip.commands(block)]
    return [([str(step.exit)], ["0"]) for step in steps]


def follow(box, repo: Path, block: docsnip.Fence, names: dict) -> list[tuple[list[str], list[str]]]:
    """One fence as the page means it: run, compare, or parse."""
    if block.text.lstrip().startswith('"PostToolUse"'):
        return fragment(block)
    if docsnip.outputs(block):
        return printed(box, repo, block, names)
    if block.text.startswith("crapkit "):
        return reproduced(box, repo, block, names)
    return ran(box, repo, block)


def differing(followed: dict[str, list]) -> dict[str, list]:
    """Per fence, the (got, page) pairs that disagree."""
    return {where: [pair for pair in pairs if pair[0] != pair[1]] for where, pairs in followed.items()}


def unignored_py_pytest(box, templates) -> Path:
    """py-pytest with a .gitignore that does not list __pycache__/, as a new repo's may not."""
    repo = repos.checkout(box, "py-pytest", cache=templates)
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    commit(box, repo, "a plain .gitignore")
    return repo


@cell("lin-onboard-skill", channel="plugin skill", harness="none (fences in order)",
      scenario="fresh: each fence of crapkit-onboard/SKILL.md in page order, the CLI installed first as it says",
      use_cases="skills, onboarding", os="linux", image="core", cadence="push")
def test_onboard_skill_fences_in_page_order(box, candidate, templates):
    cli_venv(box)
    repo = unignored_py_pytest(box, templates)
    github(box, candidate)
    harness_on_path(box)
    names = {"<home>": str(box.home), "<version>": candidate.version}
    blocks = docsnip.fences(ONBOARD)
    followed = {f"{ONBOARD}:{block.line}": follow(box, repo, block, names) for block in blocks}
    box.transcript.attach("fences", followed)

    assert len(blocks) == 6
    assert differing(followed) == dict.fromkeys(followed, [])


# --- crapkit-recover, row by row ----------------------------------------------------------

def first_command(code: int, names: dict[str, str]) -> str:
    """The table's first command for exit `code`, its placeholders filled."""
    text = (docsnip.root() / RECOVER).read_text(encoding="utf-8")
    command = re.search(rf"^\| {code} \|.*\| `([^`]+)` \|$", text, re.MULTILINE)[1]
    for placeholder, value in names.items():
        command = command.replace(placeholder, value)
    return command


def adopted(box, templates) -> Path:
    """py-pytest adopted, seeded, committed, and verified once: the baseline."""
    cli_venv(box)
    repo = measured_repo(box, templates)
    crapkit(box, repo, "ratchet", "seed")
    commit(box, repo, "adopt crapkit")
    crapkit(box, repo, "verify")
    return repo


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def replace(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def restore(box, repo: Path) -> None:
    box.run(["git", "checkout", "-q", "--", "."], cwd=repo, expect=0)


def amend(box, repo: Path) -> None:
    box.run(["git", "commit", "-q", "--amend", "-m", "adopt crapkit, reworded"], cwd=repo, env=box.commit_env(),
            expect=0)


def rename(repo: Path, old: str, new: str) -> None:
    (repo / old).rename(repo / new)


def uncovered_function(box, repo: Path) -> None:
    replace(repo / "crapkit.toml", "[crapkit]\n", "[crapkit]\ndiff_uncovered_max = 0\n")
    append(repo / "calc" / "grade.py", "\n\ndef bump(value):\n    total = value + 1\n    return total\n")


def cover_it(box, repo: Path) -> None:
    append(repo / "tests" / "test_grade.py", "\n\ndef test_bump():\n    from calc.grade import bump\n\n"
           "    assert bump(1) == 2\n")


DROP_GRADE_TEST = ('def test_an_early_high_score_is_an_a():\n    assert grade(95, 1, False, False) == "A"\n',
                   "")
BREAK_CURVE_TEST = ("assert curve([40, 90], 50) == [50, 90]", "assert curve([40, 90], 50) == [40, 90]")


@dataclass(frozen=True)
class Row:
    """One refusal of the table: how to cause it, the command that refuses, the
    placeholders of the row's first command, and the fix."""
    code: int
    command: tuple[str, ...]
    breaks: Callable
    fixes: Callable
    names: dict = field(default_factory=dict)
    says: str = ""


ROWS = {
    "3-unparseable-config": Row(3, ("verify",), lambda box, repo: append(repo / "crapkit.toml", "\n[[lane]\n"),
                                restore),
    "4-rewritten-baseline": Row(4, ("verify",), amend, lambda box, repo: crapkit(box, repo, "coverage"),
                                says="is not an ancestor of HEAD"),
    "4-not-a-repository": Row(4, ("verify",), lambda box, repo: rename(repo, ".git", ".git-away"),
                              lambda box, repo: rename(repo, ".git-away", ".git"), says="not a git repository"),
    "5-no-coverage-plugin": Row(5, ("coverage",),
                                lambda box, repo: box.run(["python", "-m", "pip", "uninstall", "-q", "-y", "pytest-cov"],
                                                          expect=0),
                                lambda box, repo: box.run(["python", "-m", "pip", "install", "-q", "pytest-cov"],
                                                          expect=0),
                                names={"NAME": "py"}, says="unrecognized arguments: --cov"),
    "6-gate": Row(6, ("rescore", "calc/big.py", "--gate"),
                  lambda box, repo: (repo / "calc" / "big.py").write_text(BREACH, encoding="utf-8"),
                  lambda box, repo: (repo / "calc" / "big.py").write_text("def route(kind):\n    return kind\n",
                                                                           encoding="utf-8"),
                  names={"FILE": "calc/big.py"}),
    "7-ratchet": Row(7, ("verify",), lambda box, repo: replace(repo / "tests" / "test_grade.py", *DROP_GRADE_TEST),
                     restore, names={"PATH": "calc/grade.py", "NAME": "grade"}),
    "8-new-test-failure": Row(8, ("verify",),
                              lambda box, repo: replace(repo / "tests" / "test_grade.py", *BREAK_CURVE_TEST),
                              restore, names={"FILE": "calc/grade.py"}),
    "9-uncovered-lines": Row(9, ("verify",), uncovered_function, cover_it),
}
NOT_A_REPO = pytest.mark.xfail(strict=True, reason="deploy-bug deploy-plugins-8: `crapkit verify` in a directory with "
                               "a crapkit.toml and no .git exits 4 printing git's `diff --no-index` usage, about 100 "
                               "lines, instead of naming the missing repository")
ROW_PARAMS = [pytest.param(name, marks=NOT_A_REPO if name == "4-not-a-repository" else ()) for name in ROWS]


@cell("lin-recover-skill", channel="plugin skill", harness="none",
      scenario="each refusal in the recover table, its first command, its fix, the refused command succeeds",
      use_cases="recovery", os="linux", image="core", cadence="nightly")
@pytest.mark.parametrize("name", ROW_PARAMS)
def test_recover_table_row(box, templates, name):
    row = ROWS[name]
    repo = adopted(box, templates)
    row.breaks(box, repo)
    refused = crapkit(box, repo, *row.command, expect=None)
    first = box.script(first_command(row.code, row.names), cwd=repo)
    row.fixes(box, repo)
    retried = crapkit(box, repo, *row.command, expect=None)
    box.transcript.note(f"first command `{' '.join(first.argv)}` exited {first.exit}")

    assert refused.exit == row.code
    assert row.says in refused.stdout + refused.stderr + lane_logs(repo)
    assert retried.exit == 0


def lane_logs(repo: Path) -> str:
    return "".join(path.read_text(encoding="utf-8", errors="replace")
                   for path in (repo / ".crapkit").glob("lane-*.log"))
