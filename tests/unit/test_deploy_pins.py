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
import toolchain  # noqa: E402

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


def _requested(monkeypatch, tmp_path, os_name, harness):
    """The pins toolchain.py downloads for one OS and harness level, recorded
    instead of fetched."""
    asked = []
    monkeypatch.setattr(toolchain, "install_archive",
                        lambda pins, stem, os, root: asked.append(toolchain.binary(pins, stem, os)["url"]) or root)
    toolchain.base_tools(PINS, os_name, tmp_path)
    toolchain.install_harness_binaries(PINS, os_name, tmp_path, harness)
    return {key for key, spec in PINS["binary"].items() if spec["url"] in asked}


@pytest.mark.parametrize("os_name", ["windows", "macos"])
def test_toolchain_installs_every_pin_for_its_os_and_no_other(monkeypatch, tmp_path, os_name):
    """A native job gets the same tools the images hold: a windows or macos pin
    nothing installs is dead, and a download for another OS would not run."""
    installed = _requested(monkeypatch, tmp_path, os_name, toolchain.HARNESS_LEVELS["full"])
    pinned = {key for key, spec in PINS["binary"].items() if spec["os"] == os_name}

    assert installed == pinned


@pytest.mark.parametrize("os_name, level, keys", [
    ("windows", "none", set()),
    ("windows", "core", {"cursor-agent-windows-x64"}),
    ("windows", "full", {"cursor-agent-windows-x64", "goose-windows-x64"}),
    ("macos", "core", {"cursor-agent-macos-arm64"}),
])
def test_a_harness_level_installs_the_binaries_its_images_hold(os_name, level, keys):
    assert set(toolchain.harness_downloads(PINS, os_name, toolchain.HARNESS_LEVELS[level])) == keys


def test_a_native_harness_binary_goes_on_harness_bin(tmp_path):
    tools = {name: f"/t/{name}" for name in ("uv", "uvx", "node", "npm", "git", "prek", "pipx", "runner", "bash")}
    tools.update(path=["/t/bin"], harness_dirs=["/t/cursor-agent-windows-x64/dist-package"])
    described = toolchain.describe(tmp_path, tools, {"3.12": "/t/python3.12"}, ["core"])

    assert described["harness_bin"][-1] == "/t/cursor-agent-windows-x64/dist-package"
    assert described["harness_bin"][0].endswith(".bin")


def test_the_cells_image_fetches_each_of_its_binaries_for_both_architectures():
    """lin-arm64 builds the cells image for linux/arm64, so every
    architecture-specific download in it has an aarch64 pin beside the x86_64 one."""
    arches = {}
    for key, spec in pinsfile.binaries(PINS, "linux").items():
        arches.setdefault((spec.get("image"), key.rsplit("-", 2)[0]), set()).add(spec["arch"])

    assert {stem: found for (image, stem), found in arches.items() if image == "cells"} == {
        "prek": {"x86_64", "aarch64"}, "pipx": {"any"}}


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


def _names(versions) -> set[str]:
    """Every tool name `versions(pins, image)` gives, across the images."""
    return {name for image in pinsfile.IMAGE_CHAIN for name in versions(PINS, image)}


def test_entry_sh_prints_a_line_under_each_name_the_pins_expect_for_a_downloaded_tool():
    entry = (DOCKER / "entry.sh").read_text(encoding="utf-8")
    downloaded = _names(pinsfile.expected_versions) - _names(pinsfile._harness_versions) - LISTED_FROM_BIN_DIRS
    missing = [name for name in sorted(downloaded) if not name.startswith("python") and f'echo "{name} ' not in entry]

    assert missing == []


def test_a_manifest_is_read_as_utf8_whatever_the_host_code_page(monkeypatch):
    printed = "zed Zed 1.21.0 \u2013 /opt/zed\n".encode("utf-8")
    monkeypatch.setattr(lock.subprocess, "run", lambda *a, **k: lock.subprocess.CompletedProcess(a, 0, printed, b""))

    assert lock.manifest("crapkit-deploy:gui") == "zed Zed 1.21.0 \u2013 /opt/zed\n"


# --- the arm64 cells image (lin-arm64) ---------------------------------------------

def test_the_arm64_image_builds_the_cells_target_for_linux_arm64():
    argv = run.build_command(PINS, "cells-arm64", "local", no_cache=False)

    assert argv[argv.index("--platform") + 1] == "linux/arm64"
    assert argv[argv.index("--target") + 1] == "cells"
    assert argv[argv.index("-t") + 1] == "crapkit-deploy:cells-arm64"
    assert pinsfile.expected_versions(PINS, "cells-arm64") == pinsfile.expected_versions(PINS, "cells")


