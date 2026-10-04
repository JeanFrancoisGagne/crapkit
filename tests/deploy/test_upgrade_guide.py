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

import json
import re
import tomllib

import pytest

from kit import docsnip, state, state_manifest, wheels
from kit.cells import cell
from kit.state import output

PACKET = "deploy-upgrade"
OLD = state.source_version("0.7.6")
PIP = "pip in the active environment"
PIP_EXTRA = "pip with the Python coverage extra"
LEGACY_CHURN = ".crapkit/churn-cache.json"
# The current churn cache by any format version: the version is in the file name
# and moves with the format (v2 to v3 with the HEAD-dated window), the adoption does not.
CHURN = re.compile(r"\.crapkit/churn-cache-v\d+\.json")


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
def test_lin_up_pip_0_7_6(box, templates, candidate):
    source, repo = walk_from_0_7_6(box, templates, candidate, "3.12")

    state.claim_holds(box, repo, source)


@cell("lin-up-pip-0.7.6", channel="pip venv", harness="none",
      scenario="upgrade from 0.7.6 state on the nightly Pythons", use_cases="upgrade guide",
      os="linux", image="core", cadence="nightly")
@pytest.mark.parametrize("python", ["3.11", "3.13"])
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

def same_analysis_upgrade(box, repo, candidate, source) -> None:
    """N-1 measured under the candidate's analysis version: one coverage, then
    verify, and the marks file stays as N-1 wrote it."""
    marks = (repo / "crapkit-ratchet.tsv").read_bytes()
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP_EXTRA))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0, note="not a guide step: one coverage, no reseed")
    state.verify(box, repo)

    assert (repo / "crapkit-ratchet.tsv").read_bytes() == marks, "a same-analysis upgrade rewrote the marks"
    state.after_upgrade(box, repo, source, candidate)


# A monorepo lane whose path_prefix names the directory its scope declares, fed a
# committed coverage.py report another checkout wrote. Before 0.8.1 the reader
# glued the prefix onto the absolute key, the scope claimed it, and every
# function scored untested with exit 0. The cell runs in a container, where
# every release refuses a coverage.py lane without container_ok before it reads
# the artifact (docs/lanes.md#containers); this lane only copies a file.
FOREIGN_KEY = "/home/user/other-checkout/backend/pkg/mod.py"
FOREIGN_TREE = {
    "backend/pkg/__init__.py": "",
    "backend/pkg/mod.py": "def f(x):\n    if x:\n        return 1\n    return 2\n",
    "fixtures/foreign-cov.json": json.dumps({
        "meta": {"branch_coverage": True},
        "files": {FOREIGN_KEY: {"missing_lines": [4], "functions": {"f": {
            "start_line": 1, "executed_lines": [1, 2, 3], "missing_lines": [4],
            "summary": {"covered_lines": 3, "num_statements": 4, "num_branches": 2,
                        "covered_branches": 1}}}}}}),
    "copy_cov.py": ("import pathlib, shutil\n"
                    "pathlib.Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
                    "shutil.copy('fixtures/foreign-cov.json', '.crapkit/cov/py.json')\n"),
    ".gitignore": ".crapkit/\n",
    "crapkit.toml": ("[[scope]]\nname = 'backend'\npaths = ['backend']\nlanguages = ['python']\n\n"
                     "[exclude]\nglobs = ['copy_cov.py']\n\n"
                     "[[lane]]\nname = 'py'\nparser = 'coveragepy'\ncontainer_ok = true\nscopes = ['backend']\n"
                     "path_prefix = 'backend'\nartifact = '.crapkit/cov/py.json'\n"
                     "full_suite = false\ncommand = 'python copy_cov.py'\n"),
}


def foreign_tree_repo(box):
    repo = box.root / "foreign-tree"
    state.write(repo, FOREIGN_TREE)
    box.run(["git", "init", "-q"], cwd=repo, expect=0)
    state.commit(box, repo, "a path_prefix lane fed another checkout's report")
    return repo


def refuses_the_foreign_tree(box, repo, release: str) -> None:
    """Not a guide step: exit 5 with the wrong-tree refusal, quoting the key as
    the runner wrote it, on N-1 and on the candidate alike."""
    step = box.run(["crapkit", "coverage"], cwd=repo, expect=5,
                   note=f"not a guide step: another checkout's report under {release}")
    assert "describes a different tree" in output(step), box.transcript.text()
    assert FOREIGN_KEY in output(step), box.transcript.text()


