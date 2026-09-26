"""The git surfaces a user wires crapkit into, driven the way the docs say.

Every surface here ends in a real `git commit` or `git merge`: the hook or the
merge driver runs because git ran it, never because a cell called
`crapkit hook-precommit` itself. Each line a user types comes from the page
that prints it (docsnip), so a moved fence fails naming the page.

    venv = gitsurf.pip_venv(box)                    # README install line, venv on PATH
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo)                        # the 60-second start, committed
    gitsurf.route1(box, repo)                       # README Route 1, pasted into sh
    gitsurf.breach(repo)                            # route( a , b , c , d ) at ccn 8
    gitsurf.assert_refused(gitsurf.commit(box, repo))   # "What a refusal looks like"
    gitsurf.decompose(repo)
    gitsurf.assert_accepted(gitsurf.commit(box, repo))
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from kit import docsnip

WINDOWS = os.name == "nt"
README = "README.md"
RATCHET_DOC = "docs/ratchet.md"
LANES_DOC = "docs/lanes.md"
ROUTE1 = "Route 1: `.git/hooks/pre-commit` (local, not committed)"
ROUTE2 = "Route 2: a committed hooks directory"
ROUTE3 = "Route 3: the pre-commit framework"
ROUTE4 = "Route 4: CI"
REFUSAL = "What a refusal looks like"
START = "The 60-second start"
DRIVER = "The git merge driver"
BREACH_NAME = "route( a , b , c , d )"
BREACH = '''def route(a, b, c, d):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    if d and a:
        return 4
    if d or b:
        return 5
    return 6
'''
DECOMPOSED = '''FIRST = {"a": 1, "b": 2, "c": 3}


def route(a, b, c, d):
    flags = {"a": a, "b": b, "c": c}
    for name, value in FIRST.items():
        if flags[name]:
            return value
    return _tail(a, b, d)


def _tail(a, b, d):
    if d and a:
        return 4
    return 5 if d or b else 6
'''


# --- where the cell runs ------------------------------------------------------------

def in_container() -> bool:
    """crapkit's own container test: /.dockerenv or CRAPKIT_INSIDE_CONTAINER=1."""
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def bindir(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


# --- reading the docs ----------------------------------------------------------------

def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, text) of each heading outside a code fence."""
    found, fenced = [], False
    for number, line in enumerate(lines):
        fenced = fenced != bool(docsnip.FENCE.match(line))
        match = None if fenced else docsnip.HEADING.match(line)
        found += [(number, len(match[1]), match[2].strip())] if match else []
    return found


def _heading(headings: list[tuple[int, int, str]], page: str, heading: str) -> tuple[int, int, str]:
    found = [entry for entry in headings if entry[2] == heading]
    if not found:
        raise docsnip.DocSnipError(f"{page} > {heading}: no such heading")
    return found[0]


def section(page: str, heading: str) -> str:
    """One section's text, its subsections included: from `heading` to the next
    heading of the same level or above."""
    lines = (docsnip.root() / page).read_text(encoding="utf-8").splitlines()
    headings = _headings(lines)
    number, level, _ = _heading(headings, page, heading)
    later = [line for line, depth, _ in headings if line > number and depth <= level]
    return "\n".join(lines[number + 1:later[0] if later else len(lines)])


def inline(page: str, heading: str, starts: str) -> str:
    """The first inline `code` span under `heading` that begins with `starts`:
    an install line the page prints inside a sentence, not in a fence."""
    spans = re.findall(r"`([^`\n]+)`", section(page, heading))
    found = [span for span in spans if span.startswith(starts)]
    if not found:
        raise docsnip.DocSnipError(f"{page} > {heading}: no inline code starting {starts!r}")
    return found[0]


def refusal() -> tuple[str, str, str]:
    """README "What a refusal looks like": the command and the refusal's first
    and last lines, which name no path and so match any repo."""
    command, output = docsnip.outputs(docsnip.fence(README, REFUSAL))[0]
    lines = output.splitlines()
    return command, lines[0], lines[-1]


def container_refusal() -> str:
    """docs/lanes.md#containers: the line a coverage.py lane prints in a container."""
    return docsnip.fence(LANES_DOC, "Containers", contains="container_ok", lang="").text.strip()


