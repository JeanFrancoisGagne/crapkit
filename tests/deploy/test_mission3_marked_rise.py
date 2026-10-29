"""A marked function whose ccn passed its mark, met through git and Claude Code.

docs/ratchet.md's commit-gate section, found by its kept anchor, prints the
walkthrough these cells run: seed, raise a marked function past its mark,
`git commit` refused naming the function and its mark, decompose, commit. Its
"Overrides and the audit trail" section prints the override of that refusal.
The fixture is the page's own: calc/parse.py's read_row( line , cols ), every
branch tested, so its mark is its ccn, 7, and one more branch passes it.

    lin-marked-rise-route1      Route 1 sh: hook exit 6, git exit 1, the split commits
    lin-marked-rise-precommit   the same refusal through the pre-commit framework
    win-marked-rise-ps51        the same under powershell.exe 5.1, captured with $x = ... 2>&1
    lin-marked-warn             a straddling ccn rise commits with the warn; a comment, the count line
    lin-marked-override         CRAPKIT_OVERRIDE_REASON passes it; the marks file keeps its bytes
    lin-marked-advisory         claude-hook's marked, warn and unmarked blocks, and silence on a comment
    win-marked-advisory         the same script on Windows
    lin-up-marked-rise-n1       N-1 commits the edit; after the upgrade the same edit is refused
    win-up-marked-rise-n1       the same upgrade under powershell.exe 5.1
    lin-up-marked-advisory-n1   N-1's plugin says nothing; after the update the marked block arrives
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from kit import docsnip, gitsurf, repos, shim, wheels
from kit.cells import cell
from test_claude_hook_stub import advisory, reset, session, write_turns
from test_claude_plugin import (CLAUDE, cli_venv, github, harness_on_path, install_old_plugin, measured_repo,
                                page_lines, run_lines, upgrade_both)
from test_commit_gate import old_readme
from test_override_commit import ALERT, OVERRIDES, alerts, set_alert
from test_precommit import framework_repo

PACKET = "mission-3"
ANCHOR = '<a id="the-commit-gate-skips-marked-functions"></a>'
UPGRADE_DOC = "docs/upgrading.md"
UPGRADE_LINE = 'python -m pip install --upgrade "crapkit[py]"'
PAST = "rose past their ratchet mark"
ROSE = "may pass their ratchet mark"
COUNT = "carry a ratchet mark and were not gated"
MARKS = "crapkit-ratchet.tsv"
PARSE = "calc/parse.py"
HANDLE = "read_row( line , cols )"
GRADE = "calc/grade.py"
GRADE_HANDLE = "grade( score , attempts , late , bonus )"
PARSE_PY = '''def read_row(line, cols):
    if not line:
        return []
    fields = line.split(",")
    if fields[0] == "#":
        return []
    if len(fields) < cols:
        return fields + [""] * (cols - len(fields))
    if len(fields) > cols:
        return fields[:cols]
    if fields[-1] == "":
        return fields[:-1]
    if cols == 1:
        return fields[0]
    return fields
'''
PARSE_TEST = '''from calc.parse import read_row


def test_each_branch_of_read_row():
    assert read_row("", 2) == []
    assert read_row("#,b", 2) == []
    assert read_row("a", 2) == ["a", ""]
    assert read_row("a,b,c", 2) == ["a", "b"]
    assert read_row("a,", 2) == ["a"]
    assert read_row("a", 1) == "a"
    assert read_row("a,b", 2) == ["a", "b"]
'''
SHORT = "    if len(fields) < cols:\n"
BANG = '    if fields[0] == "!":\n        return fields[1:]\n'
NOTE = "    # a short row gets empty fields\n"
RAISED_PY = PARSE_PY.replace(SHORT, BANG + SHORT)
COMMENTED_PY = PARSE_PY.replace(SHORT, NOTE + SHORT)
# The page's fix: the two marker checks move to a helper, and read_row is back at ccn 7.
SPLIT_PY = '''def _marker(fields):
    if fields[0] == "#":
        return []
    if fields[0] == "!":
        return fields[1:]
    return None


''' + PARSE_PY.replace('    if fields[0] == "#":\n        return []\n',
                       "    marked = _marker(fields)\n    if marked is not None:\n        return marked\n")
LAST = '    return "D"\n'
GRADE_RISE = '    if attempts > 5:\n        return "E"\n'
GRADE_NOTE = "    # every other score is a D\n"
# claude-hook's own words, read from the installed candidate, never copied here.
SAID = ("import json; from crapkit.cli import claude_hook as hook; "
        "print(json.dumps({'past': hook._PAST_MARK, 'rose': hook._ROSE, "
        "'unmarked': hook._advisory_lines('x', [], 6)[-1]}))")
# A Windows PowerShell 5.1 user's capture of the commit; COMMIT is the page's command.
CAPTURE_PS = '''try { [Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437) } catch { }
$x = COMMIT 2>&1
$code = $LASTEXITCODE
$lines = @("codepage " + [Console]::OutputEncoding.CodePage) + @($x | ForEach-Object { "$_" })
Set-Content -Path $env:MARKED_CAPTURE -Encoding utf8 -Value $lines
exit $code
'''


# --- the pages ------------------------------------------------------------------------

def anchored_heading(text: str, anchor: str = ANCHOR) -> str:
    """The heading that follows `anchor`: the cells find the commit-gate section
    by its kept anchor, so new words in the heading move no cell."""
    lines = text.splitlines()
    after = lines[lines.index(anchor) + 1:] if anchor in lines else []
    found = [match[2].strip() for match in map(docsnip.HEADING.match, after) if match]
    if not found:
        raise docsnip.DocSnipError(f"{gitsurf.RATCHET_DOC}: no heading follows {anchor}")
    return found[0]


def gate_fence(holding: str) -> docsnip.Fence:
    """The commit-gate section's first fence holding `holding`."""
    text = (docsnip.root() / gitsurf.RATCHET_DOC).read_text(encoding="utf-8")
    return docsnip.fence(gitsurf.RATCHET_DOC, anchored_heading(text), contains=holding)


