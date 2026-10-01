"""A release waits for the deploy suite at the commit it tags.

`check` once demanded a green release-cadence run of deploy.yml at the
pre-bump HEAD. The release never tags that commit, so the run proved nothing
about what it shipped, and no guard after the bump looked at deploy.yml again.

The deploy stage now reads the tag commit's git tree id, pushes the commit to
a scratch branch, dispatches the release cadence there with that tree id, and
watches the run. deploy.yml's scope job refuses a checkout whose tree differs,
so a green run named `deploy release <tree>` at the tag commit is the suite's
word on that source. The stage keeps a deploy record in the release receipt,
keyed on the commit sha and the tree id, and stage 2b refuses unless the
record's sha is the receipt's head and GitHub still holds that green run. After
a red or cancelled run under the same key the stage reruns only its failed and
cancelled jobs: on 0.8.1 one red cell redispatched every entry, up to 845
job-minutes.
`check` keeps what stage 1 can know: gh can dispatch and read the run later.

The run objects keep the fields of the REST API's workflow runs listing, as
the accuracy gate's tests do; `display_title` is where the API puts a run-name.
"""
from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from test_release_guards import git, repo, verified
from test_release_recovery import publish_adapter
from test_release_tool import _tree, release

VERSION = "0.5.2"
HEAD = "8fb7b45c7248c2ff71a472b40e1111c4a93674ce"
OTHER = "0123456789abcdef0123456789abcdef01234567"
TREE = "3f1c09a2b7de" + "0" * 28
OTHER_TREE = "9a0e44c1d2f3" + "1" * 28
REDEPLOY = f"rerun `python tools/release/release.py run deploy {VERSION}`"
RUNS_URL = ("https://api.github.com/repos/JeanFrancoisGagne/crapkit/actions/workflows/deploy.yml/"
            "runs?head_sha={head}&event=workflow_dispatch&per_page=100")
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"


def _made(days_ago: float) -> str:
    """A created_at stamp in the listing's form, `days_ago` days before now."""
    made = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return made.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(head=HEAD, tree=TREE, **changes) -> dict:
    """A release-cadence dispatch of deploy.yml as the runs listing reports it."""
    return {"id": 36011909180, "name": "deploy", "path": ".github/workflows/deploy.yml",
            "event": "workflow_dispatch", "head_branch": f"deploy-release/{VERSION}",
            "head_sha": head, "display_title": f"deploy release {tree}", "status": "completed",
            "conclusion": "success", "run_attempt": 1, "created_at": _made(1 / 24),
            "html_url": "https://github.com/JeanFrancoisGagne/crapkit/actions/runs/36011909180",
            **changes}


def _receipt(**record) -> dict:
    return {"head": HEAD, "version": VERSION, "deploy": {"sha": HEAD, "tree": TREE, **record}}


def _kit(root: Path) -> Path:
    """The deploy kit's candidate.py: a tree that carries it is gated."""
    (root / "tools" / "deploy").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "deploy" / "candidate.py").write_text("# the deploy kit\n", encoding="utf-8")
    return root


@pytest.fixture
def kit(tmp_path):
    return _kit(tmp_path / "tree")


@pytest.fixture
def github(monkeypatch):
    """GitHub's answer to the runs query, and every URL asked."""
    state = {"runs": [_run()], "asked": []}

    def remote_json(url, *, absent=False):
        state["asked"].append(url)
        return {"total_count": len(state["runs"]), "workflow_runs": list(state["runs"])}

    monkeypatch.setattr(release, "_remote_json", remote_json)
    return state


def _refusal(root, receipt) -> str:
    with pytest.raises(release.ReleaseError) as refused:
        release.deploy_gate(root, VERSION, receipt)
    return str(refused.value)


# --- the workflow names the run the gate reads ---------------------------------------------------

