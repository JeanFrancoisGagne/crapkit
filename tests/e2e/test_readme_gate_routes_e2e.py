"""README's gate routes and the handbook's Install blocks, pasted as printed.

Each test lifts a block out of the page verbatim, runs it in a fresh repo the
way a reader's shell runs a paste, stages a function over the ceiling and
commits. The commit has to stop at crapkit's gate: git refuses it and stderr
carries `crapkit gate:`. A hook git cannot run is refused too, so that line is
what tells the gate from a broken hook.

The commit runs on the PATH README's Install section promises the gate: a
`crapkit` command, which is what pipx, uv tool and a venv put there, and no
`python`, which is Debian, Ubuntu and macOS. While the hook body spelled
`exec python -m crapkit hook-precommit`, git refused every commit there with
`exec: python: not found`. Route 1 wrote `.git/hooks/pre-commit`, which does not
exist in a linked worktree, and Route 2 had no PowerShell form: both pastes
armed nothing, and the breach committed.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "calc"\npaths = ["calc"]\n'
          'languages = ["python"]\ncoverage_optional = true\n')
BREACH = ("def route(a, b, c, d):\n    if a:\n        return 1\n    if b:\n        return 2\n"
          "    if c:\n        return 3\n    if d and a:\n        return 4\n    if d or b:\n"
          "        return 5\n    return 6\n")
# A shell function at ccn 8: a cc-only scope with one mark to seed, so the
# handbook's blocks run end to end with no test runner and no lane.
DEPLOY = ('release() {\n    if [ -z "$1" ]; then\n        return 1\n    fi\n    case "$1" in\n'
          '        prod) echo prod ;;\n        stage) echo stage ;;\n        dev) echo dev ;;\n'
          '        qa) echo qa ;;\n        demo) echo demo ;;\n        *) echo other ;;\n'
          '    esac\n}\n')
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
ROUTE_ONE = "### Route 1: `.git/hooks/pre-commit` (local, not committed)"
ROUTE_TWO = "### Route 2: a committed hooks directory"
BASE = "The base: the CLI, then one repo"
ENFORCE = "Enforcement: seed the ratchet, then arm the hook"
CI = "4 · CI on a pull request"
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="the PowerShell forms are the Windows routes")


# --- the pages ----------------------------------------------------------------

def readme_fence(heading: str, lang: str) -> str:
    """The first `lang` fence under a README heading, above the next heading."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = re.split(r"^#{2,3} ", text.split(f"\n{heading}\n", 1)[1], maxsplit=1, flags=re.M)[0]
    found = re.search(rf"^```{lang}\n(.*?)^```$", section, re.M | re.S)
    assert found, f"README {heading!r} holds no {lang} block"
    return found.group(1)


def handbook_pres(heading: str) -> list[str]:
    """Every <pre> under one of the handbook's h3 headings, down to the next one."""
    page = (ROOT / "docs" / "handbook.html").read_text(encoding="utf-8")
    section = page.split(f"<h3>{heading}</h3>", 1)[1].split("<h3", 1)[0]
    return [html.unescape(code) for code in re.findall(r"<pre><code>(.*?)</code></pre>", section, re.S)]


def handbook_callout(heading: str) -> str:
    """The text of the first callout under one of the handbook's h3 headings,
    tags dropped, as a reader sees it."""
    page = (ROOT / "docs" / "handbook.html").read_text(encoding="utf-8")
    section = page.split(f"<h3>{heading}</h3>", 1)[1].split("<h3", 1)[0]
    callout = re.search(r'<div class="callout[^"]*">(.*?)</div>', section, re.S).group(1)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", callout)).split())


def handbook_lines(heading: str) -> list[str]:
    """What a reader types from the first <pre> under an h3: `$ ` prompts
    stripped, comment and blank lines dropped."""
    lines = [line.removeprefix("$ ") for line in handbook_pres(heading)[0].splitlines()]
    return [line for line in lines if line.strip() and not line.startswith("#")]


# --- the machine the reader commits on ------------------------------------------

