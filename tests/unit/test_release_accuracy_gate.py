"""The release accuracy gate: stage 2b publishes only past the accuracy suite.

The accuracy stage runs `tools/accuracy/run.py --tier release` here and
accuracy.yml's release mode on GitHub. Stage 2b then trusts neither report: it
refuses when the local receipt is missing, was made at another commit, records
a failed row, or vouches for pins, corpus or retro ledger bytes the tree no
longer holds (it hashes them itself), and when GitHub holds no successful
release-mode run at the tag commit.

The receipts below are written by hand in run.py's receipt format, and the
digests they carry are hashlib's sha256 of the fixture bytes. The GitHub run
objects keep the fields of a real response from
`GET repos/JeanFrancoisGagne/crapkit/actions/workflows/ci.yml/runs?per_page=1`
(2026-09-25); `display_title` is where the API reports a run-name.
"""
import datetime
import hashlib
import json
import subprocess
import sys

import pytest

from test_release_guards import git, repo, verified
from test_release_recovery import publish_adapter
from test_release_tool import release

VERSION = "0.5.2"
HEAD = "8fb7b45c7248c2ff71a472b40e1111c4a93674ce"
OTHER = "0123456789abcdef0123456789abcdef01234567"
FILES = {
    "tools/accuracy/run.py": b"# the tier runner\n",
    "tools/accuracy/pins.toml": b'[radon]\nversion = "6.0.1"\n',
    "tests/accuracy/corpus_goldens/corpus.toml": b'[requests]\ncommit = "0e322af"\n',
    "tests/accuracy/suite_strength/retro/ledger.tsv": b"id\ttest\nR08\tt::a\n",
}
VOUCHED = ("tools/accuracy/pins.toml", "tests/accuracy/corpus_goldens/corpus.toml",
           "tests/accuracy/suite_strength/retro/ledger.tsv")
RECORDED_RUN = {"conclusion": "success", "display_title": "ci: artifacts that expire but are "
                "never deleted stay billed", "event": "push", "head_branch": "main",
                "head_sha": HEAD, "id": 36011909180, "name": "ci",
                "path": ".github/workflows/ci.yml", "run_number": 130, "status": "completed"}
RERUN = f"rerun `python tools/release/release.py run accuracy {VERSION}`"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(root, files=FILES):
    for name, raw in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(raw)


def _saved(head=HEAD, **changes) -> dict:
    """A passing release-tier receipt as run.py writes one."""
    saved = {"schema": 1, "tier": "release", "shard": None, "local": True, "os_sensitive": False,
             "os": "windows", "python": "3.12", "head": head, "outcome": "pass", "attempts": 1,
             "digests": {name: _sha(FILES[name]) for name in VOUCHED},
             "checks": [{"key": "suite_strength", "name": "retro replays", "outcome": "pass",
                         "declared": 0, "seconds": 41.0, "tests": None},
                        {"key": "kit", "name": "the contract", "outcome": "empty",
                         "declared": 4, "seconds": 0.0, "tests": 0}]}
    saved.update(changes)
    return saved


def _receipt(root, saved):
    path = root / ".crapkit" / f"release-accuracy-{VERSION}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved), encoding="utf-8")


def _run(head=HEAD, **changes) -> dict:
    """An accuracy.yml release run: the recorded response with a dispatch's fields."""
    return {**RECORDED_RUN, "display_title": f"accuracy release {VERSION}", "event":
            "workflow_dispatch", "name": "accuracy", "path": ".github/workflows/accuracy.yml",
            "head_branch": f"accuracy-release/{VERSION}", "head_sha": head, **changes}


@pytest.fixture
def github(monkeypatch):
    """GitHub's answer to the runs query, and every URL asked."""
    state = {"runs": [_run()], "asked": []}

    def remote_json(url, *, absent=False):
        state["asked"].append(url)
        return {"total_count": len(state["runs"]), "workflow_runs": list(state["runs"])}

    monkeypatch.setattr(release, "_remote_json", remote_json)
    return state


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    _write(root)
    _receipt(root, _saved())
    return root