def _rendered(cadence: str, tree: str) -> str:
    title = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["run-name"]
    return (title.replace("${{ inputs.cadence || github.event_name }}", cadence)
            .replace("${{ inputs.tree }}", tree))


def test_a_release_dispatch_is_named_for_the_tree_it_was_given():
    assert _rendered("release", TREE) == release.deploy_title(TREE) == f"deploy release {TREE}"
    assert _rendered("nightly", "").split() == ["deploy", "nightly"]


def _dispatch_inputs() -> dict:
    on = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return on.get("on", on.get(True))["workflow_dispatch"]["inputs"]


def test_the_workflow_takes_the_tree_as_a_dispatch_input():
    assert _dispatch_inputs()["tree"]["default"] == ""


def test_the_scope_job_matches_the_tree_before_it_plans_any_cell():
    """No cell runs unless the tree the run checked out is the one the release
    dispatched with: the scope job gates every other job."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    names = [step.get("name", "") for step in jobs["scope"]["steps"]]
    (step,) = [s for s in jobs["scope"]["steps"] if s.get("if") == "inputs.tree != ''"]

    assert names.index(step["name"]) < names.index("choose the cadence and the jobs it runs")


# --- stage 2b's guard reads the record ------------------------------------------------------------

def test_a_record_at_the_tag_commit_with_its_green_run_passes(kit, github):
    release.deploy_gate(kit, VERSION, _receipt())

    assert github["asked"] == [RUNS_URL.format(head=HEAD)]


def test_a_receipt_without_a_deploy_record_refuses(kit, github):
    receipt = _receipt()
    del receipt["deploy"]

    assert _refusal(kit, receipt) == f"deploy gate: the release receipt holds no deploy record; {REDEPLOY}"
    assert github["asked"] == []


def test_a_record_made_at_another_commit_refuses_before_asking_github(kit, github):
    """The pre-bump HEAD was the one commit the old gate asked about."""
    assert _refusal(kit, _receipt(sha=OTHER)) == (
        f"deploy gate: the deploy record was made at {OTHER[:12]} and the release is at "
        f"{HEAD[:12]}; {REDEPLOY}")
    assert github["asked"] == []


@pytest.mark.parametrize("value", [None, "", "abc", TREE.upper(), TREE + "0", "0" * 64 + "0", 7])
def test_a_record_without_a_tree_id_refuses(kit, github, value):
    assert _refusal(kit, _receipt(tree=value)) == (
        f"deploy gate: the deploy record names no git tree id; {REDEPLOY}")


def test_a_record_still_keyed_on_a_source_hash_refuses(kit, github):
    """A receipt the old stage wrote names no tree; its runs were never named for one."""
    receipt = {"head": HEAD, "version": VERSION, "deploy": {"sha": HEAD, "source_hash": "3f" * 32}}

    assert _refusal(kit, receipt) == f"deploy gate: the deploy record names no git tree id; {REDEPLOY}"


@pytest.mark.parametrize("runs", [
    [],
    [_run(head=OTHER)],
    [_run(tree=OTHER_TREE)],
    [_run(display_title="deploy release")],
    [_run(display_title=f"deploy nightly {TREE}")],
    [_run(conclusion="failure")],
    [_run(status="in_progress", conclusion=None)],
], ids=["none", "other-commit", "other-source", "unnamed", "nightly", "failed", "running"])
def test_no_green_run_named_for_the_record_refuses(kit, github, runs):
    github["runs"] = runs

    said = _refusal(kit, _receipt())

    assert said.startswith(f"deploy gate: GitHub holds no successful deploy.yml run named "
                           f"`deploy release {TREE}` at {HEAD[:12]}")
    assert said.endswith(REDEPLOY)


def test_a_red_run_is_named_with_its_page(kit, github):
    github["runs"] = [_run(conclusion="failure"),
                      _run(status="queued", conclusion=None, html_url="https://github.com/x/runs/2")]

    said = _refusal(kit, _receipt())

    assert ("(runs by that name: failure https://github.com/JeanFrancoisGagne/crapkit/actions/"
            "runs/36011909180, not finished https://github.com/x/runs/2)") in said


def test_one_green_run_among_red_ones_is_enough(kit, github):
    github["runs"] = [_run(conclusion="failure", id=1), _run(id=2), _run(head=OTHER, id=3)]

    release.deploy_gate(kit, VERSION, _receipt())


def test_a_github_that_cannot_answer_shuts_the_gate(kit, monkeypatch):
    def unreachable(url, *, absent=False):
        raise release.ReleaseError(f"cannot confirm {url}: HTTP Error 401: Unauthorized")
    monkeypatch.setattr(release, "_remote_json", unreachable)

    assert _refusal(kit, _receipt()) == (
        f"deploy gate: cannot confirm {RUNS_URL.format(head=HEAD)}: HTTP Error 401: Unauthorized")


def test_a_tree_without_the_deploy_kit_is_not_gated(tmp_path, github):
    release.deploy_gate(tmp_path, VERSION, {"head": HEAD})

    assert github["asked"] == []


# --- check keeps only what stage 1 can know -------------------------------------------------------

def _logged_in(monkeypatch):
    monkeypatch.setattr(release, "preflight", lambda: [])
    monkeypatch.setattr(release.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(release, "_gh_token", lambda: "gho_token")


def test_check_no_longer_waits_for_a_run_at_the_pre_bump_head(tmp_path, monkeypatch, capsys):
    """No deploy run exists anywhere, and check passes: the run that counts is the
    deploy stage's, at the tag commit stage 2a makes after the bump."""
    root = _kit(_tree(tmp_path))
    _logged_in(monkeypatch)

    def no_read(url, *, absent=False):
        raise AssertionError(f"check read {url}")
    monkeypatch.setattr(release, "_remote_json", no_read)

    assert release.main(["check", VERSION, "--repo", str(root)]) == 0
    assert capsys.readouterr().out.strip() == f"ok: every surface at 0.5.1, {VERSION} next"


