"""Lane reuse, the failed attempt's refusal, dark-line freshness, the reach test
and the suite-drop warning, against docs/lanes.md.

- `--reuse-unchanged` reuses a lane only when nothing it reads changed, and a
  rerun names the first condition that failed, in the order Reusing artifacts
  lists them (model_verdict.rerun_condition). A lane without `inputs` answers
  for the whole tracked tree, crapkit.toml and the inherited environment; a
  lane with `inputs` for its own paths and lane table. A `cd` and a lane's
  own declared output change nothing.
- The artifact a failed attempt left behind is refused on reuse until
  something rewrites it (The artifact a failed attempt left behind is refused).
- A lane's dark lines go null, with a note naming the lane, once a file under
  its scopes changed; other changes leave them (model_verdict.lines_stale).
- An artifact whose paths reach none of its lane's scopes gets the verdict of
  the docs' table (An artifact that measured a different tree;
  model_verdict.reach_verdict).
- coverage warns when a lane ran more than a tenth fewer tests than the last
  trusted run; an absent count compares nothing (The test count is the second
  check; model_verdict.suite_dropped).

Each case builds a World (verdict_world), whose lanes write their coverage and
JUnit from a plan, so a test sets what changed and reads what crapkit decided.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import importlib
import json
import os
import re
import sys

import html5lib
from hypothesis import given, strategies as st
import pytest

from accuracy.kit import drive, repos, rulings
from accuracy.kit.settings import process
from accuracy.verdict_model import cadence
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

WORLD = (vw.World()
         .with_fn("app", vw.Fn("a1", 1, 2)).with_fn("app", vw.Fn("a2", 3, 6))
         .with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_fn("lib", vw.Fn("b2", 7, 7))
         .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
INPUTS = replace(WORLD, lane_extra=(("a", 'inputs = ["src"]\n'), ("b", 'inputs = ["lib"]\n')))
SCOPES = {"a": ("src",), "b": ("lib",)}
# What each condition's rerun sentence says, in the words of docs/lanes.md,
# Reusing artifacts ("A rerun names the first condition that failed: ...").
SAYS = {"no artifact": r"^no artifact at \S+", "wrote none": r"last attempt",
        "no proof": r"^its stamp holds no proof", "uncommitted": r"uncommitted change\(s\)",
        "head": r"^HEAD is \w+ and its artifact was built at \w+",
        "lane table": r"lane table", "environment": r"^\d+ environment variable\(s\) changed: ",
        "inputs": r"under its inputs", "bytes": r"bytes differ from its stamp"}


def _measured(make_repo, world: vw.World = WORLD, root: str = "") -> vw.Scenario:
    scenario = vw.Scenario.build(make_repo, world, root=root)
    result = scenario.run("coverage")
    assert result.code == 0, result.stdout + result.stderr
    return scenario


def _reasons(scenario: vw.Scenario, driver: drive.Driver | None = None) -> dict:
    """{lane: rerun_reason} from `coverage --reuse-unchanged --json`, after
    checking each equals the sentence its stderr line printed."""
    result = (driver or scenario.driver).run("coverage", "--reuse-unchanged", "--json")
    assert result.code == 0, result.stdout + result.stderr
    reasons = {name: lane["rerun_reason"] for name, lane in result.json()["lanes"].items()}
    printed = dict(re.findall(r"lane '(\w+)': rerunning: (.+)", result.stderr))
    assert printed == {name: why for name, why in reasons.items() if why}
    return reasons


def _names(reason: str, condition: str | None) -> bool:
    if condition is None:
        return reason == ""
    return re.search(SAYS[condition], reason) is not None


def _check(reasons: dict, facts: dict, with_inputs: bool = False) -> None:
    want = {lane: model.rerun_condition(facts.get(lane, set()), with_inputs) for lane in vw.LANES}
    assert {lane: _names(reasons[lane], want[lane]) for lane in vw.LANES} == \
        {lane: True for lane in vw.LANES}, (want, reasons)


def _append(path, text: bytes = b"# edited\n") -> None:
    path.write_bytes(path.read_bytes() + text)


# --- the whole-tree proof ------------------------------------------------------------------------

def _edit_tracked(sc):
    _append(sc.root / "src" / "app.py")
    return {"a": {"uncommitted"}, "b": {"uncommitted"}}


def _untracked(sc):
    (sc.root / "notes.txt").write_text("a draft\n", encoding="utf-8")
    return {"a": {"uncommitted"}, "b": {"uncommitted"}}


def _new_commit(sc):
    sc.commit("an empty commit")
    return {"a": {"head"}, "b": {"head"}}


def _coverage_bytes(sc):
    _append(sc.root / ".crapkit" / "cov" / "a.json", b" ")
    return {"a": {"bytes"}}


def _junit_bytes(sc):
    _append(sc.root / ".crapkit" / "cov" / "b-junit.xml", b" ")
    return {"b": {"bytes"}}


def _no_artifact(sc):
    (sc.root / ".crapkit" / "cov" / "b.json").unlink()
    return {"b": {"no artifact"}}


WHOLE_TREE = {"tracked edit": _edit_tracked, "untracked file": _untracked,
              "new commit": _new_commit, "coverage bytes": _coverage_bytes,
              "junit bytes": _junit_bytes, "no artifact": _no_artifact}


def _env_driver(sc, **env) -> drive.Driver:
    return drive.Driver(sc.root, date_now=sc.date + vw.DAY, env=env)


@pytest.mark.nightly
@pytest.mark.process
def test_an_unchanged_tree_reuses_every_lane(make_repo):
    sc = _measured(make_repo)
    _check(_reasons(sc), {})


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("change", sorted(WHOLE_TREE))
def test_each_reuse_input_forces_rerun(make_repo, change):
    """Each input the docs list reruns the lane it belongs to, and says which."""
    sc = _measured(make_repo)
    facts = WHOLE_TREE[change](sc)
    _check(_reasons(sc), facts)


@pytest.mark.nightly
@pytest.mark.process
def test_an_inherited_variable_forces_a_rerun(make_repo):
    sc = _measured(make_repo)
    reasons = _reasons(sc, _env_driver(sc, CRAPKIT_ACCURACY_PROBE="1"))
    _check(reasons, {"a": {"environment"}, "b": {"environment"}})
    assert reasons["a"].endswith("CRAPKIT_ACCURACY_PROBE")


@pytest.mark.nightly
@pytest.mark.process
def test_a_stamp_measured_on_a_dirty_tree_holds_no_proof(make_repo):
    """Measurements made while their proof did not hold (a dirty tree) cannot
    be reused automatically, even once the tree is clean again."""
    sc = vw.Scenario.build(make_repo, WORLD)
    source = sc.root / "src" / "app.py"
    clean = source.read_bytes()
    _append(source)
    assert sc.run("coverage").code == 0
    source.write_bytes(clean)
    _check(_reasons(sc), {"a": {"no proof"}, "b": {"no proof"}})


def _environment(sc):
    return {"a": {"environment"}, "b": {"environment"}}


# Applied in this order, so the commit comes before the edit and the edit stays uncommitted.
MIXABLE = {"no_artifact": _no_artifact, "coverage_bytes": _coverage_bytes,
           "new_commit": _new_commit, "tracked_edit": _edit_tracked, "environment": _environment}


def _apply(sc, chosen: set) -> dict:
    facts: dict = {"a": set(), "b": set()}
    for name in filter(chosen.__contains__, MIXABLE):
        for lane, found in MIXABLE[name](sc).items():
            facts[lane] |= found
    return facts


@pytest.mark.process
@process
@given(chosen=st.sets(st.sampled_from(sorted(MIXABLE))))
def test_the_first_failed_condition_names_the_rerun(repo_templates, tmp_path_factory, chosen):
    """Any mix of changes: each lane's reason names the first of its failed
    conditions in the docs' order. Each example measures its own repo inside
    the test call, so pytest's own PYTEST_CURRENT_TEST is one value throughout."""
    top = tmp_path_factory.mktemp("mix") / "repo"
    sc = vw.Scenario(repo_templates.copy(vw.spec(WORLD), top), WORLD)
    assert sc.run("coverage").code == 0
    facts = _apply(sc, chosen)
    env = {"CRAPKIT_ACCURACY_PROBE": "1"} if "environment" in chosen else {}
    _check(_reasons(sc, _env_driver(sc, **env)), facts)


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("what", ["cd", "own output"])
def test_cd_and_own_output_never_force_a_rerun(make_repo, what):
    """docs/lanes.md: OLDPWD, PWD, SHLVL, _ and terminal session ids are left
    out of the environment proof, and a lane's declared artifact and results
    file are never changes to the tree, ignored by git or not."""
    world = WORLD
    if what == "own output":
        world = replace(WORLD, gitignore=".crapkit/*\n!.crapkit/cov/\n.plan/\n__pycache__/\n")
    sc = _measured(make_repo, world)
    if what == "own output":
        assert ".crapkit/" in repos.git(sc.top, "status", "--porcelain", "-uall")
    shell = {"OLDPWD": "/elsewhere", "PWD": "/somewhere/else", "SHLVL": "7", "_": "/usr/bin/env",
             "WT_SESSION": "a-terminal-id"} if what == "cd" else {}
    _check(_reasons(sc, _env_driver(sc, **shell)), {})


