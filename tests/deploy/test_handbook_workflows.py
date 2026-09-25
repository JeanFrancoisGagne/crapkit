"""The handbook's six ways people run crapkit (docs/handbook.html, "Six ways
people run it"), each from the handbook's own Install and Enforcement blocks
on a repo shaped like the one the workflow names.

Every command comes from a <pre> block of the handbook, or for the campaign
from the phase lines of its figure. A workflow names an example file
(auth/session.ts, refreshToken); the cell runs the same command on its
fixture's file, the substitution a reader makes, and notes it in the
transcript. Each step asserts its exit code and the line the reader acts on.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import pytest

from kit import docsnip, installers, repos
from kit.cells import cell
from kit.installers import Expect, bare, said, shape

PACKET = "deploy-channels"
PAGE = "docs/handbook.html"
BASE = "The base: the CLI, then one repo"
ENFORCE = "Enforcement: seed the ratchet, then arm the hook"
WHENEVER = "Then, whenever"
SOLO = "1 · Solo, mid-feature"
AGENT = "2 · An agent burning down debt"
POLYGLOT = "3 · Day one on a polyglot repo"
CI = "4 · CI on a pull request"
SHOW = "5 · Showing someone who will not open a terminal"
BELOW = "All six, below the git top"
EXAMPLE = {"auth/session.ts": "calc/session.py", "refreshToken": "refresh_token"}


def pre(heading: str) -> docsnip.Fence:
    return docsnip.fence(PAGE, heading)


def typed(heading: str) -> list[str]:
    """The commands a reader types from a block, comments dropped."""
    return [bare(line) for line in docsnip.commands(pre(heading))]


def filled(line: str, names: dict[str, str]) -> str:
    for example, mine in names.items():
        line = line.replace(example, mine)
    return line


# --- the handbook's Install and Enforcement blocks -------------------------------------

def handbook_base(box, repo: Path, want: Expect = Expect()) -> None:
    """`pip install crapkit` into an activated venv, then init, doctor and
    coverage in the repo, each through the start rules (init's note, the
    container guard)."""
    install, *rest = typed(BASE)
    installers.pip_venv(box, "3.12", line=install)
    for line in rest:
        if not line.startswith("cd "):
            installers.start_step(box, repo, line, want)


def enforce(box, repo: Path, want: Expect = Expect()) -> list:
    """The Enforcement block, line by line, from `cwd`: seed, commit the marks,
    write and chmod the hook."""
    return [installers.start_step(box, repo, line, want) for line in typed(ENFORCE)]


def adopted(box, templates, name: str = "py-pytest", *, files: dict | None = None,
            want: Expect = Expect()) -> Path:
    """A template, with `files` committed first, adopted by the handbook's blocks."""
    repo = repos.checkout(box, name, cache=templates)
    for relative, text in (files or {}).items():
        _write(repo, relative, text)
        installers.commit(box, repo, f"add {relative}")
    handbook_base(box, repo, want)
    enforce(box, repo, want)
    assert os.access(repo / ".git" / "hooks" / "pre-commit", os.X_OK)
    return repo


# --- 1: solo, mid-feature --------------------------------------------------------------

SESSION_BASE = '''def refresh_token(req, store):
    return store.get(req)
'''
SESSION_UNDER = '''def refresh_token(req, store):
    if not req:
        return None
    return store.get(req)
'''
SESSION_OVER = '''def refresh_token(req, store):
    if not req:
        return None
    if req.scope and not store.allows(req.scope):
        return None
    token = store.get(req)
    if token is None:
        return None
    if token.expired and req.retry:
        return store.renew(req)
    if token.revoked or req.forced:
        return None
    return token
'''
SESSION_SPLIT = '''def _usable(token, req):
    if token is None or token.revoked:
        return False
    return not req.forced


def _renewed(token, req, store):
    return store.renew(req) if token.expired and req.retry else token


def refresh_token(req, store):
    if not req or (req.scope and not store.allows(req.scope)):
        return None
    token = store.get(req)
    return _renewed(token, req, store) if _usable(token, req) else None
'''


