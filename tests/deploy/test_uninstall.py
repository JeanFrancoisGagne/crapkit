"""Removing crapkit from a repo that adopted it, and putting it back.

A repo adopted by the README start, gated by README Route 1 and merged by the
docs/ratchet.md driver, loses its crapkit install (`pip uninstall`, the only
removal a pip user has). The cell records what git shows on the next commit,
on the box's PATH, where uv is installed, and on one without uv, and on the
next merge of the marks file, and holds README's "Removing crapkit" to both
commits. It then reinstalls with the README line onto the .crapkit/ store and
marks the old install left, and checks that doctor, verify, the hook and the
driver work again. The guide's own removal, which takes the hook and the
driver out before the package, leaves git working with no crapkit at all.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from kit import docsnip, installers, repos
from kit.cells import cell
from kit.installers import README, readme_start, said
from test_fresh_channels import _claude_plugin

PACKET = "deploy-channels"
ROUTE_1 = "Route 1: `.git/hooks/pre-commit` (local, not committed)"
RATCHET = "docs/ratchet.md"
DRIVER = "The git merge driver"
MARKS = "crapkit-ratchet.tsv"
GUIDE = "docs/upgrading.md"
FROM_REPO = "From a repo"
# ccn 7: three ifs and three boolean operators over the ceiling of 6 init writes.
BREACH = ("def route(a, b, c, d):\n    if a and b:\n        return 1\n    if c or d:\n        return 2\n"
          "    if a and d:\n        return 3\n    return 4\n")


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


def _touch(repo: Path, name: str, text: str = "def fine(a):\n    return a\n") -> None:
    (repo / "calc" / name).write_text(text, encoding="utf-8")


def without_uv(box) -> dict[str, str]:
    """The box's PATH less every directory that holds uvx: a machine with no uv."""
    return {"PATH": os.pathsep.join(d for d in box.path_dirs() if not (Path(d) / "uvx").exists())}


def removed(box, repo: Path) -> dict[str, object]:
    """pip's uninstall, then the marks merge, then a commit of a function over
    the ceiling where uv is installed and where it is not. A clean version of
    the file stays staged for the commit after the reinstall."""
    box.run(["python", "-m", "pip", "uninstall", "-y", "crapkit"], expect=0, note="pip's own removal")
    merge = box.run(["git", "merge", "tighter", "-m", "merge tighter"], cwd=repo, env=box.commit_env())
    box.run(["git", "merge", "--abort"], cwd=repo, expect=0)
    _touch(repo, "after.py", BREACH)
    box.run(["git", "add", "calc/after.py"], cwd=repo, expect=0)
    commit = ["git", "commit", "-q", "-m", "after removal"]
    seen = {"merge": merge, "uv": box.run(commit, cwd=repo, env=box.commit_env(), note="uv on PATH")}
    seen["no uv"] = box.run(commit, cwd=repo, env={**box.commit_env(), **without_uv(box)}, note="no uv on PATH")
    _touch(repo, "after.py")
    box.run(["git", "add", "calc/after.py"], cwd=repo, expect=0)
    return seen


def removal_claim() -> str:
    """README's "Removing crapkit" sentences about `pip uninstall crapkit` alone."""
    text = (docsnip.root() / README).read_text(encoding="utf-8")
    prose = " ".join(text.split("\n### Removing crapkit\n", 1)[1].split("\n## ", 1)[0].split())
    return " ".join(s for s in re.split(r"(?<=\.)\s+", prose) if "pip uninstall crapkit" in s)


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
      scenario="uninstall: what git shows on the next commit with and without uv, and on the marks merge, as "
               "README's removal text says; reinstall onto the old .crapkit; doctor, the hook, the driver and "
               "verify work again", use_cases="removal", os="linux", image="core", cadence="nightly")
def test_pip_uninstall_alone_leaves_the_hook_and_driver_armed_until_crapkit_returns(box, templates):
    repo = adopted(box, templates)
    gone = removed(box, repo)
    back = reinstalled(box, repo)

    assert gone["uv"].exit == 1 and "crapkit gate:" in said(gone["uv"]), said(gone["uv"])
    assert "Installed" in said(gone["uv"]), "uvx fetched the crapkit that gated the commit"
    assert gone["no uv"].exit == 1
    assert "No module named crapkit" in said(gone["no uv"])
    assert "`uvx crapkit`" in removal_claim() and "`No module named crapkit`" in removal_claim(), removal_claim()
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
def test_the_docs_say_how_to_remove_crapkit(box):
    pages = [README, GUIDE, "docs/adoption.md", RATCHET]

    assert [page for page in pages if _names_removal(page)]


