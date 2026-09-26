"""The gate through the pre-commit framework (Route 3) and prek.

The README's `repo:` line names GitHub; the sandbox's insteadOf rules send it
to the mirror, where the candidate is the newest release tag, so the config
runs as printed with no network. pre-commit builds the hook's venv through the
sandbox pip.conf, from the wheelhouse and the candidate's dist.
"""
from __future__ import annotations

from pathlib import Path

from kit import docsnip, gitmirror, gitsurf, repos, wheels
from kit.cells import cell

PACKET = "deploy-git"
UPGRADE_DOC = "docs/upgrading.md"
OLD = "0.7.6"


def framework_repo(box, templates, candidate, *, guard: bool = False) -> Path:
    """A repo adopted from the README, with Route 3's config committed and the
    mirror's newest tag at the candidate."""
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo, guard=guard)
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    assert gitsurf.precommit_config(box, repo) == f"v{candidate.version}"
    return repo


def precommit_cell(box, templates, candidate, guard: bool) -> None:
    repo = framework_repo(box, templates, candidate, guard=guard)
    gitsurf.precommit_install(box, repo)

    gitsurf.refused_then_accepted(box, repo)
    assert gitsurf.hook_env_version(box) == f"crapkit {candidate.version}"


@cell("lin-precommit", channel="pre-commit", harness="pre-commit 4.6.2",
      scenario="fresh: README block via mirror; refused then accepted; no container_ok, refusal asserted",
      use_cases="commit gate", os="linux", image="cells", cadence="nightly")
def test_precommit_config_from_the_readme_refuses_then_accepts(box, templates, candidate):
    precommit_cell(box, templates, candidate, guard=True)


@cell("win-precommit", channel="pre-commit", harness="pre-commit 4.6.2, PortableGit",
      scenario="fresh: refused then accepted", use_cases="commit gate", os="windows", image=None, cadence="nightly")
def test_precommit_on_windows_refuses_then_accepts(box, templates, candidate):
    precommit_cell(box, templates, candidate, guard=False)


@cell("lin-precommit-all-files", channel="pre-commit run --all-files", harness="pre-commit 4.6.2",
      scenario="fresh: breach on a clean index; what CI reports", use_cases="commit gate", os="linux",
      image="cells", cadence="nightly")
def test_precommit_all_files_in_ci_reports_a_committed_breach(box, templates, candidate):
    repo = framework_repo(box, templates, candidate)
    contributor = box.root / "contributor"
    box.run(["git", "clone", "-q", str(repo), str(contributor)], expect=0)
    gitsurf.breach(contributor)
    gitsurf.assert_accepted(gitsurf.commit(box, contributor))
    box.script(docsnip.commands(docsnip.fence(gitsurf.README, gitsurf.ROUTE3, index=1))[0], expect=0)

    ci = box.run(["pre-commit", "run", "--all-files"], cwd=contributor)
    assert ci.exit != 0 and gitsurf.BREACH_NAME in ci.stdout, f"CI passed a committed breach:\n{ci.stdout}"


@cell("lin-prek", channel=".pre-commit-config.yaml", harness="prek (pinned)",
      scenario="fresh: same config; refused then accepted", use_cases="commit gate", os="linux", image="cells",
      cadence="nightly")
def test_prek_runs_the_same_config_and_refuses_then_accepts(box, templates, candidate):
    repo = framework_repo(box, templates, candidate)
    box.run(["prek", "install"], cwd=repo, expect=0)

    gitsurf.refused_then_accepted(box, repo)


# --- upgrade: autoupdate from the 0.7.6 tag -------------------------------------------------

def readme_at(box, version: str) -> Path:
    """The README a user of `version` read, from the release tag on the mirror."""
    folder = box.root / f"readme-{version}"
    folder.mkdir()
    text = box.run(["git", f"--git-dir={box.root / 'mirror' / 'crapkit.git'}", "show", f"v{version}:README.md"],
                   expect=0).stdout
    (folder / "README.md").write_text(text, encoding="utf-8", newline="\n")
    return folder


def upgrade_cli(box, repo: Path) -> None:
    """docs/upgrading.md for a pip install with the coverage extra, then the
    measure-and-reseed block, committed."""
    box.script(gitsurf.inline(UPGRADE_DOC, "Upgrading Crapkit", 'python -m pip install --upgrade "crapkit[py]"'),
               expect=0)
    for line in docsnip.commands(docsnip.fence(UPGRADE_DOC, "Analysis version 11", contains="ratchet prune")):
        box.script(line, cwd=repo, expect=0)
    box.run(["git", "add", "crapkit-ratchet.tsv"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "reseed under the new reader"], cwd=repo, env=box.commit_env(), expect=0)


def autoupdate(box, repo: Path) -> str:
    box.run(["pre-commit", "autoupdate"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-am", "pre-commit autoupdate"], cwd=repo, env=box.commit_env(), expect=0)
    return (repo / ".pre-commit-config.yaml").read_text(encoding="utf-8")


@cell("lin-up-precommit-0.7.6", channel="pre-commit", harness="pre-commit 4.6.2",
      scenario="upgrade: autoupdate v0.7.6 to candidate tag in mirror", use_cases="commit gate", os="linux",
      image="cells", cadence="nightly")
def test_precommit_autoupdate_moves_the_hook_from_0_7_6_to_the_candidate(box, templates, candidate):
    assert OLD in wheels.releases()
    gitsurf.pip_venv(box, install=f'pip install "crapkit[py]=={OLD}"')
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo)
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    old = readme_at(box, OLD)
    assert gitsurf.precommit_config(box, repo, base=old) == f"v{OLD}"
    gitsurf.precommit_install(box, repo, base=old)
    gitsurf.refused_then_accepted(box, repo)
    assert gitsurf.hook_env_version(box) == f"crapkit {OLD}"

    upgrade_cli(box, repo)
    assert f"rev: v{candidate.version}" in autoupdate(box, repo)
    gitsurf.refused_then_accepted(box, repo, name="route_after_upgrade.py")
    assert gitsurf.hook_env_version(box) == f"crapkit {candidate.version}"