def _refusal(root, head=HEAD) -> str:
    with pytest.raises(release.ReleaseError) as refused:
        release.accuracy_gate(root, VERSION, head)
    return str(refused.value)


# --- the five refusals ------------------------------------------------------------------------

def test_a_tree_with_every_proof_in_place_passes(tree, github):
    release.accuracy_gate(tree, VERSION, HEAD)

    assert github["asked"] == [
        "https://api.github.com/repos/JeanFrancoisGagne/crapkit/actions/workflows/accuracy.yml/"
        f"runs?head_sha={HEAD}&event=workflow_dispatch&per_page=100"]


def test_a_missing_receipt_refuses(tree, github):
    (tree / ".crapkit" / f"release-accuracy-{VERSION}.json").unlink()

    said = _refusal(tree)

    assert f"no readable release accuracy receipt at .crapkit/release-accuracy-{VERSION}.json" in said
    assert RERUN in said


@pytest.mark.parametrize("text", ["", "{not json", "[]", '"pass"'])
def test_an_unreadable_receipt_refuses(tree, github, text):
    (tree / ".crapkit" / f"release-accuracy-{VERSION}.json").write_text(text, encoding="utf-8")

    assert RERUN in _refusal(tree)


def test_a_receipt_made_at_another_commit_refuses(tree, github):
    _receipt(tree, _saved(head=OTHER))

    assert _refusal(tree).startswith(f"the release accuracy receipt was made at {OTHER[:12]} "
                                     f"and the release is at {HEAD[:12]}")


@pytest.mark.parametrize("outcome", ["fail", "infra", None, "passed"])
def test_a_failed_row_refuses_by_name(tree, github, outcome):
    saved = _saved()
    saved["checks"].append({"key": "corpus_goldens", "name": "wheel diff vs 0.8.0",
                            "outcome": outcome, "declared": 0, "seconds": 3.0, "tests": None})
    _receipt(tree, saved)

    said = _refusal(tree)

    assert f"release accuracy row `corpus_goldens: wheel diff vs 0.8.0` {outcome}" in said
    assert RERUN in said


@pytest.mark.parametrize("changes", [{"checks": []}, {"checks": None}, {"tier": "push"},
                                     {"outcome": "fail"}, {"checks": ["not a row"]}])
def test_a_receipt_that_proves_no_passing_release_tier_refuses(tree, github, changes):
    _receipt(tree, _saved(**changes))

    assert RERUN in _refusal(tree)


@pytest.mark.parametrize("changes", [{"local": False}, {"local": None}, {"local": "true"},
                                     {"shard": "verdict-score"}, {"os_sensitive": True},
                                     {"os_sensitive": None}])
def test_a_receipt_of_a_narrower_selection_than_the_stage_s_refuses(tree, github, changes):
    """A release receipt made without --local on the releasing Windows 3.12
    machine holds no retro row and no mutation row, and a shard or an
    --os-sensitive receipt holds fewer still; each passed the head, tier,
    outcome, row and digest checks."""
    saved = {key: value for key, value in _saved(**changes).items() if value is not None}
    _receipt(tree, saved)

    said = _refusal(tree)

    assert "not the release tier's own selection" in said and RERUN in said