# --- the guide's removal, run as printed -------------------------------------------------

def machine_row(label: str) -> list[str]:
    """The code spans of one row of the guide's "From the machine" table."""
    text = (docsnip.root() / GUIDE).read_text(encoding="utf-8")
    (row,) = [line for line in text.splitlines() if line.startswith(f"| {label} |")]
    return re.findall(r"`([^`]+)`", row.split("|")[2])


def codex_plugin(box, repo: Path) -> None:
    box.env["CODEX_HOME"] = str(box.home / ".codex")
    for line in docsnip.commands(docsnip.fence(README, "Codex")):
        box.script(line, cwd=repo, expect=0)


def edit_committed_files(repo: Path) -> None:
    """The two edits the guide asks for in words: the driver's attribute line and
    the `# crapkit` block init wrote to .gitignore."""
    attributes = repo / ".gitattributes"
    attributes.write_text("".join(line for line in attributes.read_text(encoding="utf-8").splitlines(True)
                                  if line.strip() != "crapkit-ratchet.tsv merge=crapkit-ratchet"), encoding="utf-8")
    ignore = repo / ".gitignore"
    text = ignore.read_text(encoding="utf-8")
    ignore.write_text(text[:text.index("# crapkit\n")] if "# crapkit\n" in text else text, encoding="utf-8")


def by_the_guide(box, repo: Path) -> None:
    """docs/upgrading.md's removal in page order: the per-clone lines, the two
    edits, the committed files, then the pip row."""
    box.script(docsnip.fence(GUIDE, FROM_REPO, index=0).text, cwd=repo, expect=0, note="guide: per-clone lines")
    edit_committed_files(repo)
    box.script(docsnip.fence(GUIDE, FROM_REPO, index=1).text, cwd=repo, expect=0, note="guide: committed files")
    (pip,) = machine_row("pip, pip --user or pip from the git URL")
    box.script(pip.replace("uninstall crapkit", "uninstall -y crapkit"), cwd=repo, expect=0, note="guide: pip row")


@cell("lin-uninstall", channel="pip venv, Route 1, merge driver, both plugins", harness="git, Claude Code, Codex",
      scenario="removal by docs/upgrading.md with uv still installed: a commit over the ceiling, a pre-removal "
               "branch's merge, the plugin lists", use_cases="removal", os="linux", image="core", cadence="nightly")
def test_the_guides_removal_leaves_git_and_both_agents_working(box, templates, candidate):
    repo = adopted(box, templates)
    _claude_plugin(box, candidate)
    codex_plugin(box, repo)
    by_the_guide(box, repo)
    orphaned = box.run(["claude", "mcp", "list"], cwd=repo)
    for line in [*machine_row("the Claude Code plugin"), *machine_row("the Codex plugin")]:
        box.script(line, cwd=repo, expect=0, note=f"guide: {line}")
    _touch(repo, "after.py", BREACH)
    box.run(["git", "add", "calc/after.py"], cwd=repo, expect=0)
    commit = box.run(["git", "commit", "-q", "-m", "after removal"], cwd=repo, env=box.commit_env())
    merge = box.run(["git", "merge", "tighter", "-m", "merge tighter"], cwd=repo, env=box.commit_env())
    box.script(f"git rm -q {MARKS}", cwd=repo, expect=0, note="guide: keep the deletion")
    resolved = box.run(["git", "commit", "-q", "--no-edit"], cwd=repo, env=box.commit_env())
    claude = box.run(["claude", "plugin", "list"], cwd=repo, expect=0)
    codex = box.run(["codex", "plugin", "list", "--marketplace", "crapkit"], cwd=repo)
    driver = box.run(["git", "config", "--get", "merge.crapkit-ratchet.driver"], cwd=repo)

    assert box.which("uvx"), "uv stays installed, so a hook left armed would still gate"
    assert "Failed to connect" in orphaned.stdout, orphaned.stdout
    assert commit.exit == 0 and said(commit) == "", said(commit)
    assert merge.exit == 1 and "CONFLICT (modify/delete)" in said(merge), said(merge)
    assert "crapkit: not found" not in said(merge)
    assert resolved.exit == 0, said(resolved)
    assert "crapkit@crapkit" not in claude.stdout, claude.stdout
    assert "crapkit@crapkit" not in said(codex), said(codex)
    assert driver.exit == 1
    assert not (repo / ".crapkit").exists()
