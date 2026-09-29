"""lanes at its edges: the words of each note, the proofs a stamp records, and the owner a run carries.

A cause line is cut from the left at 200 characters, a tail keeps the last whole
lines that fit, and a sample names three paths and counts the rest. A whole-tree
proof is the digest of its sorted parts, and a lane with declared inputs is
proved by its own table bound to HEAD. Every process a lane starts, run or
retest, runs under the caller's owner, or under one the lane takes when the
caller gave none, and writes a log bounded by the lane's own byte limit.
"""
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import lane_changes, lanes
from crapkit.config import Lane
from crapkit.errors import GitError, ToolError
from crapkit.procs import NoProgress

LANE = Lane(name="unit", command="make", artifact="out/cov.json", parser="istanbul", scopes=("src",))
JUNIT = '<testsuite tests="1"><testcase classname="t.py" name="test_a"/></testsuite>'


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                          capture_output=True, check=True, encoding="utf-8").stdout.strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.ts").write_text("export function one() {\n  return 1;\n}\n", encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\nout/\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def coverage(root: Path) -> str:
    app = str(root / "src" / "app.ts")
    return json.dumps({app: {"fnMap": {"0": {"name": "one", "decl": {"start": {"line": 1}},
                                              "loc": {"start": {"line": 1}, "end": {"line": 3}}}},
                             "f": {"0": 1}, "statementMap": {}, "s": {}, "branchMap": {}, "b": {}}})


def fake_runs(monkeypatch, root: Path, *, files=None, output: str = "", stall=False) -> list:
    """Every command a lane starts, recorded; each writes `files` and `output`."""
    calls = []

    def run_bounded(command, deadline, *, stream, no_progress=None, owner=None, **kwargs):
        calls.append(SimpleNamespace(command=command, deadline=deadline, stream=stream, owner=owner,
                                     env=kwargs.get("env") or {}))
        stream.write(output)
        if stall:
            raise NoProgress(3)
        for name, text in (files or {}).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        return 0

    monkeypatch.setattr(lanes, "run_bounded", run_bounded)
    return calls


def test_a_cause_line_is_cut_from_the_left_at_200_characters():
    line = "".join(chr(97 + index % 26) for index in range(201))

    assert lanes._cut_cause(line[:200]) == line[:200]
    assert lanes._cut_cause(line) == "..." + line[-197:]


def test_the_last_attempt_starts_after_its_banner_and_a_tail_keeps_whole_lines():
    assert lanes._last_attempt(["a", "--- attempt 2 ---", "b", "c"]) == ["b", "c"]
    assert lanes._tail_lines(["aaa", "bb", "c"], 5) == ["bb", "c"]
    assert lanes._tail_lines(["abcdefgh"], 3) == ["...fgh"]


def test_the_words_a_note_is_built_from():
    assert lanes._leftover_words(["a.json", "b.xml", "c.txt"])[0] == "the a.json, b.xml and c.txt on disk"
    assert lanes._sample(["d", "c", "b", "a"]) == "a, b, c and 1 more"
    assert lanes._unproved(lanes._Proof("", {}), lanes._Proof("", {})) == "what it reads changed while it ran"
    assert lanes._unproved_reason({"unproved": ""}) == (
        "it was measured with uncommitted changes, or by a crapkit that recorded none")
    assert lanes._stamp_commit({"commit": 5}) == ""
    assert lanes._declared_paths(LANE._replace(scopes=("src", "lib")), {"src": ["src"]}) == ("src",)


def test_an_environment_move_names_only_the_variables_that_differ():
    assert lanes._environment_moved({"A": "1", "B": "2"}, {"A": "1", "B": "3", "C": "4"}) == ["B", "C"]
    assert lanes._environment_moved({"A": "1"}, None) == ["A"]
    same = {"config": "x", "lane": "y", "env": {"A": "1"}}
    moved = {"config": "z", "lane": "w", "env": {"A": "1"}}
    assert lanes._moved_parts(same, moved) == "crapkit.toml changed; its lane table changed"


def test_an_unreadable_proof_and_the_proof_fields_say_why():
    unread = lanes._unread(OSError("boom"), "x")
    fields = lanes._proof_fields(lanes._Proof("", {}, "", "w"), lanes._Proof("", {}))

    assert unread == lanes._Proof("", {}, "nothing proves its inputs unchanged: boom",
                                  "git could not read x when it was measured: boom")
    assert fields == {"proof": "", "proof_parts": {}, "unproved": "w"}
    assert lanes._proof_fields(lanes._Proof("k1", {}), lanes._Proof("k2", {}, "", "x"))["unproved"] == "x"


