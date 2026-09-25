"""An agent's shell, crapkit from pipx: the AGENTS.md burn-down loop on one
item, then every secondary command an agent or its operator reaches for.

The loop is run from AGENTS.md itself. `crapkit next-item --claim` names the
item, the fence's `crapkit brief PATH "FUNCTION" --json` line is run with
those two filled in, and steps 3 to 5 run the strings the packet hands back
(`<commands.gate>` is `packet["commands"]["gate"]`), verbatim, as the page
tells an agent to. The suite runs from its own venv: a pipx install shares
no environment with the tests it measures.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from kit import docsnip, installers, repos
from kit.cells import cell
from kit.installers import said

PACKET = "deploy-channels"
AGENTS = "AGENTS.md"
LOOP = "Burning down debt in your repo"
GRADE_FIXED = '''def _top(score, late):
    return "A" if score > 90 and not late else None


def _high(score, attempts):
    if score <= 80:
        return None
    return "B" if attempts < 3 else "C"


def _low(attempts, late, bonus):
    if bonus:
        return "C"
    return "F" if late and attempts > 2 else "D"


def grade(score, attempts, late, bonus):
    return _top(score, late) or _high(score, attempts) or _low(attempts, late, bonus)


def curve(scores, floor):
    return [max(score, floor) for score in scores]
'''
GRADE_TABLE = '''

import pytest


@pytest.mark.parametrize("args, letter", [
    ((95, 1, True, False), "B"), ((85, 5, False, False), "C"), ((50, 1, False, True), "C"),
    ((50, 3, True, False), "F"), ((50, 1, False, False), "D")])
def test_every_band(args, letter):
    assert grade(*args) == letter
'''


def _suite_venv(box) -> None:
    """The repo's own test environment, activated: pytest and pytest-cov."""
    venv = box.root / "suite-venv"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    box.run([str(installers.scripts(venv) / installers.exe("python")), "-m", "pip", "install", "-q", "pytest",
             "pytest-cov"], expect=0)
    box.prepend_path(installers.scripts(venv))


def _adopted(box, templates) -> Path:
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    installers.allow_containers_here(repo)
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    installers.commit(box, repo, "adopt crapkit")
    return repo


def _edit(box, repo: Path) -> None:
    """Step 2, the decomposition the packet's remedy asks for, with table tests."""
    (repo / "calc" / "grade.py").write_text(GRADE_FIXED, encoding="utf-8")
    with (repo / "tests" / "test_grade.py").open("a", encoding="utf-8") as tests:
        tests.write(GRADE_TABLE)


def _loop_line(box, repo: Path, line: str, item: dict, packet: dict) -> dict:
    """One line of the AGENTS.md loop fence: the brief, the edit, or a packet string."""
    if line == "<edit>":
        _edit(box, repo)
        return packet
    if line.startswith("<commands."):
        step = box.script(packet["commands"][line[len("<commands."):-1]], cwd=repo, expect=0)
        packet.setdefault("ran", {})[line] = said(step)
        return packet
    filled = line.replace("PATH", item["path"]).replace('"FUNCTION"', f'"{item["function"]}"')
    return json.loads(said(box.script(filled, cwd=repo, expect=0)))


def agents_loop(box, repo: Path) -> dict:
    """The five steps, in the fence's order, on the item next-item --claim hands out."""
    claim = installers.inline(AGENTS, LOOP, "crapkit next-item --claim")
    item = json.loads(said(box.script(claim, cwd=repo, expect=0)))["item"]
    packet: dict = {}
    for line in docsnip.commands(docsnip.fence(AGENTS, LOOP)):
        packet = _loop_line(box, repo, line, item, packet)
    return {"item": item, **packet}


def _secondary(box, repo: Path) -> dict[str, object]:
    """Every read command once, on the run the loop's verify left."""
    commands = {"watch": ["watch", "--cycles", "1"], "inventory": ["inventory", "--db", str(box.root / "inv.sqlite")],
                "doctor-json": ["doctor", "--json"], "doctor-tune": ["doctor", "--tune"],
                "doctor-files": ["doctor", "--show-files"], "report": ["report"], "trend": ["trend"],
                "runs": ["runs"], "duplication": ["duplication"], "coupling": ["coupling"],
                "overrides": ["overrides"], "clean-dry": ["clean", "--dry-run"], "clean": ["clean"],
                "explain": ["explain", "calc/grade.py", "grade"]}
    return {name: box.run(["crapkit", *argv], cwd=repo, expect=0) for name, argv in commands.items()}


