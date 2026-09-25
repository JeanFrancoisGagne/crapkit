"""A pip user upgrades a repo crapkit already manages, following
docs/upgrading.md in page order.

The source repo is built by the old release's own CLI (kit/state.py): adopted,
seeded, verified, overridden once and holding one open claim. The cell
installs that release in a venv the way the user did, upgrades it with the
command the guide's table gives, and walks the guide: the version check, doctor
and the coverage export, then coverage, prune and seed, the review, a commit and
verify. Every assertion reads what a user reads: the command's own line, `runs
--json`, `claims --json`, `overrides --json` and serverInfo from a fresh MCP
session. The state manifest rides along in the transcript and fails the cell
only for lost rows.
"""
from __future__ import annotations

import pytest

from kit import state, state_manifest, wheels
from kit.cells import cell
from kit.state import output

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")
PIP_EXTRA = "pip with the Python coverage extra"
RETENTION = ("test_retention_days", "test_retention_count")


def stamp_refusal(old: int, new: int) -> str:
    return f"[crapkit-analysis={old} lizard=1.24.0] but this run measures [crapkit-analysis={new} lizard=1.24.0]"


def source_stamp(source) -> str:
    return (source.repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()[0]


def upgrade(box, repo, candidate, installation: str) -> None:
    """The guide's table row for this install, then `crapkit --version`."""
    state.run_line(box, repo, state.upgrade_line(installation))
    printed = state.run_line(box, repo, state.guide_span("crapkit --version"))
    assert candidate.version in printed.stdout, box.transcript.text()


def reuse_reruns_every_lane(box, repo) -> None:
    """Not a guide step. The guide says the first --reuse-unchanged after the
    upgrade reruns every lane once and names why; this checks that sentence."""
    reuse = box.run(["crapkit", "coverage", "--reuse-unchanged"], cwd=repo, expect=0,
                    note="not a guide step: the first --reuse-unchanged after upgrading")
    assert "lane 'py': rerunning:" in output(reuse), box.transcript.text()


def retention_warnings(step) -> list[str]:
    return [key for key in RETENTION if f"WARN crapkit.{key} is deprecated" in output(step)]


def reseed_and_verify(box, repo, candidate) -> state.Reseed:
    """The guide's reseed fence, the review, a commit, then verify."""
    done = state.reseed(box, repo)
    state.review(box, repo, done, state.export_path(repo))
    assert f"# crapkit-analysis={state.analysis_version(candidate)} lizard=1.24.0" in done.diff, done.diff
    state.commit(box, repo, "reseed the marks under the candidate")
    state.verify(box, repo)
    return done


def saved_state_section(box, repo, source) -> None:
    """"Saved state and command behavior": runs list, then the retention keys."""
    listed = state.run_line(box, repo, state.guide_span("crapkit runs list"))
    assert all(f"run {run_id:>3} @" in listed.stdout for run_id in source.run_ids), listed.stdout
    if any("test_retention" in edit for edit in source.user_edits):
        state.delete_retention_keys(box, repo)
        assert not retention_warnings(box.run(["crapkit", "doctor"], cwd=repo, expect=0))
        state.commit(box, repo, "drop the retention keys crapkit ignores")


def after_upgrade(box, repo, source, candidate) -> None:
    """What the user checks once the guide is done."""
    state.kept(box, repo, source)
    assert state.server_info(box, repo)["version"] == candidate.version, box.transcript.text()


def walk_from_0_7_6(box, templates, candidate, python: str, prepare=None, before_reseed=None) -> tuple:
    """The guide from a 0.7.6 state. `prepare(box, repo)` runs on the fresh
    checkout, `before_reseed(box, repo)` once measure and the refusal are done."""
    source = state.build(box, OLD, cache=templates, python=python)
    repo = source.checkout(box)
    (prepare or (lambda *_: None))(box, repo)
    state.pip_venv(box, python)
    state.pip_install(box, f"crapkit[py]=={OLD}")
    before = state_manifest.take(repo)

    upgrade(box, repo, candidate, PIP_EXTRA)
    reuse_reruns_every_lane(box, repo)
    doctor, _export = state.measure(box, repo)
    assert retention_warnings(doctor) == list(RETENTION), box.transcript.text()
    refusal = box.run(["crapkit", "verify"], cwd=repo, expect=3, note="not a guide step: verify before the reseed")
    assert stamp_refusal(10, state.analysis_version(candidate)) in output(refusal)
    (before_reseed or (lambda *_: None))(box, repo)
    done = reseed_and_verify(box, repo, candidate)
    saved_state_section(box, repo, source)
    after_upgrade(box, repo, source, candidate)

    state_manifest.check(box, before, state_manifest.take(repo))
    return source, repo, done


@cell("lin-up-pip-0.7.6", channel="pip venv", harness="none",
      scenario="upgrade from 0.7.6 state: guide literally; retention keys as user edit; --reuse-unchanged as a "
               "separate non-guide step; review = marks diff equals named prunes; runs/claims/overrides kept; verify OK",
      use_cases="upgrade guide, ratchet lifecycle", os="linux", image="core", cadence="push")
def test_lin_up_pip_0_7_6(box, templates, candidate):
    _source, _repo, done = walk_from_0_7_6(box, templates, candidate, "3.12")

    assert done.pruned, "the 0.7.6 state carries a mark analysis 11 renames; prune removed none"


@cell("lin-up-pip-0.7.6", channel="pip venv", harness="none",
      scenario="upgrade from 0.7.6 state on the nightly Pythons", use_cases="upgrade guide",
      os="linux", image="core", cadence="nightly")
@pytest.mark.parametrize("python", ["3.11", "3.13"])
def test_lin_up_pip_0_7_6_other_pythons(box, templates, candidate, python):
    walk_from_0_7_6(box, templates, candidate, python)


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
def test_win_up_pip_0_7_6(box, templates, candidate):
    checkout = {}

    def untouched(box, repo):
        assert (repo / "crapkit-ratchet.tsv").read_bytes() == checkout["marks"], "measure rewrote the CRLF marks"

    _source, repo, _done = walk_from_0_7_6(
        box, templates, candidate, "3.12", prepare=lambda box, repo: checkout.update(marks=crlf_checkout(box, repo)),
        before_reseed=untouched)

    status = box.run(["git", "status", "--porcelain"], cwd=repo, expect=0).stdout
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()
    assert status.strip() == "", status
    assert marks.count(b"\n") == marks.count(b"\r\n"), f"prune and seed rewrote the CRLF marks with LF: {marks!r}"


@cell("lin-up-pip-n1", channel="pip venv", harness="none",
      scenario="upgrade from N-1 (wheelhouse.lock): verify after one coverage, no reseed",
      use_cases="upgrade guide", os="linux", image="core", cadence="push")
def test_lin_up_pip_n1(box, templates, candidate, record_property):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    source = state.build(box, n1, cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={n1}")
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()

    upgrade(box, repo, candidate, PIP_EXTRA)
    box.run(["crapkit", "coverage"], cwd=repo, expect=0, note="not a guide step: one coverage, no reseed")
    assert source_stamp(source) == f"# crapkit-analysis={state.analysis_version(candidate)} lizard=1.24.0",         "the candidate moved the analysis version past N-1's: this cell's no-reseed path no longer applies"
    state.verify(box, repo)

    assert (repo / "crapkit-ratchet.tsv").read_bytes() == marks, "a same-analysis upgrade rewrote the marks"
    after_upgrade(box, repo, source, candidate)