def test_a_receipt_written_by_the_stage_s_own_command_passes(tree, github, monkeypatch):
    """run.py's receipt, from the stage's argv through run.py's parser and main."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "accuracy_run_for_the_gate", release.Path(__file__).resolve().parents[2] / "tools/accuracy/run.py")
    tool = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, tool)
    spec.loader.exec_module(tool)
    record = {"key": "suite_strength", "name": "retro replays", "outcome": "pass", "declared": 0,
              "seconds": 1.0, "tests": None}
    notes = {"exports": {}, "oracles": {}, "events": {}, "skipped_files": {}, "infra": []}
    monkeypatch.setattr(tool, "load_checks", lambda checks=None: [])
    monkeypatch.setattr(tool, "_attempt", lambda checks, tier, workers, seed: ([record], notes))
    monkeypatch.setattr(tool, "_head", lambda: HEAD)
    monkeypatch.setattr(tool, "file_digests", lambda: {name: _sha(FILES[name]) for name in VOUCHED})
    monkeypatch.chdir(tree)
    (tree / ".crapkit" / f"release-accuracy-{VERSION}.json").unlink()

    assert tool._run_main(list(_step().commands[0][2:])) == 0
    release.accuracy_gate(tree, VERSION, HEAD)


@pytest.mark.parametrize("runs", [
    [],                                                                  # never dispatched
    [_run(head=OTHER)],                                                  # another commit
    [_run(display_title=f"accuracy nightly {VERSION}")],                 # another mode
    [_run(display_title="accuracy release 0.5.1")],                      # another version
    [_run(conclusion="failure")],
    [_run(status="in_progress", conclusion=None)],
    [_run(conclusion="cancelled"), _run(head=OTHER)],
], ids=["none", "other-sha", "nightly", "other-version", "failed", "running", "cancelled"])
def test_no_successful_release_run_at_the_tag_commit_refuses(tree, github, runs):
    github["runs"] = runs

    said = _refusal(tree)

    assert (f"GitHub holds no successful accuracy.yml run named `accuracy release {VERSION}` "
            f"at {HEAD[:12]}") in said


def test_one_passing_run_among_failed_ones_is_enough(tree, github):
    github["runs"] = [_run(conclusion="failure", id=1), _run(id=2), _run(head=OTHER, id=3)]

    release.accuracy_gate(tree, VERSION, HEAD)


# --- digests are recomputed, never read from the receipt ---------------------------------------

@pytest.mark.parametrize("name", VOUCHED)
@pytest.mark.parametrize("how", ["edited", "deleted"])
def test_a_vouched_file_that_changed_after_the_receipt_refuses(tree, github, name, how):
    if how == "edited":
        (tree / name).write_bytes(FILES[name] + b"# one more line\n")
    else:
        (tree / name).unlink()

    said = _refusal(tree)

    assert said.startswith(f"{name} hashes to ")
    assert f"receipt says {_sha(FILES[name])[:12]}" in said


@pytest.mark.parametrize("name", VOUCHED)
@pytest.mark.parametrize("claim", ["0" * 64, "", None, "flip"])
def test_a_receipt_digest_the_tree_does_not_hash_to_refuses(tree, github, name, claim):
    saved = _saved()
    real = saved["digests"][name]
    saved["digests"][name] = real[:-1] + ("0" if real[-1] != "0" else "1") if claim == "flip" else claim
    _receipt(tree, saved)

    assert f"{name} hashes to {_sha(FILES[name])[:12]} here" in _refusal(tree)


@pytest.mark.parametrize("content", [b"", b"\x00", b"a\r\n", "é".encode("utf-8"), bytes(range(256))],
                         ids=["empty", "nul", "crlf", "e-acute", "every-byte"])
def test_only_the_digest_of_the_bytes_in_the_tree_passes(tree, github, content):
    """Whatever the ledger holds, the gate passes exactly when the receipt names
    hashlib's digest of those bytes."""
    name = "tests/accuracy/suite_strength/retro/ledger.tsv"
    (tree / name).write_bytes(content)
    for claimed, passes in ((_sha(content), True), (_sha(content + b"\n"), False)):
        _receipt(tree, _saved(digests={**_saved()["digests"], name: claimed}))
        problems = release.receipt_problems(tree, json.loads(
            (tree / ".crapkit" / f"release-accuracy-{VERSION}.json").read_text("utf-8")),
            HEAD, VERSION)
        assert (problems == []) == passes, (content, claimed, problems)


def test_a_tree_without_the_accuracy_suite_is_not_gated(tmp_path, github):
    github["runs"] = []

    release.accuracy_gate(tmp_path, VERSION, HEAD)

    assert github["asked"] == []


# --- stage 2b, end to end -----------------------------------------------------------------------

