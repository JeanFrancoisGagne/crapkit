"""The plugin lines the README prints, run by the agents and releases it names.

Codex installs the same plugin as Claude Code and loads its skills, so a
skill that shows a `claude plugin` command has to say it is Claude Code's.
The first cell installs the plugin with the README's two Codex lines through
the kit's github.com mirror and reads the skills Codex installed with the
same rule tests/unit/test_docs_claims_contract.py holds the source to. The
second runs the README's lines on the oldest releases the deploy image carries:
the Claude Code plugin on 2.1.138, since the README names no Claude Code floor
for the plugin's one shell-form hook, and the Codex lines on 0.121.0, which
the README says predates `codex plugin add`.

Cells of the deploy-docs packet: the doc halves of lin-codex-plugin-fresh
(skills name no unlabelled `claude` command) and lin-plugin-floors.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from docs_support import venv_with
from kit import docsnip, gitmirror
from kit.cells import cell

PACKET = "deploy-docs"
WINDOWS = os.name == "nt"
CHECK_SKILLS = """
import json, sys
sys.path.insert(0, "tests/unit")
from test_docs_claims_contract import unlabelled_claude_calls
paths = sys.argv[1:]
print(json.dumps({path: unlabelled_claude_calls(open(path, encoding="utf-8").read()) for path in paths}))
"""
CLAUDE_FLOOR, CODEX_BEFORE_ADD = "2.1.138", "0.121.0"


def harnesses_on_path(box) -> None:
    """The pinned harness CLIs behind the toolchain on the sandbox PATH; on
    Windows git needs long paths for Codex's plugin cache."""
    box.env["PATH"] = os.pathsep.join([*box.path_dirs(), *box.toolchain["harness_bin"]])
    if WINDOWS:
        box.run(["git", "config", "--global", "core.longpaths", "true"], expect=0)


def work_repo(box) -> Path:
    repo = box.root / "work"
    repo.mkdir()
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    return repo


def readme_lines(heading: str) -> list[str]:
    return docsnip.commands(docsnip.fence("README.md", heading))


def installed_skills(box, version: str) -> list[str]:
    root = Path(box.env["CODEX_HOME"]) / "plugins" / "cache" / "crapkit" / "crapkit" / version / "skills"
    return sorted(str(path) for path in root.glob("*/SKILL.md"))


@cell("docs-codex-skills", channel="Codex marketplace, README lines via the mirror", harness="Codex",
      scenario="fresh: the README's two Codex lines install the plugin; every skill Codex installed labels its "
               "`claude plugin` commands as Claude Code's and gives the Codex command beside them",
      use_cases="Codex plugin install, skills", os=("linux", "windows"), image="core", cadence="push")
def test_the_skills_codex_installs_show_no_unlabelled_claude_command(box, candidate):
    venv_with(box, "crapkit[py]")
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    harnesses_on_path(box)
    repo = work_repo(box)
    for line in readme_lines("Codex"):
        box.script(line, cwd=repo, expect=0)
    skills = installed_skills(box, candidate.version)
    found = json.loads(box.run(["python", "-c", CHECK_SKILLS, *skills], cwd=docsnip.root(), expect=0).stdout)

    assert len(skills) == 3, skills
    assert found == {path: [] for path in skills}
    assert codex_lines(skills) == 2, "the onboard and recover skills give the Codex command"


def codex_lines(skills: list[str]) -> int:
    """How many of the skills name `codex plugin add crapkit@crapkit`."""
    return sum("codex plugin add crapkit@crapkit" in Path(path).read_text(encoding="utf-8") for path in skills)


def floor_bin(box, command: str, release: str) -> None:
    """`command` on PATH as the side install of `release` the image carries."""
    real = next(Path(directory) / f"{command}-{release}" for directory in box.toolchain["harness_bin"]
                if (Path(directory) / f"{command}-{release}").exists())
    shelf = box.root / "floor-bin"
    shelf.mkdir(exist_ok=True)
    (shelf / command).symlink_to(real)
    box.prepend_path(shelf)


def claude_plugins(box, repo: Path) -> list[str]:
    listed = json.loads(box.run(["claude", "plugin", "list", "--json"], cwd=repo, expect=0).stdout)
    return [entry.get("id") for entry in listed]


@cell("docs-plugin-floors", channel="Claude and Codex marketplaces, README lines via the mirror",
      harness="Claude Code 2.1.138, Codex 0.121.0",
      scenario="fresh: the README's Claude Code plugin lines install on the oldest release the image carries, "
               "2.1.138; its Codex lines on 0.121.0 stop at `codex plugin add`, as the README says of releases "
               "before 0.131.0",
      use_cases="plugin install", os="linux", image="core", cadence="nightly")
def test_the_floors_the_readme_names_hold(box, candidate):
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    harnesses_on_path(box)
    repo = work_repo(box)
    floor_bin(box, "claude", CLAUDE_FLOOR)
    floor_bin(box, "codex", CODEX_BEFORE_ADD)

    assert CLAUDE_FLOOR in box.run(["claude", "--version"], expect=0).stdout
    for line in readme_lines("The Claude Code plugin"):
        box.script(line, cwd=repo, expect=0)
    assert "crapkit@crapkit" in claude_plugins(box, repo)
    add = box.script(readme_lines("Codex")[1], cwd=repo)
    assert add.exit != 0 and re.search(r"unrecognized subcommand '?add", add.stderr), add.stderr
    assert "Codex 0.131.0 or newer" in (docsnip.root() / "README.md").read_text(encoding="utf-8")