def test_every_other_image_keeps_the_pinned_platform():
    assert {pinsfile.platform(PINS, image) for image in pinsfile.IMAGE_CHAIN if image != "cells-arm64"} == {
        PINS["images"]["platform"]}
    assert run.inputs_fingerprint(PINS, "cells-arm64") != run.inputs_fingerprint(PINS, "cells")


def test_an_arm64_tag_runs_under_its_own_platform_and_an_amd64_one_under_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "image_digest", lambda tag: "")
    arm = run.container_command("crapkit-deploy:cells-arm64", tmp_path, [], online=False, run_index=0)
    amd = run.container_command("crapkit-deploy:cells", tmp_path, [], online=False, run_index=0)

    assert arm[arm.index("--platform") + 1] == "linux/arm64" and "--platform" not in amd
    assert run.versions_command("cells-arm64")[-3:] == ["linux/arm64", "crapkit-deploy:cells-arm64", "versions"]
    assert pinsfile.platform_flags("crapkit-deploy:cells-arm64-baked") == ["--platform", "linux/arm64"]


def test_the_arm64_manifest_is_read_under_its_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(lock.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or lock.subprocess.CompletedProcess(argv, 0, b"", b""))
    lock.manifest("crapkit-deploy:cells-arm64")

    assert calls[0][-4:] == ["--platform", "linux/arm64", "crapkit-deploy:cells-arm64", "manifest"]


# --- the prerelease and the Cursor agent's alias -----------------------------------

def test_every_image_holds_the_prerelease_python():
    assert run.build_args(PINS)["PYTHON_PRERELEASE"] == PINS["python"]["prerelease"]
    assert pinsfile.expected_versions(PINS, "cells")["python3.15"] == PINS["python"]["prerelease"]


def test_the_cursor_agent_answers_to_the_name_cursors_docs_use():
    core = pinsfile.expected_versions(PINS, "core")

    assert core["agent"] == core["cursor-agent"] == PINS["harness"]["cursor-agent"]["version"]
    assert "/opt/harness-core/bin/agent" in DOCKERFILE


