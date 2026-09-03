"""A leading `**/` in an exclude glob matches zero or more directories.

Under 0.4.x `**/dist/**` was fed to fnmatch as written, so it needed a
directory in front of `dist` and a repo-root dist/ stayed in the corpus; init
papered over it by writing every glob twice, once with the prefix and once
without, and nothing at all spelled `generated`. One glob now reaches the root
and every nested copy, generated trees leave by default, and init writes the
list one glob per line.
"""
import subprocess
from pathlib import Path

from conftest import run_cli

_MOD = "def g(x):\n    return x or 0\n"


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, body in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit_all(repo, "init")
    return repo


def _generated_repo(tmp_path: Path) -> Path:
    """A python repo with a generated client at the root, another nested under a
    scope, a root dist/, and a pytest marker so init writes a live lane."""
    return _repo(tmp_path, {
        "pylib/mod.py": _MOD,
        "api/main.py": _MOD,
        "api/generated/client.py": _MOD,
        "generated/client.py": _MOD,
        "dist/bundle.py": _MOD,
        "pyproject.toml": '[project]\nname = "gen"\n',
    })


def _exclude_block(config: str) -> str:
    return config.split("[exclude]", 1)[1].split("\n[", 1)[0]


def test_init_never_scopes_a_generated_tree(tmp_path: Path):
    repo = _generated_repo(tmp_path)

    res = run_cli(repo, "init")

    assert res.returncode == 0, res.stderr
    config = (repo / "crapkit.toml").read_text(encoding="utf-8")
    assert 'name = "generated"' not in config, config
    assert 'name = "dist"' not in config, config
    assert "2 scope(s): api, pylib" in res.stdout, res.stdout


def test_init_writes_each_default_glob_once_on_its_own_line(tmp_path: Path):
    """405 characters on one line, every pattern twice, was the config a reader
    had to edit. One glob per line, each written once, under a comment that says
    why the prefix alone is enough."""
    repo = _generated_repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0

    block = _exclude_block((repo / "crapkit.toml").read_text(encoding="utf-8"))

    globs = [ln.strip().rstrip(",").strip('"') for ln in block.splitlines()
             if ln.strip().startswith('"')]
    assert globs, block
    assert len(globs) == len(set(globs)), "a glob written twice"
    assert all(g.startswith("**/") for g in globs), globs
    assert "**/generated/**" in globs and "**/__generated__/**" in globs
    assert "**/*.generated.*" in globs
    assert "**/" in block.split("globs")[0], "the comment above the list explains the prefix"


def test_doctor_agrees_a_root_generated_tree_is_out_of_the_corpus(tmp_path: Path):
    repo = _generated_repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0

    res = run_cli(repo, "doctor", "--show-files")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "no scope path" not in res.stdout, res.stdout
    assert "generated/client.py" not in res.stdout, res.stdout
    assert "dist/bundle.py" not in res.stdout, res.stdout
    assert "api/main.py" in res.stdout


def test_a_hand_written_root_form_keeps_matching(tmp_path: Path):
    """A committed config carrying `dist/**` beside `**/dist/**` loses nothing:
    the root form still reads the way fnmatch read it."""
    repo = _repo(tmp_path, {"pylib/mod.py": _MOD, "dist/bundle.py": _MOD})
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "pylib"\npaths = ["pylib"]\n'
        'languages = ["python"]\n\n[exclude]\nglobs = ["dist/**"]\n', encoding="utf-8")
    _commit_all(repo, "config")

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "dist/bundle.py" not in res.stdout


def test_a_committed_config_with_only_the_nested_form_now_excludes_the_root_copy(tmp_path: Path):
    """The corpus move the CHANGELOG names: `**/dist/**` alone used to leave a
    repo-root dist/ in the corpus, where doctor reported it unclaimed."""
    repo = _repo(tmp_path, {"pylib/mod.py": _MOD, "dist/bundle.py": _MOD})
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "pylib"\npaths = ["pylib"]\n'
        'languages = ["python"]\n\n[exclude]\nglobs = ["**/dist/**"]\n', encoding="utf-8")
    _commit_all(repo, "config")

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "dist/bundle.py" not in res.stdout, res.stdout
