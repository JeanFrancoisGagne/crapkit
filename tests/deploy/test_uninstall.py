"""Removing crapkit from a repo that adopted it, and putting it back.

A repo adopted by the README start, gated by README Route 1 and merged by the
docs/ratchet.md driver, loses its crapkit install (`pip uninstall`, the only
removal a pip user has). The cell records what git shows on the next commit
and the next merge of the marks file, then reinstalls with the README line
onto the .crapkit/ store and marks the old install left, and checks that
doctor, verify, the hook and the driver work again.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from kit import docsnip, installers, repos
from kit.cells import cell
from kit.installers import README, readme_start, said

PACKET = "deploy-channels"
ROUTE_1 = "Route 1: `.git/hooks/pre-commit` (local, not committed)"
RATCHET = "docs/ratchet.md"
DRIVER = "The git merge driver"
MARKS = "crapkit-ratchet.tsv"


def _mark(repo: Path, value: str) -> None:
    """Set the one mark's CRAP by hand, as a branch that tightened it would."""
    path = repo / MARKS
    text = re.sub(r"\t[0-9.]+$", f"\t{value}", path.read_text(encoding="utf-8"), flags=re.M)
    path.write_text(text, encoding="utf-8")


def _armed(box, repo: Path) -> None:
    """README Route 1, then the driver's two steps from docs/ratchet.md."""
    box.script(docsnip.fence(README, ROUTE_1).text, cwd=repo, expect=0, note="README Route 1")
    attributes, config = [docsnip.fence(RATCHET, DRIVER, index=index).text for index in (0, 1)]
    (repo / ".gitattributes").write_text(attributes.strip() + "\n", encoding="utf-8")
    installers.commit(box, repo, "merge marks with the crapkit driver", ".gitattributes")
    box.script(config, cwd=repo, expect=0, note="docs/ratchet.md: the driver, once per clone")


def _diverged(box, repo: Path) -> None:
    """Two branches that each tightened the mark: the merge the driver is for.
    The marks start from a looser hand-set value, so both stay above the
    measured CRAP and verify has nothing to refuse afterwards."""
    _mark(repo, "60.0000")
    installers.commit(box, repo, "a looser mark to tighten from", MARKS)
    box.run(["git", "checkout", "-q", "-b", "tighter"], cwd=repo, expect=0)
    _mark(repo, "52.0000")
    installers.commit(box, repo, "tighten on a branch", MARKS)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    _mark(repo, "55.0000")
    installers.commit(box, repo, "tighten on main", MARKS)


def adopted(box, templates) -> Path:
    installers.pip_venv(box, "3.12")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    readme_start(box, repo)
    _armed(box, repo)
    _diverged(box, repo)
    return repo


def _touch(repo: Path, name: str) -> None:
    (repo / "calc" / name).write_text("def fine(a):\n    return a\n", encoding="utf-8")


def removed(box, repo: Path) -> dict[str, object]:
    """pip's uninstall, then the marks merge and a commit. The commit's file
    stays staged for the commit after the reinstall."""
    box.run(["python", "-m", "pip", "uninstall", "-y", "crapkit"], expect=0, note="pip's own removal")
    merge = box.run(["git", "merge", "tighter", "-m", "merge tighter"], cwd=repo, env=box.commit_env())
    box.run(["git", "merge", "--abort"], cwd=repo, expect=0)
    _touch(repo, "after.py")
    box.run(["git", "add", "calc/after.py"], cwd=repo, expect=0)
    commit = box.run(["git", "commit", "-q", "-m", "after removal"], cwd=repo, env=box.commit_env())
    return {"commit": commit, "merge": merge}


def reinstalled(box, repo: Path) -> dict[str, object]:
    """The README line again, onto the store and marks the old install left."""
    box.script(installers.readme_install(), expect=0, note="the README install line, again")
    seen = {"doctor": box.run(["crapkit", "doctor"], cwd=repo, expect=0),
            "commit": box.run(["git", "commit", "-q", "-m", "after reinstall"], cwd=repo, env=box.commit_env())}
    seen["merge"] = box.run(["git", "merge", "tighter", "-m", "merge tighter"], cwd=repo, env=box.commit_env())
    seen["marks"] = (repo / MARKS).read_text(encoding="utf-8")
    seen["verify"] = box.run(["crapkit", "verify"], cwd=repo)
    return seen


@cell("lin-uninstall", channel="pip venv, Route 1, merge driver", harness="git",
      scenario="uninstall: the message git shows on the next commit and marks merge; reinstall onto the old "
               ".crapkit; doctor, the hook, the driver and verify work again", use_cases="removal",
      os="linux", image="core", cadence="nightly")
def test_removing_crapkit_breaks_the_hook_and_driver_until_it_returns(box, templates):
    repo = adopted(box, templates)
    gone = removed(box, repo)
    back = reinstalled(box, repo)

    assert gone["commit"].exit == 1
    assert "No module named crapkit" in said(gone["commit"])
    assert gone["merge"].exit == 1
    assert "crapkit: not found" in said(gone["merge"])
    assert "CONFLICT (content): Merge conflict in crapkit-ratchet.tsv" in said(gone["merge"])
    assert "doctor: no problems found" in back["doctor"].stdout
    assert back["commit"].exit == 0
    assert back["merge"].exit == 0
    assert "\t52.0000" in back["marks"]
    assert said(back["verify"]).startswith("verify OK"), said(back["verify"])


REMOVAL = re.compile(r"uninstall|remov\w* crapkit", re.I)


def _names_removal(page: str) -> bool:
    return bool(REMOVAL.search((docsnip.root() / page).read_text(encoding="utf-8")))


@cell("lin-uninstall", channel="all channels", harness="git",
      scenario="the docs say how to remove crapkit: the package, the hook, the merge driver and the store",
      use_cases="removal", os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-10: no page says how to remove crapkit; after "
                                       "`pip uninstall crapkit` every commit fails with 'No module named crapkit' "
                                       "from the Route 1 hook and every marks merge conflicts on 'crapkit: not "
                                       "found'")
def test_the_docs_say_how_to_remove_crapkit(box):
    pages = [README, "docs/upgrading.md", "docs/adoption.md", RATCHET]

    assert [page for page in pages if _names_removal(page)]