@pytest.mark.nightly
@pytest.mark.process
def test_nested_root_reuse_reads_the_whole_worktree(make_repo):
    """README: a crapkit root may sit below the git top. Without inputs, reuse
    covers the whole tracked tree, so a file above the root reruns both lanes."""
    sc = _measured(make_repo, root="app")
    _check(_reasons(sc), {})
    (sc.top / "README.md").write_text("above the crapkit root\n", encoding="utf-8")
    _check(_reasons(sc), {"a": {"uncommitted"}, "b": {"uncommitted"}})


# --- lanes with inputs ---------------------------------------------------------------------------

def _docs_commit(sc):
    (sc.root / "notes.txt").write_text("docs\n", encoding="utf-8")
    sc.commit("docs")
    return {}


def _untracked_elsewhere(sc):
    (sc.root / "notes.txt").write_text("a draft\n", encoding="utf-8")
    return {}


def _own_table(sc):
    sc.set(replace(sc.world, lane_extra=(("a", 'inputs = ["src"]\ntimeout_seconds = 600\n'),
                                         ("b", 'inputs = ["lib"]\n'))))
    sc.commit("lane a's table")
    return {"a": {"lane table"}}


def _config_elsewhere(sc):
    sc.set(replace(sc.world, config_extra="worklist_top = 40\n"))
    sc.commit("a repo-wide key")
    return {}


