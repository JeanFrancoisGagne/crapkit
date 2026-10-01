"""coverage.py's shards under a name that is not UTF-8.

A pytest-cov lane in a checkout under a Latin-1 directory, or on a host whose
name is Latin-1, leaves its `.coverage.<host>.<pid>.<random>` shards and no
report: coverage.py stores every measured path as UTF-8 text, and its combine
fails with a UnicodeEncodeError on U+DCE9. crapkit told the operator this was
what a killed parallel run leaves and to run `coverage combine` by hand, which
fails the same way. The lane's failure now names the directory or the host
name and the rename.
"""
import os
import sys

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane
from name_bytes import NOT_UTF8_NAMES

PY = sys.executable
POSIX_NAMES = pytest.mark.skipif(sys.platform == "win32",
                                 reason="needs a POSIX file system that stores a name whose bytes are not UTF-8")
KILLED = "which is what a killed parallel run leaves behind"
RENAME = "holds bytes that are not UTF-8, and coverage.py stores every path as UTF-8. Rename it to UTF-8"


@pytest.fixture(autouse=True)
def _host(on_a_host):
    """The lane runs no suite; the refusal inside a container is not the subject."""


def _failed(root, cwd: str) -> str:
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact=".crapkit/cov/coverage.json", parser="coveragepy", scopes=(), cwd=cwd)
    with pytest.raises(ToolError) as failed:
        run_lane(root, lane)
    return str(failed.value)


def _shard(directory, host: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f".coverage.{host}.pid5.aaaa").write_bytes(b"x")


@POSIX_NAMES
@NOT_UTF8_NAMES
def test_shards_under_a_latin1_directory_name_it_and_the_rename(tmp_path):
    _shard(tmp_path / os.fsdecode(b"caf\xe9"), "box")

    message = _failed(tmp_path, os.fsdecode(b"caf\xe9"))

    assert f"the directory {tmp_path}/caf\\xe9 {RENAME}" in message, message
    assert KILLED not in message


@POSIX_NAMES
@NOT_UTF8_NAMES
def test_shards_named_for_a_latin1_host_name_the_host_and_the_rename(tmp_path):
    _shard(tmp_path / "pkg", os.fsdecode(b"h\xf4te"))

    message = _failed(tmp_path, "pkg")

    assert "this host's name, which coverage.py puts in each shard's name (.coverage.h\\xf4te.pid5.aaaa)" in message
    assert RENAME in message and KILLED not in message, message


@pytest.mark.parametrize("directory, host", [("pkg", "box"), ("café 李", "box"), ("pkg", "hôte")],
                         ids=["ascii", "utf8-directory", "utf8-host"])
def test_shards_under_utf8_names_get_the_killed_run_recipe(tmp_path, directory, host):
    _shard(tmp_path / directory, host)

    message = _failed(tmp_path, directory)

    assert KILLED in message and RENAME not in message, message
