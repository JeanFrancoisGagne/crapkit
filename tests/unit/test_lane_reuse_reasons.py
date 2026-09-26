"""The edges of the reason `--reuse-unchanged` gives when it reruns a lane.

The common reasons are driven through the CLI in
tests/e2e/test_reuse_unchanged_reasons_e2e.py; these are the ones a hand-edited
stamp, a broken crapkit.toml or a root git cannot place reach.
"""
import json
import subprocess
from pathlib import Path

from crapkit import gitio
from crapkit.cli.scoring import _DIRTY_TREE_NOTE
from crapkit.config import Lane
from crapkit.errors import GitError
from crapkit.lanes import (_SESSION_VARIABLES, _from_top, _output_names, lane_reuse_verdict,
                           read_stamps, run_lane, uncommitted_changes, write_stamps)

ROOT = Path(__file__).resolve().parents[2]

MAKE_COV = (
    "import json, os, pathlib\n"
    "root = os.getcwd()\n"
    'app = os.path.join(root, "src", "app.ts")\n'
    'data = {app: {"fnMap": {"0": {"name": "one", "decl": {"start": {"line": 1}},\n'
    '    "loc": {"start": {"line": 1}, "end": {"line": 3}}}}, "f": {"0": 1},\n'
    '    "statementMap": {}, "s": {}, "branchMap": {}, "b": {}}}\n'
    'pathlib.Path(root, "out").mkdir(exist_ok=True)\n'
    'pathlib.Path(root, "out", "cov.json").write_text(json.dumps(data), encoding="utf-8")\n'
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                   capture_output=True, check=True)


def _lane() -> Lane:
    return Lane(name="unit", command="python make_cov.py", artifact="out/cov.json",
                parser="istanbul", scopes=("src",))


