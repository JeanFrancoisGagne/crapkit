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


def _prose(text: str) -> list[str]:
    """A page's lines outside its ``` fences."""
    kept, fenced = [], False
    for line in text.splitlines():
        mark = line.lstrip().startswith("```")
        fenced = fenced != mark
        if not (fenced or mark):
            kept.append(line)
    return kept


def _section_lines(page: str, heading: str) -> list[str]:
    """The prose lines under `heading`, up to the next heading."""
    lines, inside = [], False
    for line in _prose((docsnip.root() / page).read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if match:
            inside = match[2] == heading
        elif inside:
            lines.append(line)
    return lines


def inline(page: str, heading: str, prefix: str) -> str:
    """The first `code span` under `heading` that starts with `prefix`."""
    text = " ".join(_section_lines(page, heading))
    spans = [span for span in SPAN.findall(text) if span.startswith(prefix)]
    if not spans:
        raise docsnip.DocSnipError(f"{page} > {heading}: no `{prefix}...` span in its prose")
    return spans[0]


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
             channel: str = "pip venv", expect: int | None = 0) -> Install:
    """A venv on the toolchain's `python`, activated (its scripts first on PATH),
    then the README's `pip install crapkit` line, or `line`."""
    venv = _venv(box, python, name)
    step = box.script(line or readme_install(), expect=expect)
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


# --- running what a user runs -------------------------------------------------------

def commit(box, repo: Path, message: str, *paths: str) -> None:
    box.run(["git", "add", *(paths or ["-A"])], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


# cmd.exe echoes each line of a .cmd script after its prompt: `C:\repo>crapkit init`.
CMD_ECHO = re.compile(r"^[A-Za-z]:\\[^>\n]*>.*\n?", re.M)


def said(step) -> str:
    """What the command printed, stdout then stderr, without cmd.exe's echo of
    the script's own lines and the blank lines around the output."""
    text = (step.stdout + step.stderr).replace("\r\n", "\n")
    return CMD_ECHO.sub("", text).strip("\n")


def lines_starting(step, prefix: str) -> list[str]:
    return [line.strip() for line in said(step).splitlines() if line.strip().startswith(prefix)]


SHAPE = [(re.compile(r"\b[0-9a-f]{11,40}\b"), "<sha>"), (re.compile(r"\d+(\.\d+)?"), "<n>"),
         (re.compile(r"\s+"), " ")]


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
