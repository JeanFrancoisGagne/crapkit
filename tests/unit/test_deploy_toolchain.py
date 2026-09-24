"""tools/deploy/toolchain.py installs the images' pins natively for the Windows
and macOS jobs, and describes them in the same toolchain.json the images carry.

A key the kit reads that one side names and the other does not would pass on
one OS and fail on the other, so the native description is held to the image's.
Every download is checked against its pin before anything unpacks it.
"""
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import pins as pinsfile  # noqa: E402
import toolchain  # noqa: E402

PINS = pinsfile.load()
DOCKERFILE = (ROOT / "tests" / "deploy" / "docker" / "Dockerfile").read_text(encoding="utf-8")


def image_toolchain():
    body = DOCKERFILE.split("/opt/deploy/toolchain.json\n", 1)[1].split("\nEOF\n", 1)[0]
    return json.loads(body)


def test_the_native_description_names_every_key_the_image_names(tmp_path):
    tools = {name: f"/t/{name}" for name in ("uv", "uvx", "node", "npm", "git", "prek", "pipx", "runner", "bash")}
    tools["path"] = ["/t/bin"]
    described = toolchain.describe(tmp_path, tools, {"3.12": "/t/python3.12"}, ["core"])

    assert set(image_toolchain()) - {"python_dir"} <= set(described)
    assert described["path"][0] == "/t/bin"
    assert described["runner_python"] == "/t/runner"


def test_the_runner_venv_is_never_on_the_described_path(tmp_path):
    tools = {name: f"/t/{name}/x" for name in ("uv", "uvx", "node", "npm", "git", "prek", "pipx", "runner", "bash")}
    tools["path"] = ["/t/bin"]
    described = toolchain.describe(tmp_path, tools, {"3.12": "/py/python"}, [])

    assert "/t/runner" not in described["path"]


@pytest.mark.parametrize("stem", ["uv-windows", "node-windows", "portable-git-windows", "pwsh-windows",
                                  "prek-windows"])
def test_every_windows_tool_has_a_pinned_download(stem):
    spec = toolchain.binary(PINS, stem, "windows")

    assert spec["os"] == "windows" and len(spec["sha256"]) == 64


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_download_that_does_not_match_its_pin_is_refused(tmp_path):
    spec = {"url": "https://example.com/tool.zip", "sha256": "0" * 64}

    with pytest.raises(SystemExit, match="does not match"):
        toolchain.download(spec, tmp_path, opener=lambda url: _Response(b"other bytes"))


def test_zip_and_tar_archives_unpack_to_their_one_top_directory(tmp_path):
    archive = tmp_path / "tool.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("tool-1.0/bin/tool", "x")
    tarball = tmp_path / "tool.tar.gz"
    with tarfile.open(tarball, "w:gz") as bundle:
        data = b"y"
        info = tarfile.TarInfo("other-2.0/tool")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))

    assert toolchain.only_child(toolchain.unpack(archive, tmp_path / "a")).name == "tool-1.0"
    assert toolchain.only_child(toolchain.unpack(tarball, tmp_path / "b")).name == "other-2.0"


def test_a_step_whose_output_exists_is_not_run_again(tmp_path):
    ran = []
    target = tmp_path / "done"
    target.mkdir()

    toolchain._once(target, lambda: ran.append(1))

    assert ran == []


@pytest.mark.skipif(sys.platform != "win32", reason="the C:\\dt base is the Windows jobs' basetemp")
def test_the_windows_basetemp_has_no_short_name_component():
    assert "~" not in str(toolchain.basetemp())
