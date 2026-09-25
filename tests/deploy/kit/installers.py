"""One installer per channel, each running the line the docs print for it.

    install = installers.pip_venv(box, "3.11")
    install.run(box, repo, "init", expect=0)

Every installer takes its command from the stamped docs through docsnip (a
fence) or `inline` (a code span in a paragraph, where the README gives the
line in prose), runs it in the sandbox as a user pastes it, and returns an
Install: the argv that starts crapkit afterwards, the launcher on disk, the
interpreter the channel chose and the directory it asks the user to put on
PATH. A channel the docs name no command for (pip --user, an sdist build)
adds pip's own flag to the README's `pip install crapkit` line, and the cell
says so.

The rest of the module is what several cells of one packet share: the
container rule from docs/lanes.md#containers, a crapkit call that records its
exit, a commit with the kit's fixed dates, and the transcript-shape check that
holds a printed line to the one a page quotes.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from kit import docsnip

WINDOWS = os.name == "nt"
README = "README.md"
START = "The 60-second start"
NOT_PYTHON = "A repo that is not Python"
LANES = "docs/lanes.md"
CONTAINERS = "Containers"


# --- reading the docs ------------------------------------------------------------

def readme_install() -> str:
    """The first line of the README's 60-second start: `pip install crapkit`."""
    return docsnip.commands(docsnip.fence(README, START))[0]


def install_lines() -> list[str]:
    """The README Install section's second fence: the git tip, then a clone."""
    return docsnip.commands(docsnip.fence(README, "Install", index=1))


HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
SPAN = re.compile(r"`([^`\n]+)`")


def _page(page: str) -> str:
    return (docsnip.root() / page).read_text(encoding="utf-8")


def _prose(text: str) -> list[tuple[int, str]]:
    """(line number, line) for each line of a page outside its ``` fences."""
    kept, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        mark = line.lstrip().startswith("```")
        fenced = fenced != mark
        if not (fenced or mark):
            kept.append((number, line))
    return kept


def _headings(text: str) -> list[tuple[int, int, str]]:
    """(line number, level, title) for each markdown heading outside a fence."""
    return [(number, len(match[1]), match[2]) for number, line in _prose(text) if (match := HEADING.match(line))]


def _end(heads: list[tuple[int, int, str]], at: int, total: int) -> int:
    """The line of the next heading at or above heads[at]'s level, else past the end."""
    after = [head[0] for head in heads[at + 1:] if head[1] <= heads[at][1]]
    return after[0] if after else total


def _bounds(page: str, heading: str, index: int) -> tuple[int, int]:
    """The line span of the index-th section titled `heading`, subsections included.
    Both quickstarts title their steps alike, so `index` picks the occurrence."""
    text = _page(page)
    heads = _headings(text)
    found = [at for at, head in enumerate(heads) if head[2] == heading]
    if index >= len(found):
        raise docsnip.DocSnipError(f"{page} > {heading}: no section #{index} ({len(found)} found)")
    return heads[found[index]][0], _end(heads, found[index], len(text.splitlines()) + 1)


def section_prose(page: str, heading: str, index: int = 0) -> list[str]:
    """The prose lines of a section and its subsections, fences and headings left out."""
    first, last = _bounds(page, heading, index)
    return [line for number, line in _prose(_page(page)) if first < number < last and not HEADING.match(line)]


def section_fences(page: str, heading: str, index: int = 0) -> list:
    """The fences of a section and its subsections, in page order."""
    first, last = _bounds(page, heading, index)
    return [block for block in docsnip.fences(page) if first < block.line < last]


def spans(page: str, heading: str, index: int = 0) -> list[str]:
    """Every `code span` in a section's prose, in page order."""
    return SPAN.findall(" ".join(section_prose(page, heading, index)))


def inline(page: str, heading: str, prefix: str) -> str:
    """The first `code span` under `heading` that starts with `prefix`."""
    found = [span for span in spans(page, heading) if span.startswith(prefix)]
    if not found:
        raise docsnip.DocSnipError(f"{page} > {heading}: no `{prefix}...` span in its prose")
    return found[0]


def container_rule() -> str:
    """The TOML line docs/lanes.md#containers gives a lane a container may run."""
    blocks = [block for block in docsnip.fences(LANES) if block.heading == CONTAINERS and block.lang == "toml"]
    if not blocks:
        raise docsnip.DocSnipError(f"{LANES} > {CONTAINERS}: no toml fence")
    return blocks[0].text.strip()


# --- the container rule ----------------------------------------------------------