@pytest.mark.parametrize("gh, token, line", [
    (None, "gho_token", "deploy gate: gh is not on PATH, so the deploy stage cannot dispatch deploy.yml "
                        "or read its runs; install the GitHub CLI and run gh auth login"),
    ("/usr/bin/gh", "", "deploy gate: gh auth token returned nothing, so the deploy stage cannot "
                        "dispatch deploy.yml or read its runs; run gh auth login"),
], ids=["no-gh", "logged-out"])
def test_check_refuses_a_machine_that_cannot_dispatch_the_deploy_run(tmp_path, monkeypatch, capsys,
                                                                     gh, token, line):
    """A bare "gh" that is missing fails on Windows with a WinError that names
    no program (a deploy cell on Windows printed exactly that)."""
    root = _kit(_tree(tmp_path))
    _logged_in(monkeypatch)
    monkeypatch.setattr(release.shutil, "which", lambda name: gh)
    monkeypatch.setattr(release, "_gh_token", lambda: token)

    assert release.main(["check", VERSION, "--repo", str(root)]) == 1
    assert capsys.readouterr().out.splitlines() == [line]


def test_check_asks_nothing_of_gh_for_a_tree_without_the_deploy_kit(tmp_path):
    def asked():
        raise AssertionError("gh was asked")

    assert release.deploy_preflight(_tree(tmp_path), which=lambda name: None, token=asked) == []


# --- the plan: a stage of its own, on the tag commit ----------------------------------------------

def _deploy_step():
    (step,) = [s for s in release.plan(VERSION) if s.name == "deploy"]
    return step


def test_the_deploy_stage_follows_verify_and_precedes_every_publication():
    names = [s.name for s in release.plan(VERSION)]
    step = _deploy_step()

    assert names.index("ratchet") < names.index("accuracy") < names.index("deploy") < names.index("artifacts")
    assert (step.stage, step.background) == ("deploy", True)