def page_refusal() -> tuple[str, str, str, str]:
    """The walkthrough's seed line, its refused commit, and the refusal's head and fix lines."""
    (seed, _), (commit, output) = docsnip.outputs(gate_fence(PAST))
    lines = [line for line in output.splitlines() if not line.startswith("EXIT=")]
    return seed, commit, lines[0], lines[-1]


def page_warn() -> tuple[str, str]:
    """The warn walkthrough's commit and the warn block's head line."""
    command, output = docsnip.outputs(gate_fence(ROSE))[0]
    return command, output.splitlines()[0]


def count_line() -> str:
    return gate_fence(COUNT).text.strip()


def page_override() -> tuple[str, list[str]]:
    """The marked override's commit and the lines crapkit prints for it."""
    fence = docsnip.fence(gitsurf.RATCHET_DOC, OVERRIDES, contains="kept as it was")
    command, output = docsnip.outputs(fence)[0]
    return command, [line for line in output.splitlines() if line.startswith("crapkit")]


# --- the repo ---------------------------------------------------------------------------

def write(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text, encoding="utf-8", newline="\n")


def stage(box, repo: Path, relative: str, text: str) -> None:
    write(repo, {relative: text})
    box.run(["git", "add", relative], cwd=repo, expect=0)


def commit_all(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


def seed_parse(box, repo: Path) -> float:
    """read_row and its test committed, measured and seeded with the page's seed
    line, before a gate is armed: read_row's mark, under the ccn 8 the raised
    edit takes it to."""
    write(repo, {PARSE: PARSE_PY, "tests/test_parse.py": PARSE_TEST})
    commit_all(box, repo, "read rows")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    box.script(page_refusal()[0], cwd=repo, expect=0)
    commit_all(box, repo, "mark read_row")
    mark = gitsurf.marks(repo)[f"{PARSE}  {HANDLE}"]
    assert mark < 8, f"read_row's mark {mark} holds ccn 8 under it, so the raised edit would straddle it"
    return mark


def adopted(box, templates, install: str | None = None) -> Path:
    """The README 60-second start in py-pytest: grade carries its mark."""
    gitsurf.pip_venv(box, install=install)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo)
    return repo


def digest(repo: Path) -> str:
    return hashlib.sha256((repo / MARKS).read_bytes()).hexdigest()


def said_by(step) -> str:
    return step.stdout + step.stderr


def assert_marked_refusal(step, code: int, mark: float) -> None:
    """The page's refusal head and fix lines, and read_row's row: ccn 8, its
    place, its handle and its mark at 4 dp."""
    _, _, head, fix = page_refusal()
    said = said_by(step)
    row = rf"ccn\s+8\s+{re.escape(PARSE)}:\d+\s+{re.escape(HANDLE)}\s+{re.escape(f'mark {mark:.4f}')}"
    assert step.exit == code, f"exit {step.exit}, the page says {code}\n{said}"
    assert head in said and fix in said and re.search(row, said), f"not the page's refusal:\n{said}"


