"""A team upgrades one member at a time: the repo holds what the candidate
wrote (marks stamped under its analysis version, a portable baseline), and a
teammate, a hook or a CI job still runs 0.7.6.

Each cell drives the old reader through the surface that reads the new
output: verify and the commit hook and the merge driver in a teammate's
clone (skew-cli), the pre-commit framework pinned to rev v0.7.6
(skew-precommit), and a CI job pinned to 0.7.6 reading the committed baseline
(skew-baseline). Whatever the old reader refuses, the cell checks that the
refusal names both metric stamps, that it rewrote nothing, and that the fix
the user can follow keeps the team's marks under the newer stamp.
"""
from __future__ import annotations

import re

import pytest

from kit import docsnip, gitmirror, state
from kit.cells import cell
from kit.state import output

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")
OLD_ANALYSIS = 10
BREACH_PY = state.ROUTE_PY.replace("def route", "def dispatch")
CLEAN_PY = "def label(n):\n    return str(n)\n"
TEAM_NOTE = re.compile(r"older (crapkit|release|CLI)", re.I)


def team_repo(box, templates, candidate):
    """The shared repo as the upgraded teammate pushed it, and a fresh clone of
    it. docs/upgrading.md holds the launcher token back until every reader runs
    0.8.1, so the pushed crapkit.toml names the launcher the token stands for,
    and the marks carry the candidate's stamp: that is what the old reader meets."""
    source = state.build(box, candidate.version, cache=templates)
    upstream = source.checkout(box, "upstream")
    state.launchers_written_back(box, upstream)
    state.commit(box, upstream, "crapkit.toml names the launcher until every reader runs 0.8.1")
    box.run(["git", "clone", "-q", str(upstream), str(box.root / "teammate")], expect=0)
    return source, box.root / "teammate"


def old_teammate(box) -> None:
    state.pip_venv(box, name="old-venv")
    state.pip_install(box, f"crapkit[py]=={OLD}")


def stamp_refused(box, step, candidate) -> None:
    """Exit 3, naming the stamp the marks carry and the one 0.7.6 measures."""
    assert step.exit == 3, box.transcript.text()
    assert state.stamp_refusal(state.analysis_version(candidate), OLD_ANALYSIS) in output(step), output(step)


def commit_file(box, repo, name: str, text: str, expect: int | None) -> object:
    state.write(repo, {name: text})
    box.run(["git", "add", name], cwd=repo, expect=0)
    return box.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=repo, env=box.commit_env(), expect=expect)


def hook_gates(box, repo) -> None:
    """The committed marks carry the newer stamp; the old hook still gates."""
    assert commit_file(box, repo, "calc/label.py", CLEAN_PY, expect=0).exit == 0
    refused = commit_file(box, repo, "calc/dispatch.py", BREACH_PY, expect=None)
    assert refused.exit != 0 and "dispatch" in output(refused), box.transcript.text()
    box.run(["git", "reset", "-q", "HEAD", "--", "calc/dispatch.py"], cwd=repo, expect=0)
    (repo / "calc" / "dispatch.py").unlink()


def tightened(row: str) -> str:
    path, name, value = row.split("\t")
    return f"{path}\t{name}\t{float(value) - 1:.4f}"


def lowered(box, repo, branch: str, index: int) -> None:
    """A branch whose marks file lowers one mark, the way a verify tightens it."""
    box.run(["git", "checkout", "-q", "-B", branch], cwd=repo, expect=0)
    row = state.mark_rows(repo)[index]
    state.rewrite(repo / "crapkit-ratchet.tsv", lambda text: text.replace(row, tightened(row)))
    state.commit(box, repo, f"tighten mark {index} on {branch}")


def merge_driver_merges(box, repo) -> None:
    """docs/ratchet.md's driver, run by 0.7.6 on two sides of stamped marks."""
    attributes = docsnip.fence("docs/ratchet.md", "The git merge driver", index=0).text
    state.write(repo, {".gitattributes": attributes + "\n"})
    state.commit(box, repo, "the ratchet merge driver")
    for line in docsnip.commands(docsnip.fence("docs/ratchet.md", "The git merge driver", index=1)):
        box.script(line, cwd=repo, expect=0)
    stamp = state.stamp_of(repo)
    lowered(box, repo, "feature", 0)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    lowered(box, repo, "main", 1)
    merged = box.run(["git", "merge", "feature", "-m", "merge feature"], cwd=repo, env=box.commit_env())
    assert merged.exit == 0 and "ratchet merge:" in output(merged), box.transcript.text()
    assert state.stamp_of(repo) == stamp, "the old merge driver rewrote the newer stamp"


