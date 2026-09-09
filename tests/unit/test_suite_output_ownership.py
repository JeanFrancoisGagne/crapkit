"""Direct test runs retain their own evidence without changing lane artifacts."""
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from crapkit.config import Lane
from crapkit.lanes import measurement_owner
from test_suite_schedule import SCRIPT, fixture_env as environment, fixture_repo


def command(root, *args):
    # One unit test and one e2e test: a worker each. Leaving --unit-workers at
    # its default started four xdist workers per nested run, so the concurrent
    # pair below ran sixteen processes on a two-core runner for two tests.
    return [sys.executable, str(SCRIPT), "--repo", str(root),
            "--workers", "1", "--unit-workers", "1", *args]


def published_path(stdout):
    paths = [line.removeprefix("test evidence: ") for line in stdout.splitlines()
             if line.startswith("test evidence: ")]
    assert len(paths) == 1, stdout
    return Path(paths[0])


def assert_complete(output):
    assert sorted(case.attrib["name"] for case in ET.parse(output / "junit.xml").findall(
        ".//testcase")) == ["test_child", "test_unit"]
    assert (output / "unit.xml").is_file()
    assert (output / "e2e.xml").is_file()


def test_direct_runner_preserves_artifacts_held_by_a_live_measurement_owner(tmp_path):
    fixture_repo(tmp_path, "")
    measured = tmp_path / ".crapkit/cov"
    measured.mkdir(parents=True)
    artifacts = [measured / name for name in ("py.json", "junit.xml")]
    for path in artifacts:
        path.write_bytes(b"owned measurement evidence")
    lane = Lane(name="py", command="unused", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=(), results_artifact=".crapkit/cov/junit.xml")

    with measurement_owner(tmp_path, [lane]) as owner:
        result = subprocess.run(command(tmp_path), env=environment(tmp_path),
                                capture_output=True, text=True, timeout=60)
        owner.check()
        assert result.returncode == 0, result.stdout + result.stderr
        assert [path.read_bytes() for path in artifacts] == [b"owned measurement evidence"] * 2

    output = published_path(result.stdout)
    assert output.parent == tmp_path / ".crapkit/test-runs"
    assert_complete(output)


def test_two_direct_runners_keep_distinct_complete_evidence(tmp_path):
    fixture_repo(tmp_path, "")
    first = subprocess.Popen(command(tmp_path), env=environment(tmp_path),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(command(tmp_path), env=environment(tmp_path),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        one, error_one = first.communicate(timeout=60)
        two, error_two = second.communicate(timeout=60)
    finally:
        for child in (first, second):
            if child.poll() is None:
                child.kill()
                child.wait()
    assert first.returncode == second.returncode == 0, one + error_one + two + error_two
    paths = [published_path(one), published_path(two)]
    assert paths[0] != paths[1]
    for path in paths:
        assert_complete(path)


def test_explicit_output_resolves_against_repo_and_is_reported(tmp_path):
    fixture_repo(tmp_path, "")

    result = subprocess.run(command(tmp_path, "--output", ".crapkit/selected"),
                            env=environment(tmp_path), capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert published_path(result.stdout) == tmp_path / ".crapkit/selected"
    assert_complete(published_path(result.stdout))


def test_output_outside_repo_is_refused_before_writing(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    fixture_repo(root, "")
    output = tmp_path / "outside"

    result = subprocess.run(command(root, "--output", "../outside"),
                            env=environment(root), capture_output=True, text=True, timeout=60)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "test output must be inside" in result.stderr
    assert not output.exists()