def _script(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


# The `python` a reader's PATH holds: none (a name sh cannot run, 127, as sh
# says it for a missing command), one that imports this crapkit, or a system
# python that does not (-I drops PYTHONPATH, -S drops site-packages).
PYTHONS = {"missing": 'echo "python: not found" >&2\nexit 127',
           "crapkit": 'exec "{exe}" "$@"',
           "bare": 'exec "{exe}" -I -S "$@"'}


def shims(tmp_path: Path, *, crapkit: bool = True, python: str = "missing", uvx: bool = False) -> Path:
    """A PATH directory holding `crapkit`, which runs this crapkit, when
    `crapkit`, the `python` PYTHONS names, and with `uvx` a uvx that runs this
    crapkit for `uvx crapkit ARGS`, the way uv runs the release it fetched."""
    here, exe = tmp_path / "bin", Path(sys.executable).as_posix()
    here.mkdir()
    if crapkit:
        _script(here, "crapkit", f'exec "{exe}" -m crapkit "$@"')
        (here / "crapkit.cmd").write_text(f'@"{sys.executable}" -m crapkit %*\r\n', encoding="ascii")
    if uvx:
        _script(here, "uvx", f'[ "$1" = crapkit ] || exit 2\nshift\nexec "{exe}" -m crapkit "$@"')
    _script(here, "python", PYTHONS[python].format(exe=exe))
    return here


def _git_root() -> Path:
    git = Path(shutil.which("git") or "git")
    return next((up for up in git.parents if (up / "usr" / "bin").is_dir()), git.parent)


def _git_dir() -> str:
    """Git's cmd directory on Windows, the one the Git for Windows installer puts
    on PATH by default. Its git.exe finds the sh a hook needs; the
    mingw64/bin/git.exe a Git Bash PATH leads to does not, once usr/bin is gone."""
    cmd = _git_root() / "cmd"
    return str(cmd) if os.name == "nt" and cmd.is_dir() else str(Path(shutil.which("git")).parent)


def _tool_dirs(posix: bool) -> list[str]:
    """Where git lives, and with `posix` the coreutils a pasted sh block calls.
    A PowerShell reader on Windows has Git's cmd directory alone: no printf,
    no chmod."""
    git = _git_dir()
    if os.name != "nt":
        return [git, "/usr/bin", "/bin"]
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    usr = [str(_git_root() / "usr" / "bin")] if posix else []
    return [git, *usr, str(system / "System32"), str(system)]


def machine(tmp_path: Path, bin_dir: Path, *, posix: bool = True) -> dict:
    """The environment a paste and its commit run in: `bin_dir` first on a
    PATH of tools, a git identity, and no global git config (a global
    core.hooksPath would move every hook these tests arm)."""
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "CRAPKIT_OVERRIDE_REASON"}
    env.update(IDENTITY, GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"),
               PATH=os.pathsep.join([str(bin_dir), *_tool_dirs(posix)]))
    return env


def sh() -> str:
    """The sh a reader pastes into: Git for Windows' own on Windows."""
    found = shutil.which("sh") if os.name != "nt" else str(_git_root() / "usr" / "bin" / "sh.exe")
    if not found or not Path(found).exists():
        pytest.skip("no sh to paste the block into")
    return found


# --- the repo and the commit ------------------------------------------------------

def run(argv: list[str], cwd: Path, env: dict):
    """argv under `env`, its first word found on env's PATH the way the reader's
    shell finds it. Windows looks a bare name up on the parent's PATH instead."""
    first = shutil.which(argv[0], path=env["PATH"]) or argv[0]
    return hang_guard.run([first, *argv[1:]], cwd=cwd, env=env, text=True, encoding="utf-8",
                          errors="replace")