def container_key() -> str:
    """docs/lanes.md#containers: the toml line that lets a lane run in a container."""
    return docsnip.fence(LANES_DOC, "Containers", lang="toml").text.strip()


# --- installing crapkit ------------------------------------------------------------------

def pip_venv(box, python: str = "3.12", install: str | None = None) -> Path:
    """A fresh venv on the sandbox PATH, then the install line the README prints
    for a Python project that shares its test environment with crapkit."""
    venv = box.root / "venv"
    box.run([box.toolchain.python(python), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(bindir(venv))
    box.script(install or inline(README, "Install", 'pip install "crapkit[py]"'), expect=0)
    return venv


def allow_container_lane(repo: Path) -> None:
    """Apply the docs/lanes.md#containers key to every lane init wrote."""
    config = repo / "crapkit.toml"
    text = config.read_text(encoding="utf-8")
    keyed = re.sub(r"^\[\[lane\]\]\n", f"[[lane]]\n{container_key()}\n", text, flags=re.MULTILINE)
    config.write_text(keyed, encoding="utf-8", newline="\n")


def start_lines() -> list[str]:
    """The 60-second start after the install and the `cd`: init to the commit
    and the verify that ends it."""
    lines = docsnip.commands(docsnip.fence(README, START))
    return [line for line in lines if line.startswith(("crapkit ", "git "))]


def adopt(box, repo: Path, *, guard: bool = False) -> None:
    """The 60-second start in `repo`, committed by its own `git commit` line
    and ended by its verify. In a container, `guard=True` first asserts the
    coverage.py lane's refusal and then applies the documented fix; otherwise
    the fix goes in right after init."""
    for line in start_lines():
        _start_step(box, repo, line, guard)


def dated(box, line: str) -> dict | None:
    """The fixed author and committer dates a start's `git commit` line runs with."""
    return box.commit_env() if line.startswith("git commit") else None


def _start_step(box, repo: Path, line: str, guard: bool) -> None:
    if line == "crapkit coverage" and guard:
        _refused_in_container(box, repo, line)
    box.script(line, cwd=repo, env=dated(box, line), expect=0)
    if line == "crapkit init" and not guard:
        keyed_in_container(repo)


def _refused_in_container(box, repo: Path, line: str) -> None:
    """In a container: the coverage.py lane's refusal, then the documented fix."""
    if not in_container():
        return
    refused = box.script(line, cwd=repo, expect=5)
    assert container_refusal() in refused.stderr
    allow_container_lane(repo)


def keyed_in_container(repo: Path) -> None:
    if in_container():
        allow_container_lane(repo)


# --- the gate -------------------------------------------------------------------

def route1(box, repo: Path, *, shell: str = "sh", expect: int | None = 0):
    """README Route 1 pasted into `shell`: sh runs the heredoc, powershell and
    pwsh the PowerShell block under the same heading."""
    index = 0 if shell in ("sh", "bash") else 1
    return box.script(docsnip.fence(README, ROUTE1, index=index).text, shell=shell, cwd=repo,
                      env=box.commit_env(), expect=expect)


def route2(box, repo: Path, *, shell: str = "sh", expect: int | None = 0):
    """README Route 2, the whole block, pasted into `shell`. It commits."""
    return box.script(docsnip.fence(README, ROUTE2).text, shell=shell, cwd=repo, env=box.commit_env(),
                      expect=expect)


def breach(repo: Path, where: str = "calc", name: str = "route.py") -> str:
    """Write route( a , b , c , d ) at ccn 8 to `where`/`name`; the repo-relative path."""
    path = repo / where / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BREACH, encoding="utf-8", newline="\n")
    return path.relative_to(repo).as_posix()


def decompose(repo: Path, where: str = "calc", name: str = "route.py") -> None:
    """The same behaviour split under the ceiling, as the refusal asks."""
    (repo / where / name).write_text(DECOMPOSED, encoding="utf-8", newline="\n")


def commit(box, repo: Path, *, env: dict | None = None, stage: str = "."):
    """Stage and run the README's `git commit -m "add route"`, as a user types it."""
    box.run(["git", "add", "-A", stage], cwd=repo, expect=0)
    return box.script(refusal()[0], cwd=repo, env={**box.commit_env(), **(env or {})})


def assert_refused(step, path: str = "calc/route.py") -> None:
    """git refused the commit with the block README "What a refusal looks like"
    prints, naming the staged function, and git's own exit 1."""
    _, first, last = refusal()
    said = step.stdout + step.stderr
    assert step.exit == 1, f"git exit {step.exit}, the README says 1\n{said}"
    assert first in said and last in said, f"not the README's refusal:\n{said}"
    assert re.search(rf"ccn\s+\d+\s+{re.escape(path)}:\d+\s+{re.escape(BREACH_NAME)}", said), said


def assert_accepted(step) -> None:
    assert step.exit == 0, f"git refused a commit under the ceiling:\n{step.stdout}{step.stderr}"


def refused_then_accepted(box, repo: Path, where: str = "calc", name: str = "route.py") -> None:
    """A breach refused with the README's block, then its split accepted. A
    second round in one repo names a new file: the first split is committed."""
    path = breach(repo, where, name)
    assert_refused(commit(box, repo), path)
    decompose(repo, where, name)
    assert_accepted(commit(box, repo))


def head_mode(box, repo: Path, path: str) -> str:
    """The mode `git ls-tree HEAD` records for `path`."""
    return box.run(["git", "ls-tree", "HEAD", path], cwd=repo, expect=0).stdout.split()[0]


# --- the edit advisory and the MCP server --------------------------------------------------

AGENT_DOC = "docs/agent-json.md"


def advise(box, cwd: Path, edited: Path):
    """One Claude Code PostToolUse Edit event for `edited`, sent the way the
    plugin's hook sends it, from the session's cwd."""
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(cwd),
               "tool_input": {"file_path": str(edited)}}
    return box.run(["crapkit", "claude-hook", "--protocol", "1"], cwd=cwd, input=json.dumps(payload))