def _committed_input(sc):
    sc.set(sc.world.with_fn("lib", vw.Fn("b1", 3, 3)))
    sc.commit("lib changes")
    return {"b": {"inputs"}}


def _dirty_input(sc):
    _append(sc.root / "src" / "app.py")
    return {"a": {"inputs"}}


WITH_INPUTS = {"docs commit": _docs_commit, "untracked elsewhere": _untracked_elsewhere,
               "own lane table": _own_table, "config elsewhere": _config_elsewhere,
               "committed input": _committed_input, "dirty input": _dirty_input}


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("change", sorted(WITH_INPUTS))
def test_a_lane_with_inputs_reruns_only_for_what_it_reads(make_repo, change):
    """docs/lanes.md: with inputs, a docs commit or an untracked draft elsewhere
    reruns nothing; a change under the inputs or to the lane's own table does."""
    sc = _measured(make_repo, INPUTS)
    facts = WITH_INPUTS[change](sc)
    _check(_reasons(sc), facts, with_inputs=True)


@pytest.mark.process
def test_a_lane_with_inputs_ignores_the_inherited_environment(make_repo):
    sc = _measured(make_repo, INPUTS)
    _check(_reasons(sc, _env_driver(sc, CRAPKIT_ACCURACY_PROBE="1")), {}, with_inputs=True)


@pytest.mark.process
@pytest.mark.parametrize("spelling", cadence.tiered(["src", "./src/", "src\\\\app.py", "src/app.py"],
                                                    push={"backslash"},
                                                    ids=["plain", "dot-slash", "backslash", "file"]))
def test_lane_inputs_take_the_scope_path_spelling(make_repo, spelling):
    """docs/configuration.md, lane inputs: literal paths from the root, spelled
    as scope paths are, so ./ and a backslash name the same file."""
    world = replace(WORLD, lane_extra=(("a", f'inputs = ["{spelling}"]\n'),))
    sc = _measured(make_repo, world)
    _append(sc.root / "src" / "app.py")
    reasons = _reasons(sc)
    assert _names(reasons["a"], "inputs") and reasons["a"].endswith("src/app.py"), reasons


