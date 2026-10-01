"""candidate.py's hash-only run and the hashes candidate.json records.

One tree.tar must reach one source_hash whichever directory candidate.py writes
into and whichever OS runs it. The release's deploy record no longer reads this
hash: it is keyed on the tag commit's git tree id, because a checkout with
core.autocrlf=true writes other bytes for the same commit
(tests/unit/test_release_deploy_tree_key.py).
"""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "deploy"))

import candidate  # noqa: E402
import export  # noqa: E402

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
    """A tree already past the newest release, so nothing is stamped."""
    return _tar(tmp_path, {"pyproject.toml": '[project]\nname = "crapkit"\nversion = "0.9.0"\n',
                           "src/crapkit/__init__.py": '__version__ = "0.9.0"\n'})


@pytest.fixture
def lock(tmp_path):
    path = tmp_path / "wheelhouse.lock"
    path.write_text('[newest]\ncrapkit = "0.8.0"\nlizard = "1.24.0"\n', encoding="utf-8")
    return path


def _refuse_build(*args, **kwargs):
    raise AssertionError("--no-build built a wheel")


def fake_build(root, dist, python=None):
    dist.mkdir()
    for name in ("crapkit-0.9.0-py3-none-any.whl", "crapkit-0.9.0.tar.gz"):
        (dist / name).write_bytes(b"")
    return sorted(dist.iterdir())


def _main(tree, out, lock, *extra):
    return candidate.main(["--tree", str(tree), "--out", str(out), "--lock", str(lock), *extra])


def _record(out) -> dict:
    return json.loads((out / "candidate.json").read_text(encoding="utf-8"))