def commit(box, repo: Path, expect: int | None = None):
    """The page's `git commit` line, typed into the platform's shell as gitsurf.commit types the README's."""
    return box.script(page_refusal()[1], cwd=repo, env=box.commit_env(), expect=expect)


def refused_then_split(box, repo: Path, mark: float) -> None:
    """The raised edit: the hook run by hand exits 6, `git commit` exits 1, and the split commits."""
    stage(box, repo, PARSE, RAISED_PY)
    assert_marked_refusal(box.run(["crapkit", "hook-precommit"], cwd=repo), 6, mark)
    assert_marked_refusal(commit(box, repo), 1, mark)
    stage(box, repo, PARSE, SPLIT_PY)
    commit(box, repo, expect=0)


# --- the refusal ----------------------------------------------------------------------------

@cell("lin-marked-rise-route1", channel="Route 1 sh", harness="git 2.47",
      scenario="fresh: README 60-second start, then the page's walkthrough: seed, raise read_row past its mark, "
      "hook exit 6 and git exit 1 naming it and its mark, decompose, commit lands",
      use_cases="commit gate, ratchet", os="linux", image="cells", cadence="nightly")
def test_route1_refuses_a_marked_function_past_its_mark_then_takes_its_split(box, templates):
    repo = adopted(box, templates)
    mark = seed_parse(box, repo)
    gitsurf.route1(box, repo)

    refused_then_split(box, repo, mark)


@cell("lin-marked-rise-precommit", channel="pre-commit", harness="pre-commit 4.6.2",
      scenario="fresh: README Route 3 via the mirror; the refusal block and the hook's exit 6 in pre-commit's "
      "output, git exit 1; the split commits",
      use_cases="commit gate, ratchet", os="linux", image="cells", cadence="nightly")
def test_precommit_shows_the_marked_refusal_then_takes_the_split(box, templates, candidate):
    repo = framework_repo(box, templates, candidate)
    mark = seed_parse(box, repo)
    gitsurf.precommit_install(box, repo)
    stage(box, repo, PARSE, RAISED_PY)

    refused = commit(box, repo)
    assert_marked_refusal(refused, 1, mark)
    assert "- exit code: 6" in said_by(refused), said_by(refused)
    stage(box, repo, PARSE, SPLIT_PY)
    commit(box, repo, expect=0)


def captured_commit(box, repo: Path) -> tuple[int, list[str]]:
    """The page's commit under powershell.exe 5.1 as `$x = git commit ... 2>&1`
    under code page 437: git's exit and the captured lines, the code page first."""
    out = box.tmp / "marked-capture.txt"
    step = box.script(CAPTURE_PS.replace("COMMIT", page_refusal()[1]), shell="powershell", cwd=repo,
                      env={**box.commit_env(), "MARKED_CAPTURE": str(out)})
    return step.exit, out.read_text(encoding="utf-8-sig").splitlines()


@cell("win-marked-rise-ps51", channel="Route 1 PowerShell", harness="PortableGit, powershell.exe 5.1",
      scenario="fresh: the page's walkthrough under powershell.exe 5.1; a $x = git commit 2>&1 capture under "
      "code page 437 holds each line the hook wrote; the split commits",
      use_cases="commit gate, ratchet", os="windows", image=None, cadence="nightly")
def test_powershell_51_captures_the_marked_refusal_line_for_line(box, templates):
    repo = adopted(box, templates)
    mark = seed_parse(box, repo)
    gitsurf.route1(box, repo, shell="powershell")
    stage(box, repo, PARSE, RAISED_PY)
    hook = box.run(["crapkit", "hook-precommit"], cwd=repo)
    assert_marked_refusal(hook, 6, mark)

    code, captured = captured_commit(box, repo)
    wrote = [line for line in hook.stdout.splitlines() + hook.stderr.splitlines() if line.strip()]
    assert (code, captured[:1]) == (1, ["codepage 437"]), captured
    assert [line for line in wrote if line not in captured[1:]] == [], captured
    stage(box, repo, PARSE, SPLIT_PY)
    commit(box, repo, expect=0)


# --- the warn and the override ----------------------------------------------------------------

