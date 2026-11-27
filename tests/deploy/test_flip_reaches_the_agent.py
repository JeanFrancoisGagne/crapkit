"""The flip refusal reaches the agent in each core-image harness, and crapkit-recover routes it.

A repo's one Python scope `py` is cc-only (`coverage_optional`) and seeded:
each of its three ccn-8 functions marks 8.0000. The user then adds a
coveragepy lane and drops `coverage_optional`. verify refuses at exit 3
before any lane runs, and its refusal ends on the flip clause and the upgrade
guide's procedure (ratchet.FLIP_PROCEDURE).

Each cell runs under one harness tools/deploy/pins.toml runs in the core
image (Claude Code, Codex, the Cursor agent), with the plugin installed the
way that harness gets it, and checks two things:

- The refusal reaches the agent. verify is CLI behavior the harness carries
  through its shell, so it runs from the shell with the harness CLIs on PATH:
  exit 3, the flip clause on stderr, nothing on stdout, no lane run. Under
  Claude Code the stub model also asks for `crapkit verify` through the Bash
  tool, and the tool result Claude Code sends back holds the clause, in a
  session that offers the model crapkit-recover.
- crapkit-recover, as the harness loads it, routes the refusal: its
  paragraph that links the procedure quotes a clause the refusal ends on, and
  the candidate's upgrade guide holds the section the link names.

Where each harness reads the skill: Claude Code, the plugin the README's
Claude Code lines install (`claude plugin list --json` names its path).
Codex, the plugin the README's Codex lines install (`skills/list` names the
skill's file). The Cursor agent, the Claude Code plugin it loads: its
loadClaudePlugin reads ~/.claude/plugins/installed_plugins.json and the skills
under each enabled plugin's installPath (cursor-agent 2026.09.23's bundle).

Fresh: the README's pip start seeds the repo. Upgrade: crapkit N-1 seeds it,
the guide's pip row upgrades the CLI, and the candidate's first coverage and
verify record the scope's mode before the lane joins. These cells are surface
cells for `plugin/`: MAP.toml lists them under the plugin channels.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kit import profiles, state, stub_anthropic, wheels
from kit.cells import cell
from test_cc_only_to_measured import COVERED, LANE, MAKE_COV, MARKS, PROCEDURE, _function
from test_claude_plugin import CLAUDE, PLUGIN, github, installed, page_lines, run_lines
from test_codex_plugin import CodexSession, install as install_codex

PACKET = "metric-stamp-07"
CORE = sorted(key for key, spec in profiles.pins()["harness"].items() if spec["image"] == "core")
SCOPE = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "py"\npaths = ["py"]\nlanguages = ["python"]\n'
CC_ONLY = SCOPE + "coverage_optional = true\n"
MEASURED = f"{SCOPE}\n{LANE}"
FLIP = f"py was cc-only and is now measured by its coveragepy lane(s), so its marks hold no coverage yet: follow {PROCEDURE}"
# The skills link the crapkit repo on GitHub, since the repo an agent works in does not hold the page.
LINK = f"https://github.com/JeanFrancoisGagne/crapkit/blob/main/{PROCEDURE}"
SECTION = "### Turning a cc-only scope into a measured one"
PIP = "pip in the active environment"
USE_CASES = "verify, skills, upgrade guide"


def _marks(decorator) -> list:
    """The marks a decorator puts on a test; a lambda would read as a mark argument."""
    def test():
        pass
    return decorator(test).pytestmark


def _core_cells(prefix: str, scenario: str) -> list:
    """One parameter per core-image harness, each marked by @cell."""
    return [pytest.param(key, id=key, marks=_marks(cell(
        f"{prefix}-{key}", channel="pip venv + plugin", harness=key, scenario=scenario, use_cases=USE_CASES,
        os="linux", image="core", cadence="nightly"))) for key in CORE]


# --- the repo ------------------------------------------------------------------------

def _cc_only_repo(box) -> Path:
    """The cc-only repo, committed, its lane's generator left untracked so crapkit never scores it."""
    repo = box.root / "flip"
    state.write(repo, {"py/flip.py": "".join(_function(name) for name in COVERED),
                       ".gitignore": ".crapkit/\nmake_cov.py\n", "crapkit.toml": CC_ONLY})
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    state.commit(box, repo, "a cc-only Python scope")
    state.write(repo, {"make_cov.py": MAKE_COV})
    return repo


def _seed(box, repo: Path) -> None:
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seed")


def _fresh_repo(box, templates) -> Path:
    profiles.session_crapkit(box, templates)
    repo = _cc_only_repo(box)
    _seed(box, repo)
    return repo


def _upgraded_repo(box, candidate, record_property) -> Path:
    """N-1 seeds the repo; after the guide's pip row the candidate's first verify
    records the scope's mode, which a file 0.8.x wrote leaves to inference."""
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _cc_only_repo(box)
    _seed(box, repo)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0, note="not a guide step: one coverage after upgrading")
    state.verify(box, repo)
    state.commit(box, repo, "the candidate's first verify")
    return repo


