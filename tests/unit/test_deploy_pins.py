"""tools/deploy/pins.toml is the one source of every version the deploy suite
installs, and these tests hold the files that repeat a pin to it.

The Dockerfile takes each value as a build arg that run.py derives from
pins.toml, so an ARG with no pin, or a pin no ARG reads, is drift. The harness
package.json files repeat each CLI's version for npm; their locks must carry the
Linux, Windows and macOS platform packages, or a lock written on one OS installs
nothing on another. The workflow jobs that run the suite pin actions by SHA and
runners by label.
"""
import io
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import lock  # noqa: E402
import pins as pinsfile  # noqa: E402
import run  # noqa: E402

DOCKER = ROOT / "tests" / "deploy" / "docker"
DOCKERFILE = (DOCKER / "Dockerfile").read_text(encoding="utf-8")
PINS = pinsfile.load()
BUILDKIT_ARGS = {"TARGETARCH"}
PLATFORMS = {"linux-x64": r"linux-x64", "win32-x64": r"(win32|windows)-x64", "darwin-arm64": r"darwin-arm64"}


def test_every_dockerfile_arg_is_a_pin_and_every_pin_an_arg():
    declared = set(re.findall(r"^ARG (\w+)", DOCKERFILE, re.M)) - BUILDKIT_ARGS

    assert declared == set(run.build_args(PINS))


def test_the_syntax_line_is_the_pinned_digest():
    assert DOCKERFILE.splitlines()[0] == f"# syntax={PINS['images']['syntax']}"


@pytest.mark.parametrize("image", ["debian", "uv", "node", "buildkit", "syntax"])
def test_every_base_image_is_pinned_by_digest(image):
    assert re.search(r"@sha256:[0-9a-f]{64}$", PINS["images"][image])


def expected_dependencies(image):
    wanted = {}
    for key, spec in PINS["harness"].items():
        if spec["image"] == image and "npm" in spec:
            wanted[spec["npm"]] = spec["version"]
            wanted.update({f"{key}-{floor}": f"npm:{spec['npm']}@{floor}" for floor in spec.get("floors", [])})
    return wanted


@pytest.mark.parametrize("image", ["core", "full"])
def test_each_harness_package_json_is_the_pinned_set(image):
    package = json.loads((DOCKER / f"harness-{image}" / "package.json").read_text(encoding="utf-8"))

    assert package["dependencies"] == expected_dependencies(image)


def _lock(name):
    return json.loads((DOCKER / name / "package-lock.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["harness-core", "harness-full", "npm-fixtures"])
def test_each_lock_was_written_from_its_package_json(name):
    package = json.loads((DOCKER / name / "package.json").read_text(encoding="utf-8"))
    root = _lock(name)["packages"][""]

    for field in ("dependencies", "devDependencies"):
        assert root.get(field) == package.get(field)


def _locked(packages, name):
    return any(key.endswith("node_modules/" + name) for key in packages)


def _gap(packages, entry, pattern):
    """The entry offers this platform and the lock holds none of what it offers."""
    offered = [name for name in entry.get("optionalDependencies", {}) if re.search(pattern, name)]
    return bool(offered) and not any(_locked(packages, name) for name in offered)


def _missing_platforms(packages):
    """(package, platform) pairs a package offers that the lock left out."""
    return [(path, platform) for path, entry in packages.items() for platform, pattern in PLATFORMS.items()
            if _gap(packages, entry, pattern)]


@pytest.mark.parametrize("name", ["harness-core", "harness-full"])
def test_each_harness_lock_holds_linux_windows_and_macos_platform_packages(name):
    packages = _lock(name)["packages"]

    assert _missing_platforms(packages) == []
    assert any(re.search(PLATFORMS["win32-x64"], key) for key in packages)


def test_a_lock_that_dropped_a_platform_is_caught():
    packages = {"node_modules/tool": {"optionalDependencies": {"tool-linux-x64": "1", "tool-win32-x64": "1"}},
                "node_modules/tool-linux-x64": {}}

    assert _missing_platforms(packages) == [("node_modules/tool", "win32-x64")]


@pytest.mark.parametrize("key", sorted(PINS["binary"]))
def test_every_binary_has_an_https_url_and_a_sha256(key):
    spec = PINS["binary"][key]

    assert spec["url"].startswith("https://")
    assert re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])
    assert spec["os"] in ("linux", "windows", "macos", "any")


def _deploy_jobs():
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        jobs = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {})
        for name, job in jobs.items():
            if path.name == "deploy.yml" or name.startswith("deploy"):
                yield path.name, name, job


def _pinned(use):
    return use.startswith("./") or use in PINS["actions"].values()