def _handler(rule: str) -> dict:
    """The plugin's PostToolUse handler for one `if` rule, from the stamped hooks.json."""
    hooks = json.loads((docsnip.root() / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    return next(hook for entry in hooks["hooks"]["PostToolUse"] for hook in entry["hooks"] if hook.get("if") == rule)


def advise(box, repo: Path, path: str):
    """The advisory, spawned as Claude Code spawns the plugin's handler for an
    Edit of `path`: its command and args, the event on stdin."""
    handler = _handler("Edit(*.py)")
    event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(repo),
             "tool_input": {"file_path": str(repo / path)}}
    return box.run([handler["command"], *handler["args"]], cwd=repo, input=json.dumps(event),
                   note="the plugin's Edit(*.py) handler, spawned the way Claude Code spawns it")


def _advisory_lines(names: dict[str, str]) -> list[str]:
    """The advisory the page prints in workflow 1, in the reader's names."""
    text = pre(SOLO).text
    first = text.index("crapkit advisory:")
    return [_generic(filled(line, names)) for line in text[first:].splitlines()[:3]]


def _generic(line: str) -> str:
    """A line by shape, with lizard's two long-name spellings (`f(` and `f (`) made one."""
    return re.sub(r"\s+\(", "(", shape(line))


def _write(repo: Path, relative: str, text: str) -> None:
    (repo / relative).write_text(text, encoding="utf-8")


def _solo_edits(box, repo: Path) -> tuple:
    """Two edits: the first under the ceiling, the second over it."""
    path = EXAMPLE["auth/session.ts"]
    _write(repo, path, SESSION_UNDER)
    quiet = advise(box, repo, path)
    _write(repo, path, SESSION_OVER)
    return quiet, advise(box, repo, path)


def _solo_commands(box, repo: Path) -> dict[str, object]:
    """Workflow 1's commands in the reader's names: rescore and brief on the
    breach, the commit the gate stops, the split, the commit that lands."""
    rescore, brief, commit = [filled(line, EXAMPLE) for line in docsnip.commands(pre(SOLO))]
    box.transcript.note(f"workflow 1 on this fixture: {EXAMPLE}")
    seen = {"rescore": box.script(rescore, cwd=repo, expect=6), "brief": box.script(brief, cwd=repo, expect=0)}
    box.run(["git", "add", EXAMPLE["auth/session.ts"]], cwd=repo, expect=0)
    seen["refused"] = box.script(commit, cwd=repo, env=box.commit_env(), expect=1)
    _write(repo, EXAMPLE["auth/session.ts"], SESSION_SPLIT)
    box.run(["git", "add", EXAMPLE["auth/session.ts"]], cwd=repo, expect=0)
    seen["landed"] = box.script(commit, cwd=repo, env=box.commit_env(), expect=0)
    return seen


@cell("lin-handbook-solo", channel="pip venv", harness="none (sh + git, plugin hook contract)",
      scenario="fresh: handbook Install and Enforcement blocks, then workflow 1: advisory silent then speaking, "
               "rescore --gate, brief, the commit the gate stops, the split that lands",
      use_cases="advisory, rescore --gate, brief, commit gate", os="linux", image="core", cadence="nightly")
def test_solo_mid_feature_hears_the_advisory_then_the_gate(box, templates):
    repo = adopted(box, templates, files={EXAMPLE["auth/session.ts"]: SESSION_BASE})
    quiet, spoke = _solo_edits(box, repo)
    seen = _solo_commands(box, repo)

    assert (quiet.exit, said(quiet)) == (0, "")
    assert spoke.exit == 2
    assert [_generic(line) for line in said(spoke).splitlines()] == _advisory_lines(EXAMPLE)
    assert "refresh_token" in said(seen["rescore"])
    assert said(seen["brief"]).startswith("calc/session.py:1  refresh_token( req , store )")
    assert "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6" in said(seen["refused"])
    assert "crapkit gate" not in said(seen["landed"])


# --- 2: an agent burning down debt ---------------------------------------------------------

GRADE_WORSE = '''def grade(score, attempts, late, bonus):
    if attempts > 9:
        return "X"
    if score > 90 and not late:
        return "A"
    if score > 80:
        return "B" if attempts < 3 else "C"
    if bonus:
        return "C"
    if late and attempts > 2:
        return "F"
    return "D"


def curve(scores, floor):
    return [max(score, floor) for score in scores]
'''
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
    ((95, 1, False, False), "A"), ((95, 1, True, False), "B"), ((85, 5, False, False), "C"),
    ((50, 1, False, True), "C"), ((50, 3, True, False), "F"), ((50, 1, False, False), "D")])