def assert_advised(step, rel: str) -> None:
    """Exit 2 with the head and tail lines docs/agent-json.md captured, naming `rel`."""
    lines = docsnip.fence(AGENT_DOC, "`claude-hook`", contains="crapkit advisory:").text.splitlines()
    expected = [lines[0].replace("app/m.py", rel), lines[-1]]
    assert step.exit == 2 and [line for line in expected if line not in step.stderr] == [], step.stderr


def next_item_text(client, arguments: dict) -> tuple[bool, str]:
    """get_next_item through a live MCP session: (isError, the text it returned)."""
    result = client.call("get_next_item", arguments)
    return bool(result.get("isError")), result["content"][0]["text"]


# --- the pre-commit framework ------------------------------------------------------------

def precommit_config(box, repo: Path, *, base: Path | None = None) -> str:
    """README Route 3's .pre-commit-config.yaml, written and committed as
    printed; `base` reads an older README. The rev it names."""
    text = docsnip.fence(README, ROUTE3, index=0, base=base).text
    (repo / ".pre-commit-config.yaml").write_text(text + "\n", encoding="utf-8", newline="\n")
    box.run(["git", "add", ".pre-commit-config.yaml"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "add the crapkit gate to pre-commit"], cwd=repo, env=box.commit_env(),
            expect=0)
    return re.search(r"rev: (\S+)", text)[1]


def precommit_install(box, repo: Path, *, base: Path | None = None) -> None:
    """README Route 3's second block: install the framework, arm the hook."""
    for line in docsnip.commands(docsnip.fence(README, ROUTE3, index=1, base=base)):
        box.script(line, cwd=repo, expect=0)