def grade_rise(said: str, mark: float) -> list[tuple[int, int]]:
    """The (base, staged) ccn of each warn row naming grade at its mark."""
    row = (rf"ccn (\d+) -> (\d+)\s+{re.escape(GRADE)}:\d+\s+{re.escape(GRADE_HANDLE)}\s+"
           rf"{re.escape(f'mark {mark:.4f}')}")
    return [(int(base), int(staged)) for base, staged in re.findall(row, said)]


@cell("lin-marked-warn", channel="Route 1 sh", harness="git 2.47",
      scenario="fresh: a straddling marked function whose ccn rose commits with the warn block; a comment-only "
      "edit inside it commits with the count line and no warn",
      use_cases="commit gate, ratchet", os="linux", image="cells", cadence="nightly")
def test_a_straddling_rise_commits_with_the_warn_and_a_comment_with_the_count_line(box, templates):
    repo = adopted(box, templates)
    gitsurf.route1(box, repo)
    mark = gitsurf.marks(repo)[f"{GRADE}  {GRADE_HANDLE}"]
    raised = (repo / GRADE).read_text(encoding="utf-8").replace(LAST, GRADE_RISE + LAST)
    command, head = page_warn()
    stage(box, repo, GRADE, raised)

    warned = box.script(command, cwd=repo, env=box.commit_env(), expect=0)
    rises = grade_rise(warned.stderr, mark)
    assert head in warned.stderr and len(rises) == 1 and rises[0][1] == rises[0][0] + 1, warned.stderr
    stage(box, repo, GRADE, raised.replace(LAST, GRADE_NOTE + LAST))
    counted = box.script(command, cwd=repo, env=box.commit_env(), expect=0)
    assert count_line() in counted.stderr and head not in counted.stderr, counted.stderr


def kept_at(line: str, mark: float) -> str:
    """A page line with its trailing mark replaced by this repo's, at 4 dp."""
    return re.sub(r"\d+\.\d{4}$", f"{mark:.4f}", line)


def regressions(box, repo: Path) -> list[dict]:
    """The ratchet_regression items of `verify --json`, which exits 6 on them."""
    verdict = box.run(["crapkit", "verify", "--json"], cwd=repo, expect=6)
    return [item for item in json.loads(verdict.stdout)["findings"] if item["kind"] == "ratchet_regression"]


@cell("lin-marked-override", channel="Route 1 with alert_command", harness="git 2.47",
      scenario="fresh: CRAPKIT_OVERRIDE_REASON passes the marked refusal: one alert line naming read_row, the "
      "marks file's sha256 unchanged and absent from the commit, the override row through crapkit, then "
      "verify --json carries a ratchet_regression for it",
      use_cases="override audit, ratchet", os="linux", image="cells", cadence="nightly")
def test_an_override_passes_a_marked_refusal_and_the_mark_keeps_its_bytes(box, templates):
    repo = adopted(box, templates)
    mark = seed_parse(box, repo)
    gitsurf.route1(box, repo)
    set_alert(box, repo, ALERT)
    command, printed = page_override()
    stage(box, repo, PARSE, RAISED_PY)
    before = digest(repo)

    granted = box.script(command, cwd=repo, env=box.commit_env(), expect=0)
    expected = [kept_at(line, mark) for line in printed]
    assert [line for line in expected if line not in said_by(granted)] == [], said_by(granted)
    assert len([line for line in alerts(repo).splitlines() if HANDLE in line]) == 1, alerts(repo)
    assert digest(repo) == before
    assert MARKS not in box.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo, expect=0).stdout
    reason = re.search(r'CRAPKIT_OVERRIDE_REASON="([^"]+)"', command)[1]
    row = box.run(["crapkit", "overrides"], cwd=repo, expect=0).stdout
    assert f"{PARSE}  {HANDLE}" in row and f"({reason})" in row, row
    assert [(item["path"], item["long_name"], item["recorded"]) for item in regressions(box, repo)] == [
        (PARSE, HANDLE, mark)]


# --- the advisory ----------------------------------------------------------------------------

def edit_turns(path: Path, old: str, new: str) -> list[dict]:
    """A Read, then an Edit of `path`, as Claude Code requires."""
    return [{"tool_use": {"name": "Read", "input": {"file_path": str(path)}}},
            {"tool_use": {"name": "Edit", "input": {"file_path": str(path), "old_string": old, "new_string": new}}}]