def test_every_band(args, letter):
    assert grade(*args) == letter
'''


def _agent_step(box, repo: Path, line: str, names: dict[str, str], expect: int):
    return box.script(filled(line, names), cwd=repo, expect=expect)


def _loop(box, repo: Path, item: dict, fix) -> dict[str, object]:
    """Workflow 2 after the claim: brief, a first edit that adds a branch, rescore
    (exit 6 sends it back: the CRAP rose past the item's mark), the split,
    rescore, scoped tests, verify. An edit that only lowers the CRAP passes the
    gate while the mark still pardons the function, so the first edit has to
    make things worse to meet the exit 6 the page names."""
    brief, rescore, scoped, verify = typed(AGENT)[1:]
    names = {"PATH": item["path"], "NAME": item["handle"]}
    seen = {"brief": json.loads(said(_agent_step(box, repo, brief, names, 0)))}
    fix(partial=True)
    seen["sent back"] = _agent_step(box, repo, rescore, names, 6)
    fix(partial=False)
    seen["gate"] = _agent_step(box, repo, rescore, names, 0)
    seen["scoped"] = _agent_step(box, repo, scoped, names, 0)
    seen["verify"] = _agent_step(box, repo, verify, names, 0)
    return seen


def _fix_grade(repo: Path):
    def fix(partial: bool) -> None:
        _write(repo, "calc/grade.py", GRADE_WORSE if partial else GRADE_FIXED)
        if not partial:
            with (repo / "tests" / "test_grade.py").open("a", encoding="utf-8") as tests:
                tests.write(GRADE_TABLE)
    return fix


def _claim(box, repo: Path) -> dict:
    return json.loads(said(box.script(typed(AGENT)[0], cwd=repo, expect=0)))["item"]


@cell("lin-handbook-agent", channel="pip venv", harness="none (agent shell)",
      scenario="fresh: handbook workflow 2 on the claimed item: brief, rescore --gate exit 6 then 0, test-scoped, "
               "verify releases the claim; the stop rule reads next-item",
      use_cases="next-item --claim, brief --json, rescore --gate, test-scoped, verify", os="linux", image="core",
      cadence="nightly")
def test_an_agent_burns_down_the_claimed_item_the_handbook_way(box, templates):
    repo = adopted(box, templates)
    item = _claim(box, repo)
    seen = _loop(box, repo, item, _fix_grade(repo))
    claims = box.run(["crapkit", "claims"], cwd=repo, expect=0)
    stop = json.loads(said(box.run(["crapkit", "next-item"], cwd=repo, expect=0)))

    assert (item["path"], item["handle"]) == ("calc/grade.py", "grade")
    assert seen["brief"]["remedy"] == "decompose"
    assert re.search(r"^\s*GATE .* calc/grade\.py:1  grade\(", said(seen["sent back"]), re.M)
    assert said(seen["verify"]).startswith("verify OK")
    assert claims.stdout.strip() == "0 open claim(s)"
    assert stop["empty"] is True
    assert stop["reasons"]["no_lane_over_target"] == 0


# --- 3: day one on a polyglot repo -------------------------------------------------------

POLY_FILES = {
    "api/__init__.py": "",
    "api/grade.py": '''def grade(n, total):
    if total <= 0:
        return None
    if n < 0 or n > total:
        return None
    share = n / total
    if share > 0.9:
        return "A"
    if share > 0.8:
        return "B"
    return "C" if share > 0.5 else "F"
''',
    "ui/panel.ts": '''export function label(kind: string, hot: boolean): string {
  if (kind === "a" && hot) return "A!";
  if (kind === "a") return "A";
  if (kind === "b") return "B";
  if (kind === "c") return "C";
  return "?";
}
''',
    "ui/panel.test.ts": '''import { expect, it } from "vitest";
import { label } from "./panel";