def in_container() -> bool:
    """crapkit's own test (src/crapkit/lanes.py): /.dockerenv or the variable."""
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def _lane_blocks(text: str) -> list[list[str]]:
    """crapkit.toml split before each live `[[lane]]` header."""
    blocks: list[list[str]] = [[]]
    for line in text.splitlines(keepends=True):
        if line.strip() == "[[lane]]":
            blocks.append([])
        blocks[-1].append(line)
    return blocks


def _allowed(block: list[str], rule: str) -> list[str]:
    python = any(line.strip() == 'parser = "coveragepy"' for line in block)
    return [block[0], rule + "\n", *block[1:]] if python and block[0].strip() == "[[lane]]" else block


def allow_containers(repo: Path) -> str:
    """Apply docs/lanes.md#containers to every coverage.py lane; the rule applied."""
    rule = container_rule()
    config = repo / "crapkit.toml"
    blocks = _lane_blocks(config.read_text(encoding="utf-8"))
    config.write_text("".join(line for block in blocks for line in _allowed(block, rule)), encoding="utf-8")
    return rule


def allow_containers_here(repo: Path) -> bool:
    """The kit's side of the container guard for a cell whose scenario is not the
    guard itself: apply the documented rule only where the guard would fire."""
    if in_container():
        allow_containers(repo)
    return in_container()


# --- what an install leaves behind -------------------------------------------------

def exe(name: str) -> str:
    return name + ".exe" if WINDOWS else name


def scripts(venv: Path) -> Path:
    return venv / ("Scripts" if WINDOWS else "bin")


@dataclass
class Install:
    channel: str
    argv: list[str]
    launcher: Path | None
    interpreter: str
    path_entry: Path | None
    steps: list = field(default_factory=list)

    def run(self, box, cwd, *args: str, expect: int | None = 0, env: dict | None = None):
        """crapkit, started the way this channel starts it."""
        return box.run([*self.argv, *args], cwd=cwd, expect=expect, env=env)

    @property
    def output(self) -> str:
        return "\n".join(step.stdout + step.stderr for step in self.steps)


def interpreter_of(box, python: str | Path) -> str:
    return box.run([str(python), "-c", "import sys; print(sys.executable)"], expect=0).stdout.strip()


