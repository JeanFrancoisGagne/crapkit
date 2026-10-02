"""tools/accuracy/regenerate.py and tools/docs/generate.py write what their own tree's crapkit gives.

Both scripts imported whatever crapkit the python had installed: in a linked
worktree, the main checkout's code or a release wheel. `regenerate.py goldens`
then rewrote the goldens from another tree's measurements, and generate.py
wrote crapkit.schema.json from another tree's config contract. Change control's
own run of regenerate.py already put src/ first (_tests_env); a hand run did
not. Both scripts now put their tree's src/ first, and regenerate.py also hands
src/ and tests/ to the commands it starts, as change control does.
"""
import os
from pathlib import Path
import shutil
import sys

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
WHOSE = "getattr(crapkit, 'WHOSE', crapkit.__file__)"
GOLDEN_RUNS = f"""import subprocess, sys


def all_goldens(scratch):
    import crapkit
    child = subprocess.run([sys.executable, "-P", "-c", "import crapkit; print({WHOSE})"],
                           capture_output=True, text=True)
    print("in process:", {WHOSE})
    print("child:", child.stdout.strip() or child.stderr.strip())
    raise SystemExit(0)
"""
STUBS = {
    "src/crapkit/__init__.py": "WHOSE = 'this tree'\n",
    "tests/accuracy/__init__.py": "",
    "tests/accuracy/kit/__init__.py": "",
    "tests/accuracy/kit/corpus_run.py": "",
    "tests/accuracy/kit/goldens.py": "",
    "tests/accuracy/kit/repos.py": "EPOCH = 0\n",
    "tests/accuracy/corpus_goldens/__init__.py": "",
    "tests/accuracy/corpus_goldens/golden_runs.py": GOLDEN_RUNS,
}


def _run(argv: list[str], cwd: Path, env: dict):
    return hang_guard.run(argv, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace")


def test_regenerate_goldens_measures_with_its_own_tree_s_crapkit(tmp_path):
    """The real script in a tree whose kit is a stand-in: the goldens step
    reports which crapkit it imported, and which one a command it starts
    imports, then stops before any measuring."""
    tree = tmp_path / "tree"
    for name, text in STUBS.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text, encoding="utf-8")
    (tree / "tools/accuracy").mkdir(parents=True)
    shutil.copy2(ROOT / "tools/accuracy/regenerate.py", tree / "tools/accuracy/regenerate.py")

    done = _run([sys.executable, str(tree / "tools/accuracy/regenerate.py"), "goldens"],
                tmp_path, dict(os.environ))

    said = done.stdout + done.stderr
    assert done.returncode == 0, said
    assert "in process: this tree" in said
    assert "child: this tree" in said


def test_the_docs_generator_writes_its_own_tree_s_schema(tmp_path):
    """A crapkit ahead of the tree on the import path, standing in for an
    installed one whose config contract differs, no longer moves
    crapkit.schema.json."""
    installed = tmp_path / "installed"
    (installed / "crapkit").mkdir(parents=True)
    (installed / "crapkit/__init__.py").write_text("", encoding="utf-8")
    (installed / "crapkit/config_contract.py").write_text(
        "def schema():\n    return {'from': 'the installed crapkit'}\n", encoding="utf-8")

    done = _run([sys.executable, str(ROOT / "tools/docs/generate.py"), "--check"], ROOT,
                {**os.environ, "PYTHONPATH": str(installed)})

    said = done.stdout + done.stderr
    assert done.returncode in (0, 1) and "Traceback" not in said, said
    assert "crapkit.schema.json" not in done.stdout, said