@pytest.mark.kit
def test_the_foreign_tree_lane_clears_the_container_guard():
    """Without container_ok, every release's container guard answers the
    foreign-tree step with its host-only refusal and never reads the report."""
    lanes = tomllib.loads(FOREIGN_TREE["crapkit.toml"])["lane"]
    assert [(lane["parser"], lane.get("container_ok")) for lane in lanes] == [("coveragepy", True)]


@cell("lin-up-pip-n1", channel="pip venv", harness="none",
      scenario="upgrade from N-1 (wheelhouse.lock): verify after one coverage, no reseed, when the candidate "
               "keeps N-1's analysis version; the guide's reseed walk when it moves it; a path_prefix lane "
               "fed another checkout's report exits 5 with the wrong-tree refusal before and after",
      use_cases="upgrade guide", os="linux", image="core", cadence="push")
def test_lin_up_pip_n1(box, templates, candidate, record_property):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    source = state.build(box, n1, cache=templates)
    repo = source.checkout(box)
    install_old(box, source, "3.12", f"crapkit[py]=={n1}")
    foreign = foreign_tree_repo(box)
    refuses_the_foreign_tree(box, foreign, n1)
    moved = state.analysis_of(state.stamp_of(repo)) != state.analysis_version(candidate)
    record_property("analysis_moved", moved)

    if moved:
        state.walk(box, repo, candidate, source, state.upgrade_line(PIP_EXTRA))
    else:
        same_analysis_upgrade(box, repo, candidate, source)
    refuses_the_foreign_tree(box, foreign, candidate.version)


# --- from 0.4.x ----------------------------------------------------------------------

def churn_adopted(box, repo) -> None:
    """The 0.4.x churn cache goes the first time the candidate reads churn,
    and the current cache takes its place. The guide's steps read none, so
    this is the ranked list a user asks for next."""
    assert LEGACY_CHURN in state_manifest.files(repo), "the guide's steps already swept the 0.4.x churn cache"
    box.run(["crapkit", "worklist"], cwd=repo, expect=0, note="not a guide step: the first worklist after upgrading")
    files = state_manifest.files(repo)
    assert LEGACY_CHURN not in files and any(CHURN.fullmatch(name) for name in files), sorted(files)


def guide_refusal(candidate) -> str:
    """docs/upgrading.md 'Analysis version 8': the quoted refusal, with only its
    second stamp number moved to the candidate's analysis version."""
    [(_command, printed)] = docsnip.outputs(docsnip.fence("docs/upgrading.md", "Analysis version 8"))
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
      scenario="upgrade: 0.4.4 churn adopt-once; the guide's version 8 block compared with the second stamp number replaced",
      use_cases="upgrade guide", os="linux", image="core", cadence="nightly")
def test_lin_up_pip_0_4_4(box, templates, candidate):
    source = state.build(box, state.source_version("0.4.4"), cache=templates)
    repo = source.checkout(box)
    install_old(box, source, "3.12", f"crapkit=={source.version}")

    def guide_names_the_refusal(box, repo):
        refusal = box.run(["crapkit", "verify"], cwd=repo, expect=3, note="docs/upgrading.md 'Analysis version 8'")
        assert output(refusal).strip() == guide_refusal(candidate), box.transcript.text()

    state.walk(box, repo, candidate, source, state.upgrade_line(PIP), upgraded=guide_names_the_refusal)
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


# --- the kit's own checks of the guide reader and the manifest ----------------------------

PAGE = """# Upgrading

| Installation | Upgrade command |
|---|---|
| pip in the active environment | `python -m pip install --upgrade crapkit` |
| uv tool | `uv tool upgrade crapkit` |

| What changed | Required action |
|---|---|
| Analysis or lizard stamp | Follow the [rules](ratchet.md). |

Check `crapkit --version` and Upgrade readers before writing.
"""