def test_a_stamp_entry_records_the_commit_lane_seconds_and_digests(monkeypatch, tmp_path):
    proof = lanes._Proof("k", {"commit": "c"})
    monkeypatch.setattr(lanes, "_measurement_proof", lambda root, lane: proof)
    facts = SimpleNamespace(head_commit=lambda: "c", root=tmp_path)

    entry = lanes._stamp_entry(facts, LANE, 12.345, proof, {"artifact_sha256": "d"})

    assert entry == {"commit": "c", "lane": "unit", "seconds": 12.3, "proof": "k",
                     "proof_parts": {"commit": "c"}, "artifacts": {"out/cov.json": "d"}}


def test_the_inputs_key_binds_the_lane_to_a_commit():
    payload = json.dumps(["inputs", "c", list(LANE)], sort_keys=True).encode("utf-8")

    assert lanes._inputs_key("c", LANE) == sha(payload)
    assert lanes._inputs_gap(Path("."), LANE, "other", "c") == (
        "its lane table or env differs from the one it was measured with")


def test_a_clean_tree_is_proved_by_its_sorted_parts_and_a_dirty_one_is_named(repo):
    proof = lanes._whole_tree_proof(repo, LANE)
    lane_digest = sha(json.dumps(LANE, sort_keys=True).encode("utf-8"))
    (repo / "src" / "app.ts").write_text("changed\n", encoding="utf-8")

    assert (proof.parts["config"], proof.parts["lane"]) == (sha(b""), lane_digest)
    assert proof.key == sha(json.dumps(proof.parts, sort_keys=True).encode("utf-8"))
    assert lanes._whole_tree_proof(repo, LANE) == lanes._Proof(
        "", {}, "the working tree has 1 uncommitted change(s): src/app.ts",
        "it was measured with 1 uncommitted change(s): src/app.ts")


def test_declared_inputs_are_proved_by_the_lane_at_head_or_named_when_dirty(repo, monkeypatch):
    lane = LANE._replace(inputs=("src",))
    clean = lanes._declared_inputs_proof(repo, lane)
    (repo / "src" / "app.ts").write_text("changed\n", encoding="utf-8")

    assert clean == lanes._Proof(lanes._inputs_key(git(repo, "rev-parse", "HEAD"), lane), {})
    assert lanes._declared_inputs_proof(repo, lane) == lanes._Proof(
        "", {}, "its inputs have 1 uncommitted change(s): src/app.ts",
        "it was measured with 1 uncommitted change(s) under its inputs: src/app.ts")
    monkeypatch.setattr(lanes, "_output_names", lambda root, lane: (_ for _ in ()).throw(OSError("boom")))
    assert lanes._declared_inputs_proof(repo, lane) == lanes._unread(OSError("boom"), "its inputs")


def test_inputs_moved_since_a_commit_or_not_behind_head_say_so_in_eleven_characters(repo):
    lane = LANE._replace(inputs=("src",))
    first = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "app.ts").write_text("changed\n", encoding="utf-8")
    git(repo, "commit", "-qam", "two")

    assert lanes._inputs_moved(repo, lane, first) == f"1 change(s) under its inputs since {first[:11]}: src/app.ts"
    assert lanes._inputs_moved(repo, lane, "0123456789abcdef" * 2 + "01234567") == (
        "its artifact was built at 0123456789a, which is not behind HEAD")


def test_the_head_gap_names_both_commits_in_eleven_characters(monkeypatch):
    monkeypatch.setattr(lanes, "_whole_tree_proof", lambda root, lane: lanes._Proof("k", {"commit": "0123456789abcdef"}))

    assert lanes._whole_tree_gap(Path("."), LANE, {"proof": "k"}, "fedcba9876543210") == (
        "HEAD is 0123456789a and its artifact was built at fedcba98765")


def test_an_inherited_variable_holding_a_lone_surrogate_is_digested(monkeypatch):
    monkeypatch.setenv("CRAPKIT_EDGE_VALUE", "a\udcffb")

    digests = lanes._environment_digests()

    assert digests["CRAPKIT_EDGE_VALUE"] == sha("a\udcffb".encode("utf-8", "surrogatepass"))[:16]