def hook_env_version(box) -> str:
    """`crapkit --version` from the environment pre-commit built last for the hook."""
    launcher = "Scripts/crapkit.exe" if WINDOWS else "bin/crapkit"
    found = list((box.home / ".cache" / "pre-commit").glob(f"repo*/py_env-*/{launcher}"))
    assert found, f"pre-commit built no crapkit environment under {box.home / '.cache' / 'pre-commit'}"
    newest = max(found, key=lambda path: path.stat().st_mtime)
    return box.run([str(newest), "--version"], expect=0).stdout.strip()


# --- doctor ---------------------------------------------------------------------

def doctor(box, repo: Path, *args: str):
    return box.run(["crapkit", "doctor", *args], cwd=repo)


def warnings(step) -> list[str]:
    """doctor's WARN and FAIL lines."""
    return [line for line in step.stdout.splitlines() if line.startswith(("WARN", "FAIL"))]


def names(step, *words: str) -> bool:
    """A doctor WARN or FAIL line holds every one of `words`."""
    return any(all(word in line for word in words) for line in warnings(step))


# --- the merge driver -------------------------------------------------------------

def driver_attribute() -> str:
    return docsnip.fence(RATCHET_DOC, DRIVER, index=0).text.strip()


def driver_config() -> str:
    return docsnip.fence(RATCHET_DOC, DRIVER, index=1).text


def uvx_driver_config() -> str:
    """docs/ratchet.md's driver line for a clone that runs crapkit through uvx."""
    return docsnip.fence(RATCHET_DOC, DRIVER, contains="uvx crapkit ratchet merge").text


def commit_attribute(box, repo: Path) -> None:
    """Step 1 of docs/ratchet.md: the .gitattributes line, committed."""
    attributes = repo / ".gitattributes"
    before = attributes.read_text(encoding="utf-8") if attributes.exists() else ""
    attributes.write_text(before + driver_attribute() + "\n", encoding="utf-8", newline="\n")
    box.run(["git", "add", ".gitattributes"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "merge crapkit-ratchet.tsv with its driver"], cwd=repo,
            env=box.commit_env(), expect=0)


def configure_driver(box, repo: Path, *, shell: str = "sh"):
    """Step 2 of docs/ratchet.md: the per-clone `git config` lines."""
    return box.script(driver_config(), shell=shell, cwd=repo, expect=0)


def merge(box, repo: Path, branch: str):
    return box.run(["git", "merge", branch, "-m", f"merge {branch}"], cwd=repo, env=box.commit_env())


def _mark_rows(repo: Path) -> list[list[str]]:
    lines = (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if line and not line.startswith(("#", "path\t"))]


def marks(repo: Path) -> dict[str, float]:
    """crapkit-ratchet.tsv as {"path  long_name": crap}."""
    return {f"{row[0]}  {row[1]}": float(row[2]) for row in _mark_rows(repo)}


LEGACY_TEST = '''from calc.legacy_1 import grade_1


def test_legacy_one_gives_an_early_high_score_an_a():
    assert grade_1(95, 1, False, False) == "A"
'''
LATE_TEST = '''from calc.grade import grade


def test_a_late_high_score_is_a_b():
    assert grade(85, 1, True, False) == "B"
'''


def tighten(box, repo: Path, test: str, text: str) -> None:
    """Burn down one mark the way docs/ratchet.md says it falls: commit a test
    that covers more of the function, and the passing verify on that commit
    tightens its mark; commit the marks file it names."""
    (repo / "tests" / test).write_text(text, encoding="utf-8", newline="\n")
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", f"cover more: {test}"], cwd=repo, env=box.commit_env(), expect=0)
    verdict = _verify_after_switch(box, repo, box.run(["crapkit", "verify"], cwd=repo))
    assert "1 tightened -> git add crapkit-ratchet.tsv" in verdict.stdout, verdict.stdout
    box.run(["git", "add", "crapkit-ratchet.tsv"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "tighten the mark verify lowered"], cwd=repo, env=box.commit_env(),
            expect=0)