def _gated_repo(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    _write(root)
    git(root, "add", ".")
    git(root, "commit", "-qm", "the accuracy suite")
    git(root, "push", "-q", "origin", "main")
    verified(root, monkeypatch)
    return root


def _answer_runs(adapter, runs):
    """The publication adapter's GitHub, plus accuracy.yml's runs."""
    publications = adapter.remote_json

    def remote_json(url, *, absent=False):
        if "/actions/workflows/accuracy.yml/runs" in url:
            return {"total_count": len(runs), "workflow_runs": runs}
        return publications(url, absent=absent)
    return remote_json


@pytest.mark.parametrize("missing", ["receipt", "remote run"])
def test_stage2b_publishes_nothing_without_the_accuracy_proof(tmp_path, monkeypatch, missing):
    root = _gated_repo(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")
    adapter = publish_adapter(root, monkeypatch)
    if missing == "remote run":
        _receipt(root, _saved(head=head))
    runs = [] if missing == "remote run" else [_run(head=head)]
    monkeypatch.setattr(release, "_remote_json", _answer_runs(adapter, runs))

    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)

    assert adapter.events == []


def test_stage2b_publishes_past_a_passing_accuracy_proof(tmp_path, monkeypatch):
    root = _gated_repo(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")
    adapter = publish_adapter(root, monkeypatch)
    _receipt(root, _saved(head=head))
    monkeypatch.setattr(release, "_remote_json", _answer_runs(adapter, [_run(head=head)]))

    release.run("stage2b", VERSION, root)

    assert "push" in adapter.events


# --- the accuracy stage ---------------------------------------------------------------------------

class Stage:
    """The commands the stage runs, GitHub's runs, and what `gh run watch` answers."""

    def __init__(self, root, runs, watch=0, appear=True, artifacts=None, refuse=None):
        self.root, self.runs, self.watch, self.appear = root, runs, watch, appear
        self.artifacts, self.refuse = artifacts or {}, refuse
        self.commands, self.watched, self.asked = [], [], []

    def execute(self, command, root, dry_run, env=None):
        self.commands.append(command)
        if command[:2] == ("gh", "workflow") and self.appear:
            self.runs.append(_run(status="queued", conclusion=None, id=99))
        if command[:3] == ("gh", "run", "rerun"):
            self.rerun(command[3])
        if "--tier" in command:
            _receipt(root, _saved())

    def rerun(self, run_id):
        if self.refuse:
            raise subprocess.CalledProcessError(1, ("gh", "run", "rerun", run_id), stderr=self.refuse)
        self.runs = [{**run, "status": "queued", "conclusion": None, "run_attempt": run.get("run_attempt", 1) + 1}
                     if str(run["id"]) == run_id else run for run in self.runs]

    def remote_json(self, url, *, absent=False):
        self.asked.append(url)
        if "/artifacts" in url:
            return self.artifacts.get(url.split("/runs/")[1].split("/")[0], {"total_count": 0, "artifacts": []})
        return {"total_count": len(self.runs), "workflow_runs": list(self.runs)}

    def run(self, argv, **kwargs):
        self.watched.append(argv[3])
        if self.watch == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        for index, run in enumerate(self.runs):
            if str(run["id"]) == argv[3]:
                self.runs[index] = {**run, "status": "completed",
                                    "conclusion": "success" if self.watch == 0 else "failure"}
        return subprocess.CompletedProcess(argv, self.watch)


@pytest.fixture
def stage(tree, monkeypatch, tmp_path):
    bundle = tmp_path / "history.bundle"
    bundle.write_bytes(b"# v2 git bundle\n")
    monkeypatch.setenv("CRAPKIT_RETRO_BUNDLE", str(bundle))

    def make(runs, **kwargs):
        fake = Stage(tree, runs, **kwargs)
        monkeypatch.setattr(release, "covered_at", lambda root: "HEAD")
        monkeypatch.setattr(release, "_execute", fake.execute)
        monkeypatch.setattr(release, "_remote_json", fake.remote_json)
        monkeypatch.setattr(release.subprocess, "run", fake.run)
        monkeypatch.setattr(release, "READBACK_PAUSE", 0)
        monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
        return fake
    return make


def _step():
    (step,) = [s for s in release.plan(VERSION) if s.name == "accuracy"]
    return step


def _accuracy(tree):
    release._run_accuracy(_step(), tree, VERSION, {"head": HEAD})


def _names(fake) -> list[str]:
    return [" ".join(command[:3]) for command in fake.commands]


def test_a_passing_receipt_and_run_dispatch_nothing(tree, stage):
    fake = stage([_run()])

    _accuracy(tree)

    assert _names(fake) == [f"git push -q"] and fake.watched == []
    assert fake.commands[0][-2:] == ("--delete", f"accuracy-release/{VERSION}")


def test_no_run_pushes_the_scratch_branch_dispatches_and_watches(tree, stage):
    (tree / ".crapkit" / f"release-accuracy-{VERSION}.json").unlink()
    fake = stage([])

    _accuracy(tree)

    assert _names(fake) == [f"{release.PY} tools/accuracy/run.py --tier", "git push -q",
                            "gh workflow run", "git push -q"]
    assert fake.commands[1][-1] == f"v{VERSION}^{{commit}}:refs/heads/accuracy-release/{VERSION}"
    assert "mode=release" in fake.commands[2] and f"release_key={VERSION}" in fake.commands[2]
    assert fake.watched == ["99"]


def test_a_run_already_going_is_watched_not_redispatched(tree, stage):
    fake = stage([_run(status="in_progress", conclusion=None, id=7)])

    _accuracy(tree)

    assert "gh workflow run" not in _names(fake) and fake.watched == ["7"]


@pytest.mark.parametrize("watch, says", [("timeout", "still runs after 90 minutes"),
                                         (1, "the release run of accuracy.yml failed")])
def test_a_run_that_fails_or_outlasts_the_wait_stops_the_stage(tree, stage, watch, says):
    stage([], watch=watch)

    with pytest.raises(release.ReleaseError, match=says) as refused:
        _accuracy(tree)

    assert "https://github.com/JeanFrancoisGagne/crapkit/actions/runs/99" in str(refused.value)


def test_a_dispatch_whose_run_never_appears_stops_the_stage(tree, stage):
    stage([], appear=False)

    with pytest.raises(release.ReleaseError, match="no run named `accuracy release 0.5.2` appeared"):
        _accuracy(tree)


def test_a_failing_local_tier_names_its_rows_and_never_dispatches(tree, stage, monkeypatch):
    (tree / ".crapkit" / f"release-accuracy-{VERSION}.json").unlink()
    fake = stage([])

    def red_tier(command, root, dry_run, env=None):
        fake.commands.append(command)
        failed = _saved(outcome="fail")
        failed["checks"][0]["outcome"] = "fail"
        _receipt(root, failed)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(release, "_execute", red_tier)

    with pytest.raises(release.ReleaseError, match="release accuracy row `suite_strength: retro "
                                                   "replays` fail"):
        _accuracy(tree)

    assert len(fake.commands) == 1


# --- the commit `mutation.py covered` judges --------------------------------------------------
#
# Stage 1 commits the version bump, src/crapkit/__init__.py among it, as the tag
# commit. No mutation run can judge that commit, which exists only on the
# releasing machine, and a moved .py outside the mutated modules voids every
# carried verdict, so `covered` exited 1 at every release.

def _committed(root, files: dict) -> None:
    _write(root, {name: text.encode() for name, text in files.items()})
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "c")


@pytest.fixture
def history(tmp_path):
    root = tmp_path / "history"
    root.mkdir()
    git(root, "init", "-q")
    _committed(root, {"pyproject.toml": 'version = "0.5.1"\n', "src/crapkit/__init__.py": '__version__ = "0.5.1"\n',
                      "src/crapkit/score.py": "def crap():\n    return 1\n", "CHANGELOG.md": "# 0.5.1\n"})
    return root


def test_covered_judges_the_parent_of_a_tag_commit_that_changes_only_release_files(history):
    _committed(history, {"pyproject.toml": 'version = "0.5.2"\n', "src/crapkit/__init__.py": '__version__ = "0.5.2"\n',
                         "CHANGELOG.md": "# 0.5.2\n"})

    assert release.covered_at(history) == "HEAD~1"


def test_covered_judges_a_tag_commit_that_changes_code_itself(history):
    _committed(history, {"pyproject.toml": 'version = "0.5.2"\n', "src/crapkit/score.py": "def crap():\n    return 2\n"})

    assert release.covered_at(history) == "HEAD"


def test_covered_judges_a_commit_with_no_parent_itself(history):
    assert release.covered_at(history) == "HEAD"


def test_the_local_tier_hands_covered_the_parent_through_mutation_s_own_variable(history, monkeypatch):
    _committed(history, {"pyproject.toml": 'version = "0.5.2"\n', "src/crapkit/__init__.py": '__version__ = "0.5.2"\n'})
    _write(history)
    head = git(history, "rev-parse", "HEAD")
    monkeypatch.setenv("CRAPKIT_RETRO_BUNDLE", str(history / "pyproject.toml"))
    handed = []

    def tier(command, root, dry_run, env=None):
        handed.append(env)
        _receipt(root, _saved(head=head))

    monkeypatch.setattr(release, "_execute", tier)

    release._local_accuracy(_step(), history, VERSION, head)

    mutation = (release.Path(__file__).resolve().parents[2] / "tools/accuracy/mutation.py").read_text(encoding="utf-8")
    assert handed == [{"CRAPKIT_RETRO_BUNDLE": str(history / "pyproject.toml"), "CRAPKIT_COVERED_AT": "HEAD~1"}]
    assert 'AT_ENV = "CRAPKIT_COVERED_AT"' in mutation


# --- a red release run: rerun its failed cells while the receipts it kept live ----------------
#
# A red release run reruns only its failed and cancelled cells (`gh run rerun
# ID --failed`), so one red cell no longer redispatches all 14 jobs. Every
# receipt artifact uploads with retention-days: 1, and xplat compares the
# receipts it downloads with one another and passes on a single one. A rerun
# after a green cell's receipt expired would check the rerun cells against
# nothing, so the stage reruns only while every receipt the run uploaded is
# unexpired, and otherwise dispatches a new run.

def _stamp(minutes_from_now: float) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes_from_now)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _artifact(name, expired=False, expires_at=None) -> dict:
    """One entry of GET repos/OWNER/REPO/actions/runs/ID/artifacts: uploaded
    an hour ago with retention-days 1, unless `expires_at` says otherwise."""
    return {"id": 4200000001, "name": name, "size_in_bytes": 2048, "expired": expired,
            "created_at": _stamp(-60), "expires_at": expires_at or _stamp(23 * 60)}