def test_the_container_test_reads_the_variable_or_the_marker_file(monkeypatch):
    marker = {"there": False}
    monkeypatch.setattr(lanes, "Path", lambda path: SimpleNamespace(
        exists=lambda: path == "/.dockerenv" and marker["there"]))
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    flagged = lanes._in_container()
    monkeypatch.delenv("CRAPKIT_INSIDE_CONTAINER")
    neither = lanes._in_container()
    marker["there"] = True

    assert (flagged, neither, lanes._in_container()) == (True, False, True)


def test_files_that_cannot_be_read_answer_empty(tmp_path):
    (tmp_path / "log").write_bytes(b"ok\n\xff\n")
    (tmp_path / "stamps").mkdir()
    (tmp_path / "stamps" / ".crapkit").mkdir()
    (tmp_path / "stamps" / ".crapkit" / "artifacts.json").write_text("[1]", encoding="utf-8")

    assert lanes._file_digest(tmp_path / "gone") == ""
    assert lanes._log_lines(tmp_path / "log") == ["ok", "�"]
    assert lanes.read_stamps(tmp_path / "stamps") == {}


def test_reports_written_as_utf8_read_as_utf8(tmp_path):
    report = '<testsuite name="Á" tests="1" time="2.5"><testcase classname="t.py" name="test_a"/></testsuite>'
    (tmp_path / "junit.xml").write_bytes(report.encode("utf-8"))
    (tmp_path / "log").write_bytes("Á\n".encode("utf-8"))
    (tmp_path / ".crapkit").mkdir()
    (tmp_path / ".crapkit" / "artifacts.json").write_bytes('{"Á": {}}'.encode("utf-8"))
    lane = LANE._replace(results_artifact="junit.xml")

    assert lanes._junit_seconds(tmp_path / "junit.xml") == 2.5
    assert lanes._log_lines(tmp_path / "log") == ["Á"]
    assert lanes.read_stamps(tmp_path) == {"Á": {}}
    assert lanes._still_failed(tmp_path, lane) == set()
    assert lanes._retested_passes(tmp_path, lane, None) == {"t.py::test_a"}


def test_logs_and_stamps_land_in_directories_made_on_the_way(tmp_path):
    root = tmp_path / "not" / "yet"

    log = lanes._lane_log_path(tmp_path / "log" / "root", LANE)
    lanes.write_stamps(root, {"b.json": {"seconds": 1}, "a.json": {"seconds": 2}})

    assert log.parent.is_dir()
    assert (root / ".crapkit" / "artifacts.json").read_text(encoding="utf-8") == json.dumps(
        {"a.json": {"seconds": 2}, "b.json": {"seconds": 1}}, sort_keys=True, indent=1)


def test_a_lane_that_never_ran_starts_after_one_that_took_half_a_second(tmp_path):
    slow, fresh = LANE._replace(name="slow", artifact="a.json"), LANE._replace(name="fresh", artifact="b.json")
    lanes.write_stamps(tmp_path, {"a.json": {"seconds": 0.5}})

    assert [lane.name for lane in lanes.lane_order(tmp_path, [fresh, slow])] == ["slow", "fresh"]


def test_the_output_locks_live_under_the_home_cache_keyed_by_host_and_path(tmp_path):
    path = tmp_path / "out.json"
    host = sha(socket.gethostname().encode("utf-8"))[:16]
    key = sha(os.path.normcase(str(path.resolve())).encode("utf-8"))

    assert lanes._output_lock(path) == (
        Path.home() / ".cache" / "crapkit" / "measurements" / host / f"measurement-{key}.lock")
    with lanes.measurement_owner(tmp_path, (LANE,)):
        assert (tmp_path / ".crapkit" / "measurement.lock").is_file()


def test_the_reads_fall_back_to_facts_about_the_root(tmp_path, monkeypatch):
    def refused(*args):
        raise GitError("no git")

    with lanes.staleness_reads(tmp_path, [], {}) as facts:
        assert facts.root == tmp_path
    monkeypatch.setattr(lane_changes, "ChangeReads", refused)
    with lanes._started_reads(tmp_path, ("c",), ()) as facts:
        assert facts.root == tmp_path