def advisory_repo(box, templates) -> Path:
    """py-pytest measured the plugin cells' way, then read_row seeded; grade carries its mark too."""
    repo = measured_repo(box, templates)
    seed_parse(box, repo)
    return repo


def blocks(box, repo: Path) -> dict[str, tuple[list[dict], list[str]]]:
    """Each case's writes, and the lines its advisory must hold: head, row, closing line."""
    words = json.loads(box.run(["python", "-c", SAID], expect=0).stdout)
    marks = gitsurf.marks(repo)
    head = "crapkit advisory: 1 marked function(s) {} in {}"
    past = [head.format(words["past"][0], PARSE), f"{HANDLE}  mark {marks[f'{PARSE}  {HANDLE}']:.4f}",
            words["past"][1]]
    rose = [head.format(words["rose"][0], GRADE), f"{GRADE_HANDLE}  mark {marks[f'{GRADE}  {GRADE_HANDLE}']:.4f}",
            words["rose"][1]]
    return {"past": (edit_turns(repo / PARSE, SHORT, BANG + SHORT), past),
            "rose": (edit_turns(repo / GRADE, LAST, GRADE_RISE + LAST), rose),
            "unmarked": (write_turns(repo)["write-new"], [advisory("calc/big.py"), words["unmarked"]]),
            "comment": (edit_turns(repo / PARSE, SHORT, NOTE + SHORT), [])}


def judged(box, repo: Path, writes: list[dict], lines: list[str]) -> dict:
    """One `claude -p` session against the stub: how many advisories the last
    request carries, and which expected lines it lacks."""
    reset(box, repo)
    bodies, misses = session(box, repo, writes, lines[0] if lines else "crapkit advisory: ")
    text = json.dumps(bodies[-1].get("messages", []))
    return {"advisories": text.count("crapkit advisory: "), "missing": [line for line in lines if line not in text],
            "misses": misses}


def advisory_ready(box, candidate, templates) -> Path:
    """The CLI and the plugin from the README lines, the repo seeded, the shim first on PATH."""
    real = cli_venv(box)
    repo = advisory_repo(box, templates)
    github(box, candidate)
    harness_on_path(box)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    box.prepend_path(shim.install(box, real))
    return repo


def assert_advisories(box, repo: Path) -> None:
    results = {name: judged(box, repo, writes, lines) for name, (writes, lines) in blocks(box, repo).items()}
    box.transcript.attach("cases", results)

    found = {name: (result["advisories"], result["missing"]) for name, result in results.items()}
    assert found == {"past": (1, []), "rose": (1, []), "unmarked": (1, []), "comment": (0, [])}, results


@cell("lin-marked-advisory", channel="Claude marketplace", harness="Claude Code -p, stub Messages API",
      scenario="fresh: an Edit past read_row's mark gets the marked block and its closing line; a ccn rise "
      "inside grade's straddling mark the warn block; an unmarked breach today's closing line; a comment none",
      use_cases="PostToolUse hook, ratchet", os="linux", image="core", cadence="nightly")
def test_claude_hook_speaks_on_each_marked_edit(box, candidate, templates):
    assert_advisories(box, advisory_ready(box, candidate, templates))


@cell("win-marked-advisory", channel="Claude marketplace", harness="Claude Code, Git Bash",
      scenario="fresh: the lin-marked-advisory script on Windows, stub on 127.0.0.1",
      use_cases="PostToolUse hook, ratchet", os="windows", image=None, cadence="nightly")
def test_claude_hook_speaks_on_each_marked_edit_windows(box, candidate, templates):
    assert_advisories(box, advisory_ready(box, candidate, templates))


@cell("lin-up-marked-advisory-n1", channel="Claude marketplace", harness="Claude Code, plugin at N-1 then the "
      "README update lines",
      scenario="upgrade: under N-1 the Edit past read_row's mark is silent; after the CLI upgrade and the README "
      "update lines the same Edit gets the marked block",
      use_cases="plugin upgrade, PostToolUse hook", os="linux", image="core", cadence="nightly")
def test_the_marked_block_arrives_with_the_plugin_update(box, candidate, templates):
    mirror = install_old_plugin(box, wheels.n_minus_1(), box.root)
    repo = advisory_repo(box, templates)
    box.prepend_path(shim.install(box, box.which("crapkit")))
    writes = edit_turns(repo / PARSE, SHORT, BANG + SHORT)

    silent = judged(box, repo, writes, [])
    _, steps = upgrade_both(box, mirror, candidate, repo)
    past = judged(box, repo, writes, blocks(box, repo)["past"][1])
    assert (silent["advisories"], steps[-1].exit) == (0, 0), silent
    assert (past["advisories"], past["missing"]) == (1, []), past