RECEIPTS = ["receipt-linux-3.12-analysis", "receipt-linux-3.12-corpus", "receipt-windows-3.11",
            "receipt-windows-3.13", "receipt-macos-3.12-corpus"]


def _artifacts(run_id=7, expired=(), others=(), expires=None) -> dict:
    names = [*RECEIPTS, *others]
    expires = expires or {}
    return {str(run_id): {"total_count": len(names),
                          "artifacts": [_artifact(name, name in expired, expires.get(name)) for name in names]}}


def test_a_red_run_with_every_receipt_live_reruns_only_its_failed_cells(tree, stage):
    fake = stage([_run(conclusion="failure", id=7)], artifacts=_artifacts())

    _accuracy(tree)

    assert _names(fake) == ["git push -q", "gh run rerun", "git push -q"]
    assert fake.commands[1] == ("gh", "run", "rerun", "7", "--failed", "--repo",
                                "github.com/JeanFrancoisGagne/crapkit")
    assert fake.watched == ["7"]
    assert ("https://api.github.com/repos/JeanFrancoisGagne/crapkit/actions/runs/7/artifacts?per_page=100"
            in fake.asked)


def test_a_cancelled_run_with_every_receipt_live_reruns(tree, stage):
    fake = stage([_run(conclusion="cancelled", id=7)], artifacts=_artifacts())

    _accuracy(tree)

    assert "gh run rerun" in _names(fake) and "gh workflow run" not in _names(fake)