def test_the_hints_and_notes_hold_their_exact_words(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lanes, "_scope_changes", lambda git, lane, paths, commit: [])
    quiet = lanes._stale_artifact_note(None, LANE, {}, "c")
    monkeypatch.setattr(lanes, "_scope_changes", lambda git, lane, paths, commit: ["src/app.ts"])
    lanes._warn_unreadable_results(LANE._replace(results_artifact="r.xml"), ToolError("bad"))

    assert (quiet, lanes._stale_artifact_note(None, LANE, {}, "c")) == (
        "", "1 file(s) in its scopes changed since (their coverage is stale)")
    assert lanes._missing_plugin_hint("error: unrecognized arguments: -x", LANE, None) == ""
    assert lanes._pytest_cov_home(LANE._replace(command="npm test"), None) == (
        "the environment the lane's suite runs in")
    assert capsys.readouterr().err == (
        "crapkit: lane 'unit' reused r.xml and cannot check it: bad; the crashed-worker and "
        "no-new-failures checks cannot run for this lane\n")


def test_a_shard_note_counts_its_shards_and_a_js_lane_gets_none(tmp_path):
    lane = LANE._replace(parser="coveragepy", artifact="cov.json")
    (tmp_path / ".coverage.a").write_text("", encoding="utf-8")
    one = lanes._shard_hint(tmp_path, lane)
    (tmp_path / ".coverage.b").write_text("", encoding="utf-8")

    assert " coverage shard (.coverage.a, ...) sits in " in one
    assert " coverage shards (.coverage.a, ...) sit in " in lanes._shard_hint(tmp_path, lane)
    assert lanes._shard_hint(tmp_path, LANE) == ""


def test_a_missing_artifact_with_no_log_names_the_log_and_nothing_after(tmp_path):
    log = tmp_path / "lane.log"

    with pytest.raises(ToolError) as caught:
        lanes._raise_no_artifact(tmp_path, LANE, log, None)

    assert str(caught.value) == f"lane 'unit' produced no artifact at out/cov.json; lane log: {log}"


def test_a_root_spelled_with_its_trailing_separator_holds_its_children():
    root = os.sep + "a" + os.sep

    assert lanes._under(root, root + "b") is True


@pytest.mark.skipif(os.name == "nt", reason="Windows resolves a name up to its NUL instead of refusing it")
def test_a_name_the_platform_cannot_resolve_is_answered_as_written():
    assert lanes._resolved("a\0b") == "a\0b"


def test_an_unmeasured_lane_with_no_coverage_names_none():
    message = lanes._unmeasured_message(LANE, {}, ("src",))

    assert "scopes will score untested — either nothing in them" in message


def test_every_run_carries_the_callers_owner_or_one_the_lane_takes(repo, monkeypatch):
    lane = LANE._replace(log_max_bytes=64)
    calls = fake_runs(monkeypatch, repo, files={lane.artifact: coverage(repo)}, output="x" * 200)
    (repo / ".crapkit").mkdir()
    (repo / ".crapkit" / "lane-unit.log").write_text("old run\n", encoding="utf-8")
    owner = object()

    taken = lanes.run_lane(repo, lane)
    lanes.run_lane(repo, lane, owner=owner)

    assert calls[0].owner is not None and calls[1].owner is owner
    assert taken.stamp["seconds"] < 60
    log = (repo / ".crapkit" / "lane-unit.log").read_text(encoding="utf-8")
    assert "old run" not in log and len(log) <= 64


def test_a_lane_that_writes_nothing_is_tried_once_per_retry_and_once_more(repo, monkeypatch):
    calls = fake_runs(monkeypatch, repo)

    with pytest.raises(ToolError):
        lanes.run_lane(repo, LANE._replace(retries=1))

    assert len(calls) == 2


def test_a_stalled_lane_names_its_log(repo, monkeypatch):
    fake_runs(monkeypatch, repo, stall=True)

    with pytest.raises(ToolError) as caught:
        lanes.run_lane(repo, LANE)

    assert str(caught.value).endswith(f"log: {repo / '.crapkit' / 'lane-unit.log'}")