@pytest.mark.process
def test_a_glob_in_lane_inputs_is_refused(make_repo):
    world = replace(WORLD, lane_extra=(("a", 'inputs = ["src/*.py"]\n'),))
    result = vw.Scenario.build(make_repo, world).run("coverage")
    assert result.code != 0 and "literal paths" in result.stderr, result.stderr


@pytest.mark.nightly
@pytest.mark.process
def test_a_dirty_non_ascii_file_forces_a_rerun(make_repo):
    """git's core.quotePath (on by default) must not hide a dirty src/bêta.py:
    lane a (inputs) and lane b (whole tree) both rerun and name it as
    `git ls-files` spells it."""
    sc = vw.Scenario.build(make_repo, INPUTS)
    repos.git(sc.top, "config", "core.quotePath", "true")
    (sc.root / "src" / "bêta.py").write_bytes(b"def beta(x):\n    return x\n")
    sc.commit("a non-ASCII file")
    assert sc.run("coverage").code == 0
    _append(sc.root / "src" / "bêta.py")
    listed = repos.git(sc.top, "-c", "core.quotePath=false", "ls-files", "src").split()
    reasons = _reasons(sc)
    assert "src/bêta.py" in listed
    assert reasons["a"].endswith("src/bêta.py") and _names(reasons["a"], "inputs"), reasons


def _touch(sc) -> None:
    """A new mtime on src/app.py, the same bytes."""
    path = sc.root / "src" / "app.py"
    later = os.stat(path).st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(later, later))


@pytest.mark.process
@rulings.applies("V7")
@pytest.mark.parametrize("case", cadence.tiered(["touch", "fresh checkout"], push={"touch"}))
def test_same_bytes_touch_changes_nothing(make_repo, case):
    """A file whose bytes match HEAD is no change, whatever diff.autoRefreshIndex
    the repo sets: `git status`, which compares content, reads the tree clean.
    "touch": the index was fresh when coverage ran, then src/app.py was
    touched. "fresh checkout": coverage ran on a copy whose index stat
    information predates every file. Either way both lanes are reused and the
    dark lines stay."""
    sc = vw.Scenario.build(make_repo, WORLD)
    if case == "touch":
        repos.git(sc.top, "status", "--porcelain")
    repos.git(sc.top, "config", "diff.autoRefreshIndex", "false")
    assert sc.run("coverage").code == 0
    if case == "touch":
        _touch(sc)
    lines = _app_item(sc.driver)["uncovered_lines"]
    said = _reuse_words(_reasons(sc), lines)
    clean = repos.git(sc.top, "--no-optional-locks", "status", "--porcelain") == ""
    rulings.pin_ruling("V7", crapkit=said, oracle="reused, lines kept" if clean else "changed")


def _fail_status_once(monkeypatch) -> list:
    """Make the first git status read under a lane's inputs fail, as a full disk
    or a git process that cannot start makes it fail; the list records the
    paths of each read that failed."""
    changes = importlib.import_module("crapkit.lane_changes")
    original, failed = changes.ChangeReads.status_names, []

    def status_names(self):
        if failed:
            return original(self)
        failed.append(self._spec[1:])
        raise changes.GitError("git exited 128: forced at the seam")

    monkeypatch.setattr(changes.ChangeReads, "status_names", status_names)
    return failed


@pytest.mark.process
@rulings.applies("V10")
def test_a_git_failure_while_stamping_claims_no_uncommitted_change(make_repo, monkeypatch):
    """docs/lanes.md, Reusing artifacts: a rerun names the first condition that
    failed. git's status read fails once while a lane's inputs are proved, on a
    tree `git status` reads clean. That lane reruns, since nothing proved it,
    and the other is reused; the rerun sentence must not claim uncommitted
    changes the tree never had."""
    sc = vw.Scenario.build(make_repo, INPUTS)
    failed = _fail_status_once(monkeypatch)
    assert sc.run("coverage").code == 0
    monkeypatch.undo()
    lane = next(name for name, paths in SCOPES.items() if paths == failed[0])
    clean = repos.git(sc.top, "--no-optional-locks", "status", "--porcelain") == ""
    reasons = _reasons(sc)
    said = [_claim(reasons.pop(lane)), *map(_claim, reasons.values())]
    rulings.pin_ruling("V10", crapkit=", then ".join(said),
                       oracle="rerun, other cause, then reused" if clean else "tree changed")


