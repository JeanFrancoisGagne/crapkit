"""R97: run tests/e2e/test_init_doctor_e2e.py under pytest-cov at one commit and
require the CLI entry points it drives to be measured.

    <retro venv python> R97.py WORKTREE [--keep DIR LABEL]

tools/accuracy/retro.py builds the venv from the header below: the commit's
crapkit installed editable, so the e2e test's CLI children import the commit's
own code, and pytest-cov 7.1.0, the release that dropped its subprocess hook.
The floor rule is test_self_measure_floor's; this probe only produces the
report it reads. --keep saves the report and the commit's cli/admin.py, which
is how the two recordings under suite_strength/recorded/ were made.
"""
# requires: pytest==9.1.1 pytest-cov==7.1.0 coverage==7.16.1 pytest-xdist==3.8.0
# install: editable
# source: 8ec9449's commit message measured this test file under pytest-cov 7.1.0: admin.py 0/498 statements, cmd_init 0% without the patch key; 317/498 and cmd_init 100% with it
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve()
FLOOR = HERE.parents[2] / "test_self_measure_floor.py"
TEST_FILE = "tests/e2e/test_init_doctor_e2e.py"


def _floor():
    spec = importlib.util.spec_from_file_location("r97_floor", FLOOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(tree: Path, out: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    argv = [sys.executable, "-m", "pytest", TEST_FILE, "-q", "-p", "no:randomly",
            "-p", "no:cacheprovider", "--cov=crapkit", f"--cov-report=json:{out}"]
    subprocess.run(argv, cwd=tree, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out.is_file():
        raise RuntimeError(f"pytest-cov wrote no report for {TEST_FILE} at {tree}")


def _keep(keep: Path, tree: Path, report: Path, label: str) -> None:
    keep.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report, keep / f"r97-{label}-py.json")
    shutil.copyfile(tree / "src/crapkit/cli/admin.py", keep / "admin.py")


def main(argv: list[str]) -> int:
    tree = Path(argv[1]).resolve()
    floor = _floor()
    with tempfile.TemporaryDirectory(prefix="crapkit-r97-") as scratch:
        report = Path(scratch) / "py.json"
        _report(tree, report)
        if "--keep" in argv:
            _keep(Path(argv[argv.index("--keep") + 1]), tree, report, argv[-1])
        entries = floor.entry_points(tree / "src" / "crapkit" / "cli")
        wanted = {name: entries[name] for name in floor.INIT_DOCTOR}
        missing = floor.unmeasured(floor.load(report), wanted)
    assert missing == [], f"entry points with no executed line: {missing}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
