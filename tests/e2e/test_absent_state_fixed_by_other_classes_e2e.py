"""Absent state read as an answer, in four places, driven through the CLI.

- claude-hook before the first commit: `git diff HEAD` fails on an unborn HEAD,
  and the failed diff read as "nothing changed" in a staged file.
- the dark-line note for a lane with no stamp: an absent stamp read as edits
  nobody made, "files in its scopes changed". A path no lane covers must not
  borrow another lane's note either.
- the reuse refusal a failed attempt records lives in .crapkit/artifacts.json,
  so a lost stamps file took the refusal with it and `coverage
  --reuse-artifacts` scored the dead lane's leftover. Stamps written by crapkit
  0.4.15 never held a refusal: the upgrade limit and its remedy.
- a coverage.py report missing a member every current producer writes, and an
  istanbul branch record with fewer or more hit counts than its locations.

Every variation runs, the passing ones included. A case whose fix had not
landed was a strict xfail that said what fixes it; the stamp-freshness work
landed each of those fixes, and the markers came off.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import hang_guard
import process_table
from conftest import child_env, git, git_commit_all, git_init_repo, run_cli

CRAPKIT_TREE = Path(__file__).resolve().parents[2]
FALSE_CAUSE = "files in its scopes changed"


def write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def new_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for rel, text in files.items():
        write(repo, rel, text)
    return git_init_repo(repo)


def committed(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = new_repo(tmp_path, files)
    git_commit_all(repo, "init")
    return repo


def toml(scopes: dict[str, str], lanes: str) -> str:
    """crapkit.toml: one python scope per name -> dir, then the lane blocks."""
    blocks = "".join(f'\n[[scope]]\nname = "{name}"\npaths = ["{path}"]\nlanguages = ["python"]\n'
                     for name, path in scopes.items())
    return f"[crapkit]\ntarget = 6\n{blocks}\n{lanes}"


def istanbul_lane(name: str, scope: str, command: str, artifact: str) -> str:
    return (f'[[lane]]\nname = "{name}"\ncommand = "{command}"\nartifact = "{artifact}"\n'
            f'parser = "istanbul"\nscopes = ["{scope}"]\n')


# One ccn-2 function at lines 1-4, the span the lane's record gives it.
HOT = "def hot(n):\n    if n:\n        return 1\n    return 0\n"

# Writes the istanbul record for one 4-line function `fn` in `rel` to `out`:
# `python cov.py <rel> <fn> <out>`. Every statement ran.
COV_WRITER = '''import json, os, sys
rel, fn, out = sys.argv[1:4]
key = os.path.join(os.getcwd(), *rel.split("/"))
record = {"path": key,
  "fnMap": {"0": {"name": fn, "decl": {"start": {"line": 1}},
                  "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
  "f": {"0": 1}, "branchMap": {}, "b": {},
  "statementMap": {"1": {"start": {"line": 2}, "end": {"line": 2}}}, "s": {"1": 1}}
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
json.dump({key: record}, open(out, "w", encoding="utf-8"))
'''


def run_writer(repo: Path, *argv: str) -> None:
    res = hang_guard.run([sys.executable, *argv], cwd=repo, text=True)
    assert res.returncode == 0, res.stderr


# --- claude-hook before the first commit --------------------------------------

KNOT = ("def knot(a, b, c, d, e, f, g, h, i):\n"
        + "".join(f"    if {v}:\n        return {n}\n" for n, v in enumerate("abcdefghi"))
        + "    return 0\n")
HOOK_TOML = toml({"src": "src"}, istanbul_lane("py", "src", "never runs", "cov.json"))


def hook(repo: Path) -> subprocess.CompletedProcess:
    event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(repo),
             "tool_input": {"file_path": str(repo / "src" / "a.py")}}
    return run_cli(repo, "claude-hook", "--protocol", "1", stdin=json.dumps(event),
                   encoding="utf-8", errors="replace")


def staged_before_first_commit(tmp_path: Path) -> Path:
    repo = new_repo(tmp_path, {"crapkit.toml": HOOK_TOML, "src/a.py": KNOT})
    git(repo, "add", "-A")
    return repo


def untracked_before_first_commit(tmp_path: Path) -> Path:
    return new_repo(tmp_path, {"crapkit.toml": HOOK_TOML, "src/a.py": KNOT})


def edited_after_first_commit(tmp_path: Path) -> Path:
    repo = committed(tmp_path, {"crapkit.toml": HOOK_TOML, "src/a.py": "def knot(a):\n    return a\n"})
    write(repo, "src/a.py", KNOT)
    return repo


@pytest.mark.parametrize("build", [
    pytest.param(staged_before_first_commit, id="staged-before-first-commit"),
    pytest.param(untracked_before_first_commit, id="untracked-before-first-commit"),
    pytest.param(edited_after_first_commit, id="edited-after-first-commit"),
])
def test_claude_hook_names_the_function_over_the_ceiling(build, tmp_path: Path):
    repo = build(tmp_path)

    res = hook(repo)

    assert res.returncode == 2, res.stderr
    assert "ccn 10" in " ".join(res.stderr.split()) and "knot(" in res.stderr, res.stderr


def test_the_commit_gate_stops_the_same_function_in_the_root_commit(tmp_path: Path):
    """The gate reads the index, so it never depended on HEAD: the advisory above
    is the only half an unborn HEAD silenced."""
    repo = staged_before_first_commit(tmp_path)

    res = run_cli(repo, "hook-precommit", encoding="utf-8", errors="replace")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "knot(" in res.stdout + res.stderr


# --- the dark-line note when a lane has no stamp ------------------------------

STAMP_FILES = {
    "src/a.py": HOT,
    "cov.py": COV_WRITER,
    "crapkit.toml": toml({"src": "src"},
                         istanbul_lane("py", "src", "python cov.py src/a.py hot cov.json",
                                       "cov.json")),
    ".gitignore": ".crapkit/\ncov.json\n",
}


def stamped(repo: Path) -> None:
    assert run_cli(repo, "coverage").returncode == 0


def reuse_only(repo: Path) -> None:
    """The artifact came from elsewhere (a CI job, a shard merge) and was only
    ever reused. Reuse writes no stamp."""
    run_writer(repo, "cov.py", "src/a.py", "hot", "cov.json")
    assert run_cli(repo, "coverage", "--reuse-artifacts").returncode == 0


def stamps_deleted(repo: Path) -> None:
    stamped(repo)
    (repo / ".crapkit" / "artifacts.json").unlink()


def stamp_mangled(repo: Path) -> None:
    stamped(repo)
    path = repo / ".crapkit" / "artifacts.json"
    stamps = json.loads(path.read_text(encoding="utf-8"))
    stamps["cov.json"] = "mangled"
    path.write_text(json.dumps(stamps), encoding="utf-8")


def dark_lines(repo: Path) -> dict:
    res = run_cli(repo, "explain", "src/a.py", "hot", "--json", encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)["functions"][0]


def assert_clean(repo: Path) -> None:
    status = hang_guard.run(["git", "status", "--porcelain"], cwd=repo, text=True).stdout
    assert status.strip() == "", "every case here runs on a clean tree"


def test_a_stamped_lane_names_its_dark_lines(tmp_path: Path):
    repo = committed(tmp_path, STAMP_FILES)
    stamped(repo)
    assert_clean(repo)

    fn = dark_lines(repo)

    assert fn["uncovered_lines"] == [] and "uncovered_lines_note" not in fn


# The cause a missing stamp gets is the lane freshness verdict's, which reads the
# stamp file's explicit states (lane_stamps, Q33, Q67).
@pytest.mark.parametrize("lose_stamp", [
    pytest.param(reuse_only, id="reuse-only"),
    pytest.param(stamps_deleted, id="stamps-deleted"),
    pytest.param(stamp_mangled, id="stamp-mangled"),
])
def test_a_missing_stamp_is_named_and_a_real_run_clears_it(lose_stamp, tmp_path: Path):
    """On a clean tree the note used to read "files in its scopes changed since
    cov.json was written (uncommitted edits count)" and told the reader to
    commit, which cannot clear it. The cause is the missing stamp, and a
    `coverage` run that runs the lane writes one."""
    repo = committed(tmp_path, STAMP_FILES)
    lose_stamp(repo)
    assert_clean(repo)

    fn = dark_lines(repo)
    brief = json.loads(run_cli(repo, "brief", "src/a.py", "hot", "--json",
                               encoding="utf-8").stdout)
    assert run_cli(repo, "report").returncode == 0
    page = (repo / ".crapkit" / "report.html").read_text(encoding="utf-8")

    note = fn["uncovered_lines_note"]
    assert fn["uncovered_lines"] is None
    assert "lane 'py'" in note and "no stamp" in note, note
    assert "coverage`" in note and "--reuse-artifacts" not in note, \
        f"the step that clears it is a run of the lane: {note}"
    assert FALSE_CAUSE not in note and "commit or revert" not in note, note
    assert brief["uncovered_lines_note"] == note
    assert FALSE_CAUSE not in page

    stamped(repo)

    assert dark_lines(repo)["uncovered_lines"] == [], "the real run wrote the stamp"


# --- the note for a path no lane covers --------------------------------------

NO_LANE_FILES = {
    "src/a.py": HOT,
    "lib/b.py": HOT.replace("hot", "cold"),
    "cov.py": COV_WRITER,
    "crapkit.toml": toml({"src": "src", "lib": "lib"},
                         istanbul_lane("lib", "lib", "python cov.py lib/b.py cold cov.json",
                                       "cov.json")),
    ".gitignore": ".crapkit/\ncov.json\n",
}


def lib_never_stamped(repo: Path) -> None:
    run_writer(repo, "cov.py", "lib/b.py", "cold", "cov.json")
    assert run_cli(repo, "coverage", "--reuse-artifacts").returncode == 0


def lib_edited_since_measured(repo: Path) -> None:
    assert run_cli(repo, "coverage").returncode == 0
    write(repo, "lib/b.py", HOT.replace("hot", "cold") + "\n\nX = 1\n")


def brief_of(repo: Path, path: str, fn: str) -> subprocess.CompletedProcess:
    res = run_cli(repo, "brief", path, fn, "--json", encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res


def brief_fields(repo: Path) -> dict:
    body = json.loads(brief_of(repo, "src/a.py", "hot").stdout)
    assert body["scored"]["flag"] == "no-lane"
    return body


@pytest.mark.parametrize("stale_lib", [lib_never_stamped, lib_edited_since_measured],
                         ids=["lib-never-stamped", "lib-edited-since-measured"])
@pytest.mark.parametrize("reader", [
    pytest.param(brief_fields, id="brief"),
    pytest.param(dark_lines, id="explain"),
])
def test_a_path_no_lane_covers_never_reads_another_lanes_note(reader, stale_lib, tmp_path: Path):
    """Lane 'lib' covers scope 'lib' only. Its note, stale or unstamped, used to
    reach src/a.py too, and rerunning lane 'lib' as it said changes nothing for a
    scope no lane measures."""
    repo = committed(tmp_path, NO_LANE_FILES)
    stale_lib(repo)

    src = reader(repo)

    assert src["uncovered_lines"] is None
    assert src["uncovered_lines_note"] == (
        "no lane covers scope 'src', so no artifact can name uncovered lines for src/a.py; "
        "add 'src' to a [[lane]]'s scopes to measure it")


def test_the_lane_note_still_reaches_the_lanes_own_files(tmp_path: Path):
    repo = committed(tmp_path, NO_LANE_FILES)
    lib_never_stamped(repo)

    lib = json.loads(brief_of(repo, "lib/b.py", "cold").stdout)

    assert lib["uncovered_lines"] is None
    assert "lane 'lib'" in lib["uncovered_lines_note"]


# --- the reuse refusal a lost stamps file takes with it -----------------------

# Writes coverage/unit.json while lane_mode.txt says ok; exits 1 and writes
# nothing when it says fail, which leaves the previous run's artifact on disk.
LANE = '''import pathlib, subprocess, sys
if pathlib.Path("lane_mode.txt").read_text().strip() == "fail":
    sys.exit(1)
sys.exit(subprocess.run([sys.executable, "cov.py", "src/a.py", "hot", "coverage/unit.json"]).returncode)
'''

REFUSAL_FILES = {
    "src/a.py": HOT,
    "cov.py": COV_WRITER,
    "lane.py": LANE,
    "crapkit.toml": toml({"src": "src"},
                         istanbul_lane("unit", "src", "python lane.py", "coverage/unit.json")),
    ".gitignore": ".crapkit/\ncoverage/\nlane_mode.txt\n",
}
REFUSED_KEY = "coverage/unit.json"


def lane_mode(repo: Path, mode: str) -> None:
    (repo / "lane_mode.txt").write_text(mode, encoding="utf-8")


def measured_then_dead(repo: Path, **writer) -> None:
    """Run 1 writes the artifact; run 2's lane exits 1 and writes nothing, so
    the artifact on disk is run 1's and the stamp records the refusal."""
    lane_mode(repo, "ok")
    first = run_cli(repo, "coverage", **writer)
    assert first.returncode == 0, first.stderr
    lane_mode(repo, "fail")
    failed = run_cli(repo, "coverage", **writer)
    assert failed.returncode == 5, failed.stderr
    assert "wrote no artifact this run" in failed.stderr, failed.stderr