def test_the_stage_runs_no_deploy_kit_command_to_key_the_record():
    """The key is git's tree id; candidate.py's hash of working-tree bytes split
    one 0.8.1 commit into 2d2b705b on Windows and fedbb54a on the runner."""
    commands = _deploy_step().commands

    assert [command[0] for command in commands] == ["git", "gh", "gh", "gh", "git"]
    assert not any("tools/deploy/" in arg for command in commands for arg in command)


def test_the_stage_runs_the_release_cadence_on_the_tag_commit_and_publishes_no_release_ref():
    commands = _deploy_step().commands
    pushed = [command[-1] for command in commands if command[:2] == ("git", "push")]
    (dispatch,) = [command for command in commands if command[:3] == ("gh", "workflow", "run")]

    assert pushed == [f"v{VERSION}^{{commit}}:refs/heads/deploy-release/{VERSION}", f"deploy-release/{VERSION}"]
    assert dispatch[3] == "deploy.yml"
    assert dispatch[dispatch.index("--ref") + 1] == f"deploy-release/{VERSION}"
    assert dispatch[-4:] == ("-f", "cadence=release", "-f", f"tree=$(git rev-parse v{VERSION}^{{tree}})")


def test_the_dispatch_hands_the_workflow_the_tag_commits_tree(tmp_path):
    root = repo(tmp_path, bumped=True)
    git(root, "tag", f"v{VERSION}")
    (dispatch,) = [command for command in _deploy_step().commands if command[:3] == ("gh", "workflow", "run")]

    assert release._arguments(dispatch, root)[-1] == f"tree={git(root, 'rev-parse', 'HEAD^{tree}')}"


def test_the_stage_list_names_the_deploy_stage(tmp_path, capsys):
    assert release.main(["run", "nosuch", VERSION, "--repo", str(tmp_path)]) == 1

    assert "stages: stage1, stage2a, verify, accuracy, deploy, stage2b," in capsys.readouterr().err


