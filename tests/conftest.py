"""Private test packages with access to the current runtime's dependencies."""
import os
from pathlib import Path
import subprocess
import sysconfig
import venv

import pytest


def _dependency_venv(root: Path) -> tuple[Path, Path]:
    venv.EnvBuilder(with_pip=False).create(root)
    python = root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    result = subprocess.run([str(python), '-I', '-X', 'utf8', '-c',
                             "import sysconfig; print(sysconfig.get_path('purelib'))"],
                            capture_output=True, text=True, encoding='utf-8', check=True)
    child_site = Path(result.stdout.strip())
    dependencies = sorted({sysconfig.get_path(kind) for kind in ('purelib', 'platlib')})
    # The child site stays first. Process dependency hooks for subprocess coverage.
    startup = '; '.join('site.addsitedir(' + ascii(path) + ')' for path in dependencies)
    # ASCII escapes also support Python versions that read .pth in the locale encoding.
    (child_site / 'test-dependencies.pth').write_text(
        'import site; ' + startup + '\n', encoding='ascii')
    return python, child_site


@pytest.fixture
def dependency_venv():
    """Create a private venv without installing the suite's dependencies again."""
    return _dependency_venv