def stamps_path(repo: Path) -> Path:
    return repo / ".crapkit" / "artifacts.json"


def intact(repo: Path) -> None:
    pass


def file_deleted(repo: Path) -> None:
    stamps_path(repo).unlink()


def cut_short(repo: Path) -> None:
    """What a crash in the middle of the plain write leaves."""
    text = stamps_path(repo).read_text(encoding="utf-8")
    stamps_path(repo).write_text(text[: len(text) // 2], encoding="utf-8")


def entry_not_an_object(repo: Path) -> None:
    stamps = json.loads(stamps_path(repo).read_text(encoding="utf-8"))
    stamps[REFUSED_KEY] = "hand-edited"
    stamps_path(repo).write_text(json.dumps(stamps), encoding="utf-8")


def top_level_not_an_object(repo: Path) -> None:
    stamps = json.loads(stamps_path(repo).read_text(encoding="utf-8"))
    stamps_path(repo).write_text(json.dumps([stamps]), encoding="utf-8")


def trusted_run_ids(repo: Path) -> list[int]:
    res = run_cli(repo, "runs", "--json", encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return [r["id"] for r in json.loads(res.stdout)["runs"] if r["kind"] == "coverage"]


@pytest.mark.parametrize("damage", [
    pytest.param(intact, id="stamps-intact"),
    pytest.param(file_deleted, id="stamps-file-deleted"),
    pytest.param(cut_short, id="stamps-file-cut-short-mid-write"),
    pytest.param(entry_not_an_object, id="stamps-entry-not-an-object"),
    pytest.param(top_level_not_an_object, id="stamps-top-level-not-an-object"),
])
def test_reuse_never_scores_the_leftover_of_a_lane_that_failed(damage, tmp_path: Path):
    repo = committed(tmp_path, REFUSAL_FILES)
    measured_then_dead(repo)
    damage(repo)

    reused = run_cli(repo, "coverage", "--reuse-artifacts", encoding="utf-8", errors="replace")

    assert reused.returncode == 5, reused.stdout + reused.stderr
    assert "lane 'unit'" in reused.stderr and "unit.json" in reused.stderr, reused.stderr
    assert trusted_run_ids(repo) == [1], "the dead lane's leftover became no trusted run"


def old_release_src(tag: str, into: Path) -> Path:
    """The `src` tree of a release tag, extracted from this checkout's git."""
    res = hang_guard.run(["git", "-C", str(CRAPKIT_TREE), "archive", "--format=tar", tag, "src"])
    if res.returncode != 0:
        pytest.skip(f"needs the {tag} tag in the checkout (CI checks out with fetch-depth: 0): "
                    f"{res.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(res.stdout)) as archive:
        extract(archive, into)
    return into / "src"


def old_release_env(src: Path) -> dict:
    """A child that imports the old release's crapkit first. A measured run of
    the suite passes its coverage config to every child through the
    environment, and would measure the old release as crapkit too."""
    return {"PYTHONPATH": str(src), "COVERAGE_PROCESS_CONFIG": None,
            "COVERAGE_PROCESS_START": None}


def extract(archive: tarfile.TarFile, into: Path) -> None:
    if hasattr(tarfile, "data_filter"):
        archive.extractall(into, filter="data")
    else:  # Python 3.11 before 3.11.4 has no extraction filters
        archive.extractall(into)


def test_stamps_written_by_0_4_15_hold_no_refusal_and_one_real_run_restores_it(tmp_path: Path):
    """0.4.15 failed a lane that wrote nothing (exit 5) but recorded no refusal,
    and nothing else on disk says the attempt failed. So the first reuse after
    the upgrade scores the leftover: the documented upgrade limit. The remedy
    the upgrade note gives is one `coverage` run without --reuse-artifacts,
    which reruns the lane and records the refusal the old release never wrote.
    If a later release refuses this reuse, the first assertion flips and the
    upgrade note goes with it."""
    old_env = old_release_env(old_release_src("v0.4.15", tmp_path / "old"))
    old = {"spawn": True, "env_extra": old_env, "encoding": "utf-8", "errors": "replace"}
    imported = hang_guard.run([sys.executable, "-c", "import crapkit; print(crapkit.__version__)"],
                              env=child_env(old_env), text=True)
    assert imported.stdout.strip() == "0.4.15", imported.stdout + imported.stderr
    repo = committed(tmp_path, REFUSAL_FILES)
    with process_table.hold(naming=False):
        measured_then_dead(repo, **old)
    stamps = json.loads(stamps_path(repo).read_text(encoding="utf-8"))
    assert "refused_mtime_ns" not in stamps[REFUSED_KEY], "0.4.15 records no refusal"

    first_reuse = run_cli(repo, "coverage", "--reuse-artifacts", encoding="utf-8", errors="replace")
    real_run = run_cli(repo, "coverage", encoding="utf-8", errors="replace")
    reuse_after = run_cli(repo, "coverage", "--reuse-artifacts", encoding="utf-8", errors="replace")

    assert first_reuse.returncode == 0, "the upgrade limit: nothing records the failed attempt"
    assert real_run.returncode == 5 and "wrote no artifact this run" in real_run.stderr, \
        real_run.stderr
    assert reuse_after.returncode == 5, reuse_after.stdout + reuse_after.stderr
    assert "wrote no artifact on its last attempt" in reuse_after.stderr, reuse_after.stderr


# --- a coverage.py report missing a member every producer writes --------------

PY_SRC = "def hot(a):\n    x = 1\n    if a:\n        x = 2\n    return x\n"
PY_FILES = {
    "src/a.py": PY_SRC,
    "run_it.py": "import sys; sys.path.insert(0, 'src'); import a; a.hot(1)\n",
    "crapkit.toml": toml({"src": "src"},
                         '[[lane]]\nname = "py"\ncommand = "never runs"\n'
                         'artifact = "coverage.json"\nparser = "coveragepy"\nscopes = ["src"]\n'),
    ".gitignore": ".crapkit/\ncoverage.json\n.coverage\n",
}


def coverage_py_report(tmp_path: Path, edit) -> Path:
    """A real `coverage run --branch` + `coverage json` report, then `edit`
    applied to function hot's region."""
    repo = committed(tmp_path, PY_FILES)
    for argv in (["run", "--branch", "--source=src", "run_it.py"],
                 ["json", "-o", "coverage.json"]):
        res = hang_guard.run([sys.executable, "-m", "coverage", *argv], cwd=repo, text=True)
        assert res.returncode == 0, res.stderr
    report = json.loads((repo / "coverage.json").read_text(encoding="utf-8"))
    (region,) = [fns["functions"]["hot"] for rel, fns in report["files"].items()
                 if rel.replace("\\", "/").endswith("src/a.py")]
    if "start_line" not in region:
        pytest.skip(f"coverage {report['meta']['version']} writes no start_line; "
                    "7.13.1 and later do, and CI installs the newest")
    edit(region)
    (repo / "coverage.json").write_text(json.dumps(report), encoding="utf-8")
    return repo


def scored_cov(repo: Path) -> float:
    res = run_cli(repo, "brief", "src/a.py", "hot", "--json", encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)["scored"]["cov"]


def complete(region: dict) -> None:
    pass


def statement_counts_missing(region: dict) -> None:
    del region["summary"]["num_statements"], region["summary"]["covered_lines"]


def test_a_summary_without_statement_counts_scores_as_the_complete_report(tmp_path: Path):
    """A kind with neither count is a kind the report did not measure; the
    branch pair still decides, so the score does not move."""
    full = coverage_py_report(tmp_path / "full", complete)
    partial = coverage_py_report(tmp_path / "partial", statement_counts_missing)

    runs = [run_cli(repo, "coverage", "--reuse-artifacts") for repo in (full, partial)]

    assert [r.returncode for r in runs] == [0, 0], [r.stderr for r in runs]
    assert scored_cov(partial) == scored_cov(full) == 0.5


def summary_missing(region: dict) -> None:
    del region["summary"]


def no_branch(region: dict) -> None:
    """hot as coverage.py writes a function with no branch: 0 of 0."""
    region["summary"].update(num_branches=0, covered_branches=0)


def statement_counts_missing_with_no_branch(region: dict) -> None:
    no_branch(region)
    statement_counts_missing(region)


def test_a_function_with_no_branch_scores_from_its_statements(tmp_path: Path):
    """The control for the refusal below: with 0 of 0 branches the statement
    pair decides, and hot(1) ran all 4 of its statements."""
    repo = coverage_py_report(tmp_path, no_branch)

    res = run_cli(repo, "coverage", "--reuse-artifacts")

    assert res.returncode == 0, res.stderr
    assert scored_cov(repo) == 1.0


def branch_counts_missing(region: dict) -> None:
    del region["summary"]["num_branches"], region["summary"]["covered_branches"]


def start_line_missing(region: dict) -> None:
    """What coverage.py 7.6 to 7.13.0 wrote."""
    del region["start_line"]


@pytest.mark.parametrize("edit, names", [
    pytest.param(summary_missing, ("coverage.json", "hot", "summary"), id="function-summary-missing"),
    pytest.param(branch_counts_missing, ("hot", "branch counts"), id="branch-counts-missing"),
    pytest.param(statement_counts_missing_with_no_branch,
                 ("coverage.json", "src/a.py: hot", "no statement counts and no branch"),
                 id="statement-counts-missing-with-no-branch"),
    pytest.param(start_line_missing, ("coverage.json", "hot", "start_line", "7.13.1"),
                 id="start-line-missing"),
])
def test_a_report_missing_a_member_is_refused_by_name(edit, names, tmp_path: Path):
    repo = coverage_py_report(tmp_path, edit)

    res = run_cli(repo, "coverage", "--reuse-artifacts", encoding="utf-8", errors="replace")

    assert res.returncode == 5, res.stdout + res.stderr
    for name in names:
        assert name in res.stderr, f"{name!r} missing from: {res.stderr}"


# --- an istanbul branch record with fewer or more hit counts than locations ---

BRANCH_FILES = {
    "src/a.py": HOT,
    "crapkit.toml": toml({"src": "src"}, istanbul_lane("js", "src", "never runs", "cov.json")),
    ".gitignore": ".crapkit/\ncov.json\n",
}


def istanbul_report(repo: Path, hits: list) -> Path:
    """hot's istanbul record: an if/else at line 2 with two locations, `hits`
    as its `b` array, every statement run."""
    key = str(repo / "src" / "a.py")
    record = {"path": key,
              "fnMap": {"0": {"name": "hot", "decl": {"start": {"line": 1}},
                              "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
              "f": {"0": 1},
              "branchMap": {"0": {"loc": {"start": {"line": 2}}, "type": "if",
                                  "locations": [{"start": {"line": 3}}, {"start": {"line": 4}}]}},
              "b": {"0": hits},
              "statementMap": {"0": {"start": {"line": 2}}, "1": {"start": {"line": 3}}},
              "s": {"0": 1, "1": 1}}
    (repo / "cov.json").write_text(json.dumps({key: record}), encoding="utf-8")
    return repo


def test_a_complete_branch_record_scores_its_paths(tmp_path: Path):
    repo = istanbul_report(committed(tmp_path, BRANCH_FILES), [1, 0])

    res = run_cli(repo, "coverage", "--reuse-artifacts")

    assert res.returncode == 0, res.stderr
    assert scored_cov(repo) == 0.5


@pytest.mark.parametrize("hits", [[1], [], [1, 0, 1]], ids=["one-of-two", "none-of-two",
                                                         "three-of-two"])
def test_a_branch_record_that_miscounts_its_locations_is_refused_by_name(hits, tmp_path: Path):
    repo = istanbul_report(committed(tmp_path, BRANCH_FILES), hits)

    res = run_cli(repo, "coverage", "--reuse-artifacts", encoding="utf-8", errors="replace")

    assert res.returncode == 5, res.stdout + res.stderr
    assert (f"cov.json: src/a.py: branch '0' has {len(hits)} hit count(s) in `b` for its 2 "
            "location(s)") in res.stderr, res.stderr
