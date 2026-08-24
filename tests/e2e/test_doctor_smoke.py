"""Doctor's lane smoke: config rot (renamed script, vanished runner) surfaces at
doctor time, not 40 minutes into a coverage run. No lane command is executed —
the check resolves the executable and the repo-relative files the command names."""
import os
import subprocess
import sys
from pathlib import Path


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=120, env=dict(os.environ))


def _repo(tmp_path: Path, lane_command: str) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function f() { return 1; }\n", encoding="utf-8")
    (tmp_path / "make_cov.py").write_text("print('stub')\n", encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
        f'[[lane]]\nname = "unit"\ncommand = "{lane_command}"\n'
        'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
                   cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_doctor_passes_when_the_lane_command_resolves(tmp_path: Path):
    repo = _repo(tmp_path, "python make_cov.py")
    res = run_cli(repo, "doctor")
    assert res.returncode == 0, res.stdout + res.stderr


def test_doctor_flags_an_executable_that_does_not_exist(tmp_path: Path):
    repo = _repo(tmp_path, "no-such-runner-anywhere --coverage")
    res = run_cli(repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "no-such-runner-anywhere" in res.stdout


def test_doctor_flags_a_named_script_that_left_the_repo(tmp_path: Path):
    repo = _repo(tmp_path, "python scripts/run_gone.py")
    res = run_cli(repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "scripts/run_gone.py" in res.stdout