def named_fix_restamps(box, repo, candidate) -> None:
    """The fix 0.7.6 names, followed: its seed restamps the team's marks under
    its own analysis version, and the upgraded teammate's verify then refuses."""
    box.run(["crapkit", "ratchet", "seed"], cwd=repo, expect=0, note="the fix 0.7.6's refusal names")
    assert state.stamp_of(repo) == f"# {state.metric(OLD_ANALYSIS)}"
    upgraded = state.venv_cli(box, candidate.version, box.root / "candidate-venv")
    upgraded.run(box, repo, "coverage")
    refused = upgraded.run(box, repo, "verify", expect=3)
    assert f"recorded under [{state.metric(OLD_ANALYSIS)}]" in output(refused), box.transcript.text()


@cell("lin-skew-cli", channel="0.7.6 readers of candidate output", harness="git",
      scenario="skew: stamp-11 marks read by 0.7.6 verify, commit hook and merge driver; refusal names the fix",
      use_cases="team version skew", os="linux", image="core", cadence="nightly")
def test_lin_skew_cli(box, templates, candidate):
    gaps = state.Gaps()
    _source, repo = team_repo(box, templates, candidate)
    old_teammate(box)
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()

    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    stamp_refused(box, box.run(["crapkit", "verify"], cwd=repo), candidate)
    assert (repo / "crapkit-ratchet.tsv").read_bytes() == marks, "the refusal rewrote the marks"
    route1 = docsnip.fence("README.md", "Route 1: `.git/hooks/pre-commit` (local, not committed)")
    box.script(route1.text, cwd=repo, expect=0, note="README Route 1, pasted whole")
    hook_gates(box, repo)
    merge_driver_merges(box, repo)
    named_fix_restamps(box, repo, candidate)
    gaps.check(bool(TEAM_NOTE.search(state.page())),
               f"{state.GUIDE}: nothing tells a team that an older crapkit refuses newer marks and names a reseed "
               "that restamps them backwards; upgrade every clone, CI pin and hook rev before reseeding")
    gaps.raise_any()


def precommit_at(box, repo, rev: str) -> None:
    """README Route 3: the config block with its rev pinned to `rev`, then the install lines."""
    block = docsnip.fence("README.md", "Route 3: the pre-commit framework", index=0).text
    state.write(repo, {".pre-commit-config.yaml": re.sub(r"rev: v\S+", f"rev: {rev}", block) + "\n"})
    state.commit(box, repo, f"pre-commit gate at {rev}")
    for line in docsnip.commands(docsnip.fence("README.md", "Route 3: the pre-commit framework", index=1)):
        box.script(line, cwd=repo, expect=0)


@cell("lin-skew-precommit", channel="pre-commit rev v0.7.6", harness="pre-commit",
      scenario="skew: a repo whose marks the candidate stamped, gated by the crapkit-gate hook at rev v0.7.6",
      use_cases="team version skew, commit gate", os="linux", image="core", cadence="nightly")
def test_lin_skew_precommit(box, templates, candidate):
    _source, repo = team_repo(box, templates, candidate)
    gitmirror.make(box)
    state.pip_venv(box, name="precommit-venv")
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()

    precommit_at(box, repo, f"v{OLD}")
    hook_gates(box, repo)

    hooks = list((box.home / ".cache" / "pre-commit").rglob(f"crapkit-{OLD}.dist-info"))
    assert hooks, "the pre-commit hook did not run crapkit from rev v0.7.6"
    assert (repo / "crapkit-ratchet.tsv").read_bytes() == marks


@cell("lin-skew-baseline", channel="CI pinned to 0.7.6", harness="none",
      scenario="skew: candidate baseline read by 0.7.6 verify --baseline-tsv; refusal names the fix",
      use_cases="team version skew, portable baseline", os="linux", image="core", cadence="nightly")
def test_lin_skew_baseline(box, templates, candidate):
    source = state.build(box, candidate.version, cache=templates)
    upstream = source.checkout(box, "upstream")
    emit, check = docsnip.commands(docsnip.fence("README.md", "Route 4: CI"))
    writer = state.venv_cli(box, candidate.version, box.root / "candidate-venv")
    writer.run(box, upstream, *emit.split()[1:])
    state.commit(box, upstream, "the portable baseline")
    box.run(["git", "clone", "-q", str(upstream), str(box.root / "ci")], expect=0)
    old_teammate(box)

    refused = box.run(check.split(), cwd=box.root / "ci")
    stamp_refused(box, refused, candidate)
    state.guide_says("Upgrade readers before writing")


@pytest.mark.kit
def test_a_tightened_mark_is_one_lower():
    assert tightened("calc/a.py\tf( x )\t50.8750") == "calc/a.py\tf( x )\t49.8750"
