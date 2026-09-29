"""Private test packages with access to the current runtime's dependencies."""
import os
from pathlib import Path
import site
import subprocess
import sys
import venv

import pytest

import git_env

# Run from a hook or `git bisect run`, the suite inherits the variables that
# point git at the repo running it; each test's git works in its own repo.
for _name in git_env.repo_env_names():
    os.environ.pop(_name, None)


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


@pytest.fixture
def on_a_host(monkeypatch):
    """crapkit refuses a coveragepy lane inside a container unless the lane sets
    container_ok (docs/configuration.md#lane). A test about what such a lane does
    on a host stubs the container check, so it passes in the accuracy image and
    under CRAPKIT_INSIDE_CONTAINER=1 too."""
    from crapkit import lanes
    monkeypatch.setattr(lanes, "_in_container", lambda: False)