def _measured(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function one() {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\nout/\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    lane = _lane()
    write_stamps(tmp_path, {lane.artifact: run_lane(tmp_path, lane).stamp})
    return tmp_path


def _edit_stamp(repo: Path, edit) -> None:
    stamps = read_stamps(repo)
    edit(stamps[_lane().artifact])
    (repo / ".crapkit" / "artifacts.json").write_text(json.dumps(stamps), encoding="utf-8")


def test_a_stamp_without_its_parts_says_it_cannot_name_what_moved(tmp_path, monkeypatch):
    repo = _measured(tmp_path)
    _edit_stamp(repo, lambda stamp: stamp.pop("proof_parts"))
    monkeypatch.setenv("CRAPKIT_REASON_PROBE", "moved")

    assert lane_reuse_verdict(repo, _lane()).reason == (
        "crapkit.toml, its lane table or the environment changed, and its stamp does not "
        "record which")


def test_a_stamp_without_artifact_digests_is_named(tmp_path):
    repo = _measured(tmp_path)
    _edit_stamp(repo, lambda stamp: stamp.pop("artifacts"))

    assert lane_reuse_verdict(repo, _lane()).reason == "its stamp records no digest of its artifact"


def test_the_measured_stamp_keeps_digests_never_environment_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_REASON_SECRET", "hunter2-value")
    monkeypatch.setenv("OLDPWD", "/where/the/shell/was")
    repo = _measured(tmp_path)

    stamp = read_stamps(repo)[_lane().artifact]
    assert set(stamp["proof_parts"]) == {"commit", "config", "env", "lane"}
    assert len(stamp["proof_parts"]["env"]["CRAPKIT_REASON_SECRET"]) == 16
    assert "hunter2-value" not in json.dumps(stamp)
    assert "OLDPWD" not in stamp["proof_parts"]["env"]
    assert lane_reuse_verdict(repo, _lane()).reason == ""


def _fail_status_once(monkeypatch) -> None:
    """The first read of the uncommitted set fails, as a full disk or a git
    process that cannot start makes it fail; every later read answers."""
    real, failed = gitio.status_names, []

    def status_names(root):
        if failed:
            return real(root)
        failed.append(root)
        raise GitError("git exited 128: forced")

    monkeypatch.setattr(gitio, "status_names", status_names)


def test_a_git_failure_while_measuring_is_named_not_read_as_uncommitted_changes(tmp_path, monkeypatch):
    """git's read of the clean tree fails as the lane starts. Nothing proved the
    lane, so it reruns, and the rerun names the failed read: the stamp used to
    say the lane was measured with uncommitted changes the tree never had."""
    _fail_status_once(monkeypatch)
    repo = _measured(tmp_path)
    monkeypatch.undo()

    assert lane_reuse_verdict(repo, _lane()).reason == (
        "its stamp holds no proof: git could not read the working tree when it was measured: "
        "git exited 128: forced")


def test_a_stamp_that_records_no_cause_keeps_the_old_sentence(tmp_path):
    """A stamp an older crapkit wrote holds no `unproved`: the rerun can only
    name both causes it may have had."""
    repo = _measured(tmp_path)
    _edit_stamp(repo, lambda stamp: stamp.update(proof=""))

    assert lane_reuse_verdict(repo, _lane()).reason == (
        "its stamp holds no proof: it was measured with uncommitted changes, or by a crapkit "
        "that recorded none")


def test_an_unreadable_commit_since_the_stamp_is_named_as_a_failed_read(tmp_path):
    """A lane that declares inputs is reused while its stamp commit is behind
    HEAD. A commit on the way back to it cannot be read, so `merge-base
    --is-ancestor` exits 1, its "no", and prints `error: Could not read <sha>`:
    the rerun said the artifact was built at a commit not behind HEAD."""
    lane = _lane()._replace(inputs=("src", "make_cov.py"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function one() {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\nout/\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    write_stamps(tmp_path, {lane.artifact: run_lane(tmp_path, lane).stamp})
    lost = _note_commit(tmp_path, "two")
    _note_commit(tmp_path, "three")
    loose = tmp_path / ".git" / "objects" / lost[:2] / lost[2:]
    loose.chmod(0o644)
    loose.unlink()

    reason = lane_reuse_verdict(tmp_path, lane).reason

    assert reason.startswith("nothing proves its inputs unchanged: git "), reason
    assert "merge-base --is-ancestor" in reason and lost in reason


def _note_commit(repo: Path, text: str) -> str:
    (repo / "notes.txt").write_text(text, encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", text)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


def test_a_clean_measurement_records_no_cause(tmp_path):
    stamp = read_stamps(_measured(tmp_path))[_lane().artifact]

    assert stamp["proof"] and "unproved" not in stamp


def test_outside_git_no_change_is_reported(tmp_path):
    assert uncommitted_changes(tmp_path) == []


def test_an_unparsable_crapkit_toml_leaves_only_the_lanes_own_outputs(tmp_path):
    (tmp_path / "crapkit.toml").write_text("[[lane]\nname = ", encoding="utf-8")

    assert _output_names(tmp_path, _lane()) == frozenset({"out/cov.json"})


def test_every_configured_lane_output_is_named_from_the_root(tmp_path):
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
        '[[lane]]\nname = "py"\ncommand = "x"\nartifact = "./scripts/cov.json"\nparser = "istanbul"\n'
        'scopes = ["src"]\nresults_artifact = "scripts\\\\junit.xml"\n', encoding="utf-8")

    assert _output_names(tmp_path, _lane()) == frozenset(
        {"out/cov.json", "scripts/cov.json", "scripts/junit.xml"})


def test_names_under_a_root_git_cannot_place_are_not_excused(tmp_path):
    assert _from_top(tmp_path / "a", tmp_path / "b", frozenset({"out/cov.json"})) == frozenset()


def test_names_under_a_subdirectory_root_are_spelled_from_the_top(tmp_path):
    (tmp_path / "web").mkdir()

    assert _from_top(tmp_path / "web", tmp_path.resolve(), frozenset({"out/cov.json"})) == frozenset(
        {"web/out/cov.json"})


def _flat(page: str) -> str:
    return " ".join((ROOT / page).read_text(encoding="utf-8").split())


def test_the_pages_quote_the_note_a_partial_run_on_a_dirty_tree_prints():
    for page in ("docs/lanes.md", "docs/agent-json.md"):
        assert f"``{_DIRTY_TREE_NOTE}``" in _flat(page), page


def test_every_variable_the_lanes_page_says_the_proof_leaves_out_is_left_out():
    text = _flat("docs/lanes.md")
    named = text.split("The environment half of the proof leaves out", 1)[1].split("so a `cd`", 1)[0]
    quoted = {word.strip("`,.:") for word in named.split() if word.startswith("`")}

    assert quoted and quoted <= _SESSION_VARIABLES, quoted - _SESSION_VARIABLES