def _claim(reason: str) -> str:
    if not reason:
        return "reused"
    return "rerun, uncommitted changes" if "uncommitted" in reason else "rerun, other cause"


def _reuse_words(reasons: dict, lines) -> str:
    reused = "reused" if set(reasons.values()) == {""} else "rerun"
    return f"{reused}, lines {'null' if lines is None else 'kept'}"


# --- the artifact a failed attempt left behind ----------------------------------------------------

def _runs(sc) -> list[model.Run]:
    kinds = {"coverage": model.COVERAGE, "verify": model.VERIFY, "partial": model.PARTIAL,
             "inventory": model.INVENTORY, "hook": model.HOOK}
    return [model.Run(row["id"], kinds[row["kind"]], row["commit_sha"],
                      None if row["verdict_ok"] is None else bool(row["verdict_ok"]))
            for row in sc.runs()]


def _fail_lane_b(sc, frozen_log: int | None = None) -> None:
    frozen = (("b", frozen_log),) if frozen_log else ()
    sc.set(replace(sc.world, failing_lanes=frozenset({"b"}), frozen_log=frozen))
    result = sc.run("coverage")
    assert result.code == 5 and "lane 'b'" in result.stderr, result.stdout + result.stderr


@pytest.mark.process
def test_failed_lane_leftover_artifact_never_scores(make_repo):
    """The file a failed attempt left on disk is refused by --reuse-artifacts
    (exit 5) and verify --reuse-artifacts until something rewrites it."""
    sc = _measured(make_repo)
    _fail_lane_b(sc)
    reused = sc.run("coverage", "--reuse-artifacts")
    assert reused.code == 5 and "last attempt" in reused.stderr, reused.stderr
    verified = sc.run("verify", "--reuse-artifacts")
    assert verified.code == 5, verified.stdout + verified.stderr
    runs = _runs(sc)
    assert [run.id for run in runs if model.trusted(run)] == [1] and model.baseline(runs).id == 1
    _append(sc.root / ".crapkit" / "cov" / "b.json", b" ")
    assert sc.run("coverage", "--reuse-artifacts").code == 0


@pytest.mark.nightly
@pytest.mark.process
def test_a_refusal_persists_within_one_mtime_tick(make_repo):
    """The refusal rides on the failed attempt, not on the lane log's mtime: a
    lane that sets its log back to the time the previous run left, as a coarse
    filesystem would, is still refused on reuse."""
    sc = _measured(make_repo)
    log = sc.root / ".crapkit" / "lane-b.log"
    _fail_lane_b(sc, frozen_log=log.stat().st_mtime_ns)
    reused = sc.run("coverage", "--reuse-artifacts")
    assert reused.code == 5 and "lane 'b'" in reused.stderr, reused.stderr


@pytest.mark.nightly
@pytest.mark.process
def test_a_failed_attempt_reruns_under_reuse_unchanged(make_repo):
    sc = _measured(make_repo)
    _fail_lane_b(sc)
    sc.set(replace(sc.world, failing_lanes=frozenset()))
    reasons = _reasons(sc)
    assert reasons["a"] == "" and _names(reasons["b"], "wrote none"), reasons


# --- dark-line freshness ----------------------------------------------------------------------------

def _test_file_edit(sc):
    _append(sc.root / "tests" / "test_a.py")
    return {"tests/test_a.py"}


def _source_edit(sc):
    _append(sc.root / "src" / "app.py")
    return {"src/app.py"}


def _source_commit(sc):
    sc.set(sc.world.with_fn("app", vw.Fn("a1", 1, 2, tag="touched")))
    sc.commit("touch a1")
    return {"src/app.py"}


def _config_commit(sc):
    sc.set(replace(sc.world, config_extra="worklist_top = 40\n"))
    sc.commit("config")
    return {"crapkit.toml"}


LINE_CHANGES = {"untracked elsewhere": lambda sc: (_untracked(sc), {"notes.txt"})[1],
                "test file edit": _test_file_edit, "source edit": _source_edit,
                "source commit": _source_commit, "config commit": _config_commit}


def _app_item(driver: drive.Driver) -> dict:
    items = driver.run("next-item", "--top", "50").json()["items"]
    return next(item for item in items if item["path"] == "src/app.py")