def test_a_reused_artifact_warns_about_changed_scopes_and_stamps_nothing(repo, monkeypatch, capsys):
    fake_runs(monkeypatch, repo, files={LANE.artifact: coverage(repo)})
    lanes.write_stamps(repo, {LANE.artifact: lanes.run_lane(repo, LANE).stamp})
    monkeypatch.setattr(lanes, "_scope_changes", lambda git, lane, paths, commit: ["src/app.ts"])

    reused = lanes.run_lane(repo, LANE, reuse_artifact=True, scope_paths={"src": ["src"]})

    head = git(repo, "rev-parse", "HEAD")
    assert reused.stamp == {}
    assert f"crapkit: lane 'unit' artifact was built at {head[:11]}; 1 file(s) in its scopes" in (
        capsys.readouterr().err)


def test_a_retest_appends_to_the_lane_log_under_the_lanes_deadline_and_owner(repo, monkeypatch):
    lane = LANE._replace(retest_command="retest {tests}", results_artifact="out/junit.xml", timeout_seconds=7)
    calls = fake_runs(monkeypatch, repo, files={lane.results_artifact: JUNIT})
    (repo / ".crapkit").mkdir()
    (repo / ".crapkit" / "lane-unit.log").write_text("earlier\n", encoding="utf-8")

    passed = lanes.retest_lane(repo, lane, {"t.py::test_a"})

    literals = {key: value for key, value in calls[0].env.items() if key.startswith("CRAPKIT_LITERAL")}
    assert literals == lanes._retest_template(lane.retest_command, {"t.py::test_a"})[1]
    assert passed == {"t.py::test_a"}
    assert (calls[0].deadline, calls[0].owner is not None, calls[0].stream is not None) == (7, True, True)
    assert (repo / ".crapkit" / "lane-unit.log").read_text(encoding="utf-8").startswith("earlier\n")


def test_a_retest_log_stays_within_the_lanes_byte_limit(repo, monkeypatch):
    lane = LANE._replace(retest_command="retest {tests}", results_artifact="out/junit.xml", log_max_bytes=64)
    fake_runs(monkeypatch, repo, files={lane.results_artifact: JUNIT}, output="x" * 200)

    lanes.retest_lane(repo, lane, {"t.py::test_a"})

    assert len((repo / ".crapkit" / "lane-unit.log").read_text(encoding="utf-8")) <= 64


def test_a_retest_template_gets_the_sorted_tests_their_files_and_one_name_pattern(monkeypatch):
    from crapkit import procs

    seen = []
    monkeypatch.setattr(procs, "prepare_template", lambda template, values: seen.append(values) or ("", {}))
    tests = {"tests/b.py::test_x[1-a::b]", "tests/a.py::C::test_y", "tests/a.py"}

    lanes._retest_template("run {tests}", tests)

    assert seen == [{"tests": sorted(tests), "files": ["tests/a.py", "tests/b.py"],
                     "names": [r"C::test_y|test_x\[1\-a::b\]"]}]


def test_the_config_outputs_come_from_the_bytes_given_not_the_file(tmp_path):
    (tmp_path / "crapkit.toml").write_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
        '[[lane]]\nname = "py"\ncommand = "x"\nartifact = "other.json"\nparser = "istanbul"\n'
        'scopes = ["src"]\n', encoding="utf-8")

    assert lanes._output_names(tmp_path, LANE, b"") == frozenset({"out/cov.json"})


def test_a_first_attempt_starts_its_log_afresh(repo, monkeypatch):
    fake_runs(monkeypatch, repo, files={LANE.artifact: coverage(repo)})
    (repo / ".crapkit").mkdir()
    (repo / ".crapkit" / "lane-unit.log").write_text("old run\n", encoding="utf-8")

    lanes.run_lane(repo, LANE)

    assert "old run" not in (repo / ".crapkit" / "lane-unit.log").read_text(encoding="utf-8")


def test_a_run_stamps_the_head_its_callers_facts_name(repo, monkeypatch):
    fake_runs(monkeypatch, repo, files={LANE.artifact: coverage(repo)})
    facts = SimpleNamespace(root=repo, head_commit=lambda: "f" * 40)

    assert lanes.run_lane(repo, LANE, git=facts).stamp["commit"] == "f" * 40


def test_a_lone_sources_check_reads_git_at_the_root_wherever_the_caller_stands(repo, tmp_path, monkeypatch):
    fake_runs(monkeypatch, repo, files={LANE.artifact: coverage(repo)})
    lanes.write_stamps(repo, {LANE.artifact: lanes.run_lane(repo, LANE).stamp})
    monkeypatch.chdir(tmp_path)

    assert lanes.lane_sources_gap(repo, LANE, {"src": ["src"]}) is None
