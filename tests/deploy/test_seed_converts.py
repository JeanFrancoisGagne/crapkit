"""A repo 0.8.1 seeded, upgraded to the candidate: the first seed writes one stamp per scope.

The source is the state 0.8.1's own CLI built (init, coverage, seed, verify, an
override), whose marks file holds the single-number stamp. The cell installs
0.8.1 in a venv the way its user did, upgrades with the command the guide's
table gives, then runs `crapkit coverage` and `crapkit ratchet seed`. The seed
signs each scope with the stamp the fresh run recorded, so the file now opens
with the per-scope line and holds one `#scope` line per scope the config
declares, every mark 0.8.1 signed is still there, and verify passes.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from kit import state
from kit.cells import cell

PACKET = "metric-stamp-06"
OLD = state.source_version("0.8.1")
PIP_EXTRA = "pip with the Python coverage extra"
MARKS = "crapkit-ratchet.tsv"


def marked(repo: Path) -> set[tuple[str, str]]:
    """The (path, function) of each mark, whatever its value."""
    return {tuple(row.split("\t")[:2]) for row in state.mark_rows(repo)}


def scope_lines(repo: Path) -> list[str]:
    """The scope name of each `#scope` line, in file order."""
    lines = (repo / MARKS).read_text(encoding="utf-8").splitlines()
    return [line.split("\t")[1] for line in lines if line.startswith("#scope\t")]


def declared_scopes(repo: Path) -> list[str]:
    config = tomllib.loads((repo / "crapkit.toml").read_text(encoding="utf-8"))
    return sorted(row["name"] for row in config["scope"])


@cell("lin-up-pip-0.8.1-seed-converts", channel="pip venv", harness="none",
      scenario="upgrade from 0.8.1 state: crapkit coverage, then crapkit ratchet seed, turns the single-number "
               "marks file into one stamp per scope; every 0.8.1 mark kept; verify OK",
      use_cases="upgrade guide, ratchet lifecycle", os="linux", image="core", cadence="nightly")
def test_lin_up_pip_0_8_1_seed_converts(box, templates, candidate):
    source = state.build(box, OLD, cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={OLD}", "pytest", "pytest-cov")
    assert OLD in box.run(["crapkit", "--version"], expect=0).stdout
    assert state.stamp_of(repo).startswith("# crapkit-analysis="), box.transcript.text()
    signed = marked(repo)

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP_EXTRA))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    seed = box.run(["crapkit", "ratchet", "seed"], cwd=repo, expect=0)

    assert state.stamp_of(repo).startswith(f"# crapkit-stamp=2 lizard={state.lizard_version()}: {MARKS} "), \
        box.transcript.text()
    assert scope_lines(repo) == declared_scopes(repo), (repo / MARKS).read_text(encoding="utf-8")
    assert signed <= marked(repo), f"the seed dropped a mark 0.8.1 signed:\n{state.output(seed)}"
    state.commit(box, repo, "the first seed under the candidate: one stamp per scope")
    state.verify(box, repo)
