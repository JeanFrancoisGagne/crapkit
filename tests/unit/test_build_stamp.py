"""The build writes the commit it was built from into the package.

`crapkit --version --json` answers a source checkout from git, but an installed
wheel has no checkout beside it, and every wheel printed commit null: a deploy
run's candidate, a `pip install git+URL` and a PyPI wheel could not be told
apart from another build that said the same version. setup.py's build_py and
sdist write crapkit/_build.json, and the reader in crapkit.cli.parser prints it.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from cli_inproc_repo import commit_all, git
from hang_guard import HANG_SECONDS

from crapkit.cli import parser

ROOT = Path(__file__).resolve().parents[2]


def _load_setup():
    spec = importlib.util.spec_from_file_location("_crapkit_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load_setup()


def _checkout(root: Path) -> str:
    (root / "src" / "crapkit").mkdir(parents=True)
    (root / "src" / "crapkit" / "__init__.py").write_text("", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "a checkout")
    return git(root, "rev-parse", "HEAD").strip()


def test_the_build_writes_the_file_the_reader_reads():
    assert setup.STAMP == parser._BUILD_STAMP


def test_a_clean_checkout_stamps_its_commit(tmp_path):
    commit = _checkout(tmp_path)

    assert setup.checkout_stamp(tmp_path) == {"commit": commit, "dirty": False}


@pytest.mark.parametrize("change", ["edited", "staged", "untracked"])
def test_a_checkout_holding_changes_stamps_dirty(tmp_path, change):
    _checkout(tmp_path)
    source = tmp_path / "src" / "crapkit" / ("new.py" if change == "untracked" else "__init__.py")
    source.write_text("X = 1\n", encoding="utf-8")
    if change == "staged":
        git(tmp_path, "add", "-A")

    assert setup.checkout_stamp(tmp_path)["dirty"] is True


def test_build_output_git_ignores_leaves_the_checkout_clean(tmp_path):
    _checkout(tmp_path)
    (tmp_path / "build" / "lib").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "x.py").write_text("X = 1\n", encoding="utf-8")

    assert setup.checkout_stamp(tmp_path)["dirty"] is False


def test_a_tree_inside_another_repository_takes_no_stamp(tmp_path):
    """An exported tree unpacked inside some repository is not that repository's HEAD."""
    _checkout(tmp_path)
    tree = tmp_path / "staged"
    tree.mkdir()

    assert setup.checkout_stamp(tree) is None


def test_a_checkout_git_cannot_read_takes_no_stamp(tmp_path):
    git(tmp_path, "init", "-q")

    assert setup.checkout_stamp(tmp_path) is None


@pytest.mark.parametrize("failure", [FileNotFoundError("git"),
                                     subprocess.TimeoutExpired("git", 30)])
def test_no_git_or_a_hung_git_takes_no_stamp(tmp_path, monkeypatch, failure):
    _checkout(tmp_path)

    def refuse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(setup.subprocess, "run", refuse)

    assert setup.checkout_stamp(tmp_path) is None


def test_the_stamp_written_is_the_one_the_reader_prints(tmp_path):
    commit = _checkout(tmp_path / "tree")
    package = tmp_path / "lib" / "crapkit"

    setup.write_stamp(package, setup.tree_stamp(tmp_path / "tree"))

    assert parser._stamped_identity(package) == (commit, False)


def _stamp(package: Path, stamp) -> None:
    package.mkdir(parents=True, exist_ok=True)
    (package / setup.STAMP).write_text(json.dumps(stamp), encoding="utf-8")


def test_a_tree_with_no_git_carries_the_stamp_its_sdist_holds(tmp_path):
    carried = {"commit": "ab" * 20, "dirty": True}
    _stamp(tmp_path / "sdist" / "src" / "crapkit", carried)

    stamp = setup.tree_stamp(tmp_path / "sdist")
    setup.write_stamp(tmp_path / "lib" / "crapkit", stamp)

    assert stamp == carried
    assert parser._stamped_identity(tmp_path / "lib" / "crapkit") == ("ab" * 20, True)


@pytest.mark.parametrize("carried", [[], {"commit": "abc", "dirty": False},
                                     {"commit": "ab" * 20}])
def test_a_stamp_the_tree_cannot_vouch_for_is_removed(tmp_path, carried):
    """A build/lib reused from an earlier build must not keep that build's commit."""
    _stamp(tmp_path / "sdist" / "src" / "crapkit", carried)
    _stamp(tmp_path / "lib" / "crapkit", {"commit": "cd" * 20, "dirty": False})
    stamp = setup.tree_stamp(tmp_path / "sdist")
    setup.write_stamp(tmp_path / "lib" / "crapkit", stamp)

    assert stamp is None
    assert not (tmp_path / "lib" / "crapkit" / setup.STAMP).exists()


# --- the real build ---------------------------------------------------------------
#
# setuptools runs setup.py beside crapkit's own pyproject.toml, as `python -m
# build`, pip and uv do. Skipped where setuptools cannot build a wheel.

_HOOK = "import sys, setuptools.build_meta as b; print(getattr(b, sys.argv[1])(sys.argv[2]))"
# tarfile's own filter where this Python has one (3.11.4 and later).
_EXTRACT = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}


def _project(root: Path) -> Path:
    (root / "src" / "crapkit").mkdir(parents=True)
    (root / "src" / "crapkit" / "__init__.py").write_text('__version__ = "0.0.0"\n',
                                                          encoding="utf-8")
    for name in ("pyproject.toml", "README.md", "setup.py", ".gitignore"):
        shutil.copyfile(ROOT / name, root / name)
    return root


def _built(tree: Path, hook: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([sys.executable, "-c", _HOOK, hook, str(out)], cwd=tree,
                          capture_output=True, text=True, timeout=HANG_SECONDS)
    assert done.returncode == 0, done.stderr
    return out / done.stdout.strip().splitlines()[-1]


def _wheel_stamp(wheel: Path):
    with zipfile.ZipFile(wheel) as archive:
        name = f"crapkit/{setup.STAMP}"
        return json.loads(archive.read(name)) if name in archive.namelist() else None


@pytest.fixture
def builds():
    pytest.importorskip("setuptools.command.bdist_wheel")


def test_a_wheel_built_in_a_checkout_names_its_commit(tmp_path, builds):
    tree = _project(tmp_path / "checkout")
    git(tree, "init", "-q")
    commit_all(tree, "a release candidate")
    commit = git(tree, "rev-parse", "HEAD").strip()

    assert _wheel_stamp(_built(tree, "build_wheel", tmp_path / "dist")) == {
        "commit": commit, "dirty": False}


def test_a_wheel_built_from_the_sdist_names_the_checkouts_commit(tmp_path, builds):
    """`python -m build` builds the wheel from the sdist, which has no .git."""
    tree = _project(tmp_path / "checkout")
    git(tree, "init", "-q")
    commit_all(tree, "a release candidate")
    commit = git(tree, "rev-parse", "HEAD").strip()
    sdist = _built(tree, "build_sdist", tmp_path / "sdist")
    with tarfile.open(sdist) as archive:
        archive.extractall(tmp_path / "unpacked", **_EXTRACT)
    unpacked, = (tmp_path / "unpacked").iterdir()

    assert _wheel_stamp(_built(unpacked, "build_wheel", tmp_path / "dist")) == {
        "commit": commit, "dirty": False}


def test_a_wheel_built_with_no_checkout_at_hand_carries_no_stamp(tmp_path, builds):
    tree = _project(tmp_path / "export")

    assert _wheel_stamp(_built(tree, "build_wheel", tmp_path / "dist")) is None
