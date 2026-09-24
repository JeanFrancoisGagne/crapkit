"""tools/deploy/candidate.py stamps the tree under test past every release.

pip, uv and pipx only treat the candidate as an upgrade when its version is
higher than the newest release the wheelhouse holds. A tree still at the
released version gets one patch more, written through release.py's own
SURFACES table so README, pyproject and the manifests all agree.
"""
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import candidate  # noqa: E402

RELEASE_PY = '''from typing import NamedTuple
class Surface(NamedTuple):
    path: str
    pattern: str
    count: int
SURFACES = (
    Surface("pyproject.toml", 'version = "{v}"', 1),
    Surface("README.md", "crapkit {v}\\n", 1),
    Surface("README.md", "rev: v{v}", 1),
)
'''


@pytest.mark.parametrize("tree, newest, stamped", [
    ("0.8.0", "0.8.0", "0.8.1"),
    ("0.7.9", "0.8.0", "0.8.1"),
    ("0.9.0", "0.8.0", "0.9.0"),
])
def test_the_stamp_is_past_the_newest_release(tree, newest, stamped):
    assert candidate.stamp_version(tree, newest) == stamped


def _tar(tmp_path, files):
    path = tmp_path / "tree.tar"
    with tarfile.open(path, "w") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def tree(tmp_path):
    return _tar(tmp_path, {
        "pyproject.toml": '[project]\nname = "crapkit"\nversion = "0.8.0"\n',
        "README.md": "$ crapkit --version\ncrapkit 0.8.0\n\n    rev: v0.8.0\n",
        "tools/release/release.py": RELEASE_PY,
    })


@pytest.fixture
def lock(tmp_path):
    path = tmp_path / "wheelhouse.lock"
    path.write_text('[newest]\ncrapkit = "0.8.0"\nlizard = "1.24.0"\n', encoding="utf-8")
    return path


def fake_build(root, dist, python=None):
    dist.mkdir()
    for name in ("crapkit-0.8.1-py3-none-any.whl", "crapkit-0.8.1.tar.gz"):
        (dist / name).write_bytes(b"")
    return sorted(dist.iterdir())


def test_every_surface_names_the_candidate(tree, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(candidate, "build", fake_build)
    record = candidate.candidate(tree, tmp_path / "out", lock)
    staged = tmp_path / "out" / "staged"

    assert record["version"] == "0.8.1"
    assert 'version = "0.8.1"' in (staged / "pyproject.toml").read_text(encoding="utf-8")
    assert (staged / "README.md").read_text(encoding="utf-8") == (
        "$ crapkit --version\ncrapkit 0.8.1\n\n    rev: v0.8.1\n")
    assert sorted(record["stamped"]) == ["README.md", "pyproject.toml"]


def test_the_record_names_both_artifacts_and_the_release_it_follows(tree, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(candidate, "build", fake_build)
    candidate.candidate(tree, tmp_path / "out", lock)
    record = json.loads((tmp_path / "out" / "candidate.json").read_text(encoding="utf-8"))

    assert (record["wheel"], record["sdist"]) == ("crapkit-0.8.1-py3-none-any.whl", "crapkit-0.8.1.tar.gz")
    assert (record["tree_version"], record["newest_release"]) == ("0.8.0", "0.8.0")
    assert len(record["source_hash"]) == len(record["file_list_hash"]) == 64


def test_the_source_hash_moves_with_a_byte_and_the_list_hash_does_not(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.py").write_text("X = 1\n", encoding="utf-8")
    before = candidate.hashes(root)
    (root / "a.py").write_text("X = 2\n", encoding="utf-8")
    after = candidate.hashes(root)

    assert before["source_hash"] != after["source_hash"]
    assert before["file_list_hash"] == after["file_list_hash"]