def test_the_deploy_stage_refuses_a_tree_verify_never_passed(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    git(root, "tag", f"v{VERSION}")
    ran = []
    monkeypatch.setattr(release, "_execute", lambda command, root, dry_run: ran.append(command))

    with pytest.raises(release.ReleaseError):
        release.run("deploy", VERSION, root)

    assert ran == []


# --- the stage, run -------------------------------------------------------------------------------

class Stage:
    """The commands the stage runs, GitHub's runs, and what `gh run watch` answers."""

    def __init__(self, runs, watch=0, appear=True, tree=TREE, refuse=None, rerun_ends=False):
        self.runs, self.watch, self.appear, self.tree = runs, watch, appear, tree
        self.refuse, self.rerun_ends = refuse, rerun_ends
        self.commands, self.watched = [], []

    def key(self, root, version):
        if self.tree is None:
            raise release.ReleaseError(f"v{version} names no tree to key the deploy record on")
        return self.tree

    def execute(self, command, root, dry_run):
        self.commands.append(command)
        if command[:3] == ("gh", "workflow", "run") and self.appear:
            self.runs.append(_run(status="queued", conclusion=None, id=99))
        if command[:3] == ("gh", "run", "rerun"):
            self.rerun(command[3])

    def rerun(self, run_id):
        """gh refuses, or GitHub lists the run's next attempt: queued, or already
        finished red when `rerun_ends` (a scope job that fails again in seconds)."""
        if self.refuse:
            raise subprocess.CalledProcessError(1, ("gh", "run", "rerun", run_id), stderr=self.refuse)
        if not self.appear:
            return
        state = {"status": "completed", "conclusion": "failure"} if self.rerun_ends else             {"status": "queued", "conclusion": None}
        self.runs = [{**run, **state, "run_attempt": run["run_attempt"] + 1} if str(run["id"]) == run_id
                     else run for run in self.runs]

    def remote_json(self, url, *, absent=False):
        return {"total_count": len(self.runs), "workflow_runs": list(self.runs)}

    def run(self, argv, **kwargs):
        self.watched.append((argv[3], kwargs["timeout"]))
        if self.watch == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        for index, run in enumerate(self.runs):
            if str(run["id"]) == argv[3]:
                self.runs[index] = {**run, "status": "completed",
                                    "conclusion": "success" if self.watch == 0 else "failure"}
        return subprocess.CompletedProcess(argv, self.watch)


@pytest.fixture
def stage(kit, monkeypatch):
    def make(runs, **kwargs):
        fake = Stage(runs, **kwargs)
        monkeypatch.setattr(release, "_execute", fake.execute)
        monkeypatch.setattr(release, "_remote_json", fake.remote_json)
        monkeypatch.setattr(release, "deploy_key", fake.key)
        monkeypatch.setattr(release.subprocess, "run", fake.run)
        monkeypatch.setattr(release, "READBACK_PAUSE", 0)
        monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
        return fake
    return make


def _deploy(kit) -> dict:
    receipt = {"head": HEAD, "version": VERSION}
    release._run_deploy(_deploy_step(), kit, VERSION, receipt)
    return receipt


def _names(fake) -> list[str]:
    return [" ".join(command[:3]) for command in fake.commands]


def _saved(kit) -> dict:
    return json.loads((kit / ".crapkit" / "release-receipt.json").read_text(encoding="utf-8"))


def test_no_run_dispatches_watches_and_keeps_the_record(kit, stage):
    fake = stage([])

    receipt = _deploy(kit)

    assert _names(fake) == ["git push -q", "gh workflow run", "git push -q"]
    assert fake.watched == [("99", 150 * 60)]
    assert receipt["deploy"] == _saved(kit)["deploy"] == {"sha": HEAD, "tree": TREE}


def test_a_green_run_for_the_record_dispatches_nothing(kit, stage):
    fake = stage([_run()])

    _deploy(kit)

    assert _names(fake) == ["git push -q"] and fake.watched == []
    assert fake.commands[-1][-2:] == ("--delete", f"deploy-release/{VERSION}")


def test_a_green_run_for_another_tree_is_not_the_record(kit, stage):
    """The tag's tree is TREE; a green run named for another tree tested other source."""
    fake = stage([_run(tree=OTHER_TREE)])

    _deploy(kit)

    assert "gh workflow run" in _names(fake) and fake.watched == [("99", 150 * 60)]


def test_a_run_already_going_is_watched_not_redispatched(kit, stage):
    fake = stage([_run(status="in_progress", conclusion=None, id=7)])

    _deploy(kit)

    assert "gh workflow run" not in _names(fake) and fake.watched == [("7", 150 * 60)]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_red_run_under_the_same_key_reruns_only_its_failed_jobs(kit, stage, conclusion):
    """Green entries of the earlier attempt hold for this tree: every entry
    needs only the scope job and downloads nothing another job wrote."""
    fake = stage([_run(conclusion=conclusion, id=7)])

    receipt = _deploy(kit)

    assert _names(fake) == ["git push -q", "gh run rerun", "git push -q"]
    assert fake.commands[1] == ("gh", "run", "rerun", "7", "--failed",
                                "--repo", "github.com/JeanFrancoisGagne/crapkit")
    assert fake.watched == [("7", 150 * 60)]
    assert receipt["deploy"] == {"sha": HEAD, "tree": TREE}


def test_the_newest_red_run_is_the_one_rerun(kit, stage):
    fake = stage([_run(conclusion="failure", id=7), _run(conclusion="cancelled", id=12),
                  _run(conclusion="failure", id=9)])

    _deploy(kit)

    assert fake.commands[1][3] == "12" and fake.watched == [("12", 150 * 60)]


def test_a_red_run_for_another_tree_is_not_rerun(kit, stage):
    """A different key dispatches anew: nothing the red run tested is this tree."""
    fake = stage([_run(tree=OTHER_TREE, conclusion="failure", id=7)])

    _deploy(kit)

    assert "gh run rerun" not in _names(fake) and fake.watched == [("99", 150 * 60)]


def test_a_rerun_whose_attempt_never_appears_stops_the_stage(kit, stage):
    stage([_run(conclusion="failure", id=7)], appear=False)

    with pytest.raises(release.ReleaseError, match="was rerun, but GitHub lists no attempt after #1"):
        _deploy(kit)


def test_a_rerun_that_ends_between_polls_still_counts(kit, stage):
    """The scope job can fail again within seconds, so the run reads completed
    by the first poll. Its run_attempt moved, and the stage watches that attempt."""
    fake = stage([_run(conclusion="failure", id=7)], rerun_ends=True)

    _deploy(kit)

    assert _names(fake) == ["git push -q", "gh run rerun", "git push -q"]
    assert fake.watched == [("7", 150 * 60)]


# --- a rerun only while the green entries it keeps are recent ---------------------------------
#
# `gh run rerun --failed` keeps every green entry of the earlier attempts. The
# weekly entries install the harnesses at their newest release and read PyPI
# and npm, none of which the tree id covers, so a red run older than three days
# is not rerun: a new dispatch runs every entry again.

def test_a_red_run_four_days_old_dispatches_anew(kit, stage, capsys):
    fake = stage([_run(conclusion="failure", id=7, created_at=_made(4))])

    receipt = _deploy(kit)

    assert _names(fake) == ["git push -q", "gh workflow run", "git push -q"]
    assert fake.watched == [("99", 150 * 60)] and receipt["deploy"] == {"sha": HEAD, "tree": TREE}
    assert "began more than 3 days ago" in capsys.readouterr().out


@pytest.mark.parametrize("created_at", [None, "", "yesterday", "2026-09-30T10:00:00"])
def test_a_red_run_whose_age_cannot_be_read_dispatches_anew(kit, stage, created_at):
    fake = stage([_run(conclusion="failure", id=7, created_at=created_at)])

    _deploy(kit)

    assert _names(fake) == ["git push -q", "gh workflow run", "git push -q"]


@pytest.mark.parametrize("conclusion, said", [
    ("failure", "run 7 cannot be rerun: it was created more than 30 days ago"),
    ("cancelled", "run 7 cannot be rerun: no failed jobs"),
    ("startup_failure", "run 7 cannot be rerun: its workflow file may be broken"),
], ids=["30-day-window", "no-failed-job", "startup-failure"])
def test_a_rerun_gh_refuses_dispatches_anew(kit, stage, conclusion, said, capsys):
    fake = stage([_run(conclusion=conclusion, id=7)], refuse=said)

    receipt = _deploy(kit)

    assert _names(fake) == ["git push -q", "gh run rerun", "gh workflow run", "git push -q"]
    assert fake.watched == [("99", 150 * 60)] and receipt["deploy"] == {"sha": HEAD, "tree": TREE}
    assert "could not be rerun" in capsys.readouterr().out


def test_a_red_run_under_three_days_old_reruns(kit, stage):
    fake = stage([_run(conclusion="failure", id=7, created_at=_made(2.9))])

    _deploy(kit)

    assert _names(fake) == ["git push -q", "gh run rerun", "git push -q"] and fake.watched == [("7", 150 * 60)]


@pytest.mark.parametrize("watch, says", [("timeout", "still runs after 150 minutes"),
                                         (1, "the release run of deploy.yml failed")])
def test_a_run_that_fails_or_outlasts_the_wait_stops_the_stage_and_keeps_no_record(kit, stage, watch, says):
    stage([], watch=watch)

    with pytest.raises(release.ReleaseError, match=says) as refused:
        _deploy(kit)

    assert "https://github.com/JeanFrancoisGagne/crapkit/actions/runs/99" in str(refused.value)
    assert REDEPLOY in str(refused.value)
    assert not (kit / ".crapkit" / "release-receipt.json").exists()


def test_a_dispatch_whose_run_never_appears_stops_the_stage(kit, stage):
    stage([], appear=False)

    with pytest.raises(release.ReleaseError, match=f"no run named `deploy release {TREE}` appeared "
                                                   f"at {HEAD[:12]}"):
        _deploy(kit)


def test_a_tag_with_no_tree_pushes_and_dispatches_nothing(kit, stage):
    fake = stage([], tree=None)

    with pytest.raises(release.ReleaseError, match="names no tree"):
        _deploy(kit)

    assert fake.commands == []


# --- stage 2b, end to end -------------------------------------------------------------------------

def _gated_repo(tmp_path, monkeypatch):
    root = _kit(repo(tmp_path, bumped=True))
    git(root, "add", ".")
    git(root, "commit", "-qm", "the deploy kit")
    git(root, "push", "-q", "origin", "main")
    verified(root, monkeypatch)
    return root


def _answer_runs(adapter, runs):
    """The publication adapter's GitHub, plus deploy.yml's runs."""
    publications = adapter.remote_json

    def remote_json(url, *, absent=False):
        if "/actions/workflows/deploy.yml/runs" in url:
            return {"total_count": len(runs), "workflow_runs": runs}
        return publications(url, absent=absent)
    return remote_json


def _record(root, **record):
    path = root / ".crapkit" / "release-receipt.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**receipt, "deploy": record}), encoding="utf-8")


@pytest.mark.parametrize("missing", ["record", "record at the pre-bump head", "remote run"])
def test_stage2b_publishes_nothing_without_the_deploy_record(tmp_path, monkeypatch, missing):
    root = _gated_repo(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")
    adapter = publish_adapter(root, monkeypatch)
    if missing == "record at the pre-bump head":
        _record(root, sha=git(root, "rev-parse", "HEAD~1"), tree=TREE)
    elif missing == "remote run":
        _record(root, sha=head, tree=TREE)
    monkeypatch.setattr(release, "_remote_json", _answer_runs(adapter, [_run(head=head)]
                                                              if missing != "remote run" else []))

    with pytest.raises(release.ReleaseError, match="^deploy gate: "):
        release.run("stage2b", VERSION, root)

    assert adapter.events == []


def test_stage2b_publishes_past_a_deploy_record_at_the_tag_commit(tmp_path, monkeypatch):
    root = _gated_repo(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")
    adapter = publish_adapter(root, monkeypatch)
    _record(root, sha=head, tree=TREE)
    monkeypatch.setattr(release, "_remote_json", _answer_runs(adapter, [_run(head=head)]))

    release.run("stage2b", VERSION, root)

    assert "push" in adapter.events


# --- the published cadence, after the surfaces read back --------------------------------------------

def test_the_surfaces_stage_dispatches_the_published_cadence_after_the_readback():
    steps = [step for step in release.plan(VERSION) if step.stage == "surfaces"]

    assert [step.name for step in steps] == ["surfaces", "published"]
    (command,) = steps[1].commands
    assert command[:4] == ("gh", "workflow", "run", "deploy.yml")
    assert command[command.index("--ref") + 1] == "main"
    assert ("-f", "cadence=published") == command[-4:-2]
    assert ("-f", f"ref=v{VERSION}") == command[-2:]


def test_a_dry_run_of_the_surfaces_stage_prints_the_dispatch(tmp_path, capsys):
    assert release.main(["run", "surfaces", VERSION, "--repo", str(_tree(tmp_path)), "--dry-run"]) == 0

    printed = capsys.readouterr().out
    assert printed.index(f"verify {VERSION}") < printed.index("gh workflow run deploy.yml")
    assert "cadence=published" in printed and f"ref=v{VERSION}" in printed