def test_no_build_records_the_hashes_and_names_no_artifact(tree, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(candidate, "build", _refuse_build)
    out = tmp_path / "out"

    assert _main(tree, out, lock, "--no-build") == 0

    record = _record(out)
    assert "wheel" not in record and "sdist" not in record and not (out / "dist").exists()
    assert record["source_hash"] == candidate.hashes(out / "staged")["source_hash"]
    assert (record["version"], record["stamped"]) == ("0.9.0", [])


def test_building_does_not_move_the_source_hash(tree, lock, tmp_path, monkeypatch):
    monkeypatch.setattr(candidate, "build", fake_build)

    built = candidate.candidate(tree, tmp_path / "built", lock)
    staged = candidate.candidate(tree, tmp_path / "staged", lock, build_dist=False)

    assert built["source_hash"] == staged["source_hash"]
    assert (built["wheel"], built["sdist"]) == ("crapkit-0.9.0-py3-none-any.whl", "crapkit-0.9.0.tar.gz")


def test_a_hash_only_run_prints_its_source_hash(tree, lock, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(candidate, "build", _refuse_build)

    assert _main(tree, tmp_path / "scope", lock, "--no-build") == 0

    expected = _record(tmp_path / "scope")["source_hash"]
    assert capsys.readouterr().out == f"candidate: crapkit 0.9.0 (not built; source_hash {expected})\n"


def test_the_kit_no_longer_takes_a_source_hash_to_match(tree, lock, tmp_path):
    """The record's key is the git tree id; a byte hash split one commit in two."""
    with pytest.raises(SystemExit):
        _main(tree, tmp_path / "scope", lock, "--no-build", "--source-hash", "0" * 64)


def test_a_built_candidate_still_prints_its_artifacts(tree, lock, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(candidate, "build", fake_build)

    assert _main(tree, tmp_path / "out", lock) == 0
    assert capsys.readouterr().out == (
        "candidate: crapkit 0.9.0 (crapkit-0.9.0-py3-none-any.whl, crapkit-0.9.0.tar.gz)\n")


def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=Deploy Test", "-c", "user.email=deploy@example.test", *args],
                   cwd=root, check=True, capture_output=True)


def _checkout(tmp_path) -> Path:
    """A commit carrying a package and the ignore rule the real tree has for .crapkit/."""
    root = tmp_path / "checkout"
    (root / "src" / "crapkit").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "crapkit"\nversion = "0.9.0"\n', encoding="utf-8")
    (root / "src" / "crapkit" / "__init__.py").write_text('__version__ = "0.9.0"\n', encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "the tag commit")
    return root


def _hash_checkout(root, out, lock) -> str:
    export.export(root, out)
    assert _main(out / "tree.tar", out / "candidate", lock, "--no-build") == 0
    return _record(out / "candidate")["source_hash"]


def test_two_out_directories_reach_one_hash_for_one_commit(tmp_path, lock, monkeypatch):
    """One out directory under the checkout's ignored .crapkit/, one under a
    runner's temp directory. Neither output reaches the tree it hashes."""
    monkeypatch.setattr(candidate, "build", _refuse_build)
    root = _checkout(tmp_path)

    inside = _hash_checkout(root, root / ".crapkit" / "deploy-record", lock)
    outside = _hash_checkout(root, tmp_path / "runner-temp" / "deploy-record", lock)

    assert inside == outside


# Four names whose order a Path sort sets by platform. WindowsPath compares
# case-folded, so action.yml sorted before AGENTS.md there and after it on
# Linux. PosixPath compares part by part, so tools/deploy/ sorted before
# tools/deploy-notes.md, where the plain names sort the other way.
MIXED = {"pyproject.toml": '[project]\nname = "crapkit"\nversion = "0.9.0"\n',
         "AGENTS.md": "# agents\n", "action.yml": "name: crapkit\n",
         "tools/deploy/candidate.py": "X = 1\n", "tools/deploy-notes.md": "notes\n"}


def _hashes_by_name(files: dict) -> tuple[str, str]:
    """source_hash and file_list_hash, computed here over the plain names in str order."""
    sources, listing = hashlib.sha256(), hashlib.sha256()
    for name in sorted(files):
        listing.update(name.encode("utf-8") + b"\0")
        sources.update(name.encode("utf-8") + b"\0" + hashlib.sha256(files[name].encode("utf-8")).digest())
    return sources.hexdigest(), listing.hexdigest()


def test_the_hash_orders_files_by_their_posix_name_on_every_os(tmp_path, lock, monkeypatch):
    """The release hashes the tag commit on Windows, and deploy.yml's scope job
    hashes it again on Linux. Sorting Path objects gave one 0.8.1 tree.tar
    e07f35f5 on Windows and 2fb2a212 on Linux, so the scope job failed every
    dispatch."""
    monkeypatch.setattr(candidate, "build", _refuse_build)
    expected = _hashes_by_name(MIXED)

    code = _main(_tar(tmp_path, MIXED), tmp_path / "scope", lock, "--no-build")

    record = _record(tmp_path / "scope")
    assert (record["source_hash"], record["file_list_hash"]) == expected
    assert code == 0


RELEASE_PY = ('from typing import NamedTuple\n'
              'class Surface(NamedTuple):\n    path: str\n    pattern: str\n    count: int\n'
              'SURFACES = (Surface("pyproject.toml", \'version = "{v}"\', 1),)\n')
AT_THE_RELEASE = {"pyproject.toml": '[project]\nname = "crapkit"\nversion = "0.8.0"\n',
                  "tools/release/release.py": RELEASE_PY}


def test_a_stamped_tree_hashes_alike_from_any_out_directory(tmp_path, lock, monkeypatch):
    """A tree at the newest release is stamped through release.py's SURFACES,
    read from the staged copy. Importing it wrote
    tools/release/__pycache__/release.cpython-312.pyc into staged/, whose bytes
    carry the staged path, so the stage and the scope job hashed one tree.tar
    to two values. A python that writes bytecode, as the scope job's does."""
    monkeypatch.setattr(candidate, "build", _refuse_build)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    tree = _tar(tmp_path, AT_THE_RELEASE)

    stage = candidate.candidate(tree, tmp_path / "stage", lock, build_dist=False)
    scope = candidate.candidate(tree, tmp_path / "runner-temp" / "scope", lock, build_dist=False)

    assert stage["stamped"] == ["pyproject.toml"]
    assert stage["source_hash"] == scope["source_hash"]
    assert sorted(path.relative_to(tmp_path / "stage" / "staged").as_posix()
                  for path in (tmp_path / "stage" / "staged").rglob("*") if path.is_file()) == sorted(AT_THE_RELEASE)