def _config_line(page: str, key: str) -> str:
    """A `key = value` line from the fence on `page` that shows the key set."""
    block = next(block for block in docsnip.fences(page) if f"\n{key} = " in f"\n{block.text}")
    return next(line for line in block.text.splitlines() if line.startswith(f"{key} = "))


def _set_key(repo: Path, line: str) -> None:
    config = repo / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("[crapkit]\n", f"[crapkit]\n{line}\n", 1),
                      encoding="utf-8")


def _mutate(box, repo: Path) -> tuple:
    """mutate refuses without a mutation_command, then runs the one
    docs/configuration.md shows on the lines the edit changed."""
    refused = box.run(["crapkit", "mutate"], cwd=repo, expect=3)
    _set_key(repo, _config_line("docs/configuration.md", "mutation_command"))
    ran = box.run(["crapkit", "mutate", "--files", "calc/grade.py", "--max-mutants", "3", "--json"], cwd=repo,
                  expect=0)
    return refused, ran


def _digest(box, repo: Path) -> tuple:
    """digest right after the loop: its verify run pairs with the adoption run,
    so the numbers moved; --alert needs its command."""
    moved = box.run(["crapkit", "digest"], cwd=repo, expect=0)
    refused = box.run(["crapkit", "digest", "--alert"], cwd=repo, expect=3)
    _set_key(repo, 'alert_command = "cat > .crapkit/alert.txt"')
    sent = box.run(["crapkit", "digest", "--alert"], cwd=repo, expect=0)
    return moved, refused, sent


def _sarif(box, repo: Path) -> dict[str, dict]:
    box.run(["crapkit", "coverage", "--sarif", str(box.root / "coverage.sarif")], cwd=repo, expect=0)
    box.run(["crapkit", "verify", "--sarif", str(box.root / "verify.sarif")], cwd=repo, expect=0)
    return {name: json.loads((box.root / f"{name}.sarif").read_text(encoding="utf-8")) for name in ("coverage", "verify")}


@cell("lin-pipx-agent-loop", channel="pipx", harness="none (agent shell)",
      scenario="fresh: AGENTS.md loop; plus watch --cycles, mutate, inventory --db, coverage/verify --sarif, "
               "digest --alert, doctor --json/--tune/--show-files, report, trend, runs, duplication, coupling, "
               "overrides, clean", use_cases="next-item, claims, brief, explain, verify, secondary commands",
      os="linux", image="core", cadence="push")
def test_an_agent_burns_down_one_item_from_a_pipx_install(box, templates, candidate):
    install = installers.pipx(box)
    _suite_venv(box)
    repo = _adopted(box, templates)
    loop = agents_loop(box, repo)
    digest = _digest(box, repo)
    claims = box.run(["crapkit", "claims"], cwd=repo, expect=0)
    mutated = _mutate(box, repo)
    installers.commit(box, repo, "grade: split into bands")
    done = json.loads(said(box.run(["crapkit", "next-item"], cwd=repo, expect=0)))
    sarif = _sarif(box, repo)
    seen = _secondary(box, repo)

    assert candidate.version in install.run(box, repo, "--version").stdout
    assert loop["item"]["path"] == "calc/grade.py"
    assert loop["remedy"] == "decompose"
    assert loop["commands"]["gate"] == "crapkit rescore calc/grade.py --gate"
    assert said_line(loop["ran"]["<commands.verify>"]).startswith("verify OK")
    assert claims.stdout.strip() == "0 open claim(s)"
    assert "mutate needs [crapkit] mutation_command" in said(mutated[0])
    assert json.loads(mutated[1].stdout)["mutants"] == 3, said(mutated[1])
    assert done["empty"] is True
    assert all(document["version"] == "2.1.0" for document in sarif.values())
    assert digest[0].stdout.strip()
    assert "digest --alert needs [crapkit] alert_command" in said(digest[1])
    assert (repo / ".crapkit" / "alert.txt").read_text(encoding="utf-8").startswith("crapkit digest (runs ")
    assert seen["watch"].stdout.startswith("watching ")
    assert (box.root / "inv.sqlite").is_file()
    assert seen["duplication"].stdout.strip() == "no near-duplicate functions found"
    assert json.loads(seen["doctor-json"].stdout)["problems"] == []
    assert seen["doctor-tune"].stdout.startswith("# doctor --tune: suggestions")
    assert "calc/grade.py" in seen["doctor-files"].stdout
    assert Path(seen["report"].stdout.strip()) == repo / ".crapkit" / "report.html"
    assert re.search(r"coverage +verdict=-.*\n.*verify +verdict=ok", seen["runs"].stdout)
    assert seen["explain"].stdout.startswith("calc/grade.py  grade( score , attempts , late , bonus )")


def said_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""
