"""Private test packages with access to the current runtime's dependencies."""
import os
from pathlib import Path
import site
import subprocess
import sys
import venv

import pytest

DEPLOY = Path(__file__).resolve().parent / "deploy"


def pytest_ignore_collect(collection_path, config):
    """tests/deploy runs only under CRAPKIT_DEPLOY=1. Its cells need the pinned
    toolchain that tools/deploy/run.py provides, so a bare `pytest` leaves the
    tree uncollected rather than failing on a harness this machine lacks."""
    if os.environ.get("CRAPKIT_DEPLOY") == "1":
        return None
    return True if DEPLOY in (Path(collection_path), *Path(collection_path).parents) else None


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _parent_site_dirs() -> list[str]:
    """The site directories this interpreter imports from, in its sys.path order.

    Not purelib and platlib: a venv made with --system-site-packages keeps an
    empty site-packages of its own and imports pytest from the base install and
    lizard from the user site."""
    sites = {_key(path) for path in (*site.getsitepackages(), site.getusersitepackages())}
    return list(dict.fromkeys(path for path in sys.path if _key(path) in sites))


def _dependency_venv(root: Path) -> tuple[Path, Path]:
    venv.EnvBuilder(with_pip=False).create(root)
    python = root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    result = subprocess.run([str(python), '-I', '-X', 'utf8', '-c',
                             "import sysconfig; print(sysconfig.get_path('purelib'))"],
                            capture_output=True, text=True, encoding='utf-8', check=True)
    child_site = Path(result.stdout.strip())
    # The child site stays first. Process dependency hooks for subprocess coverage.
    startup = '; '.join('site.addsitedir(' + ascii(path) + ')' for path in _parent_site_dirs())
    # ASCII escapes also support Python versions that read .pth in the locale encoding.
    (child_site / 'test-dependencies.pth').write_text(
        'import site; ' + startup + '\n', encoding='ascii')
    return python, child_site


@pytest.fixture
def dependency_venv():
    """Create a private venv without installing the suite's dependencies again."""
    return _dependency_venv


HOME_VARIABLES = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")


def _os_home() -> Path:
    """The home this user's other processes see: USERPROFILE as logon set it on
    Windows, the password database's entry on POSIX, which is what Path.home()
    reads there once HOME is gone."""
    if os.name == "nt":
        return Path.home()
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.fixture
def without_home_variables(monkeypatch) -> Path:
    """An environment holding none of the variables Path.home() reads, the way a
    client that builds a server's environment from an allowlist, a service or a
    scheduled task starts crapkit. Returns the home this user's other processes
    see, which is where crapkit's caches and locks must still land."""
    expected = _os_home()
    for name in HOME_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return expected
