"""The ratchet merge driver, installed from docs/ratchet.md, run by `git merge`.

Two branches burn down debt on adjacent lines of crapkit-ratchet.tsv, which
git's text merge conflicts on. With the driver the merge is clean and keeps
the lower mark per key; across metric stamps it refuses and git falls back to
a text conflict, as the page prints.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kit import docsnip, gitsurf, repos, wheels
from kit.cells import cell

PACKET = "deploy-git"
UPGRADE_DOC = "docs/upgrading.md"
OLD = "0.7.6"
BRANCH_SWITCH = ("deploy-bug deploy-git-4: after a passing verify on one branch, verify on another exits 4 "
                 "and blames a rebase or amend: its baseline is the other branch's run, which is not in this "
                 "branch's history")
PS_ROUTE2 = ("deploy-bug deploy-git-6: README Route 2 prints only an sh block; pasted into PowerShell the "
             "heredoc does not parse, no hook is written and the next commit is ungated")


def driven(box, templates, *, shell: str = "sh") -> Path:
    """The brownfield template adopted, with both driver steps from the page."""
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "brownfield", cache=templates)
    gitsurf.adopt(box, repo)
    gitsurf.commit_attribute(box, repo)
    gitsurf.configure_driver(box, repo, shell=shell)
    return repo


def hand_edit(repo: Path, key_part: str, value: str, *, unstamped: bool = False) -> None:
    """Rewrite one mark by hand; `unstamped` drops the stamp lines the way a
    marks file written before 0.4.5 has none."""
    path = repo / "crapkit-ratchet.tsv"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if _kept(line, unstamped)]
    lines = [_valued(line, key_part, value) for line in lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _kept(line: str, unstamped: bool) -> bool:
    return not (unstamped and line.startswith("# crapkit-"))


def _valued(line: str, key_part: str, value: str) -> str:
    return line.rsplit("\t", 1)[0] + "\t" + value if key_part in line else line


def commit_all(box, repo: Path, message: str) -> None:
    box.run(["git", "commit", "-q", "-am", message], cwd=repo, env=box.commit_env(), expect=0)


def stamp_refusal(box, repo: Path) -> None:
    """A branch whose marks carry no stamp against main's stamped marks."""
    box.run(["git", "checkout", "-q", "-b", "legacy"], cwd=repo, expect=0)
    hand_edit(repo, "legacy_2.py", "30.0000", unstamped=True)
    commit_all(box, repo, "legacy marks")
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    hand_edit(repo, "legacy_3.py", "30.0000")
    commit_all(box, repo, "hand-tightened mark")
    gitsurf.assert_driver_refused(gitsurf.merge(box, repo, "legacy"), gitsurf.stamp(repo), "[unstamped]")


@cell("lin-merge-driver", channel="merge driver", harness="git 2.47",
      scenario="fresh: docs/ratchet.md lines; clean merge; different stamps refused", use_cases="ratchet merge",
      os="linux", image="cells", cadence="push")
def test_the_driver_merges_two_burn_downs_and_refuses_mixed_stamps(box, templates):
    repo = driven(box, templates)
    expected = gitsurf.diverge(box, repo)

    gitsurf.assert_driver_merged(gitsurf.merge(box, repo, "feature"), repo, expected)
    stamp_refusal(box, repo)


@pytest.mark.xfail(strict=True, reason=BRANCH_SWITCH)
@cell("lin-merge-driver", channel="merge driver", harness="git 2.47",
      scenario="fresh: docs/ratchet.md lines; clean merge; different stamps refused", use_cases="ratchet merge",
      os="linux", image="cells", cadence="push")
def test_verify_on_main_after_a_verify_on_a_branch_measures_mains_history(box, templates):
    repo = driven(box, templates)
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    gitsurf.tighten(box, repo, "test_legacy_one.py", gitsurf.LEGACY_TEST)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    (repo / "tests" / "test_grade_late.py").write_text(gitsurf.LATE_TEST, encoding="utf-8", newline="\n")
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "cover a late grade"], cwd=repo, env=box.commit_env(), expect=0)

    verdict = box.run(["crapkit", "verify"], cwd=repo)
    assert verdict.exit == 0 and "1 tightened" in verdict.stdout, verdict.stdout + verdict.stderr


