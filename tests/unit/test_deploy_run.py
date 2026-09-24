"""tools/deploy/run.py: one entry point that builds the deploy images and runs
cells in them, the same way on a laptop and in CI.

These tests hold the commands it composes: a build that takes every pin as a
build arg for linux/amd64, a container with no network and a non-root uid, a
marker expression that always keeps the kit's own tests, and a repeat check
that fails when a cell's verdict moves between two fresh runs.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import pins as pinsfile  # noqa: E402
import run  # noqa: E402

PINS = pinsfile.load()


def test_a_build_targets_the_image_for_linux_amd64_with_every_pin():
    argv = run.build_command(PINS, "core", "local", no_cache=False)

    assert argv[:3] == ["docker", "buildx", "build"]
    assert argv[argv.index("--platform") + 1] == "linux/amd64"
    assert argv[argv.index("--target") + 1] == "core"
    assert f"--build-arg=DEBIAN_IMAGE={PINS['images']['debian']}" in argv
    assert len([arg for arg in argv if arg.startswith("--build-arg=")]) == len(run.build_args(PINS))
    assert "--no-cache" not in argv


def test_the_builder_runs_the_pinned_buildkit_image():
    argv = run.builder_command(PINS, "crapkit-deploy")

    assert argv[argv.index("--driver") + 1] == "docker-container"
    assert argv[-1] == f"image={PINS['images']['buildkit']}"
    assert run.build_command(PINS, "core", "local", no_cache=True, builder="b")[3:5] == ["--builder", "b"]


def test_the_gha_cache_is_one_scope_per_image_that_never_fails_the_build():
    assert run.cache_flags("gha", "core") == [
        "--cache-from=type=gha,scope=crapkit-deploy-core",
        "--cache-to=type=gha,scope=crapkit-deploy-core,mode=min,ignore-error=true"]
    assert run.cache_flags("local", "core") == []


def test_a_push_run_on_core_selects_the_kit_and_the_push_cells_core_can_hold():
    assert run.marker_expression("push", "linux", "core", online=False) == (
        "kit or (push and linux and (image_cells or image_core) and not online)")


def test_a_native_run_selects_by_os_alone():
    args = run.parse(["--native", "--os", "windows", "--cell", "win-pip-start", "-n", "2"])

    assert run.pytest_args(args) == ["-m", "kit or (push and windows and not online)",
                                     "--deploy-cell=win-pip-start", "-n", "2"]


def test_the_release_cadence_runs_every_tier(monkeypatch):
    assert run.marker_expression("release", "linux", None, online=True) == (
        "kit or ((push or nightly or weekly or online) and linux and online)")


def test_a_cell_runs_as_uid_1000_with_no_network(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "image_digest", lambda tag: "sha256:abc")
    argv = run.container_command("crapkit-deploy:core", tmp_path, ["-m", "kit"], online=False, run_index=0)

    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert argv[argv.index("--network") + 1] == "none"
    assert "CRAPKIT_DEPLOY_IMAGE_DIGEST=sha256:abc" in argv
    assert argv[-5:] == ["sh", "/out/in/entry.sh", "-m", "kit", "--junitxml=/out/junit-0.xml"]


def test_an_online_cell_keeps_the_network_and_a_baked_image_reads_its_own_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "image_digest", lambda tag: "")
    argv = run.container_command("crapkit-deploy:core-baked", tmp_path, [], online=True, run_index=1)

    assert "--network" not in argv
    assert "/opt/deploy/in/entry.sh" in argv


def test_the_out_dir_holds_the_export_and_the_entry_point(tmp_path, monkeypatch):
    monkeypatch.setattr(run.export, "export", lambda repo, out: (out / "src.bundle", out / "tree.tar"))
    out = run.prepare_out(tmp_path / "out")

    assert (out / "in" / "entry.sh").read_bytes() == run.ENTRY.read_bytes()


JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="tests.deploy.test_a" name="test_one"/>
<testcase classname="tests.deploy.test_a" name="test_two"><failure message="x"/></testcase>
<testcase classname="tests.deploy.test_a" name="test_three"><skipped/></testcase>
</testsuite></testsuites>"""


def test_verdicts_read_pass_fail_and_skip(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")

    assert run.verdicts(path) == {"tests.deploy.test_a::test_one": "passed",
                                  "tests.deploy.test_a::test_two": "failed",
                                  "tests.deploy.test_a::test_three": "skipped"}


def test_a_repeat_names_the_cell_whose_verdict_moved():
    first = {"a": "passed", "b": "passed"}
    second = {"a": "passed", "b": "failed", "c": "passed"}

    assert run.differing([first, second]) == ["b", "c"]
    assert run.differing([first, dict(first)]) == []


@pytest.mark.parametrize("platform, expected", [("win32", "windows"), ("darwin", "macos"), ("linux", "linux")])
def test_a_native_run_defaults_to_this_machines_os(monkeypatch, platform, expected):
    monkeypatch.setattr(run.sys, "platform", platform)

    assert run.host_os(native=True) == expected
    assert run.host_os(native=False) == "linux"
