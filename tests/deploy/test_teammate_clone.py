"""A teammate's clone of a repo that already adopted crapkit.

The repo commits the gate (Route 2) and the merge attribute. The teammate runs
the one setup line the README puts in CONTRIBUTING, `git config
core.hooksPath githooks`, and not the driver's `git config` lines, which
docs/ratchet.md says every clone needs. Git then merges crapkit-ratchet.tsv
as text, and the conflict lands where a hand can raise a mark.
"""
from __future__ import annotations

from pathlib import Path


from kit import docsnip, gitsurf, repos
from kit.cells import cell

PACKET = "deploy-git"
CONFLICT = "CONFLICT (content): Merge conflict in crapkit-ratchet.tsv"


def teammate(box, templates) -> Path:
    """The upstream repo adopted with Route 2 and the attribute, then a clone
    that ran the README's CONTRIBUTING line and measured once."""
    gitsurf.pip_venv(box)
    upstream = repos.checkout(box, "brownfield", cache=templates)
    gitsurf.adopt(box, upstream)
    gitsurf.commit_attribute(box, upstream)
    gitsurf.route2(box, upstream)
    clone = box.root / "teammate"
    box.run(["git", "clone", "-q", str(upstream), str(clone)], expect=0)
    contributing = docsnip.commands(docsnip.fence(gitsurf.README, gitsurf.ROUTE2))[-1]
    assert contributing == "git config core.hooksPath githooks"
    box.script(contributing, cwd=clone, expect=0)
    box.run(["crapkit", "coverage"], cwd=clone, expect=0)
    return clone


@cell("lin-teammate-clone", channel="clone of an adopted repo", harness="git 2.47",
      scenario="fresh: README + CONTRIBUTING lines; merge without the driver configured; assert git's text merge and doctor",
      use_cases="ratchet merge, commit gate", os="linux", image="cells", cadence="push")
def test_a_clone_without_the_driver_gets_gits_text_conflict(box, templates):
    clone = teammate(box, templates)
    gitsurf.diverge(box, clone)

    step = gitsurf.merge(box, clone, "feature")
    assert step.exit == 1 and CONFLICT in step.stdout, step.stdout + step.stderr
    assert "<<<<<<<" in (clone / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


@cell("lin-teammate-clone", channel="clone of an adopted repo", harness="git 2.47",
      scenario="fresh: README + CONTRIBUTING lines; merge without the driver configured; assert git's text merge and doctor",
      use_cases="ratchet merge, commit gate", os="linux", image="cells", cadence="push")
def test_doctor_names_the_driver_a_clone_never_configured(box, templates):
    clone = teammate(box, templates)

    report = gitsurf.doctor(box, clone)
    assert gitsurf.names(report, "merge.crapkit-ratchet.driver"), report.stdout