def _uses(job):
    return [step["uses"] for step in job.get("steps", []) if "uses" in step]


def _unpinned(job):
    """The job's runner label when it is not a pin, and every action not pinned by SHA."""
    runner = [] if job.get("runs-on") in PINS["runners"].values() else [job.get("runs-on")]
    return [use for use in _uses(job) if not _pinned(use)] + runner


def test_deploy_jobs_pin_actions_by_sha_and_runners_by_label():
    loose = [(workflow, name, use) for workflow, name, job in _deploy_jobs() for use in _unpinned(job)]

    assert loose == []


def test_the_wheelhouse_lock_covers_every_pinned_release_on_every_row():
    assert lock.coverage_problems(PINS, lock.read()) == []


def test_the_image_toolchain_names_every_key_the_kit_reads():
    body = DOCKERFILE.split("/opt/deploy/toolchain.json\n", 1)[1].split("\nEOF\n", 1)[0]

    assert {"path", "git", "uv", "uvx", "node", "npm", "pipx", "prek", "python_install_dir",
            "wheelhouse", "npm_cache", "npm_fixtures", "harness_bin", "runner_python"} <= set(json.loads(body))


# --- lock.py ------------------------------------------------------------------

ROW = {"name": "linux-x86_64-cp312", "python": "3.12", "os": "linux", "arch": "x86_64"}


def _file(name, packagetype="bdist_wheel"):
    return {"filename": name, "packagetype": packagetype, "url": f"https://files/{name}",
            "digests": {"sha256": "0" * 64}}