def _venv(box, python: str, name: str) -> Path:
    venv = box.root / name
    box.run([box.toolchain.python(python), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(scripts(venv))
    return venv


def pip_venv(box, python: str = "3.12", *, line: str | None = None, name: str = "venv",
             channel: str = "pip venv", expect: int | None = 0, cwd=None) -> Install:
    """A venv on the toolchain's `python`, activated (its scripts first on PATH),
    then the README's `pip install crapkit` line, or `line`, run in `cwd`."""
    venv = _venv(box, python, name)
    step = box.script(line or readme_install(), expect=expect, cwd=cwd)
    launcher = scripts(venv) / exe("crapkit")
    return Install(channel, [str(launcher)], launcher, interpreter_of(box, scripts(venv) / exe("python")),
                   scripts(venv), [step])


def pip_extra(box, python: str = "3.12", *, heading: str = "Install", expect: int | None = 0) -> Install:
    """`pip install "crapkit[py]"`, as the README's prose gives it."""
    line = inline(README, heading, 'pip install "crapkit[py]"')
    return pip_venv(box, python, line=line, channel="pip [py]", expect=expect)


def system_pip(box, line: str | None = None):
    """The README line run by the system python's own pip, no venv: the step."""
    return box.script(line or readme_install(), note="system pip: the README install line, no venv")


def _pipx_bin(box) -> Path:
    return Path(box.run(["pipx", "environment", "--value", "PIPX_BIN_DIR"], expect=0).stdout.strip())


def pipx(box) -> Install:
    """`pipx install crapkit` from the README's 'A repo that is not Python'."""
    step = box.script(inline(README, NOT_PYTHON, "pipx install"), expect=0)
    bindir = _pipx_bin(box)
    box.prepend_path(bindir)
    launcher = bindir / exe("crapkit")
    return Install("pipx", [str(launcher)], launcher, _shebang_python(launcher), bindir, [step])


def _shebang_python(launcher: Path) -> str:
    """The interpreter a POSIX console script names; Windows keeps it in the exe."""
    if WINDOWS:
        return ""
    first = Path(os.path.realpath(launcher)).read_text(encoding="utf-8", errors="replace").splitlines()[0]
    return first[2:].strip()


def uv_tool(box) -> Install:
    """`uv tool install crapkit` from the same README paragraph."""
    step = box.script(inline(README, NOT_PYTHON, "uv tool install"), expect=0)
    bindir = Path(box.run(["uv", "tool", "dir", "--bin"], expect=0).stdout.strip())
    box.prepend_path(bindir)
    launcher = bindir / exe("crapkit")
    return Install("uv tool", [str(launcher)], launcher, _shebang_python(launcher), bindir, [step])


def uvx_prefix() -> list[str]:
    """`uvx crapkit`, the prefix of every command in 'A repo that is not Python'."""
    first = docsnip.commands(docsnip.fence(README, NOT_PYTHON))[0]
    return first.split()[:2]


def uvx(box, env: dict | None = None) -> Install:
    """uvx installs nothing a user keeps: each command fetches into uv's cache."""
    step = box.run([*uvx_prefix(), "--version"], env=env, expect=0, note="uvx: the first run fills the cache")
    return Install("uvx", uvx_prefix(), None, "", None, [step])


def pip_git(box, python: str = "3.12") -> Install:
    """The README's `pip install git+https://...` line, which gitmirror answers."""
    return pip_venv(box, python, line=install_lines()[0], channel="pip git")


def pip_local(box, clone: Path, python: str = "3.12") -> Install:
    """The README's `pip install .`, run at the clone root as the README says."""
    venv = _venv(box, python, "venv")
    step = box.script(install_lines()[1], cwd=clone, expect=0)
    launcher = scripts(venv) / exe("crapkit")
    return Install("pip .", [str(launcher)], launcher, str(scripts(venv) / exe("python")), scripts(venv), [step])


def development_lines() -> list[str]:
    """The README's Development fence: the editable install, the hooks path, the runner."""
    return docsnip.commands(docsnip.fence(README, "Development"))


def editable(box, clone: Path, python: str = "3.12") -> Install:
    """The README's `pip install -e ".[dev]"`, at the clone root."""
    venv = _venv(box, python, "venv-dev")
    step = box.script(development_lines()[0], cwd=clone, expect=0)
    launcher = scripts(venv) / exe("crapkit")
    return Install("pip -e", [str(launcher)], launcher, str(scripts(venv) / exe("python")), scripts(venv), [step])


def sdist(box, python: str = "3.12") -> Install:
    """The README line with pip's `--no-binary crapkit`: the sdist in find-links is
    built on this machine, as a mirror that carries no wheel makes pip do."""
    return pip_venv(box, python, line=readme_install() + " --no-binary crapkit", channel="pip --no-binary")


def user_scripts(box, python: str) -> Path:
    """Where `pip install --user` puts console scripts for this interpreter."""
    scheme = "nt_user" if WINDOWS else "posix_user"
    code = f"import sysconfig; print(sysconfig.get_path('scripts', '{scheme}'))"
    return Path(box.run([python, "-c", code], expect=0).stdout.strip())


def pip_user(box, python: str) -> Install:
    """The README line through `python -m pip` with pip's `--user`: the docs name
    no user-site command, so the channel is pip's own flag on their line."""
    line = f'"{python}" -m {readme_install()} --user'
    step = box.script(line, note="pip --user: the README line with pip's own --user")
    bindir = user_scripts(box, python)
    launcher = bindir / exe("crapkit")
    return Install("pip --user", [str(launcher)], launcher, python, bindir, [step])


def marker(box, python: str) -> Path:
    """The interpreter's PEP 668 EXTERNALLY-MANAGED file, present or not."""
    code = "import sysconfig; print(sysconfig.get_path('stdlib'))"
    return Path(box.run([python, "-c", code], expect=0).stdout.strip()) / "EXTERNALLY-MANAGED"


def markerless_python(box, minor: str) -> str:
    """A copy of the toolchain's CPython with its PEP 668 marker removed: the
    python.org installer's shape, which marks nothing. uv marks every Python it
    manages, so the toolchain's own copies refuse `pip install --user`."""
    source = Path(box.toolchain.python(minor)).resolve()
    home = source.parent if WINDOWS else source.parent.parent
    copy = box.root / "python-org"
    shutil.copytree(home, copy, symlinks=True)
    python = copy / source.relative_to(home)
    marked = marker(box, str(python))
    if marked.exists():
        marked.unlink()
    return str(python)


def online_env(box) -> dict[str, str]:
    """The sandbox's offline switches turned off, for the network cells: pip reads
    a config with no find-links, uv resolves against PyPI."""
    conf = box.root / "pip-online.conf"
    conf.write_text("[global]\ndisable-pip-version-check = true\n", encoding="utf-8")
    empty = box.root / "no-find-links"
    empty.mkdir(exist_ok=True)
    return {"PIP_CONFIG_FILE": str(conf), "UV_OFFLINE": "false", "UV_NO_INDEX": "false",
            "UV_FIND_LINKS": str(empty)}


# --- running what a user runs -------------------------------------------------------

def commit(box, repo: Path, message: str, *paths: str) -> None:
    box.run(["git", "add", *(paths or ["-A"])], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


# cmd.exe echoes each line of a .cmd script after its prompt: `C:\repo>crapkit init`.
CMD_ECHO = re.compile(r"^[A-Za-z]:\\[^>\n]*>.*\n?", re.M)
# The colors vitest prints through npm whether or not its stdout is a terminal.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def said(step) -> str:
    """What the command printed, stdout then stderr, without cmd.exe's echo of
    the script's own lines, terminal colors and the blank lines around it."""
    text = ANSI.sub("", (step.stdout + step.stderr).replace("\r\n", "\n"))
    return CMD_ECHO.sub("", text).strip("\n")


def lines_starting(step, prefix: str) -> list[str]:
    return [line.strip() for line in said(step).splitlines() if line.strip().startswith(prefix)]


SHAPE = [(re.compile(r"\b[0-9a-f]{11,40}\b"), "<sha>"), (re.compile(r"\d+(\.\d+)?"), "<n>"),
         (re.compile(r"\bgrade [A-F][+-]?"), "grade <g>"), (re.compile(r"\s+"), " ")]


def shape(line: str) -> str:
    """A printed line with its commit ids, numbers and spacing made generic, so a
    fixture's line compares with the one a page quotes from another repo."""
    for pattern, token in SHAPE:
        line = pattern.sub(token, line)
    return line.strip()


def page_outputs(page: str, heading: str, *, index: int = 0, contains: str | None = None) -> dict[str, str]:
    """command -> the output a transcript fence prints under it."""
    return dict(docsnip.outputs(docsnip.fence(page, heading, index=index, contains=contains)))


def fence_commands(page: str, heading: str, *, index: int = 0, contains: str | None = None) -> list[str]:
    return docsnip.commands(docsnip.fence(page, heading, index=index, contains=contains))


# --- the README's 60-second start ------------------------------------------------------

PYCOV_HINT = re.compile(r"cannot import pytest_cov - run `([^`]+)`")
GUARD = "runs the python suite, which is host-only (container runs OOM)"


@dataclass(frozen=True)
class Expect:
    """What the start prints for one fixture repo: the lane init detects and the
    worklist row its over-ceiling function makes."""
    lane: str = "py"
    row: str = r"calc/grade\.py:\d+\s+grade\( score , attempts , late , bonus \)"


def _init(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert f"detected 1 lane(s) from this repo's own files: {want.lane} - next: run `crapkit coverage`" in said(step)
    hint = PYCOV_HINT.search(said(step))
    if hint:
        box.script(hint[1], cwd=repo, expect=0, note="the command init's note names")
    return step


def _doctor(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert "ok   lizard 1.24.0" in step.stdout
    return step


def _coverage(box, repo, line, want: Expect):
    """A coverage.py lane in a container meets the guard first; the user applies
    the rule the refusal names from docs/lanes.md#containers and reruns."""
    if in_container() and want.lane == "py":
        refused = box.script(line, cwd=repo, expect=5)
        assert GUARD in said(refused) and "set container_ok = true" in said(refused)
        box.transcript.note(f"applied docs/lanes.md#containers: {allow_containers(repo)}")
    step = box.script(line, cwd=repo, expect=0)
    assert "-> next: crapkit worklist" in step.stdout
    return step


def _worklist(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert re.search(want.row, step.stdout), step.stdout
    return step


def _seed(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert said(step).startswith("crapkit-ratchet.tsv: added 1, tightened 0")
    return step


def _plain(box, repo, line, want: Expect):
    return box.script(line, cwd=repo, expect=0, env=box.commit_env())


START_STEPS = {"crapkit init": _init, "crapkit doctor": _doctor, "crapkit coverage": _coverage,
               "crapkit worklist": _worklist, "crapkit ratchet seed": _seed}


def _step_rule(line: str):
    return next((rule for prefix, rule in START_STEPS.items() if line.startswith(prefix)), _plain)


def readme_start(box, repo: Path, want: Expect = Expect()) -> dict[str, object]:
    """The README's 60-second start after its install line, then the verify its
    prose says establishes the first passing verdict. `cd your-repo` is the
    cell's cwd."""
    steps = {}
    for line in fence_commands(README, START)[1:]:
        if not line.startswith("cd "):
            steps[line] = _step_rule(line)(box, repo, line, want)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    verify = inline(README, START, "crapkit verify")
    steps[verify] = box.script(verify, cwd=repo, expect=0)
    assert said(steps[verify]).startswith("verify OK")
    return steps
