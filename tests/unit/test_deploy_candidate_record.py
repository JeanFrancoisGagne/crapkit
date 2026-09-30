"""candidate.py's hash-only run, which keys the release's deploy record.

`release.py run deploy` runs export.py and then candidate.py with --no-build on
the tag commit's tree, and dispatches deploy.yml's release cadence with the
source_hash candidate.json records. The workflow's scope job runs the same two
commands on the tree it checked out, with --source-hash, and fails the run
when the tree hashes otherwise. Both sides must reach one hash for one commit,
whichever directory they write into.
"""
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

OTHER = "0" * 64


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


def test_the_hash_the_release_dispatched_with_passes(tree, lock, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(candidate, "build", _refuse_build)
    _main(tree, tmp_path / "release", lock, "--no-build")
    expected = _record(tmp_path / "release")["source_hash"]
    capsys.readouterr()

    assert _main(tree, tmp_path / "scope", lock, "--no-build", "--source-hash", expected) == 0
    assert capsys.readouterr().out == f"candidate: crapkit 0.9.0 (not built; source_hash {expected})\n"


def test_another_hash_fails_the_run_and_names_both(tree, lock, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(candidate, "build", _refuse_build)

    assert _main(tree, tmp_path / "scope", lock, "--no-build", "--source-hash", OTHER) == 1

    found = _record(tmp_path / "scope")["source_hash"]
    assert capsys.readouterr().err == (f"candidate: the tree hashes to source_hash {found}, and the "
                                       f"release dispatched this run for {OTHER}\n")


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


def test_the_stage_and_the_scope_job_reach_one_hash_for_one_commit(tmp_path, lock, monkeypatch):
    """The stage writes under the checkout's ignored .crapkit/deploy-record; the
    scope job writes under the runner's temp directory. Neither output reaches
    the tree it hashes."""
    monkeypatch.setattr(candidate, "build", _refuse_build)
    root = _checkout(tmp_path)

    inside = _hash_checkout(root, root / ".crapkit" / "deploy-record", lock)
    outside = _hash_checkout(root, tmp_path / "runner-temp" / "deploy-record", lock)

    assert inside == outside
