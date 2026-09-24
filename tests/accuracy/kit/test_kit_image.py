"""The accuracy image's two scripts: install-tools.sh and the entry point.

install-tools.sh installs every binary oracle from the url its pin names and
refuses a download whose sha256 differs; entry.sh picks the cell's Python and
runs the command it was handed. Both run inside the image, so these tests run
on Linux; ACCURACY_BIN, ACCURACY_DOWNLOADS and ACCURACY_VENVS point the scripts
at a temporary directory instead of /opt.
"""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from accuracy.kit import oracles

pytestmark = [pytest.mark.process, pytest.mark.platform("linux")]
IMAGE = oracles.REPO / "tools" / "accuracy" / "image"
TOOL = b"#!/bin/sh\necho shellmetrics\n"


def _pins(path: Path, url: str, sha256: str, installed: Path) -> Path:
    path.write_text(
        '[oracle."weird-tool"]\nversion = "1.2.3"\n\n'
        f'[oracle.shellmetrics]\nversion = "b3bfff2"\nurl = "{url}"\n'
        f'sha256 = "{sha256}"\npath = "{installed}"\n', encoding="utf-8")
    return path


def _install(tmp_path: Path, pins: Path, *tools: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ACCURACY_BIN": str(tmp_path / "bin"), "KEEP_DOWNLOADS": "1",
           "ACCURACY_DOWNLOADS": str(tmp_path / "downloads")}
    return subprocess.run(["sh", str(IMAGE / "install-tools.sh"), str(pins), *tools],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "published" / "shellmetrics"
    path.parent.mkdir()
    path.write_bytes(TOOL)
    return path


def test_a_download_matching_its_pin_installs_at_the_pinned_path(tmp_path, source):
    installed = tmp_path / "installed" / "shellmetrics"
    installed.parent.mkdir()
    pins = _pins(tmp_path / "pins.toml", source.as_uri(), hashlib.sha256(TOOL).hexdigest(),
                 installed)

    done = _install(tmp_path, pins, "shellmetrics")

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == "install-tools: shellmetrics b3bfff2"
    assert installed.read_bytes() == TOOL
    assert os.access(installed, os.X_OK)


def test_a_kept_download_is_reused_without_fetching_again(tmp_path, source):
    installed = tmp_path / "installed"
    pins = _pins(tmp_path / "pins.toml", source.as_uri(), hashlib.sha256(TOOL).hexdigest(),
                 installed)
    assert _install(tmp_path, pins, "shellmetrics").returncode == 0
    source.unlink()
    installed.unlink()

    done = _install(tmp_path, pins, "shellmetrics")

    assert done.returncode == 0, done.stderr
    assert installed.read_bytes() == TOOL


def test_a_download_that_differs_from_its_pin_stops_the_build_naming_the_tool(tmp_path, source):
    installed = tmp_path / "installed"
    pins = _pins(tmp_path / "pins.toml", source.as_uri(), "0" * 64, installed)

    done = _install(tmp_path, pins, "shellmetrics")

    assert done.returncode == 1
    assert (f"install-tools: shellmetrics from {source.as_uri()} does not match the sha256 "
            "in pins.toml") in done.stderr
    assert not installed.exists()


def test_a_tool_with_no_pin_or_no_installer_is_refused(tmp_path):
    pins = tmp_path / "pins.toml"
    pins.write_text('[oracle."weird-tool"]\nversion = "1.2.3"\n', encoding="utf-8")

    unpinned = _install(tmp_path, pins, "shellmetrics")
    unknown = _install(tmp_path, pins, "weird-tool")

    assert unpinned.returncode == 1
    assert "install-tools: no pin named shellmetrics" in unpinned.stderr
    assert unknown.returncode == 1
    assert unknown.stdout.splitlines() == ["install-tools: weird-tool 1.2.3"]
    assert "install-tools: no installer for weird-tool" in unknown.stderr


def _venvs(tmp_path: Path) -> Path:
    python = tmp_path / "venvs" / "3.13" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    return tmp_path / "venvs"


def _enter(tmp_path: Path, env: dict, *argv: str) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ["PATH"], "ACCURACY_VENVS": str(_venvs(tmp_path))}
    return subprocess.run(["sh", str(IMAGE / "entry.sh"), *argv], cwd=tmp_path,
                          capture_output=True, text=True, env={**base, **env}, timeout=60)


def test_the_entry_point_runs_the_command_in_the_cells_venv(tmp_path):
    done = _enter(tmp_path, {"CRAPKIT_ACCURACY_PY": "3.13", "HOME": "/"},
                  "sh", "-c", 'echo "$VIRTUAL_ENV"; echo "${PATH%%:*}"; echo "$HOME"')

    venv = tmp_path / "venvs" / "3.13"
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [str(venv), str(venv / "bin"), "/tmp/accuracy-home"]


def test_the_entry_point_refuses_a_python_the_image_does_not_hold(tmp_path):
    done = _enter(tmp_path, {"CRAPKIT_ACCURACY_PY": "3.10"}, "true")

    assert done.returncode == 3
    assert "accuracy-entry: this image has no Python 3.10" in done.stderr