@pytest.mark.parametrize("expired", [["receipt-linux-3.12-corpus"], RECEIPTS], ids=["one", "all"])
def test_a_red_run_whose_receipt_expired_dispatches_anew(tree, stage, expired, capsys):
    fake = stage([_run(conclusion="failure", id=7)], artifacts=_artifacts(expired=expired))

    _accuracy(tree)

    assert _names(fake) == ["git push -q", "gh workflow run", "git push -q"] and fake.watched == ["99"]
    assert "receipt" in capsys.readouterr().out


@pytest.mark.parametrize("expires_at", [_stamp(10), _stamp(release.ACCURACY_WATCH_SECONDS / 60 - 1), "soon", None],
                         ids=["in-10-min", "inside-the-watch", "unreadable", "missing"])
def test_a_red_run_whose_receipt_expires_before_the_rerun_ends_dispatches_anew(tree, stage, expires_at, capsys):
    """xplat downloads the receipts only after the rerun cells finish, up to the
    90-minute watch later. A receipt live when the rerun starts but gone by then
    leaves xplat fewer receipts to compare, and it passes on one."""
    listing = _artifacts(expires={"receipt-windows-3.11": expires_at})
    if expires_at is None:
        del listing["7"]["artifacts"][2]["expires_at"]
    fake = stage([_run(conclusion="failure", id=7)], artifacts=listing)

    _accuracy(tree)

    assert _names(fake) == ["git push -q", "gh workflow run", "git push -q"] and fake.watched == ["99"]
    assert "receipt" in capsys.readouterr().out