it("labels a hot a", () => expect(label("a", true)).toBe("A!"));
''',
    "infra/main.rs": '''pub fn route(kind: u8) -> &'static str {
    match kind {
        0 => "none",
        1 => "api",
        2 => "ui",
        3 => "ops",
        _ => "other",
    }
}
''',
    "ops/deploy.sh": '''release() {
    if [ -z "$1" ]; then
        return 1
    fi
    case "$1" in
        prod) echo "prod" ;;
        stage) echo "stage" ;;
        *) echo "other" ;;
    esac
}
''',
    "tests/test_grade.py": 'from api.grade import grade\n\n\ndef test_a():\n    assert grade(95, 100) == "A"\n',
    "pyproject.toml": '[project]\nname = "poly"\nversion = "0.1.0"\n\n[tool.pytest.ini_options]\n'
                      'testpaths = ["tests"]\npythonpath = ["."]\n',
}


def _package_json(box) -> str:
    vitest = json.loads((Path(box.toolchain["npm_fixtures"]) / "package.json").read_text(encoding="utf-8"))
    package = {"name": "poly", "private": True, "scripts": {"test": "vitest run"},
               "devDependencies": {"vitest": vitest["devDependencies"]["vitest"]}}
    return json.dumps(package, indent=2) + "\n"


def polyglot_repo(box) -> Path:
    """Python, TypeScript, Rust and shell, one function each shaped like the
    page's rows. The kit's templates hold no four-language repo, so the cell
    builds this one the way repos.py builds its own."""
    repo = box.root / "poly"
    for relative, text in {**POLY_FILES, "package.json": _package_json(box)}.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text, encoding="utf-8", newline="\n")
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    installers.commit(box, repo, "four languages")
    return repo


def _polyglot_env(box) -> None:
    """The repo's own test tools in the venv first, as a repo with a pytest suite
    already has them, then the handbook's install line."""
    installers.pip_venv(box, "3.12", line="python -m pip install -q pytest pytest-cov",
                        channel="the repo's own test tools")
    box.script(typed(BASE)[0], expect=0, note="the handbook's install line")


def _polyglot_run(box, repo: Path) -> dict[str, tuple]:
    """Each command of workflow 3's transcript: (what it printed, what the page prints)."""
    return {command: (box.script(command, cwd=repo), printed) for command, printed in docsnip.outputs(pre(POLYGLOT))}


def _rows(text: str) -> list[str]:
    return [_generic(line) for line in text.splitlines() if line.strip().startswith("risk")]


@cell("lin-handbook-polyglot", channel="pip venv", harness="none",
      scenario="fresh: handbook workflow 3 on Python, TypeScript, Rust and shell: init's lines as printed, the "
               "cc-only scopes, inventory and the worklist rows the page shows",
      use_cases="init, doctor, inventory, worklist", os="linux", image="core", cadence="nightly")
def test_day_one_on_a_polyglot_repo_prints_the_pages_init_and_rows(box):
    _polyglot_env(box)
    repo = polyglot_repo(box)
    seen = _polyglot_run(box, repo)
    init, doctor, inventory, worklist = seen.values()
    config = (repo / "crapkit.toml").read_text(encoding="utf-8")

    assert said(init[0]).splitlines() == init[1].splitlines()
    assert config.count("coverage_optional = true") == 2
    assert (doctor[0].exit, said(doctor[0]).splitlines()[-1]) == (0, "doctor: no problems found")
    assert shape(said(inventory[0])).startswith(shape(inventory[1]))
    assert _rows(said(worklist[0])) == _rows(html.unescape(worklist[1]))


@cell("lin-handbook-polyglot", channel="pip venv", harness="none",
      scenario="fresh: doctor prints the two FAIL lines workflow 3 tells the reader to read as a fork",
      use_cases="doctor", os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-8: docs/handbook.html workflow 3 shows doctor "
                                       "FAILing scopes infra and ops, but init now writes coverage_optional = true "
                                       "for them and doctor prints no problems")
def test_doctor_prints_the_fork_workflow_3_describes(box):
    _polyglot_env(box)
    repo = polyglot_repo(box)
    doctor = dict(_polyglot_run(box, repo))["crapkit doctor"]
    printed = [line for line in doctor[1].splitlines() if line.startswith("FAIL")]

    assert printed and all(line in said(doctor[0]) for line in printed)


# --- 4: CI on a pull request ------------------------------------------------------------------

BREACH = '''