def adopted(tmp_path: Path, env: dict) -> Path:
    """A committed repo with a crapkit.toml, the ignore line init writes and
    one clean module."""
    repo = tmp_path / "repo"
    (repo / "calc").mkdir(parents=True)
    (repo / "calc" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    for argv in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-qm", "adopt"]):
        assert run(argv, repo, env).returncode == 0
    return repo


def worktree(repo: Path, env: dict) -> Path:
    """A linked worktree of `repo`, where `.git` is a file."""
    tree = repo.parent / "linked"
    assert run(["git", "worktree", "add", "-q", "-b", "work", str(tree)], repo, env).returncode == 0
    assert (tree / ".git").is_file()
    return tree


def paste(argv: list[str], block: str, cwd: Path, env: dict, suffix: str = ".sh"):
    """The block run as one paste, from a file the way a shell reads stdin."""
    script = cwd.parent / f"paste-{cwd.name}{suffix}"
    script.write_text(block, encoding="utf-8", newline="\n")
    return run([*argv, str(script)], cwd, env)


def commit_breach(repo: Path, env: dict):
    (repo / "calc" / "route.py").write_text(BREACH, encoding="utf-8")
    assert run(["git", "add", "-A"], repo, env).returncode == 0
    return run(["git", "commit", "-qm", "add route"], repo, env)


def assert_gated(repo: Path, env: dict, pasted) -> None:
    committed = commit_breach(repo, env)

    assert committed.returncode != 0, f"the breach committed; the paste said: {pasted.stderr}"
    assert "crapkit gate:" in committed.stderr, committed.stderr


# --- Route 1 and Route 2, sh ---------------------------------------------------------

def test_route_one_gates_a_commit_where_crapkit_is_on_path_and_python_is_not(tmp_path):
    env = machine(tmp_path, shims(tmp_path))
    repo = adopted(tmp_path, env)

    assert_gated(repo, env, paste([sh()], readme_fence(ROUTE_ONE, "sh"), repo, env))


def test_route_one_pasted_in_a_linked_worktree_arms_the_gate(tmp_path):
    """`.git` is a file there, and `cat > .git/hooks/pre-commit` said
    `Directory nonexistent`, wrote nothing, and let the breach through."""
    env = machine(tmp_path, shims(tmp_path))
    tree = worktree(adopted(tmp_path, env), env)

    assert_gated(tree, env, paste([sh()], readme_fence(ROUTE_ONE, "sh"), tree, env))


def test_the_hook_body_falls_back_to_python_m_crapkit(tmp_path):
    """A venv whose console script is not on the hook's PATH still has its
    python: the second line of the body is what runs the gate then."""
    env = machine(tmp_path, shims(tmp_path, crapkit=False, python="crapkit"))
    repo = adopted(tmp_path, env)

    assert_gated(repo, env, paste([sh()], readme_fence(ROUTE_ONE, "sh"), repo, env))


def readme_exit_code(said: str) -> int:
    """The exit code README's gate section gives, above Route 1, for the hook
    that prints `said`."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    intro = " ".join(text.split("\n## The gate\n", 1)[1].split(f"\n{ROUTE_ONE}\n", 1)[0].split())
    sentences = [sentence for sentence in re.split(r"(?<=\.)\s+", intro) if said in sentence]
    assert len(sentences) == 1, f"README's gate section names {said!r} in {len(sentences)} sentences"
    stated = re.search(r"\bexits (\d+)\b", sentences[0])
    assert stated, sentences[0]
    return int(stated.group(1))


@pytest.mark.parametrize("python, said", [("missing", "python: not found"),
                                          ("bare", "No module named crapkit")])
def test_the_pages_name_what_a_hook_that_reaches_no_crapkit_prints(tmp_path, python, said):
    """README said the hook exits 127 whenever its PATH holds neither a
    `crapkit` nor a `python` that imports it. That holds with no python at all.
    A system python without crapkit runs, prints `No module named crapkit` and
    exits 1. Git refuses the commit both ways, with that line on stderr, and
    the handbook's callout names it for the reader who sees it."""
    env = machine(tmp_path, shims(tmp_path, crapkit=False, python=python))
    repo = adopted(tmp_path, env)
    paste([sh()], readme_fence(ROUTE_ONE, "sh"), repo, env)

    committed = commit_breach(repo, env)
    hook = run([sh(), ".git/hooks/pre-commit"], repo, env)

    assert committed.returncode != 0 and said in committed.stderr, committed.stderr
    assert hook.returncode == readme_exit_code(said), hook.stderr
    assert said in handbook_callout(ENFORCE), "the handbook names the line the reader sees"


def test_the_hook_body_reaches_the_gate_through_uvx(tmp_path):
    """uvx puts no `crapkit` on PATH. On a machine with uv and no `python`, the
    uvx-only start README gives a repo that is not Python, the body's uvx line
    is what runs the gate."""
    env = machine(tmp_path, shims(tmp_path, crapkit=False, uvx=True))
    repo = adopted(tmp_path, env)

    assert_gated(repo, env, paste([sh()], readme_fence(ROUTE_ONE, "sh"), repo, env))


REMOVAL_PAGES = [("README.md", "### Removing crapkit"), ("docs/upgrading.md", "## Removing crapkit"),
                 ("docs/handbook.html", "Removing crapkit")]


def removal_prose(page: str, heading: str) -> str:
    """The text under a removal heading, down to the next heading, as a reader
    reads it: fences cut out, tags dropped with <code> read as backticks."""
    text = (ROOT / page).read_text(encoding="utf-8")
    if page.endswith(".html"):
        body = text.split(f"<h3>{heading}</h3>", 1)[1].split("<h3", 1)[0]
        return " ".join(html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</?code>", "`", body))).split())
    body = re.split(r"^#{1,6} ", text.split(f"\n{heading}\n", 1)[1], maxsplit=1, flags=re.M)[0]
    return " ".join(re.sub(r"^```.*?^```", "", body, flags=re.M | re.S).split())


def removal_claim(page: str, heading: str) -> str:
    """The sentences under a removal heading that say what `pip uninstall
    crapkit` alone leaves."""
    sentences = re.split(r"(?<=\.)\s+", removal_prose(page, heading))
    return " ".join(sentence for sentence in sentences if "pip uninstall crapkit" in sentence)


@pytest.mark.parametrize("page, heading", REMOVAL_PAGES)
@pytest.mark.parametrize("uv, said, named", [(True, "crapkit gate:", "`uvx crapkit`"),
                                             (False, "No module named crapkit", "`No module named crapkit`")],
                         ids=["uv", "no-uv"])
def test_the_removal_text_says_what_the_hook_does_once_the_package_is_gone(tmp_path, page, heading, uv, said,
                                                                          named):
    """After `pip uninstall crapkit` the hook finds no `crapkit` command and a
    python that no longer imports it. Where uv is installed, the body's uvx
    line fetches crapkit and the gate keeps judging every commit; without uv,
    every commit stops on `No module named crapkit`. The removal text said
    every commit stops, which a team with uv never saw."""
    env = machine(tmp_path, shims(tmp_path, crapkit=False, python="bare", uvx=uv))
    repo = adopted(tmp_path, env)
    paste([sh()], readme_fence(ROUTE_ONE, "sh"), repo, env)

    committed = commit_breach(repo, env)

    assert committed.returncode != 0 and said in committed.stderr, committed.stderr
    assert named in removal_claim(page, heading), removal_claim(page, heading)


def test_route_two_gates_a_commit_where_crapkit_is_on_path_and_python_is_not(tmp_path):
    env = machine(tmp_path, shims(tmp_path))
    repo = adopted(tmp_path, env)

    assert_gated(repo, env, paste([sh()], readme_fence(ROUTE_TWO, "sh"), repo, env))


# --- Route 1 and Route 2, PowerShell ----------------------------------------------------

def powershells() -> list[str]:
    return [shell for shell in ("powershell", "pwsh") if shutil.which(shell)]


def ps_paste(shell: str, block: str, cwd: Path, env: dict):
    return paste([shutil.which(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
                 block, cwd, env, ".ps1")


@WINDOWS
@pytest.mark.parametrize("shell", powershells() or ["powershell"])
def test_route_one_powershell_form_arms_the_gate_in_a_linked_worktree(tmp_path, shell):
    env = machine(tmp_path, shims(tmp_path), posix=False)
    tree = worktree(adopted(tmp_path, env), env)
    block = readme_fence(ROUTE_ONE, "powershell")

    assert_gated(tree, env, ps_paste(shell, block, tree, env))


@WINDOWS
@pytest.mark.parametrize("shell", powershells() or ["powershell"])
def test_route_two_has_a_powershell_form_that_arms_the_gate(tmp_path, shell):
    """The sh block's heredoc does not parse in PowerShell: the paste stopped
    before any hook was written, and the next commit went through ungated."""
    env = machine(tmp_path, shims(tmp_path), posix=False)
    repo = adopted(tmp_path, env)
    block = readme_fence(ROUTE_TWO, "powershell")

    assert_gated(repo, env, ps_paste(shell, block, repo, env))


def launcher_hooks() -> dict[str, str]:
    """The PowerShell blocks that write the launcher's own path into the hook:
    README Route 1's, and the two lines of the handbook's Enforcement form that do."""
    form = next(pre for pre in handbook_pres(ENFORCE) if "Set-Content" in pre)
    return {"README": readme_fence(ROUTE_ONE, "powershell"),
            "handbook": "\n".join(line for line in form.splitlines() if "$crapkit" in line)}


@WINDOWS
@pytest.mark.parametrize("page, heading", REMOVAL_PAGES)
@pytest.mark.parametrize("form", ["README", "handbook"])
def test_the_removal_text_says_what_a_powershell_hook_does_once_the_launcher_is_gone(tmp_path, form, page,
                                                                                   heading):
    """The PowerShell forms write the launcher's own path, and `pip uninstall
    crapkit` deletes that launcher. Git's sh then stops every commit on
    `No such file or directory`, with uv installed or not: this hook has no
    uvx line. The removal text named only the sh hook's two outcomes, so a
    PowerShell reader with uv expected the gate to keep judging."""
    bin_dir = shims(tmp_path, python="bare", uvx=True)
    env = machine(tmp_path, bin_dir, posix=False)
    repo = adopted(tmp_path, env)
    armed = ps_paste("powershell", launcher_hooks()[form], repo, env)
    assert armed.returncode == 0, armed.stderr
    for launcher in bin_dir.glob("crapkit*"):
        launcher.unlink()

    committed = commit_breach(repo, env)

    assert committed.returncode != 0 and "No such file or directory" in committed.stderr, committed.stderr
    assert "crapkit gate:" not in committed.stderr, committed.stderr
    assert "`No such file or directory`" in removal_claim(page, heading), removal_claim(page, heading)


# --- the handbook ------------------------------------------------------------------------

def hook_lines() -> str:
    return "\n".join(line for line in handbook_lines(ENFORCE) if "pre-commit" in line)


def test_the_handbook_hook_lines_arm_the_gate_in_a_linked_worktree(tmp_path):
    env = machine(tmp_path, shims(tmp_path))
    tree = worktree(adopted(tmp_path, env), env)

    assert_gated(tree, env, paste([sh()], hook_lines(), tree, env))


@WINDOWS
@pytest.mark.parametrize("shell", powershells() or ["powershell"])
def test_the_handbook_enforcement_block_has_a_powershell_form_that_arms_the_gate(tmp_path, shell):
    """The block was sh alone. Pasted into PowerShell, `printf` is no command
    and 5.1 cannot parse `&&`, so no hook was written and the breach committed.
    A PowerShell reader pastes the section's PowerShell form, or the only block
    there is."""
    env = machine(tmp_path, shims(tmp_path), posix=False)
    repo = adopted(tmp_path, env)
    assert run([sys.executable, "-m", "crapkit", "coverage"], repo, env).returncode == 0
    blocks = handbook_pres(ENFORCE)
    form = next((pre for pre in blocks if "Set-Content" in pre), blocks[0])

    assert_gated(repo, env, ps_paste(shell, form, repo, env))


def shell_repo(tmp_path: Path, env: dict) -> Path:
    """A committed repo with one shell function over the ceiling and no
    crapkit files yet."""
    repo = tmp_path / "repo"
    (repo / "ops").mkdir(parents=True)
    (repo / "ops" / "deploy.sh").write_text(DEPLOY, encoding="utf-8", newline="\n")
    for argv in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-qm", "ops"]):
        assert run(argv, repo, env).returncode == 0
    return repo


def typed(repo: Path, env: dict, lines: list[str]) -> None:
    """Each line the way a reader types it, one at a time, each one exiting 0."""
    for line in lines:
        done = run([sh(), "-c", line], repo, env)
        assert done.returncode == 0, f"{line}\n{done.stdout}{done.stderr}"


def test_the_handbook_blocks_alone_give_a_fresh_ci_clone_its_config(tmp_path):
    """Workflow 4's pull request job runs `verify --baseline-tsv` in a fresh
    clone. The Enforcement block committed only crapkit-ratchet.tsv, so that
    clone had no crapkit.toml and verify stopped at `no crapkit.toml` (exit 3)
    before any verdict."""
    env = machine(tmp_path, shims(tmp_path, python="crapkit"))
    repo = shell_repo(tmp_path, env)
    typed(repo, env, [line for line in handbook_lines(BASE) if not line.startswith(("pip ", "cd "))])
    typed(repo, env, handbook_lines(ENFORCE))
    emit, check = (line for line in handbook_lines(CI) if line.startswith("crapkit verify"))
    typed(repo, env, [emit, "git add crapkit-baseline.tsv && git commit -qm baseline"])
    assert run(["git", "clone", "-q", str(repo), str(tmp_path / "ci")], tmp_path, env).returncode == 0

    verdict = run([sh(), "-c", check], tmp_path / "ci", env)

    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