@pytest.mark.kit
def test_the_upgrade_table_reads_command_rows_only():
    assert state.upgrade_rows(PAGE) == {"pip in the active environment": "python -m pip install --upgrade crapkit",
                                        "uv tool": "uv tool upgrade crapkit"}
    assert state.upgrade_line("uv tool", PAGE) == "uv tool upgrade crapkit"
    assert state.upgrade_command("uv tool upgrade", PAGE) == "uv tool upgrade crapkit"
    with pytest.raises(state.GuideGap, match="no row for 'pipx'"):
        state.upgrade_line("pipx", PAGE)
    with pytest.raises(state.GuideGap, match="no `pipx upgrade ...` command"):
        state.upgrade_command("pipx upgrade", PAGE)


@pytest.mark.kit
def test_prose_steps_are_code_spans_and_sentences_the_page_carries():
    assert state.guide_span("crapkit --version", PAGE) == "crapkit --version"
    with pytest.raises(state.GuideGap, match="never names `crapkit mutate --drop-pool`"):
        state.guide_span("crapkit mutate --drop-pool", PAGE)
    with pytest.raises(state.GuideGap):
        state.guide_says("teammate", PAGE)
    assert state.guide_says("Upgrade readers before writing", PAGE)


@pytest.mark.kit
def test_deferred_findings_raise_last_as_their_own_kind():
    gaps, bugs = state.Gaps(), state.Bugs()
    gaps.check(True, "never raised")
    gaps.raise_any()
    gaps.check(False, "a gap")
    bugs.check(False, "a bug")
    with pytest.raises(state.GuideGap, match="a gap"):
        gaps.raise_any()
    with pytest.raises(state.KnownBug, match="a bug"):
        bugs.raise_any()


@pytest.mark.kit
def test_a_scope_block_pasted_twice_loses_only_its_copy():
    block = '[[scope]]\nname = "calc"\npaths = ["calc"]\n\n'
    other = '[[scope]]\nname = "web"\npaths = ["web"]\n\n'
    text = "[crapkit]\ntarget = 6\n\n" + block + block + other + "[exclude]\n"
    assert state._without_repeats(text) == "[crapkit]\ntarget = 6\n\n" + block + other + "[exclude]\n"


@pytest.mark.kit
def test_the_manifest_fails_on_lost_rows_and_nothing_else():
    before = {"store": {"counts": {"runs": 4, "attempts": 1, "run_rollup": 9}, "runs": [1, 2, 3, 4],
                        "claims": [["calc/a.py", "f( x )"]], "overrides": [["calc/a.py", "f( x )", "why"]]}}
    grown = {"store": {"counts": {"runs": 6, "attempts": 1, "run_rollup": 0}, "runs": [1, 2, 3, 4, 5, 6],
                       "claims": [["calc/a.py", "f( x )"]], "overrides": [["calc/a.py", "f( x )", "why"]]}}
    lost = {"store": {"counts": {"runs": 3, "attempts": 0}, "runs": [1, 2, 4], "claims": [], "overrides": []}}

    assert state_manifest.losses(before, grown) == []
    assert state_manifest.losses(before, lost) == [
        "table runs: 4 row(s) before, 3 after", "table attempts: 1 row(s) before, 0 after", "runs: 3 is gone",
        "claims: ['calc/a.py', 'f( x )'] is gone", "overrides: ['calc/a.py', 'f( x )', 'why'] is gone"]


@pytest.mark.kit
def test_the_manifest_reads_a_store_from_a_copy(tmp_path):
    import sqlite3
    (tmp_path / ".crapkit").mkdir()
    (tmp_path / "crapkit-ratchet.tsv").write_text("# crapkit-analysis=11 lizard=1.24.0\n# crapkit-keys=1\npath\n")
    with sqlite3.connect(tmp_path / ".crapkit" / "crap.sqlite") as db:
        db.execute("create table runs (id integer)")
        db.execute("insert into runs values (1)")
    db.close()

    taken = state_manifest.take(tmp_path)

    assert taken["stamp"] == ["# crapkit-analysis=11 lizard=1.24.0", "# crapkit-keys=1"]
    assert taken["store"]["counts"] == {"runs": 1} and taken["store"]["runs"] == [1]
    assert taken["store"]["claims"] == [] and sorted(taken["files"]) == [".crapkit/crap.sqlite", "crapkit-ratchet.tsv"]
