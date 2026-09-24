"""kit.repos: repos built from a spec, copied per test, and scored twice by crapkit."""
from pathlib import Path
import re
import tomllib

import pytest

import hang_guard
from accuracy.kit import drive, repos

SEED = Path(__file__).resolve().parent / "fixtures" / "seed"
DAY = 86_400


def _log(top, *fmt):
    return repos.git(top, "log", "--format=" + "%x09".join(fmt)).splitlines()


@pytest.mark.nightly
@pytest.mark.process
def test_two_builds_of_one_spec_have_the_same_commit_ids(tmp_path):
    spec = repos.Spec(steps=(repos.Commit(files={"a.py": "x = 1\n"}, date=repos.EPOCH),
                             repos.Commit(files={"a.py": "x = 2\n"}, date=repos.EPOCH + DAY)))

    first = repos.build(spec, tmp_path / "one")
    second = repos.build(spec, tmp_path / "two")

    assert _log(first.top, "%H") == _log(second.top, "%H")


@pytest.mark.nightly
@pytest.mark.process
def test_commits_carry_the_spec_dates_and_identity(tmp_path):
    spec = repos.Spec(steps=(repos.Commit(files={"a.py": "x = 1\n"}, date=1_000_000_000,
                                          author=("Zoë", "zoe@example.com")),))

    built = repos.build(spec, tmp_path / "r")

    assert _log(built.top, "%at", "%ct", "%an", "%ae") == [
        "1000000000\t1000000000\tZoë\tzoe@example.com"]


@pytest.mark.nightly
@pytest.mark.process
def test_bytes_are_stored_as_written_and_non_ascii_paths_come_back_quoted(tmp_path):
    spec = repos.Spec(steps=(repos.Commit(files={"crlf.txt": b"a\r\nb\r\n", "café.py": "x = 1\n"}),))

    built = repos.build(spec, tmp_path / "r")

    assert repos.git(built.top, "config", "core.autocrlf").strip() == "false"
    assert repos.git(built.top, "config", "core.eol").strip() == "lf"
    stored = hang_guard.run(["git", "cat-file", "blob", "HEAD:crlf.txt"], cwd=built.top)
    assert stored.stdout == b"a\r\nb\r\n"
    assert '"caf\\303\\251.py"' in repos.git(built.top, "ls-files")


@pytest.mark.nightly
@pytest.mark.process
def test_renames_branches_and_merges(tmp_path):
    spec = repos.Spec(steps=(
        repos.Commit(files={"old.py": "def f():\n    return 1\n"}, date=repos.EPOCH),
        repos.Branch("side"),
        repos.Commit(files={"side.py": "y = 1\n"}, date=repos.EPOCH + DAY),
        repos.Checkout("main"),
        repos.Commit(renames={"old.py": "pkg/new.py"}, date=repos.EPOCH + 2 * DAY),
        repos.Merge("side", date=repos.EPOCH + 3 * DAY),
    ))

    built = repos.build(spec, tmp_path / "r")

    assert len(_log(built.top, "%P")[0].split()) == 2
    assert "R100\told.py\tpkg/new.py" in repos.git(built.top, "log", "--name-status", "-M",
                                                   "--format=")
    assert sorted(repos.git(built.top, "ls-files").split()) == ["pkg/new.py", "side.py"]


@pytest.mark.nightly
@pytest.mark.process
def test_a_root_below_the_git_top(make_repo):
    spec = repos.Spec(steps=(repos.Commit(files={"pkg/crapkit.toml": "[crapkit]\n"}),),
                      root="pkg")

    built = make_repo(spec)

    assert built.root == built.top / "pkg"
    assert (built.root / "crapkit.toml").is_file()


@pytest.mark.nightly
@pytest.mark.process
def test_each_copy_starts_from_the_template(make_repo, repo_templates):
    spec = repos.Spec(steps=(repos.Commit(files={"a.py": "x = 1\n"}),))

    first = make_repo(spec)
    (first.top / "a.py").write_text("changed\n", encoding="utf-8")
    second = make_repo(spec)

    assert (second.top / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    names = [path.name for path in repo_templates.base.iterdir()]
    assert repos.digest(spec) in names
    assert not [name for name in names if name.endswith(".building")]


def test_the_digest_moves_with_any_byte_date_or_root():
    base = repos.Spec(steps=(repos.Commit(files={"a": b"1"}, date=5),))
    variants = [repos.Spec(steps=(repos.Commit(files={"a": b"2"}, date=5),)),
                repos.Spec(steps=(repos.Commit(files={"a": b"1"}, date=6),)),
                repos.Spec(steps=(repos.Commit(files={"a": b"1"}, date=5),), root="x")]

    assert len({repos.digest(spec) for spec in [base, *variants]}) == 4


@pytest.mark.nightly
@pytest.mark.process
def test_the_copy_command_moves_the_mtime_every_run(tmp_path):
    (tmp_path / "rec.json").write_bytes(b"{}")
    command = repos.copy_command(("rec.json", "out/a.json"))
    env = drive.child_env()
    stamps = []
    for _ in range(2):
        done = hang_guard.run(command, shell=True, cwd=tmp_path, env=env)
        assert done.returncode == 0, done.stderr
        stamps.append((tmp_path / "out" / "a.json").stat().st_mtime_ns)

    assert stamps[1] > stamps[0]
    assert (tmp_path / "out" / "a.json").read_bytes() == b"{}"


def test_every_seed_lane_uses_the_one_command_form():
    config = tomllib.loads((SEED / "crapkit.toml").read_text(encoding="utf-8"))

    for lane in config["lane"]:
        words = lane["command"].split('" ', 1)[1].split()
        pairs = list(zip(words[::2], words[1::2]))
        assert lane["command"] == repos.copy_command(*pairs)


@pytest.mark.process
@pytest.mark.parametrize("spawn", [False, True], ids=["in_process", "spawned"])
def test_coverage_scores_a_kit_repo_twice_in_a_row(make_repo, spawn):
    built = make_repo(repos.tree_spec(SEED))
    driver = drive.Driver(built.root, spawn=spawn)

    runs = [driver.run("coverage", "--json") for _ in range(2)]

    assert [run.code for run in runs] == [0, 0], [run.stderr for run in runs]
    assert [run.json()["functions"] for run in runs] == [9, 9]
    counts = driver.store("select run_id, count(*) as n from functions group by run_id")
    assert counts == [{"run_id": 1, "n": 9}, {"run_id": 2, "n": 9}]


@pytest.mark.process
def test_a_recorded_lane_scores_inside_a_container(make_repo):
    """The accuracy image is a container, where crapkit refuses a coveragepy
    lane without container_ok. A lane that copies a recording runs no suite, so
    the seed's lanes and every lane_toml table say container_ok."""
    built = make_repo(repos.tree_spec(SEED))
    driver = drive.Driver(built.root, env={"CRAPKIT_INSIDE_CONTAINER": "1"})

    run = driver.run("coverage", "--json")

    assert run.code == 0, run.stderr
    table = tomllib.loads(repos.lane_toml("py", "a.json", "coveragepy", ["py"], "r.json"))
    assert table["lane"][0]["container_ok"] is True


def test_the_seed_holds_no_path_of_the_machine_that_recorded_it():
    for path in (SEED / "recorded").iterdir():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\/]|/home/|/Users/|/tmp/", text), path.name