SWITCHED = re.compile(r"baseline commit \w+ is not an ancestor of HEAD .* - run `(crapkit coverage)` for a fresh baseline")


def _verify_after_switch(box, repo: Path, verdict):
    """A verify on the second branch finds the first branch's run as its
    baseline and exits 4 (deploy-git-4). The cell runs the fix the line
    prints, as a user would, and verifies again."""
    fix = SWITCHED.search(verdict.stderr)
    if verdict.exit == 0 or fix is None:
        return verdict
    box.transcript.note("deploy-git-4: verify after a branch switch exited 4; running the fix it printed")
    box.script(fix[1], cwd=repo, expect=0)
    return box.run(["crapkit", "verify"], cwd=repo, expect=0)


def diverge(box, repo: Path) -> dict[str, float]:
    """Two branches that both burn down debt in the brownfield template:
    `feature` tightens legacy_1, main tightens grade, on adjacent lines of the
    marks file. The marks a correct merge holds: the lower value per key."""
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    tighten(box, repo, "test_legacy_one.py", LEGACY_TEST)
    feature = marks(repo)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    tighten(box, repo, "test_grade_late.py", LATE_TEST)
    return {key: min(value, feature[key]) for key, value in marks(repo).items()}


def assert_driver_merged(step, repo: Path, expected: dict[str, float]) -> None:
    """The driver's line, git's clean merge, and the lower mark per key."""
    said = step.stdout + step.stderr
    assert step.exit == 0 and f"ratchet merge: {len(expected)} mark(s)" in said, said
    assert marks(repo) == expected


def committed_marks(box, repo: Path, ref: str = "HEAD") -> str:
    """The marks file as `ref` holds it, one "path  long_name  crap" line per mark."""
    text = box.run(["git", "show", f"{ref}:crapkit-ratchet.tsv"], cwd=repo, expect=0).stdout
    return "\n".join("  ".join(line.split("\t")) for line in text.splitlines())


UNSTAMPED = "[unstamped]"


def _analysis(stamp: str) -> int:
    return int(re.search(r"crapkit-analysis=(\d+)", stamp)[1])


def _newer_side(ours: str, theirs: str) -> tuple[str, str]:
    """("ours" or "theirs", its stamp): the side whose analysis version is higher."""
    return ("ours", ours) if _analysis(ours) > _analysis(theirs) else ("theirs", theirs)


def expected_refusal(ours: str, theirs: str) -> list[str]:
    """docs/ratchet.md's refusal for a merge of these two stamps, filled in: the
    example that asks to re-seed either side when one carries no stamp, else the
    one that names the newer side and the metric to re-seed under."""
    uncompared = UNSTAMPED in (ours, theirs)
    fence = docsnip.fence(RATCHET_DOC, DRIVER, contains="re-baseline one side" if uncompared else "is newer")
    lines = docsnip.outputs(fence)[0][1].splitlines()
    first = re.sub(r"ours is \[[^]]*\] and theirs is \[[^]]*\]",
                   lambda _: f"ours is {ours} and theirs is {theirs}", lines[0])
    if not uncompared:
        side, stamp = _newer_side(ours, theirs)
        first = re.sub(r"(ours|theirs) is newer, so with a crapkit that measures \[[^]]*\]",
                       lambda _: f"{side} is newer, so with a crapkit that measures {stamp}", first)
    return [first, *lines[1:]]


def assert_driver_refused(step, ours: str, theirs: str) -> None:
    """docs/ratchet.md's refusal with this merge's two stamps filled in, then
    git's own conflict lines where the page prints them."""
    said = step.stdout + step.stderr
    assert step.exit == 1, said
    assert [line for line in expected_refusal(ours, theirs) if line not in said] == [], said


def stamp(repo: Path) -> str:
    """The marks file's metric stamp, bracketed the way the merge driver names it."""
    first = (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()[0]
    return "[" + first.lstrip("# ").strip() + "]"
