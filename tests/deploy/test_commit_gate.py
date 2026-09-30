"""The commit gate, armed the way the README's routes arm it.

Each cell pastes a route's block into the shell it was written for, then
commits: a function over the ceiling must be refused with the block README
"What a refusal looks like" prints, and the decomposed version accepted. A
gate that git never runs is the failure these cells exist for, so the silent
cases (a clone that skipped the hooks path, a global core.hooksPath, husky
taking the hooks path back) end in what the user can learn from doctor.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kit import docsnip, gitmirror, gitsurf, repos, wheels
from kit.cells import cell

PACKET = "deploy-git"
UPGRADE_DOC = "docs/upgrading.md"
OLD_HOOKS = "0.4.0"


def adopted(box, templates, name: str = "py-pytest") -> Path:
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, name, cache=templates)
    gitsurf.adopt(box, repo)
    return repo


def gate_or_doctor(box, repo: Path, *words: str) -> None:
    """The user learns the gate is off: git refuses the breach, or doctor names it."""
    gitsurf.breach(repo)
    step = gitsurf.commit(box, repo)
    if step.exit != 0:
        return gitsurf.assert_refused(step)
    report = gitsurf.doctor(box, repo)
    assert gitsurf.names(report, *words), f"the breach committed and doctor named nothing:\n{report.stdout}"


# --- Route 1 and Route 2 ----------------------------------------------------------

@cell("lin-gate-route1-sh", channel="Route 1 sh", harness="git 2.47",
      scenario="fresh: README heredoc; commit refused with 'What a refusal looks like' text, then accepted",
      use_cases="commit gate", os="linux", image="cells", cadence="push")
def test_route1_heredoc_refuses_a_breach_then_accepts_its_split(box, templates):
    repo = adopted(box, templates)
    gitsurf.route1(box, repo)

    gitsurf.refused_then_accepted(box, repo)


@cell("lin-gate-route2", channel="Route 2", harness="git 2.47",
      scenario="fresh: README steps; mode 100755; a second clone without hooksPath commits ungated and doctor names it",
      use_cases="commit gate, doctor", os="linux", image="cells", cadence="push")
def test_route2_commits_an_executable_hook_that_refuses_then_accepts(box, templates):
    repo = adopted(box, templates)
    gitsurf.route2(box, repo)

    assert gitsurf.head_mode(box, repo, "githooks/pre-commit") == "100755"
    assert not gitsurf.names(gitsurf.doctor(box, repo), "githooks")
    gitsurf.refused_then_accepted(box, repo)


@cell("lin-gate-route2", channel="Route 2", harness="git 2.47",
      scenario="fresh: README steps; mode 100755; a second clone without hooksPath commits ungated and doctor names it",
      use_cases="commit gate, doctor", os="linux", image="cells", cadence="push")
def test_route2_a_clone_that_skipped_the_hooks_path_hears_it_from_doctor(box, templates):
    repo = adopted(box, templates)
    gitsurf.route2(box, repo)
    clone = box.root / "teammate"
    box.run(["git", "clone", "-q", str(repo), str(clone)], expect=0)

    gate_or_doctor(box, clone, "core.hooksPath", "githooks")


# --- Route 1 from PowerShell ---------------------------------------------------------

def powershell_route1(box, templates, shell: str) -> None:
    repo = adopted(box, templates)
    gitsurf.route1(box, repo, shell=shell)
    gitsurf.refused_then_accepted(box, repo)
    out_file_variant(box, repo, shell)


def out_file_variant(box, repo: Path, shell: str) -> None:
    """The same hook written with Out-File, the form the README warns about:
    when the file opens with a byte-order mark git cannot spawn it, and doctor
    says so; when it does not, the gate still refuses."""
    block = docsnip.fence(gitsurf.README, gitsurf.ROUTE1, index=1).text
    box.script(out_file_script(block), shell=shell, cwd=repo, expect=0)
    gitsurf.breach(repo)
    if (repo / ".git/hooks/pre-commit").read_bytes()[:2] in (b"\xff\xfe", b"\xef\xbb"):
        return bom_refusal(box, repo)
    gitsurf.assert_refused(gitsurf.commit(box, repo))


def out_file_script(block: str) -> str:
    """The README's PowerShell block with its last line, the Set-Content write,
    turned into Out-File to the same path. Every line before it runs as printed."""
    *setup, write = block.splitlines()
    path, value = re.search(r'-Path (\S+) .*-Value (".*")$', write).groups()
    return "\n".join([*setup, f"{value} | Out-File {path}", ""])


@pytest.mark.kit
def test_the_out_file_variant_runs_the_readme_block_with_only_its_write_changed():
    """The PowerShell block of Route 1 finds the hook's path in its own line
    before the Set-Content line. Read at fixed line numbers, the variant took
    that line for the write, and win-gate-route1-pwsh and -ps51 stopped on a
    TypeError before writing any hook."""
    block = docsnip.fence(gitsurf.README, gitsurf.ROUTE1, index=1).text.splitlines()
    script = out_file_script("\n".join(block)).splitlines()

    assert script[:-1] == block[:-1]
    assert re.fullmatch(r'".*" \| Out-File \S+', script[-1]), script[-1]
    assert script[-1].split()[-1] == re.search(r"-Path (\S+)", block[-1])[1]


def bom_refusal(box, repo: Path) -> None:
    step = gitsurf.commit(box, repo)
    assert "error: cannot spawn .git/hooks/pre-commit" in step.stderr, step.stderr
    assert gitsurf.names(gitsurf.doctor(box, repo), ".git/hooks/pre-commit", "byte-order mark")


@cell("win-gate-route1-ps51", channel="Route 1 PowerShell", harness="PortableGit, powershell.exe 5.1",
      scenario="fresh: README block under powershell.exe 5.1; refused then accepted; BOM variant WARN",
      use_cases="commit gate, doctor", os="windows", image=None, cadence="push")
def test_route1_powershell_51_block_refuses_then_accepts(box, templates):
    powershell_route1(box, templates, "powershell")


@cell("win-gate-route1-pwsh", channel="Route 1 PowerShell", harness="PortableGit, pwsh 7",
      scenario="fresh: README block under pwsh; refused then accepted; BOM variant WARN",
      use_cases="commit gate, doctor", os="windows", image=None, cadence="push")
def test_route1_pwsh_block_refuses_then_accepts(box, templates):
    powershell_route1(box, templates, "pwsh")


# --- hooks paths the route does not own -----------------------------------------------

@cell("lin-global-hookspath", channel="Route 1 with global core.hooksPath", harness="git 2.47",
      scenario="fresh: silent skip; does doctor warn", use_cases="commit gate, doctor", os="linux",
      image="cells", cadence="nightly")
def test_route1_under_a_global_hooks_path_is_refused_or_named(box, templates):
    repo = adopted(box, templates)
    box.run(["git", "config", "--global", "core.hooksPath", str(box.home / ".githooks")], expect=0)
    gitsurf.route1(box, repo)

    gate_or_doctor(box, repo, "core.hooksPath")


def npm_fixture(box, name: str) -> str:
    package = Path(box.toolchain["npm_fixtures"]) / "package.json"
    return json.loads(package.read_text(encoding="utf-8"))["devDependencies"][name]


def husky(box, repo: Path) -> None:
    """husky's own setup: install it, `husky init`, commit what it wrote."""
    package = {"name": "calc", "private": True, "scripts": {"test": "python -m pytest -q"}}
    (repo / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text((repo / ".gitignore").read_text(encoding="utf-8") + "node_modules/\n",
                                     encoding="utf-8", newline="\n")
    box.run(["npm", "install", "--offline", "--save-dev", f"husky@{npm_fixture(box, 'husky')}"], cwd=repo, expect=0)
    box.run(["npx", "--offline", "husky", "init"], cwd=repo, expect=0)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "husky"], cwd=repo, env=box.commit_env(), expect=0)


