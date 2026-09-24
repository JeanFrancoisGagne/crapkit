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


INSPECT = """Name:          desktop-linux
Driver:        docker

Nodes:
Name:             desktop-linux
Status:           running
BuildKit version: {version}
Labels:
 org.mobyproject.buildkit.worker.executor:             containerd
"""


def _docker(monkeypatch, version: str, calls: list):
    def fake(argv, **kwargs):
        calls.append(argv)
        out = "desktop-linux\n" if argv[:3] == ["docker", "context", "show"] else INSPECT.format(version=version)
        return run.subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(run.subprocess, "run", fake)


def test_the_daemons_builder_serves_when_its_buildkit_is_the_pinned_version(monkeypatch):
    calls = []
    _docker(monkeypatch, run.pinned_buildkit(PINS), calls)

    assert run.pinned_buildkit(PINS) == "v0.33.0"
    assert run.choose_builder(PINS, None, "local") == "desktop-linux"
    assert not [argv for argv in calls if argv[:3] == ["docker", "buildx", "create"]]


def test_any_other_buildkit_builds_on_the_pinned_container_builder(monkeypatch):
    calls = []
    _docker(monkeypatch, "v0.29.0", calls)

    assert run.choose_builder(PINS, None, "local") == run.CONTAINER_BUILDER


def test_the_gha_cache_and_a_named_builder_skip_the_daemons_builder(monkeypatch):
    calls = []
    _docker(monkeypatch, run.pinned_buildkit(PINS), calls)

    assert run.choose_builder(PINS, None, "gha") == run.CONTAINER_BUILDER
    assert run.choose_builder(PINS, "ci-builder", "local") == "ci-builder"


def test_an_image_whose_tools_drifted_from_the_pins_is_named(monkeypatch, tmp_path):
    printed = b"uv uv 0.12.17\nclaude 2.1.281 (Claude Code)\n"
    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: run.subprocess.CompletedProcess(a, 0, printed, b""))
    problems = run.check_versions(PINS, "core", tmp_path)

    assert "uv: pinned 0.12.18, image prints 'uv 0.12.17'" in problems
    assert not [problem for problem in problems if problem.startswith("claude:")]
    assert run.versions_command("core")[-2:] == ["crapkit-deploy:core", "versions"]


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


def test_a_build_only_run_records_the_build_and_exports_nothing(tmp_path, monkeypatch):
    def no_export(repo, out):
        raise AssertionError("a build-only run exported the tree")
    monkeypatch.setattr(run.export, "export", no_export)
    monkeypatch.setattr(run, "build", lambda *a, **k: {})
    monkeypatch.setattr(run, "check_versions", lambda pins, image, out: [])

    assert run.main(["--build-only", "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out").is_dir() and not (tmp_path / "out" / "in").exists()


def test_a_skipped_build_is_not_checked_again(tmp_path, monkeypatch):
    def no_check(pins, image, out):
        raise AssertionError("an unchanged image was checked again")
    monkeypatch.setattr(run, "build", lambda *a, **k: {"skipped": "inputs unchanged"})
    monkeypatch.setattr(run, "check_versions", no_check)

    assert run.main(["--build-only", "--out", str(tmp_path / "out")]) == 0


def test_an_image_that_fails_its_pins_loses_its_tag(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(run, "build", lambda *a, **k: {})
    monkeypatch.setattr(run, "check_versions", lambda pins, image, out: ["uv: pinned 1, image prints '2'"])
    monkeypatch.setattr(run.subprocess, "run", lambda argv, **kw: calls.append(argv))

    with pytest.raises(SystemExit, match="does not match pins.toml"):
        run.main(["--build-only", "--image", "full", "--out", str(tmp_path / "out")])
    assert calls == [["docker", "image", "rm", "crapkit-deploy:full"]]


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


# --- a no-change rebuild ---------------------------------------------------------

def test_a_later_dockerignore_rule_wins():
    rules = run.context_rules("# note\n*\n!a/\na/b/\n!a/b/keep.txt\n")

    assert run.in_context("a/x.txt", rules) and run.in_context("a/b/keep.txt", rules)
    assert not run.in_context("a/b/y.txt", rules) and not run.in_context("c.txt", rules)


def test_the_real_dockerignore_uses_only_rules_the_reader_understands():
    for _included, pattern in run.context_rules(run.DOCKERIGNORE.read_text(encoding="utf-8")):
        assert pattern == "*" or not set(pattern) & set("*?[]\\"), pattern


def test_the_build_context_is_what_the_dockerignore_lets_in():
    files = {path.relative_to(ROOT).as_posix() for path in run.context_files()}

    assert {"tests/deploy/docker/entry.sh", "tests/deploy/docker/Dockerfile", "tools/deploy/pins.toml"} <= files
    assert not [name for name in files if name.startswith("tests/deploy/docker/prototype/")]
    assert "tools/deploy/run.py" not in files


def _fake_root(tmp_path):
    ignore = tmp_path / "tests" / "deploy" / "docker" / "Dockerfile.dockerignore"
    ignore.parent.mkdir(parents=True)
    ignore.write_text("*\n!tests/deploy/docker/\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_the_inputs_fingerprint_moves_with_the_context_the_pins_and_the_target(tmp_path):
    root = _fake_root(tmp_path)
    first = run.inputs_fingerprint(PINS, "core", root)
    (root / "outside.py").write_text("x = 2\n", encoding="utf-8")

    assert run.inputs_fingerprint(PINS, "core", root) == first
    assert run.inputs_fingerprint(PINS, "full", root) != first
    assert run.inputs_fingerprint({**PINS, "images": {**PINS["images"], "snapshot": "x"}}, "core", root) != first
    (root / "tests" / "deploy" / "docker" / "Dockerfile.dockerignore").write_text("*\n", encoding="utf-8")
    assert run.inputs_fingerprint(PINS, "core", root) != first


def test_a_build_labels_the_image_with_its_inputs():
    argv = run.build_command(PINS, "core", "local", no_cache=False, inputs="abc")

    assert f"--label={run.INPUTS_LABEL}=abc" in argv


def test_an_image_built_from_the_same_inputs_is_not_rebuilt(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run, "image_label", lambda tag: run.inputs_fingerprint(PINS, "core"))
    monkeypatch.setattr(run, "image_size", lambda tag: 7)
    monkeypatch.setattr(run.subprocess, "run", lambda argv, **kw: calls.append(argv))
    record = run.build(PINS, "core", "local", False, tmp_path)

    assert record["skipped"] == "inputs unchanged" and record["size_bytes"] == 7 and calls == []
    assert run.json.loads((tmp_path / "build.json").read_text(encoding="utf-8"))[-1]["skipped"]


def test_no_cache_rebuilds_whatever_the_label_says(monkeypatch):
    monkeypatch.setattr(run, "image_label", lambda tag: "abc")

    assert not run.unchanged("crapkit-deploy:core", "abc", no_cache=True)
    assert run.unchanged("crapkit-deploy:core", "abc", no_cache=False)
    assert not run.unchanged("crapkit-deploy:core", "abd", no_cache=False)


def test_what_an_image_prints_is_read_as_utf8_whatever_the_host_code_page(monkeypatch, tmp_path):
    printed = "zed Zed 1.21.0 \u2013 /opt/zed\n".encode("utf-8")
    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: run.subprocess.CompletedProcess(a, 0, printed, b""))
    run.check_versions(PINS, "gui", tmp_path)

    assert (tmp_path / "versions-gui.txt").read_text(encoding="utf-8") == "zed Zed 1.21.0 \u2013 /opt/zed\n"