def test_the_best_wheel_is_the_most_specific_one_the_row_can_install():
    ranks = {tag: index for index, tag in enumerate(lock.supported_tags(ROW))}
    files = [_file("pkg-1.0-py3-none-any.whl"), _file("pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
             _file("pkg-1.0-cp312-cp312-win_amd64.whl"), _file("pkg-1.0.tar.gz", "sdist")]

    assert lock.best_file(files, ranks)["filename"] == "pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"


def test_a_release_with_no_fitting_wheel_falls_back_to_its_sdist():
    ranks = {tag: index for index, tag in enumerate(lock.supported_tags(ROW))}
    files = [_file("pkg-1.0-cp38-cp38-win_amd64.whl"), _file("pkg-1.0.tar.gz", "sdist")]

    assert lock.best_file(files, ranks)["filename"] == "pkg-1.0.tar.gz"


def test_the_lock_text_reads_back_as_written(tmp_path):
    data = {"newest": {"crapkit": "0.8.0", "lizard": "1.24.0"},
            "file": [{"name": "a-1-py3-none-any.whl", "project": "a", "version": "1", "url": "https://x/a",
                      "sha256": "1" * 64, "rows": ["r1", "r2"]}]}
    path = tmp_path / "wheelhouse.lock"
    path.write_text(lock.dumps(data), encoding="utf-8")

    assert lock.read(path) == data


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_refuses_bytes_that_do_not_match_the_lock(tmp_path):
    entry = {"name": "a-1-py3-none-any.whl", "url": "https://x/a", "sha256": "1" * 64}

    with pytest.raises(SystemExit, match="does not match its pinned sha256"):
        lock.fetch_one(entry, tmp_path, opener=lambda url: _Response(b"not the pinned bytes"))
    assert list(tmp_path.iterdir()) == []


def test_fetch_keeps_a_file_that_already_holds_the_pinned_bytes(tmp_path):
    (tmp_path / "a.whl").write_bytes(b"pinned")
    entry = {"name": "a.whl", "url": "https://x/a", "sha256": lock.sha256(tmp_path / "a.whl")}

    assert lock.fetch_one(entry, tmp_path, opener=None) == tmp_path / "a.whl"


def test_fetch_removes_a_file_the_lock_no_longer_names(tmp_path):
    (tmp_path / "old-1-py3-none-any.whl").write_bytes(b"superseded")
    (tmp_path / "a.whl").write_bytes(b"pinned")
    data = {"file": [{"name": "a.whl", "url": "https://x/a", "sha256": lock.sha256(tmp_path / "a.whl"), "rows": ["r"]}]}

    assert lock.fetch(data, {"r"}, tmp_path, opener=None) == [tmp_path / "a.whl"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.whl"]


def test_drift_names_a_newer_release_on_pypi():
    data = {"newest": {"crapkit": "0.8.0", "lizard": "1.24.0"}}
    newer = {"crapkit": "0.8.1", "lizard": "1.24.0"}

    assert lock.drift_problems(data, newest=newer.get) == [
        "crapkit 0.8.1 is on PyPI; the lock names 0.8.0"]


def test_coverage_names_a_row_missing_a_release():
    pins = {"wheelhouse": {"crapkit": ["0.7.6"], "lizard": "1.24.0", "row": [{"name": "r1"}]}}
    data = {"newest": {"crapkit": "0.8.0"},
            "file": [{"project": "crapkit", "version": "0.8.0", "rows": ["r1"]},
                     {"project": "lizard", "version": "1.24.0", "rows": ["r1"]}]}

    assert lock.coverage_problems(pins, data) == ["r1: no crapkit==0.7.6"]


# --- the image manifest ----------------------------------------------------------

def test_a_manifest_refresh_replaces_one_image_and_keeps_the_others(tmp_path, monkeypatch):
    path = tmp_path / "image-manifest.lock"
    path.write_text(lock.manifest_text({"crapkit-deploy:core": "uv 1\n", "crapkit-deploy:full": "uv 1\ngemini 2\n"}),
                    encoding="utf-8")
    monkeypatch.setattr(lock, "manifest", lambda image: "uv 2\n")

    assert lock.main(["manifest", "--image", "crapkit-deploy:core", "--manifest", str(path)]) == 0
    assert lock.manifest_blocks(path.read_text(encoding="utf-8")) == {
        "crapkit-deploy:core": "uv 2\n", "crapkit-deploy:full": "uv 1\ngemini 2\n"}


def test_a_manifest_check_names_the_line_that_moved_and_writes_nothing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "image-manifest.lock"
    path.write_text(lock.manifest_text({"crapkit-deploy:core": "uv 1\nnode 22\n"}), encoding="utf-8")
    monkeypatch.setattr(lock, "manifest", lambda image: "uv 2\nnode 22\n")

    assert lock.main(["manifest", "--check", "--image", "crapkit-deploy:core", "--manifest", str(path)]) == 1
    printed = capsys.readouterr().err
    assert "-uv 1" in printed and "+uv 2" in printed
    assert lock.manifest_blocks(path.read_text(encoding="utf-8"))["crapkit-deploy:core"] == "uv 1\nnode 22\n"
    monkeypatch.setattr(lock, "manifest", lambda image: "uv 1\nnode 22\n")
    assert lock.main(["manifest", "--check", "--image", "crapkit-deploy:core", "--manifest", str(path)]) == 0


# --- pins.py ------------------------------------------------------------------

def test_versions_name_a_tool_whose_output_lacks_its_pin():
    expected = {"uv": "0.12.18", "claude": "2.1.281"}
    printed = "uv uv 0.12.18 (x86_64-unknown-linux-gnu)\nclaude 2.1.280 (Claude Code)\n"

    assert pinsfile.version_problems(expected, printed) == [
        "claude: pinned 2.1.281, image prints '2.1.280 (Claude Code)'"]


def test_each_image_expects_the_tools_of_the_images_under_it():
    core, full = pinsfile.expected_versions(PINS, "core"), pinsfile.expected_versions(PINS, "full")

    assert core["claude"] == PINS["harness"]["claude-code"]["version"]
    assert "gemini" not in core and full["gemini"] == PINS["harness"]["gemini-cli"]["version"]
    assert full["python3.14"] == "3.14.7"


def test_each_harness_floor_is_held_to_its_pin():
    core = pinsfile.expected_versions(PINS, "core")

    assert core["claude-2.1.139"] == "2.1.139" and core["claude-2.1.138"] == "2.1.138"
    assert core["codex-0.121.0"] == "0.121.0"


def test_a_harness_whose_binary_prints_another_version_is_held_to_that_text():
    full = pinsfile.expected_versions(PINS, "full")
    junie = PINS["harness"]["junie"]

    assert junie["version"] != junie["prints"] and full["junie"] == junie["prints"]
    assert pinsfile.version_problems({"junie": full["junie"]}, f"junie Junie version: {junie['prints']}\n") == []


# entry.sh names these after the file its bin-dir loop finds, like a harness.
LISTED_FROM_BIN_DIRS = {"bun", "act"}


def test_entry_sh_prints_a_line_under_each_name_the_pins_expect_for_a_downloaded_tool():
    entry = (DOCKER / "entry.sh").read_text(encoding="utf-8")
    commands = {spec["command"] for spec in PINS["harness"].values() if "command" in spec}
    named = {name for image in pinsfile.IMAGE_CHAIN for name in pinsfile.expected_versions(PINS, image)
             if name not in commands and "-" not in name and not name.startswith("python")}

    assert [name for name in sorted(named - LISTED_FROM_BIN_DIRS) if f'echo "{name} ' not in entry] == []