@pytest.mark.process
@pytest.mark.parametrize("change", cadence.tiered(sorted(LINE_CHANGES),
                                                  push=sorted(LINE_CHANGES)[:1]))
def test_line_freshness_reads_source_changes_only(make_repo, change):
    """docs/lanes.md: a stale artifact silences the dark-line fields with a
    note naming the lane; only a change under the lane's scopes makes it stale."""
    sc = _measured(make_repo)
    changed = LINE_CHANGES[change](sc)
    item = _app_item(sc.driver)
    stale = model.lines_stale(SCOPES["a"], changed)
    assert (item["uncovered_lines"] is None) == stale, item
    assert ("lane 'a'" in item.get("uncovered_lines_note", "")) == stale, item


@pytest.mark.process
def test_an_inherited_variable_leaves_the_dark_lines(make_repo):
    sc = _measured(make_repo)
    assert _app_item(_env_driver(sc, CRAPKIT_ACCURACY_PROBE="1"))["uncovered_lines"] is not None


def _unstamp_lane_b(sc):
    """Remove lane b's artifact and stamp; return the call that puts both back."""
    stamps, artifact = sc.root / ".crapkit" / "artifacts.json", sc.root / ".crapkit" / "cov" / "b.json"
    saved, saved_artifact = stamps.read_bytes(), artifact.read_bytes()
    kept = {key: entry for key, entry in json.loads(saved).items() if entry.get("lane") != "b"}
    stamps.write_text(json.dumps(kept), encoding="utf-8")
    artifact.unlink()

    def restore():
        artifact.write_bytes(saved_artifact)
        stamps.write_bytes(saved)
    return restore


def _stamp_once_reads_start(monkeypatch, restore) -> list:
    """Wrap crapkit's lanes.staleness_reads so `restore` runs once its reads
    have started; the returned list records each time it did."""
    lanes = importlib.import_module("crapkit.lanes")
    original, fired = lanes.staleness_reads, []

    @contextmanager
    def stamped_meanwhile(*args, **kwargs):
        with original(*args, **kwargs) as facts:
            restore()
            fired.append(True)
            yield facts

    monkeypatch.setattr(lanes, "staleness_reads", stamped_meanwhile)
    return fired


@pytest.mark.process
def test_unstamped_lane_paths_count_for_staleness(make_repo, monkeypatch):
    """A lane unstamped when the staleness reads start and stamped before its
    verdict (a concurrent coverage) is judged on its own scope: an uncommitted
    edit under lib still makes lane b stale. The stamp lands at the seam where
    the reads have started (lanes.staleness_reads), in process."""
    sc = _measured(make_repo)
    restore = _unstamp_lane_b(sc)
    _append(sc.root / "lib" / "util.py")
    fired = _stamp_once_reads_start(monkeypatch, restore)
    items = drive.Driver(sc.root, date_now=sc.date + vw.DAY).run("next-item", "--top", "50").json()
    lib = next(item for item in items["items"] if item["path"] == "lib/util.py")
    assert fired and lib["uncovered_lines"] is None, lib
    assert "lane 'b'" in lib["uncovered_lines_note"], lib


@pytest.mark.process
def test_the_report_banner_names_every_stale_lane(make_repo, tmp_path):
    """README, report: a banner naming every stale lane, the lanes next-item's
    note names."""
    sc = _measured(make_repo)
    _source_edit(sc)
    out = tmp_path / "report.html"
    assert sc.run("report", "--out", str(out)).code == 0
    text = " ".join(html5lib.parse(out.read_bytes(), namespaceHTMLElements=False).itertext())
    note = _app_item(sc.driver)["uncovered_lines_note"]
    assert "lane 'a'" in note and "lane 'a'" in text and "lane 'b'" not in text


# --- the reach test -----------------------------------------------------------------------------------

OTHER = "C:/other/checkout" if sys.platform == "win32" else "/other/checkout"
REACH = {"in scope": ["src/app.py"], "in-tree, no scope": ["tests/test_a.py"],
         "another tree": [f"{OTHER}/src/app.py"], "a climb": ["../elsewhere/src/app.py"],
         "this tree, absolute": ["{root}/src/app.py"],
         "mixed outside": [f"{OTHER}/x.py", "tests/t.py"],
         "one in scope": ["src/app.py", f"{OTHER}/x.py"]}