# --- the upgrade from N-1 ---------------------------------------------------------------------

def under_n1(box, templates, shell: str) -> tuple[Path, float]:
    """N-1 from the wheelhouse, the 60-second start and read_row seeded under it,
    and the Route 1 block N-1's README printed for `shell`."""
    n1 = wheels.n_minus_1()
    repo = adopted(box, templates, install=f'pip install "crapkit[py]=={n1}"')
    assert n1 in box.run(["crapkit", "--version"], expect=0).stdout
    mark = seed_parse(box, repo)
    block = docsnip.fence(gitsurf.README, gitsurf.ROUTE1, index=0 if shell == "sh" else 1, base=old_readme(box, n1))
    box.script(block.text, shell=shell, cwd=repo, env=box.commit_env(), expect=0)
    return repo, mark


def n1_commits_the_edit(box, repo: Path) -> None:
    """Under N-1 the raised edit commits, on a side branch, with the count line."""
    box.run(["git", "checkout", "-q", "-b", "under-n1"], cwd=repo, expect=0)
    stage(box, repo, PARSE, RAISED_PY)
    passed = commit(box, repo, expect=0)
    assert count_line() in passed.stderr, passed.stderr
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)


def upgrade_cell(box, templates, candidate, shell: str) -> None:
    """N-1's hook passed the edit; the candidate refuses it, reading N-1's marks file unchanged."""
    repo, mark = under_n1(box, templates, shell)
    before = digest(repo)
    n1_commits_the_edit(box, repo)
    box.script(gitsurf.inline(UPGRADE_DOC, "Upgrading crapkit", UPGRADE_LINE), expect=0)
    assert candidate.version in box.run(["crapkit", "--version"], expect=0).stdout
    assert digest(repo) == before

    stage(box, repo, PARSE, RAISED_PY)
    refused = commit(box, repo)
    assert_marked_refusal(refused, 1, mark)
    assert "seed" not in said_by(refused) and digest(repo) == before, said_by(refused)
    stage(box, repo, PARSE, COMMENTED_PY)
    counted = commit(box, repo, expect=0)
    assert count_line() in counted.stderr, counted.stderr


@cell("lin-up-marked-rise-n1", channel="pip venv, Route 1", harness="git 2.47",
      scenario="upgrade: under N-1 the raised edit commits with the count line; after the offline upgrade the "
      "marks file's bytes are unchanged and the same edit is refused; a comment-only edit still commits",
      use_cases="commit gate, upgrade", os="linux", image="cells", cadence="nightly")
def test_the_edit_n1_committed_is_refused_after_the_upgrade(box, templates, candidate):
    upgrade_cell(box, templates, candidate, "sh")


@cell("win-up-marked-rise-n1", channel="pip venv, Route 1 PowerShell", harness="PortableGit, powershell.exe 5.1",
      scenario="upgrade: the lin-up-marked-rise-n1 script under powershell.exe 5.1",
      use_cases="commit gate, upgrade", os="windows", image=None, cadence="nightly")
def test_the_edit_n1_committed_is_refused_after_the_upgrade_windows(box, templates, candidate):
    upgrade_cell(box, templates, candidate, "powershell")


# --- the kit's reading of the pages -----------------------------------------------------------

@pytest.mark.kit
def test_the_gate_section_is_found_by_its_kept_anchor_whatever_its_heading_says():
    page = f"intro\n{ANCHOR}\n## New words for the rule\n\ntext\n"

    assert anchored_heading(page) == "New words for the rule"
    with pytest.raises(docsnip.DocSnipError, match="no heading follows"):
        anchored_heading("## The commit gate skips marked functions\n")


@pytest.mark.kit
def test_every_line_the_cells_follow_is_on_the_pages():
    seed, commit, head, fix = page_refusal()

    assert seed.startswith("crapkit ratchet seed") and commit.startswith("git commit ")
    assert PAST in head and fix.startswith("bring each one's ccn back")
    assert ROSE in page_warn()[1] and COUNT in count_line()
    assert page_override()[0].startswith("CRAPKIT_OVERRIDE_REASON=")