@cell("lin-husky", channel="Route 2 in a husky repo", harness="husky 9 + git",
      scenario="fresh: README Route 2 after husky owns core.hooksPath", use_cases="commit gate", os="linux",
      image="cells", cadence="nightly")
def test_route2_in_a_husky_repo_survives_npm_install_or_is_named(box, templates):
    repo = adopted(box, templates)
    husky(box, repo)
    gitsurf.route2(box, repo)
    box.run(["npm", "install", "--offline"], cwd=repo, expect=0)

    gate_or_doctor(box, repo, "core.hooksPath")


# --- hooks written under 0.4.0 ----------------------------------------------------------

def old_readme(box, version: str) -> Path:
    """The README a user of `version` read, from the release tag on the mirror."""
    mirror = gitmirror.make(box)
    folder = box.root / f"readme-{version}"
    folder.mkdir()
    text = box.run(["git", f"--git-dir={mirror.path}", "show", f"v{version}:README.md"], expect=0).stdout
    (folder / "README.md").write_text(text, encoding="utf-8", newline="\n")
    return folder


def old_route(box, repo: Path, base: Path, heading: str) -> bytes:
    box.script(docsnip.fence("README.md", heading, base=base).text, shell="sh", cwd=repo, env=box.commit_env(),
               expect=0)
    hooks = box.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=repo, expect=0).stdout.strip()
    return (repo / hooks).read_bytes()


def old_adoption(box, templates, base: Path, heading: str) -> tuple[Path, bytes]:
    repo = repos.checkout(box, "py-pytest", cache=templates, repo_name=f"repo-{heading.split(':')[0]}")
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    return repo, old_route(box, repo, base, heading)


@cell("lin-up-hooks-0.4.0", channel="Routes 1,2 installed under 0.4.0", harness="git 2.47",
      scenario="upgrade: scripts untouched; candidate rules gate", use_cases="commit gate", os="linux",
      image="cells", cadence="nightly")
def test_hooks_written_under_0_4_0_gate_with_the_candidate_after_upgrade(box, templates, candidate):
    assert OLD_HOOKS in wheels.releases()
    gitsurf.pip_venv(box, install=f"pip install crapkit=={OLD_HOOKS}")
    assert OLD_HOOKS in box.run(["crapkit", "--version"], expect=0).stdout
    base = old_readme(box, OLD_HOOKS)
    routes = [old_adoption(box, templates, base, gitsurf.ROUTE1), old_adoption(box, templates, base, gitsurf.ROUTE2)]
    box.script(gitsurf.inline(UPGRADE_DOC, "Upgrading crapkit", "python -m pip install --upgrade crapkit"), expect=0)

    assert candidate.version in box.run(["crapkit", "--version"], expect=0).stdout
    for repo, hook in routes:
        hooks = box.run(["git", "rev-parse", "--git-path", "hooks/pre-commit"], cwd=repo, expect=0).stdout.strip()
        assert (repo / hooks).read_bytes() == hook
        gitsurf.refused_then_accepted(box, repo)