def test_a_native_install_copies_each_launcher_under_the_alias(tmp_path):
    for name in ("cursor-agent.cmd", "cursor-agent.ps1", "cursor-agent-sea", "node.exe"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    made = toolchain.alias_launchers(tmp_path, toolchain.harness_spec(PINS, "cursor-agent-windows-x64"))

    assert sorted(path.name for path in made) == ["agent.cmd", "agent.ps1"]
    assert (tmp_path / "agent.cmd").read_text(encoding="utf-8") == "cursor-agent.cmd"
    assert toolchain.alias_launchers(tmp_path, toolchain.harness_spec(PINS, "goose-windows-x64")) == []


# --- the README's npm lines the fixture stage caches --------------------------------

# page, the fenced line a user runs, the line the npm-fixtures stage runs for it
DOCUMENTED_NPM = [
    ("README.md", 'npm i -D "@vitest/coverage-v8@<your vitest major>"',
     'npm i -D --ignore-scripts "@vitest/coverage-v8@${vitest%%.*}"'),
    ("docs/lanes.md", "npm i -D @vitest/coverage-v8", "npm i -D --ignore-scripts @vitest/coverage-v8;"),
]


@pytest.mark.parametrize("page, documented, cached", DOCUMENTED_NPM)
def test_the_npm_fixture_stage_caches_what_each_documented_install_line_fetches(page, documented, cached):
    """An offline cell runs the docs' line against the image's npm cache, so the
    stage runs that same line at build time; a moved line fails here first."""
    lines = {line.strip() for line in (ROOT / page).read_text(encoding="utf-8").splitlines()}

    assert documented in lines
    assert cached in DOCKERFILE


def test_the_manifest_lists_the_npm_cache_the_unlocked_lines_filled():
    entry = (DOCKER / "entry.sh").read_text(encoding="utf-8")

    assert 'echo "## npm-cache"' in entry and "npm cache ls --cache /opt/npm-cache" in entry


# --- two checkouts on one machine ---------------------------------------------------

def test_the_toolchain_root_and_basetemp_can_be_named(monkeypatch, tmp_path):
    """pytest empties its basetemp when a run starts, and a toolchain refresh
    prunes wheels an older lock named, so a second checkout running native cells
    beside the first names its own of both."""
    monkeypatch.setenv(toolchain.ROOT_ENV, str(tmp_path / "chain"))
    monkeypatch.setenv(toolchain.BASETEMP_ENV, str(tmp_path / "dt"))

    assert toolchain.default_root() == tmp_path / "chain"
    assert toolchain.basetemp() == toolchain.long_path((tmp_path / "dt").resolve())
    assert (tmp_path / "dt").is_dir()


def test_without_a_name_the_toolchain_uses_the_os_cache(monkeypatch):
    monkeypatch.delenv(toolchain.ROOT_ENV, raising=False)
    monkeypatch.delenv(toolchain.BASETEMP_ENV, raising=False)

    assert toolchain.default_root().name == "crapkit-deploy"
    assert toolchain.basetemp().name in ("dt", "crapkit-deploy-tmp")


# --- build args and the layer cache -------------------------------------------------

def _instructions(text):
    """Dockerfile instructions with continuation lines joined, comments and heredoc bodies dropped."""
    joined = re.sub(r"\\\n", " ", re.sub(r"<<'EOF'.*?\nEOF\n", "<<EOF\n", text, flags=re.S))
    return [line for line in joined.splitlines() if line and not line.startswith("#") and not line[0].isspace()]


def _stage_pairs(lines):
    pairs, pending = [], []
    for line in lines:
        keyword, _, rest = line.partition(" ")
        if keyword == "ARG":
            pending.append(rest)
        elif keyword == "RUN":
            pairs, pending = pairs + [(name, rest) for name in pending], []
    return pairs


def _first_run_after_each_arg(text):
    """(arg, the first RUN after it in the same stage), for every stage ARG; the
    global ARGs above the first FROM feed FROM lines and are left out."""
    stages = "".join("\n" + line for line in _instructions(text)).split("\nFROM ")[1:]
    return [pair for stage in stages for pair in _stage_pairs(stage.splitlines())]


def test_each_build_arg_sits_just_above_the_run_that_reads_it():
    """An ARG joins the cache key of every RUN after it, used or not: a Goose pin
    declared above the full image's npm ci reran it, and a Python pin above the
    apt step reran apt."""
    stray = [(name, run_line[:40]) for name, run_line in _first_run_after_each_arg(DOCKERFILE)
             if f"${name}" not in run_line and "${" + name not in run_line]

    assert stray == []


def test_a_stray_arg_above_an_unrelated_run_is_caught():
    text = "FROM a AS b\nARG PIN\nARG OTHER\nRUN apt-get install x\nRUN echo $PIN $OTHER\n"

    assert [name for name, line in _first_run_after_each_arg(text) if f"${name}" not in line] == ["PIN", "OTHER"]


def test_a_second_checkout_tags_its_images_under_its_own_repository(monkeypatch):
    """Two checkouts building different pins on one daemon would each replace
    the other's crapkit-deploy:<image>, and each run would rebuild."""
    monkeypatch.setenv(run.REPO_ENV, "crapkit-deploy-next")
    argv = run.build_command(PINS, "core", "local", no_cache=False)

    assert argv[argv.index("-t") + 1] == "crapkit-deploy-next:core"
    assert run.versions_command("core")[-2] == "crapkit-deploy-next:core"
    monkeypatch.delenv(run.REPO_ENV)
    assert run.image_tag("core") == "crapkit-deploy:core"


def test_a_manifest_records_under_the_images_own_name_whatever_its_repository(tmp_path, monkeypatch):
    path = tmp_path / "image-manifest.lock"
    monkeypatch.setattr(lock, "manifest", lambda image: "uv 1\n")

    assert lock.main(["manifest", "--image", "crapkit-deploy-next:cells-arm64", "--manifest", str(path)]) == 0
    assert lock.manifest_blocks(path.read_text(encoding="utf-8")) == {"crapkit-deploy:cells-arm64": "uv 1\n"}
    assert lock.main(["manifest", "--check", "--image", "other:cells-arm64", "--manifest", str(path)]) == 0


def test_every_pinned_download_names_a_user_agent_the_cursor_cdn_serves(monkeypatch):
    """downloads.cursor.com answers Python's own User-Agent with 403, so a native
    toolchain could not install the Cursor agent the Linux images curl."""
    seen = []
    monkeypatch.setattr(lock.urllib.request, "urlopen", seen.append)
    lock.urlopen("https://downloads.cursor.com/lab/x/windows/x64/agent-cli-package.zip")

    assert seen[0].get_header("User-agent") == lock.USER_AGENT
    assert toolchain.download.__defaults__ == (lock.urlopen,) and lock.fetch.__defaults__ == (lock.urlopen,)
