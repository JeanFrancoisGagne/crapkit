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


def handbook_lines(heading: str) -> list[str]:
    """What a reader types from the handbook's first <pre> under an h3: `$ `
    prompts stripped, comment and blank lines dropped."""
    page = (ROOT / "docs" / "handbook.html").read_text(encoding="utf-8")
    code = re.search(r"<pre><code>(.*?)</code></pre>", page.split(f"<h3>{heading}</h3>", 1)[1], re.S)
    lines = [line.removeprefix("$ ") for line in html.unescape(code.group(1)).splitlines()]
    return [line for line in lines if line.strip() and not line.startswith("#")]


# --- the machine the reader commits on ------------------------------------------

def _script(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def shims(tmp_path: Path, *, crapkit: bool = True, python: bool = False) -> Path:
    """A PATH directory holding `crapkit`, which runs this crapkit, when
    `crapkit`, and `python`: an interpreter that imports crapkit when `python`,
    else a name sh cannot run (127, as sh says it for a missing command)."""
    here, exe = tmp_path / "bin", Path(sys.executable).as_posix()
    here.mkdir()
    if crapkit:
        _script(here, "crapkit", f'exec "{exe}" -m crapkit "$@"')
        (here / "crapkit.cmd").write_text(f'@"{sys.executable}" -m crapkit %*\r\n', encoding="ascii")
    _script(here, "python", f'exec "{exe}" "$@"' if python else 'echo "python: not found" >&2\nexit 127')
    return here


def _git_root() -> Path:
    git = Path(shutil.which("git") or "git")
    return next((up for up in git.parents if (up / "usr" / "bin").is_dir()), git.parent)


def _tool_dirs() -> list[str]:
    """Where git, sh and the coreutils a paste calls live, and nothing else."""
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        return [str(Path(shutil.which("git")).parent), str(_git_root() / "usr" / "bin"),
                str(system / "System32"), str(system)]
    return [str(Path(shutil.which("git")).parent), "/usr/bin", "/bin"]


def machine(tmp_path: Path, bin_dir: Path) -> dict:
    """The environment a paste and its commit run in: `bin_dir` first on a
    PATH of tools, a git identity, and no global git config (a global
    core.hooksPath would move every hook these tests arm)."""
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "CRAPKIT_OVERRIDE_REASON"}
    env.update(IDENTITY, GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"),
               PATH=os.pathsep.join([str(bin_dir), *_tool_dirs()]))
    return env


def sh() -> str:
    """The sh a reader pastes into: Git for Windows' own on Windows."""
    found = shutil.which("sh") if os.name != "nt" else str(_git_root() / "usr" / "bin" / "sh.exe")
    if not found or not Path(found).exists():
        pytest.skip("no sh to paste the block into")
    return found


# --- the repo and the commit ------------------------------------------------------

def run(argv: list[str], cwd: Path, env: dict):
    return hang_guard.run(argv, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace")


def adopted(tmp_path: Path, env: dict) -> Path:
    """A committed repo with a crapkit.toml and one clean module."""
    repo = tmp_path / "repo"
    (repo / "calc").mkdir(parents=True)
    (repo / "calc" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
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
    env = machine(tmp_path, shims(tmp_path, crapkit=False, python=True))
    repo = adopted(tmp_path, env)

    assert_gated(repo, env, paste([sh()], readme_fence(ROUTE_ONE, "sh"), repo, env))


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
    env = machine(tmp_path, shims(tmp_path))
    tree = worktree(adopted(tmp_path, env), env)
    block = readme_fence(ROUTE_ONE, "powershell")

    assert_gated(tree, env, ps_paste(shell, block, tree, env))


@WINDOWS
@pytest.mark.parametrize("shell", powershells() or ["powershell"])
def test_route_two_has_a_powershell_form_that_arms_the_gate(tmp_path, shell):
    """The sh block's heredoc does not parse in PowerShell: the paste stopped
    before any hook was written, and the next commit went through ungated."""
    env = machine(tmp_path, shims(tmp_path))
    repo = adopted(tmp_path, env)
    block = readme_fence(ROUTE_TWO, "powershell")

    assert_gated(repo, env, ps_paste(shell, block, repo, env))


# --- the handbook ------------------------------------------------------------------------

def hook_lines() -> str:
    return "\n".join(line for line in handbook_lines(ENFORCE) if "pre-commit" in line)


def test_the_handbook_hook_lines_arm_the_gate_in_a_linked_worktree(tmp_path):
    env = machine(tmp_path, shims(tmp_path))
    tree = worktree(adopted(tmp_path, env), env)

    assert_gated(tree, env, paste([sh()], hook_lines(), tree, env))


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
    env = machine(tmp_path, shims(tmp_path, python=True))
    repo = shell_repo(tmp_path, env)
    typed(repo, env, [line for line in handbook_lines(BASE) if not line.startswith(("pip ", "cd "))])
    typed(repo, env, handbook_lines(ENFORCE))
    emit, check = (line for line in handbook_lines(CI) if line.startswith("crapkit verify"))
    typed(repo, env, [emit, "git add crapkit-baseline.tsv && git commit -qm baseline"])
    assert run(["git", "clone", "-q", str(repo), str(tmp_path / "ci")], tmp_path, env).returncode == 0

    verdict = run([sh(), "-c", check], tmp_path / "ci", env)

    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