def test_a_red_run_whose_receipts_outlive_the_watch_reruns(tree, stage):
    fake = stage([_run(conclusion="failure", id=7)],
                 artifacts=_artifacts(expires={"receipt-windows-3.11": _stamp(release.ACCURACY_WATCH_SECONDS / 60 + 5)}))

    _accuracy(tree)

    assert "gh run rerun" in _names(fake) and "gh workflow run" not in _names(fake)


def test_an_artifact_list_github_answers_with_an_error_dispatches_anew(tree, stage, monkeypatch, capsys):
    """_remote_json raises on an HTTP or network error; the README and be47b39c
    promise a new dispatch for a list GitHub cannot answer, not a stopped stage."""
    fake = stage([_run(conclusion="failure", id=7)], artifacts=_artifacts())
    listed = fake.remote_json

    def bad_gateway(url, *, absent=False):
        if "/artifacts" in url:
            raise release.ReleaseError(f"cannot confirm {url}: HTTP Error 502: Bad Gateway")
        return listed(url, absent=absent)

    monkeypatch.setattr(release, "_remote_json", bad_gateway)

    _accuracy(tree)

    assert "gh run rerun" not in _names(fake) and fake.watched == ["99"]
    assert "502" in capsys.readouterr().out


def test_an_expired_artifact_that_is_no_receipt_does_not_block_the_rerun(tree, stage):
    fake = stage([_run(conclusion="failure", id=7)],
                 artifacts=_artifacts(others=["mutants-meta-1"], expired=["mutants-meta-1"]))

    _accuracy(tree)

    assert "gh run rerun" in _names(fake)


@pytest.mark.parametrize("answer", [{}, {"artifacts": None}, {"artifacts": "none"}], ids=["no-key", "null", "text"])
def test_an_unreadable_artifact_list_dispatches_anew(tree, stage, answer):
    fake = stage([_run(conclusion="failure", id=7)], artifacts={"7": answer})

    _accuracy(tree)

    assert "gh run rerun" not in _names(fake) and "gh workflow run" in _names(fake)


def test_a_rerun_gh_refuses_dispatches_anew(tree, stage):
    fake = stage([_run(conclusion="failure", id=7)], artifacts=_artifacts(),
                 refuse="run 7 cannot be rerun: it was created more than 30 days ago")

    _accuracy(tree)

    assert _names(fake) == ["git push -q", "gh run rerun", "gh workflow run", "git push -q"]
    assert fake.watched == ["99"]


def test_a_red_run_at_another_commit_is_not_rerun(tree, stage):
    fake = stage([_run(conclusion="failure", id=7, head_sha=OTHER)], artifacts=_artifacts())

    _accuracy(tree)

    assert "gh run rerun" not in _names(fake) and fake.watched == ["99"]


def test_the_newest_red_run_is_the_one_rerun(tree, stage):
    fake = stage([_run(conclusion="failure", id=7), _run(conclusion="failure", id=12)],
                 artifacts={**_artifacts(7), **_artifacts(12)})

    _accuracy(tree)

    assert fake.commands[1][3] == "12" and fake.watched == ["12"]
