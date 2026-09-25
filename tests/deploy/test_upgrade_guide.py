"""A pip user upgrades a repo crapkit already manages, following
docs/upgrading.md in page order.

The source repo is built by the old release's own CLI (kit/state.py): adopted,
seeded, verified, overridden once and holding one open claim. The cell
installs that release in a venv the way the user did, upgrades it with the
command the guide's table gives, and walks the guide (state.walk): the version
check, doctor (its failures fixed the way it says) and the coverage export,
then coverage, prune and seed, the review, a commit and verify. Every
assertion reads what a user reads: the command's own line, `runs --json`,
`claims --json`, `overrides --json` and serverInfo from a fresh MCP session.
The state manifest rides along in the transcript and fails the cell only for
lost rows.
"""
from __future__ import annotations

import re

import pytest

from kit import docsnip, state, state_manifest, wheels
from kit.cells import cell
from kit.state import output

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")
PIP = "pip in the active environment"
PIP_EXTRA = "pip with the Python coverage extra"
LEGACY_CHURN, CHURN = ".crapkit/churn-cache.json", ".crapkit/churn-cache-v2.json"
CLAIM_LOST = pytest.mark.xfail(
    strict=True, raises=state.KnownBug,
    reason="deploy-bug deploy-upgrade-1: a claim taken before the upgrade stops holding a def analysis 11 "
           "renames; next-item hands the claimed function out again under its new name")


def install_old(box, source, python: str, requirement: str) -> None:
    """The release as its user installed it: a venv on PATH, pip, offline."""
    state.pip_venv(box, python)
    state.pip_install(box, requirement, "pytest", "pytest-cov")
    assert source.version in box.run(["crapkit", "--version"], expect=0).stdout


# --- from 0.7.6 ----------------------------------------------------------------------

def reuse_reruns_every_lane(box, repo) -> None:
    """Not a guide step. The guide says the first --reuse-unchanged after the
    upgrade reruns every lane once and names why; this checks that sentence."""
    reuse = box.run(["crapkit", "coverage", "--reuse-unchanged"], cwd=repo, expect=0,
                    note="not a guide step: the first --reuse-unchanged after upgrading")
    assert "lane 'py': rerunning:" in output(reuse), box.transcript.text()


def walk_from_0_7_6(box, templates, candidate, python: str, prepare=state.nothing, measured=state.nothing):
    source = state.build(box, OLD, cache=templates, python=python)
    repo = source.checkout(box)
    prepare(box, repo)
    install_old(box, source, python, f"crapkit[py]=={OLD}")

    def measured_0_7_6(box, repo, doctor):
        assert state.retention_warnings(doctor) == list(state.RETENTION), box.transcript.text()
        measured(box, repo, doctor)

    done = state.walk(box, repo, candidate, source, state.upgrade_line(PIP_EXTRA),
                      upgraded=reuse_reruns_every_lane, measured=measured_0_7_6)
    assert done.pruned, "the 0.7.6 state carries a mark analysis 11 renames; prune removed none"
    return source, repo


@cell("lin-up-pip-0.7.6", channel="pip venv", harness="none",
      scenario="upgrade from 0.7.6 state: guide literally; retention keys as user edit; --reuse-unchanged as a "
               "separate non-guide step; review = marks diff equals named prunes; runs/claims/overrides kept; verify OK",
      use_cases="upgrade guide, ratchet lifecycle", os="linux", image="core", cadence="push")
@CLAIM_LOST
def test_lin_up_pip_0_7_6(box, templates, candidate):
    source, repo = walk_from_0_7_6(box, templates, candidate, "3.12")

    state.claim_holds(box, repo, source)


@cell("lin-up-pip-0.7.6", channel="pip venv", harness="none",
      scenario="upgrade from 0.7.6 state on the nightly Pythons", use_cases="upgrade guide",
      os="linux", image="core", cadence="nightly")
@pytest.mark.parametrize("python", ["3.11", "3.13"])
@CLAIM_LOST
def test_lin_up_pip_0_7_6_other_pythons(box, templates, candidate, python):
    source, repo = walk_from_0_7_6(box, templates, candidate, python)

    state.claim_holds(box, repo, source)


def crlf_checkout(box, repo) -> bytes:
    """The clone a Windows teammate has: core.autocrlf=true writes CRLF."""
    box.run(["git", "config", "--global", "core.autocrlf", "true"], expect=0)
    box.run(["git", "rm", "-r", "-q", "--cached", "."], cwd=repo, expect=0)
    box.run(["git", "reset", "-q", "--hard"], cwd=repo, expect=0)
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()
    assert b"\r\n" in marks, marks
    return marks


@cell("win-up-pip-0.7.6", channel="pip venv", harness="none",
      scenario="upgrade: guide; autocrlf=true CRLF marks left alone", use_cases="upgrade guide",
      os="windows", image=None, cadence="push")