# --- upgrade: a branch still stamped by 0.7.6 ------------------------------------------------

def reseed(box, repo: Path) -> None:
    """docs/upgrading.md's block after an upgrade, committed."""
    for line in docsnip.commands(docsnip.fence(UPGRADE_DOC, "Analysis version 11", contains="ratchet prune")):
        box.script(line, cwd=repo, expect=0)
    commit_all(box, repo, "reseed under the new reader")


@cell("lin-up-merge-driver-0.7.6", channel="merge driver", harness="git 2.47",
      scenario="upgrade: stamp 11 vs 10 refusal; guide recovery; clean merge", use_cases="ratchet merge",
      os="linux", image="cells", cadence="nightly")
def test_a_branch_stamped_by_0_7_6_is_refused_then_merges_after_its_reseed(box, templates, candidate):
    assert OLD in wheels.releases()
    gitsurf.pip_venv(box, install=f'pip install "crapkit[py]=={OLD}"')
    repo = repos.checkout(box, "brownfield", cache=templates)
    gitsurf.adopt(box, repo)
    gitsurf.commit_attribute(box, repo)
    gitsurf.configure_driver(box, repo)
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    gitsurf.tighten(box, repo, "test_legacy_one.py", gitsurf.LEGACY_TEST)
    old_stamp = gitsurf.stamp(repo)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    box.script(gitsurf.inline(UPGRADE_DOC, "Upgrading Crapkit", 'python -m pip install --upgrade "crapkit[py]"'),
               expect=0)
    reseed(box, repo)

    gitsurf.assert_driver_refused(gitsurf.merge(box, repo, "feature"), gitsurf.stamp(repo), old_stamp)
    box.run(["git", "merge", "--abort"], cwd=repo, expect=0)
    box.run(["git", "checkout", "-q", "feature"], cwd=repo, expect=0)
    reseed(box, repo)
    feature = gitsurf.marks(repo)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    expected = {key: min(value, feature[key]) for key, value in gitsurf.marks(repo).items()}
    gitsurf.assert_driver_merged(gitsurf.merge(box, repo, "feature"), repo, expected)


# --- Windows: Route 2 and the driver from Git Bash, then from PowerShell -------------------------

@cell("win-route2-merge-sh", channel="Route 2 + merge driver", harness="PortableGit, Git Bash",
      scenario="fresh: README lines in Git Bash", use_cases="commit gate, merge", os="windows", image=None,
      cadence="nightly")
def test_route2_and_the_driver_from_git_bash(box, templates):
    repo = driven(box, templates, shell="bash")
    gitsurf.route2(box, repo, shell="bash")

    assert gitsurf.head_mode(box, repo, "githooks/pre-commit") == "100755"
    expected = gitsurf.diverge(box, repo)
    gitsurf.assert_driver_merged(gitsurf.merge(box, repo, "feature"), repo, expected)
    gitsurf.refused_then_accepted(box, repo)


@cell("win-route2-merge-ps", channel="Route 2 + merge driver", harness="PortableGit, powershell.exe 5.1",
      scenario="fresh: what a PowerShell user gets from the README lines", use_cases="commit gate, merge",
      os="windows", image=None, cadence="nightly")
def test_the_driver_lines_from_powershell_merge_cleanly(box, templates):
    repo = driven(box, templates, shell="powershell")
    expected = gitsurf.diverge(box, repo)

    gitsurf.assert_driver_merged(gitsurf.merge(box, repo, "feature"), repo, expected)


@pytest.mark.xfail(strict=True, reason=PS_ROUTE2)
@cell("win-route2-merge-ps", channel="Route 2 + merge driver", harness="PortableGit, powershell.exe 5.1",
      scenario="fresh: what a PowerShell user gets from the README lines", use_cases="commit gate, merge",
      os="windows", image=None, cadence="nightly")
def test_route2_pasted_into_powershell_arms_the_gate(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo)
    gitsurf.route2(box, repo, shell="powershell", expect=None)

    gitsurf.refused_then_accepted(box, repo)