VERDICT_SAYS = {"ok": None, "warn": "will score untested",
                "other tree": "describes a different tree", "absolute": "spelled it absolutely"}


def _write_lane_a(sc, keys: list[str]) -> None:
    regions = vw.plan(sc.world)["a"]["files"]["src/app.py"]
    report = vw.report({key: regions for key in keys})
    (sc.root / ".crapkit" / "cov" / "a.json").write_text(json.dumps(report), encoding="utf-8")


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("case", sorted(REACH))
def test_an_artifact_that_reaches_no_scope_gets_the_docs_verdict(make_repo, case):
    sc = _measured(make_repo)
    root = str(sc.root.resolve()).replace("\\", "/")
    keys = [key.replace("{root}", root) for key in REACH[case]]
    verdict = model.reach_verdict(keys, root, SCOPES["a"])
    _write_lane_a(sc, keys)
    result = sc.run("coverage", "--reuse-artifacts")
    assert result.code == (5 if verdict in ("other tree", "absolute") else 0), result.stderr
    assert _said_verdicts(result.stderr) == ({verdict} - {"ok"}), (verdict, result.stderr)


def _said_verdicts(stderr: str) -> set:
    return {name for name, text in VERDICT_SAYS.items() if text and text in stderr}


# --- the suite-drop warning ----------------------------------------------------------------------------

# (last trusted run's count, this run's count, warns): docs/lanes.md, The test count is the
# second check. The first row is the docs' own example; the rest sit either side of a tenth.
DROPS = [(20, 12, True), (10, 9, False), (10, 8, True), (100, 90, False), (100, 89, True),
         (None, 5, False), (5, None, False), (1, 1, False)]


@pytest.mark.parametrize("before, now, warns", DROPS)
def test_the_model_reads_the_drop_rows(before, now, warns):
    assert model.suite_dropped(before, now) is warns


def _b_tests(world: vw.World, count: int) -> vw.World:
    kept = tuple(t for t in world.tests if t.lane != "b")
    return replace(world, tests=kept + tuple(vw.Test(f"t{n}", lane="b") for n in range(count)))


DROP_LINE = re.compile(r"lane 'b' ran (\d+) tests, (\d+) fewer than the last trusted run's (\d+)")


@pytest.mark.process
@pytest.mark.parametrize("before, now", cadence.tiered([(10, 9), (10, 8), (20, 12)],
                                                       push=[(10, 9)], unpack=True))
def test_coverage_warns_past_a_tenth_drop(make_repo, before, now):
    sc = _measured(make_repo, _b_tests(WORLD, before))
    sc.set(_b_tests(WORLD, now))
    result = sc.run("coverage")
    assert result.code == 0
    found = DROP_LINE.search(result.stderr)
    want = (now, before - now, before) if model.suite_dropped(before, now) else None
    assert (tuple(map(int, found.groups())) if found else None) == want, result.stderr


@pytest.mark.nightly
@pytest.mark.process
def test_a_countless_run_between_counted_runs_compares_nothing(make_repo):
    """The comparison is against the last trusted run's count; a run whose lane
    wrote none carries no count, so the next counted run compares nothing."""
    sc = _measured(make_repo, _b_tests(WORLD, 10))
    sc.set(replace(_b_tests(WORLD, 10), no_junit=frozenset({"b"})))
    sc.commit("lane b declares no results_artifact")
    assert sc.run("coverage").code == 0
    sc.set(_b_tests(WORLD, 5))
    sc.commit("results_artifact back")
    result = sc.run("coverage")
    assert result.code == 0 and model.suite_dropped(None, 5) is False
    assert DROP_LINE.search(result.stderr) is None, result.stderr


@pytest.mark.process
@rulings.applies("V6")
def test_a_lane_with_no_count_reports_no_drop(make_repo):
    """Both counts are optional and neither absence is an error: a lane that
    wrote no JUnit this run is not a lane that ran 0 tests."""
    sc = _measured(make_repo, _b_tests(WORLD, 10))
    sc.set(replace(_b_tests(WORLD, 10), no_junit=frozenset({"b"})))
    sc.commit("lane b declares no results_artifact")
    result = sc.run("coverage")
    assert result.code == 0
    said = "warned" if DROP_LINE.search(result.stderr) else "silent"
    rulings.pin_ruling("V6", crapkit=said, oracle="warned" if model.suite_dropped(10, None) else "silent")
