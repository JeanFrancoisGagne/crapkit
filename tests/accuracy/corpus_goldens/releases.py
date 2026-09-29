"""The last crapkit releases on PyPI, as unpacked wheels a test puts on PYTHONPATH.

tools/accuracy/wheel_diff.py lists the releases and fetches each wheel once
into the wheelhouse (CRAPKIT_ACCURACY_WHEELHOUSE in CI, a cache directory
otherwise), checked against the sha256 PyPI publishes. A fetch that fails is
an infra miss: it is noted in the run log, so run.py retries the check once
and exits 3, and then it is raised.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys

from accuracy.kit import runlog

TOOL = Path(__file__).resolve().parents[3] / "tools" / "accuracy" / "wheel_diff.py"
MODULE = "accuracy_wheel_diff"


def wheel_diff():
    """tools/accuracy/wheel_diff.py, loaded once by path. It is registered under the
    name its spec carries too: the mutation tools stage renames a mutated file's
    spec, and a dataclass finds its module in sys.modules by that name."""
    if MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(MODULE, TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = sys.modules[MODULE] = module
        spec.loader.exec_module(module)
    return sys.modules[MODULE]


@contextmanager
def _infra_on_fetch_failure():
    tool = wheel_diff()
    try:
        yield tool
    except tool.WheelDiffError as failed:
        runlog.note("infra", message=str(failed))
        raise


def last(count: int, at_most: str | None = None) -> list[str]:
    """The newest `count` releases, newest first, none above `at_most`."""
    with _infra_on_fetch_failure() as tool:
        return tool.releases(count, at_most)


def upload_date(version: str) -> str:
    with _infra_on_fetch_failure() as tool:
        return tool.upload_date(version)


def site(version: str, dest: Path) -> Path:
    """The release's wheel unpacked under dest, where `import crapkit` resolves."""
    with _infra_on_fetch_failure() as tool:
        wheel = tool.resolve(f"crapkit=={version}", tool.default_wheelhouse())
    return tool.unpack(wheel, dest)
