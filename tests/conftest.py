"""Private test packages with access to the current runtime's dependencies."""
import os
from pathlib import Path
import site
import subprocess
import sys
import venv

import pytest


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