@CLAIM_LOST
def test_win_up_pip_0_7_6(box, templates, candidate):
    checkout = {}

    def untouched(box, repo, _doctor):
        assert (repo / "crapkit-ratchet.tsv").read_bytes() == checkout["marks"], "measure rewrote the CRLF marks"

    source, repo = walk_from_0_7_6(
        box, templates, candidate, "3.12", prepare=lambda box, repo: checkout.update(marks=crlf_checkout(box, repo)),
        measured=untouched)

    status = box.run(["git", "status", "--porcelain"], cwd=repo, expect=0).stdout
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()
    assert status.strip() == "", status
    assert marks.count(b"\n") == marks.count(b"\r\n"), f"prune and seed rewrote the CRLF marks with LF: {marks!r}"
    state.claim_holds(box, repo, source)


# --- from N-1 ------------------------------------------------------------------------

@cell("lin-up-pip-n1", channel="pip venv", harness="none",
      scenario="upgrade from N-1 (wheelhouse.lock): verify after one coverage, no reseed",
      use_cases="upgrade guide", os="linux", image="core", cadence="push")
def test_lin_up_pip_n1(box, templates, candidate, record_property):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    source = state.build(box, n1, cache=templates)
    repo = source.checkout(box)
    install_old(box, source, "3.12", f"crapkit[py]=={n1}")
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP_EXTRA))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0, note="not a guide step: one coverage, no reseed")
    assert state.analysis_of(state.stamp_of(repo)) == state.analysis_version(candidate), \
        "the candidate moved the analysis version past N-1's: this cell's no-reseed path no longer applies"
    state.verify(box, repo)

    assert (repo / "crapkit-ratchet.tsv").read_bytes() == marks, "a same-analysis upgrade rewrote the marks"
    state.after_upgrade(box, repo, source, candidate)


# --- from 0.4.x ----------------------------------------------------------------------

def churn_adopted(box, repo) -> None:
    """The 0.4.x churn cache goes the first time the candidate reads churn,
    and the current cache takes its place. The guide's steps read none, so
    this is the ranked list a user asks for next."""
    assert LEGACY_CHURN in state_manifest.files(repo), "the guide's steps already swept the 0.4.x churn cache"
    box.run(["crapkit", "worklist"], cwd=repo, expect=0, note="not a guide step: the first worklist after upgrading")
    files = state_manifest.files(repo)
    assert LEGACY_CHURN not in files and CHURN in files, sorted(files)


def readme_refusal(candidate) -> str:
    """README 'Upgrading from 0.4.4': the quoted refusal, with only its second
    stamp number moved to the candidate's analysis version."""
    [(_command, printed)] = docsnip.outputs(docsnip.fence("README.md", "Upgrading from 0.4.4"))
    second = list(re.finditer(r"crapkit-analysis=(\d+)", printed))[1]
    return printed[:second.start(1)] + str(state.analysis_version(candidate)) + printed[second.end(1):]


@cell("lin-up-pip-0.4.0", channel="pip venv", harness="none",
      scenario="upgrade: 0.4.0 stamp 6, old store and churn; doctor's failures fixed as it says; guide to verify OK",
      use_cases="upgrade guide", os="linux", image="core", cadence="nightly")
def test_lin_up_pip_0_4_0(box, templates, candidate):
    source = state.build(box, state.source_version("0.4.0"), cache=templates)
    repo = source.checkout(box)
    install_old(box, source, "3.12", f"crapkit=={source.version}")
    assert LEGACY_CHURN in state_manifest.files(repo)
    assert state.stamp_of(repo) == f"# {state.metric(6)}"

    state.walk(box, repo, candidate, source, state.upgrade_line(PIP))
    churn_adopted(box, repo)


@cell("lin-up-pip-0.4.4", channel="pip venv", harness="none",
      scenario="upgrade: 0.4.4 churn adopt-once; README block compared with the second stamp number replaced",
      use_cases="upgrade guide", os="linux", image="core", cadence="nightly")
def test_lin_up_pip_0_4_4(box, templates, candidate):
    source = state.build(box, state.source_version("0.4.4"), cache=templates)
    repo = source.checkout(box)
    install_old(box, source, "3.12", f"crapkit=={source.version}")

    def readme_names_the_refusal(box, repo):
        refusal = box.run(["crapkit", "verify"], cwd=repo, expect=3, note="README 'Upgrading from 0.4.4'")
        assert output(refusal).strip() == readme_refusal(candidate), box.transcript.text()

    state.walk(box, repo, candidate, source, state.upgrade_line(PIP), upgraded=readme_names_the_refusal)
    churn_adopted(box, repo)


# --- Windows, every source -------------------------------------------------------------

@cell("win-up-sources", channel="pip venv", harness="none",
      scenario="upgrade from 0.4.0, 0.4.15, 0.5.1, 0.6.0, N-1 on Windows", use_cases="upgrade guide",
      os="windows", image=None, cadence="nightly")
@pytest.mark.parametrize("named", ["0.4.0", "0.4.15", "0.5.1", "0.6.0", "n-1"])
def test_win_up_sources(box, templates, candidate, named, record_property):
    version = wheels.n_minus_1() if named == "n-1" else state.source_version(named)
    record_property("n_minus_1", wheels.n_minus_1())
    source = state.build(box, version, cache=templates)
    repo = source.checkout(box)
    install_old(box, source, "3.12", f"crapkit=={version}")

    state.walk(box, repo, candidate, source, state.upgrade_line(PIP))
