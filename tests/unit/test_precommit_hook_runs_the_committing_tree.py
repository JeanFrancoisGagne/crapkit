"""git-hooks/pre-commit judges a commit with the committing tree's own crapkit.

The hook used to run `python -m crapkit hook-precommit` with whatever crapkit the
python on PATH had installed: a release wheel, or an editable install of another
checkout. A commit in any linked worktree was then judged by another tree's code,
and a change to the gate never gated its own commit. The hook now puts the
committing tree's src/ first on PYTHONPATH.

Each tree here holds a stand-in crapkit whose gate prints which tree it belongs
to and decides the commit: the main checkout's accepts, the linked worktree's
refuses. The installed crapkit would judge the one staged line and accept both.
git runs the real hook file through core.hooksPath, the way CONTRIBUTING sets it.
"""
import os
from pathlib import Path
import subprocess
import sys
import venv

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ("-c", "user.name=Hook Test", "-c", "user.email=hook@example.test")
STAND_IN = """import sys
print({tree!r}, *sys.argv[1:])
sys.exit({code})
"""


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def _stand_in(tree: Path, name: str, code: int) -> None:
    package = tree / "src/crapkit"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(STAND_IN.format(tree=name, code=code), encoding="utf-8")


def _env(python: str = sys.executable) -> dict:
    """This interpreter first on PATH, so the hook's bare `python` is the one
    whose crapkit the suite imports."""
    return {**os.environ, "PATH": os.pathsep.join((str(Path(python).parent), os.environ["PATH"]))}


def _hooked_commit(tree: Path, env: dict) -> subprocess.CompletedProcess:
    staged = tree / "a.py"
    staged.write_text(staged.read_text(encoding="utf-8") + "\n\ndef more(b):\n    return b\n",
                      encoding="utf-8")
    _git(tree, "add", "a.py")
    return hang_guard.run(["git", *IDENTITY, "commit", "-q", "-m", "hooked"], cwd=tree, env=env,
                          text=True, encoding="utf-8", errors="replace")


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout whose crapkit accepts, and a linked worktree whose crapkit refuses."""
    main, linked = tmp_path / "main", tmp_path / "linked"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _stand_in(main, "the main checkout's gate", 0)
    (main / "a.py").write_text("def fine(a):\n    return a\n", encoding="utf-8")
    _git(main, "add", ".")
    _git(main, "commit", "-q", "-m", "base")
    _git(main, "worktree", "add", "-q", "--detach", str(linked))
    _stand_in(linked, "the linked worktree's gate", 6)
    _git(main, "config", "core.hooksPath", (ROOT / "git-hooks").as_posix())
    return main, linked


def test_each_tree_s_commit_is_judged_by_that_tree_s_own_crapkit(tmp_path):
    main, linked = _repo(tmp_path)

    accepted = _hooked_commit(main, _env())
    refused = _hooked_commit(linked, _env())

    said = accepted.stdout + accepted.stderr
    assert accepted.returncode == 0, said
    assert "the main checkout's gate hook-precommit" in said
    said = refused.stdout + refused.stderr
    assert refused.returncode != 0, said
    assert "the linked worktree's gate hook-precommit" in said
    assert "crapkit: commit blocked by the complexity gate." in said
    assert "main checkout" not in said


def test_a_python_without_crapkit_s_dependencies_is_named_not_a_traceback(tmp_path):
    """The tree supplies crapkit itself, so the probe also asks for lizard: a
    python without it gets the install line, not an ImportError from the gate."""
    main, _ = _repo(tmp_path)
    bare = tmp_path / "bare"
    venv.EnvBuilder(with_pip=False).create(bare)
    python = bare / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {name: value for name, value in _env(str(python)).items() if name != "PYTHONPATH"}

    refused = _hooked_commit(main, env)

    said = refused.stdout + refused.stderr
    assert refused.returncode != 0, said
    assert "crapkit: NOT INSTALLED - the complexity gate cannot run" in said
    assert "the main checkout's gate" not in said and "Traceback" not in said