def breach(a, b, c, d):
    if a and b:
        return 1
    if b or c:
        return 2
    if c and d:
        return 3
    return 4 if a else 5
'''


def _ci_step(line: str) -> str:
    return next(command for command in typed(CI) if command.startswith(line))


def _default_branch(box, repo: Path) -> None:
    """On the default branch after a passing verify: the baseline committed.
    crapkit.toml is committed too: the handbook's blocks commit only the marks,
    and a CI clone has nothing else to read the lanes from."""
    box.run(["crapkit", "verify"], cwd=repo, expect=0)
    box.script(_ci_step("crapkit verify --emit-baseline"), cwd=repo, expect=0)
    installers.commit(box, repo, "crapkit config and the portable baseline", "crapkit.toml", ".gitignore",
                      "crapkit-baseline.tsv")


def _pull_request(box, repo: Path, body: str) -> Path:
    """A contributor's clone, a branch with `body` appended to calc/grade.py."""
    clone = box.root / "contributor"
    box.run(["git", "clone", "-q", str(repo), str(clone)], expect=0)
    box.run(["git", "checkout", "-q", "-b", "change"], cwd=clone, expect=0)
    grade = clone / "calc" / "grade.py"
    grade.write_text(grade.read_text(encoding="utf-8") + body, encoding="utf-8")
    installers.commit(box, clone, "a change")
    return clone


def _ci_job(box, clone: Path, name: str) -> Path:
    """The PR job: a fresh clone of the branch and a fresh venv with the suite's tools."""
    job = box.root / name
    box.run(["git", "clone", "-q", "--branch", "change", str(clone), str(job)], expect=0)
    installers.pip_venv(box, "3.12", line="python -m pip install -q pytest pytest-cov", name=f"venv-{name}",
                        channel="the CI job's test tools")
    box.script(typed(BASE)[0], expect=0, note="the handbook's install line in the CI job")
    return job


@cell("lin-handbook-ci", channel="pip venv", harness="none (a CI job's shell)",
      scenario="fresh: handbook workflow 4: --emit-baseline on the default branch, a fresh clone where bare verify "
               "exits 1, --baseline-tsv --github refusing a breach with an annotation and passing a clean change",
      use_cases="verify --emit-baseline, --baseline-tsv, --github", os="linux", image="core", cadence="nightly")
def test_ci_on_a_pull_request_reads_the_committed_baseline(box, templates):
    repo = adopted(box, templates)
    _default_branch(box, repo)
    breach = _ci_job(box, _pull_request(box, repo, BREACH), "job-breach")
    bare_verify = box.run(["crapkit", "verify"], cwd=breach, expect=1)
    refused = box.script(_ci_step("crapkit verify --baseline-tsv"), cwd=breach, expect=6)

    assert "no" in said(bare_verify) and "crapkit coverage" in said(bare_verify)
    assert re.search(r"^::error file=calc/grade\.py,line=\d+", refused.stdout, re.M), said(refused)


@cell("lin-handbook-ci", channel="pip venv", harness="none (a CI job's shell)",
      scenario="fresh: with only what the handbook's blocks commit, the PR job's fresh clone reaches a verdict",
      use_cases="verify --baseline-tsv", os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-11: the handbook's Install and Enforcement "
                                       "blocks commit only crapkit-ratchet.tsv, so the fresh clone workflow 4 runs "
                                       "in has no crapkit.toml and verify --baseline-tsv refuses before a verdict")
def test_the_handbook_blocks_alone_give_the_ci_clone_its_config(box, templates):
    repo = adopted(box, templates)
    box.run(["crapkit", "verify"], cwd=repo, expect=0)
    box.script(_ci_step("crapkit verify --emit-baseline"), cwd=repo, expect=0)
    installers.commit(box, repo, "the portable baseline", "crapkit-baseline.tsv")
    job = _ci_job(box, _pull_request(box, repo, BREACH), "job-literal")
    verdict = box.script(_ci_step("crapkit verify --baseline-tsv"), cwd=job)

    assert verdict.exit == 6, said(verdict)


@cell("lin-handbook-ci", channel="pip venv", harness="none (a CI job's shell)",
      scenario="fresh: a clean pull request passes the committed baseline", use_cases="verify --baseline-tsv",
      os="linux", image="core", cadence="nightly")
def test_a_clean_pull_request_passes_the_committed_baseline(box, templates):
    repo = adopted(box, templates)
    _default_branch(box, repo)
    clean = _ci_job(box, _pull_request(box, repo, "\n\ndef fine(a):\n    return a\n"), "job-clean")
    passed = box.script(_ci_step("crapkit verify --baseline-tsv"), cwd=clean, expect=0)

    assert [line for line in said(passed).splitlines() if line.startswith("verify OK")]


# --- 5: showing someone who will not open a terminal ------------------------------------------------

def _page_text(path: Path) -> str:
    body = re.sub(r"<(style|script)\b.*?</\1>", "", path.read_text(encoding="utf-8"), flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))


@cell("lin-handbook-show", channel="pip venv", harness="none",
      scenario="fresh: handbook workflow 5: report writes one page with grades by scope, the worklist with each "
               "row's explain call, the trend, and a banner naming a stale lane after an edit",
      use_cases="report, explain", os="linux", image="core", cadence="nightly")
def test_the_report_holds_the_three_sections_and_the_stale_banner(box, templates):
    repo = adopted(box, templates)
    report = box.script(typed(SHOW)[0], cwd=repo, expect=0)
    fresh = _page_text(Path(said(report).strip()))
    explain = re.search(r"crapkit explain \S+ \S+", fresh)[0]
    opened = box.script(explain, cwd=repo, expect=0)
    _write(repo, "calc/grade.py", (repo / "calc" / "grade.py").read_text(encoding="utf-8") + "\n\ndef f(a):\n    return a\n")
    stale = box.script(typed(SHOW)[0], cwd=repo, expect=0)

    assert Path(said(report).strip()) == repo / ".crapkit" / "report.html"
    assert all(section in fresh for section in ("Grades by scope", "Worklist:", "Trend:"))
    assert said(opened).startswith("calc/grade.py  grade( score , attempts , late , bonus )")
    assert "stale" not in fresh
    assert "1 of 1 lanes are stale" in _page_text(Path(said(stale).strip()))
    assert "lane 'py'" in _page_text(Path(said(stale).strip()))


# --- 6: the whole codebase, and all six below the git top ---------------------------------------------

def campaign_phases() -> list[str]:
    """The phase lines of the campaign figure, in order: the figure's mono text."""
    text = (docsnip.root() / PAGE).read_text(encoding="utf-8")
    section = text[text.index('id="campaign"'):]
    section = section[:section.index("<h2", 1)]
    return [html.unescape(line) for line in re.findall(r'class="sm mono">([^<]+)</text>', section)]


def phase_1(n: int, m: int) -> list[str]:
    """Phase 1's two commands, `worklist --batches N · brief --batch M --json`, filled in."""
    parts = [part.strip() for part in campaign_phases()[1].split("·")]
    return [f"crapkit {filled(part, {'N': str(n), 'M': str(m)})}" for part in parts]


def _fix_legacy(repo: Path, item: dict):
    """The split and a band table for a copied grade function with no tests."""
    module, handle = Path(item["path"]).stem, item["handle"]

    def fix(partial: bool) -> None:
        body = GRADE_WORSE if partial else GRADE_FIXED
        _write(repo, item["path"], body.replace("def grade(", f"def {handle}("))
        if not partial:
            table = GRADE_TABLE.replace("grade(*args)", f"{handle}(*args)")
            _write(repo, f"tests/test_{module}.py", f"from calc.{module} import {handle}\n{table}")
    return fix


def _batches(text: str) -> list[set[str]]:
    """The files each batch of `worklist --batches` names."""
    blocks = re.split(r"^batch \d+", text, flags=re.M)[1:]
    return [set(re.findall(r"(calc/\S+\.py)", block)) for block in blocks]


@cell("lin-handbook-whole", channel="pip venv", harness="none (a campaign's shell)",
      scenario="fresh: the campaign: phase 0 from the Install and Enforcement blocks, phase 1's worklist "
               "--batches and brief --batch, one phase 2 loop committed through the armed hook, digest",
      use_cases="worklist --batches, brief --batch, the loop, digest", os="linux", image="core",
      cadence="nightly")
def test_the_campaign_cuts_the_map_and_closes_one_item_through_the_gate(box, templates):
    repo = adopted(box, templates, "brownfield", want=Expect(marks=5))
    batches, packets = [box.script(line, cwd=repo, expect=0) for line in phase_1(2, 2)]
    item = _claim(box, repo)
    seen = _loop(box, repo, item, _fix_legacy(repo, item))
    installers.commit(box, repo, f"{item['handle']}: split into bands")
    digest = box.run(["crapkit", "digest"], cwd=repo, expect=0)
    cut = _batches(said(batches))

    assert len(cut) == 2 and not cut[0] & cut[1], said(batches)
    assert len(json.loads(said(packets))["packets"]) == 2
    assert item["path"].startswith("calc/legacy_")
    assert said(seen["verify"]).startswith("verify OK")
    assert said(digest).strip()


BELOW_ROUTE = '''def route(kind):
    return kind