def _flip(box, repo: Path) -> None:
    """The coveragepy lane joins `py`, and `coverage_optional` goes."""
    text = (repo / MARKS).read_text(encoding="utf-8")
    assert re.search(r"^#scope\tpy\t.*coverage cc-only$", text, re.M), text
    assert [line.rsplit("\t", 1)[1] for line in text.splitlines() if line.startswith("py/")] == ["8.0000"] * 3
    state.write(repo, {"crapkit.toml": MEASURED})


# --- where each harness reads crapkit-recover ----------------------------------------------

RECOVER = Path("skills") / "crapkit-recover" / "SKILL.md"


def _claude_plugin(box, candidate, repo: Path) -> Path:
    """The plugin the README's Claude Code lines install; its root."""
    github(box, candidate)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    return Path(installed(box)["installPath"])


def _skill_in_claude_code(box, candidate, repo: Path) -> Path:
    return _claude_plugin(box, candidate, repo) / RECOVER


def _skill_in_cursor(box, candidate, repo: Path) -> Path:
    """The Cursor agent's loader takes each enabled plugin's skills from the
    installPath Claude Code's installed_plugins.json records."""
    _claude_plugin(box, candidate, repo)
    record = Path(box.env["CLAUDE_CONFIG_DIR"]) / "plugins" / "installed_plugins.json"
    return Path(json.loads(record.read_text(encoding="utf-8"))["plugins"][PLUGIN][0]["installPath"]) / RECOVER


def _skill_in_codex(box, candidate, repo: Path) -> Path:
    install_codex(box, candidate.staged, candidate.version, repo)
    with CodexSession(box) as codex:
        skills = codex.listing("skills/list", repo)
    return next(Path(skill["path"]) for skill in skills if skill["name"] == "crapkit:crapkit-recover")


SKILL_IN = {"claude-code": _skill_in_claude_code, "codex": _skill_in_codex, "cursor-agent": _skill_in_cursor}


# --- the checks ----------------------------------------------------------------------------

def assert_the_shell_carries_the_refusal(box, repo: Path, key: str) -> None:
    """verify from the harness's shell: exit 3, the flip clause, no lane run."""
    command = profiles.pins()["harness"][key]["command"]
    assert box.which(command), f"{key}: {command} is not on PATH: {box.env['PATH']}"

    refused = box.script("crapkit verify", shell="sh", cwd=repo, expect=3)

    assert refused.stdout == "" and FLIP in refused.stderr, box.transcript.text()
    assert not (repo / ".crapkit" / "cov" / "py.json").exists(), "verify ran the lane before it refused"


def assert_claude_hands_the_model_the_refusal(box, repo: Path) -> None:
    """One `claude -p` session: the stub asks for `crapkit verify` through Bash."""
    script = [{"tool_use": {"name": "Bash", "input": {"command": "crapkit verify"}}}, {"text": "done"}]
    with stub_anthropic.serve(script) as stub:
        box.run(["claude", "-p", "verify the repo", "--permission-mode", "bypassPermissions"], cwd=repo, expect=0,
                env=stub_anthropic.claude_env(stub.url))
    bodies = stub.bodies()
    results = profiles.tool_results(bodies)
    box.transcript.attach("tool-results", results)

    assert "crapkit-recover" in json.dumps(bodies[0]), "the session offers the model no crapkit-recover"
    assert any(FLIP in text for text in results), results


def assert_recover_routes_it(skill: Path, guide: Path) -> None:
    """The skill's paragraph that links the procedure quotes a clause the refusal ends on."""
    paragraphs = [block for block in re.split(r"\n\s*\n", skill.read_text(encoding="utf-8")) if f"]({LINK})" in block]
    quoted = [clause for block in paragraphs for clause in re.findall(r"`([^`]+)`", block)]

    assert [clause for clause in quoted if FLIP.endswith(f"{clause} {PROCEDURE}")], (skill, paragraphs)
    assert SECTION in guide.read_text(encoding="utf-8").splitlines()


def assert_the_flip_reaches_the_agent(box, candidate, repo: Path, key: str) -> None:
    _flip(box, repo)
    profiles.add_harnesses(box)
    skill = SKILL_IN[key](box, candidate, repo)
    box.transcript.note(f"{key} reads crapkit-recover from {skill}")

    assert_the_shell_carries_the_refusal(box, repo, key)
    if key == "claude-code":
        assert_claude_hands_the_model_the_refusal(box, repo)
    assert_recover_routes_it(skill, candidate.staged / "docs" / "upgrading.md")


@pytest.mark.parametrize("key", _core_cells(
    "lin-flip-route", "fresh: README pip start, a cc-only scope seeded, then a coveragepy lane joins: verify exit 3 "
                      "in the harness's shell names the procedure, and crapkit-recover as the harness loads it "
                      "links that section"))
def test_flip_route_fresh(box, candidate, templates, key):
    assert_the_flip_reaches_the_agent(box, candidate, _fresh_repo(box, templates), key)


@pytest.mark.parametrize("key", _core_cells(
    "lin-up-flip-route", "upgrade from N-1 by the guide's pip row: N-1 seeds the cc-only scope, the candidate's first "
                         "verify records its mode, a coveragepy lane joins: verify exit 3 names the procedure, and "
                         "crapkit-recover links that section"))
def test_flip_route_upgrade(box, candidate, record_property, key):
    assert_the_flip_reaches_the_agent(box, candidate, _upgraded_repo(box, candidate, record_property), key)
