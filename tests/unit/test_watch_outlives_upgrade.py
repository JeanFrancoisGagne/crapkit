"""`crapkit watch` running across an upgrade of its own package stops and says
to restart it.

`pip install -U crapkit` replaces the package's files under a running watcher,
which keeps the old code in memory. Its first rescore after that imported the
process-ownership modules, now the new release's files, and the watcher died
with a traceback from inside them. It now reads the version the package
directory holds before each rescore and, when that changed, exits 1 with one
line that names both versions and the restart.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import crapkit
from crapkit import _package
from crapkit.cli import admin
from crapkit.errors import CrapkitError
from hang_guard import communicate, next_line

import pytest

_CONFIG = '[crapkit]\ntarget = 6\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _repo(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (root / "crapkit.toml").write_text(_CONFIG, encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for argv in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "start"]):
        subprocess.run(git + argv, cwd=root, check=True, capture_output=True)
    return root


def _upgrade(package: Path) -> None:
    """What `pip install -U` leaves: a new version, and every module the old
    process has not imported yet written for another API."""
    for module in package.rglob("*.py"):
        if module.name != "__init__.py" or module.parent != package:
            module.write_text("raise ImportError('written by the next release')\n", encoding="utf-8")
    (package / "__init__.py").write_text('__version__ = "99.0.0"\n', encoding="utf-8")


def test_a_watcher_stops_and_names_the_restart_at_its_first_rescore_after_an_upgrade(tmp_path):
    site = tmp_path / "site"
    shutil.copytree(Path(crapkit.__file__).parent, site / "crapkit",
                    ignore=shutil.ignore_patterns("__pycache__"))
    repo = _repo(tmp_path / "repo")
    env = {**os.environ, "PYTHONPATH": str(site), "PYTHONDONTWRITEBYTECODE": "1"}
    watcher = subprocess.Popen([sys.executable, "-m", "crapkit", "watch", "--repo", str(repo),
                                "--interval", "0.1"], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    assert next_line(watcher).startswith("watching 1 tracked files")
    _upgrade(site / "crapkit")

    (repo / "src" / "a.py").write_text("def f(x):\n    if x:\n        return 1\n    return x\n",
                                       encoding="utf-8")
    _, err = communicate(watcher)

    assert watcher.returncode == 1, err
    assert "Traceback" not in err, err
    assert err.strip() == (
        f"crapkit: crapkit was upgraded from {crapkit.__version__} to 99.0.0 while `crapkit "
        f"watch` ran, and this process still runs {crapkit.__version__}'s code, which cannot "
        "load the new files; restart `crapkit watch`")


def test_the_check_reads_the_package_directory_the_process_imported():
    assert _package._INIT == Path(crapkit.__file__)
    assert _package.installed_version() == crapkit.__version__
    assert _package.upgraded_to() is None


def test_an_unchanged_package_rescores_and_a_changed_one_refuses(monkeypatch, tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text(f'__version__ = "{crapkit.__version__}"\n', encoding="utf-8")
    monkeypatch.setattr(_package, "_INIT", init)
    admin._refuse_upgraded()

    init.write_text('__version__ = "99.0.0"\n', encoding="utf-8")
    with pytest.raises(CrapkitError, match="restart `crapkit watch`"):
        admin._refuse_upgraded()
