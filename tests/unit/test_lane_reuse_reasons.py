"""The edges of the reason `--reuse-unchanged` gives when it reruns a lane.

The common reasons are driven through the CLI in
tests/e2e/test_reuse_unchanged_reasons_e2e.py; these are the ones a hand-edited
stamp, a broken crapkit.toml or a root git cannot place reach.
"""
import json
import subprocess
from pathlib import Path

from crapkit.cli.scoring import _DIRTY_TREE_NOTE
from crapkit.config import Lane
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
                   capture_output=True, check=True, timeout=60)


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