'''
BELOW_BREACH = '''def route(kind, hot, live, admin):
    if kind and hot:
        return 1
    if live or admin:
        return 2
    if kind and live:
        return 3
    if hot or admin:
        return 4
    return 5 if kind else 6
'''


def _nested(box, templates) -> tuple[Path, Path]:
    """subdir-root with an app/ package, adopted from packages/api by the handbook's blocks."""
    top = repos.checkout(box, "subdir-root", cache=templates)
    root = top / "packages" / "api"
    (root / "app").mkdir()
    _write(root, "app/__init__.py", "")
    _write(root, "app/route.py", BELOW_ROUTE)
    installers.commit(box, top, "an app package")
    handbook_base(box, root)
    return top, root


def _below_lines(box, top: Path, root: Path):
    """The page's three lines: add at the top, cd into the root, run the hook's command."""
    add, cd, hook = typed(BELOW)
    _write(root, "app/route.py", BELOW_BREACH)
    box.script(add, cwd=top, expect=0)
    assert cd == f"cd {root.relative_to(top).as_posix()}"
    return box.script(hook, cwd=root, expect=6)


@cell("lin-handbook-whole", channel="pip venv", harness="none (sh + git)",
      scenario="fresh: 'All six, below the git top': the root in packages/api, the page's staged breach and the "
               "hook command's refusal as printed", use_cases="hook-precommit, subdir root", os="linux",
      image="core", cadence="nightly")
def test_below_the_git_top_the_hook_command_refuses_the_staged_breach(box, templates):
    top, root = _nested(box, templates)
    refused = _below_lines(box, top, root)
    printed = [line for line in pre(BELOW).text.splitlines() if not line.startswith("$ ")]

    assert [shape(line) for line in said(refused).splitlines()] == [shape(line) for line in printed]


@cell("lin-handbook-whole", channel="pip venv", harness="none (sh + git)",
      scenario="fresh: below the git top, the Enforcement block arms a hook git runs, and that hook gates a commit "
               "made in packages/api", use_cases="commit gate, subdir root", os="linux", image="core",
      cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-9: from a crapkit root below the git top, the "
                                       "handbook's Enforcement block cannot write .git/hooks/pre-commit, and the "
                                       "hook it writes at the git top refuses every commit with 'no crapkit.toml "
                                       "at <top>'")
def test_below_the_git_top_the_enforcement_block_arms_a_working_hook(box, templates):
    top, root = _nested(box, templates)
    armed = enforce_where_it_can(box, root)
    _write(root, "app/route.py", BELOW_BREACH)
    box.run(["git", "add", "app/route.py"], cwd=root, expect=0)
    refused = box.run(["git", "commit", "-q", "-m", "breach"], cwd=root, env=box.commit_env(), expect=1)

    assert armed
    assert "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6" in said(refused)


def enforce_where_it_can(box, root: Path) -> bool:
    """The Enforcement block from the crapkit root, each line recorded, none asserted."""
    steps = [box.script(line, cwd=root, env=box.commit_env()) for line in typed(ENFORCE)]
    return all(step.exit == 0 for step in steps)
