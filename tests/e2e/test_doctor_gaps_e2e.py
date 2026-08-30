"""Two gaps that used to commit with zero gating: tracked source no scope path
claims, and a scope no lane's scopes list covers. Doctor fails on both. The byte
ceiling and coverage_optional scopes are the two legitimate ways out."""
import json
import subprocess
from pathlib import Path

from conftest import run_cli

_APP_TS = "export function f(a: number) { return a ? 1 : 2; }\n"

_HEAD = ('[crapkit]\ntarget = 6\n\n'
         '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n')

_SHIMS = '[[scope]]\nname = "shims"\npaths = ["shims"]\nlanguages = ["python"]\n'

_LANE = ('[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
         'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')

_MAKE_COV = '''import json, os

app = os.path.join(os.getcwd(), "src", "app.ts")
artifact = {app: {
    "path": app,
    "fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1}},
                    "loc": {"start": {"line": 1}, "end": {"line": 1}}}},
    "f": {"0": 3},
    "branchMap": {"0": {"loc": {"start": {"line": 1}},
                        "locations": [{"start": {"line": 1}}, {"start": {"line": 1}}]}},
    "b": {"0": [1, 1]},
}}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump(artifact, fh)
'''


def _config(scopes: str = "", globs: tuple[str, ...] = (), max_bytes: str = "") -> str:
    """The lane script sits at the repo root, so every config here excludes it by
    name: a python scope would otherwise claim it as source nobody scoped."""
    listed = ", ".join(json.dumps(g) for g in ("make_cov.py", *globs))
    return f'{_HEAD}{scopes}\n[exclude]\nglobs = [{listed}]\n{max_bytes}\n{_LANE}'


def _repo(tmp_path: Path, config: str, extra: dict | None = None) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(_APP_TS, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text("print('stub')\n", encoding="utf-8")
    for rel, body in (extra or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(config, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
                   cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_doctor_fails_on_tracked_source_no_scope_path_claims(tmp_path: Path):
    repo = _repo(tmp_path, _config(), {"tools/helper.ts": _APP_TS})
    res = run_cli(repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "tools/helper.ts" in res.stdout
    assert "1 tracked file" in res.stdout


def test_an_excluded_unclaimed_file_is_not_a_problem(tmp_path: Path):
    repo = _repo(tmp_path, _config(globs=("tools/**",)), {"tools/helper.ts": _APP_TS})
    res = run_cli(repo, "doctor")
    assert res.returncode == 0, res.stdout + res.stderr


def test_doctor_fails_on_a_scope_no_lane_covers(tmp_path: Path):
    repo = _repo(tmp_path, _config(scopes=_SHIMS), {"shims/mod.py": "def g(x):\n    return x or 0\n"})
    res = run_cli(repo, "doctor")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "shims" in res.stdout and "no lane" in res.stdout


def test_a_coverage_optional_scope_needs_no_lane(tmp_path: Path):
    repo = _repo(tmp_path, _config(scopes=_SHIMS + "coverage_optional = true\n"),
                 {"shims/mod.py": "def g(x):\n    return x or 0\n"})
    res = run_cli(repo, "doctor")
    assert res.returncode == 0, res.stdout + res.stderr


def test_doctor_reports_files_over_max_file_bytes_without_failing(tmp_path: Path):
    repo = _repo(tmp_path, _config(max_bytes="max_file_bytes = 500\n"),
                 {"src/blob.ts": "// " + "x" * 900 + "\n"})
    res = run_cli(repo, "doctor")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "src/blob.ts" in res.stdout and "max_file_bytes" in res.stdout


def test_inventory_json_counts_the_files_the_byte_ceiling_cut(tmp_path: Path):
    repo = _repo(tmp_path, _config(max_bytes="max_file_bytes = 500\n"),
                 {"src/blob.ts": "// " + "x" * 900 + "\n"})
    res = run_cli(repo, "inventory", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    summary = json.loads(res.stdout)
    assert summary["skipped_max_bytes"] == 1
    assert summary["files"] == 1, "only src/app.ts survived the cut"


def test_coverage_json_counts_cc_only_rows_and_byte_skips(tmp_path: Path):
    repo = _repo(tmp_path,
                 _config(scopes=_SHIMS + "coverage_optional = true\n",
                         max_bytes="max_file_bytes = 500\n"),
                 {"shims/mod.py": "def g(x):\n    return x or 0\n",
                  "src/blob.ts": "// " + "x" * 900 + "\n",
                  "make_cov.py": _MAKE_COV})
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    summary = json.loads(res.stdout)
    assert summary["cc_only"] == 1, "the shim function scores cc-only, never no-lane"
    assert summary["no_lane"] == 0
    assert summary["skipped_max_bytes"] == 1


HOOK = "#!/bin/sh\nexec python -m crapkit hook-precommit\n"


def _hooked(tmp_path: Path, mode: str) -> Path:
    """A repo whose committed hook is armed through core.hooksPath, at `mode`."""
    repo = _repo(tmp_path, _config(globs=("githooks/**",)), {"githooks/pre-commit": HOOK})
    subprocess.run(["git", "update-index", f"--chmod={mode}", "githooks/pre-commit"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.hooksPath", "githooks"], cwd=repo,
                   check=True, capture_output=True)
    return repo


def test_doctor_warns_when_a_committed_hook_is_not_executable(tmp_path: Path):
    """Git skips a 100644 hook without a word, so `core.hooksPath` arms nothing
    on Linux and macOS. crapkit's own repo shipped exactly that."""
    res = run_cli(_hooked(tmp_path, "-x"), "doctor")

    assert res.returncode == 0, "a hook mode is a warning, not a config failure"
    assert "githooks/pre-commit" in res.stdout
    assert "git update-index --chmod=+x githooks/pre-commit" in res.stdout


def test_the_hook_warning_is_gone_once_the_bit_is_in_the_index(tmp_path: Path):
    res = run_cli(_hooked(tmp_path, "+x"), "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "update-index" not in res.stdout


def test_a_repo_with_no_hooks_path_is_never_asked_about_modes(tmp_path: Path):
    repo = _repo(tmp_path, _config(globs=("githooks/**",)), {"githooks/pre-commit": HOOK})

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "update-index" not in res.stdout


def test_the_hook_warning_rides_the_json_report(tmp_path: Path):
    res = run_cli(_hooked(tmp_path, "-x"), "doctor", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    report = json.loads(res.stdout)
    assert report["problems"] == []
    assert any("githooks/pre-commit" in w for w in report["warnings"])


def test_a_hooks_path_outside_the_repo_is_not_a_doctor_failure(tmp_path: Path):
    """`git ls-files` rejects a path outside the worktree with exit 128. An
    absolute `core.hooksPath` is a legitimate setup, not a crapkit error."""
    repo = _repo(tmp_path, _config())
    outside = tmp_path.parent / "hooks-elsewhere"
    subprocess.run(["git", "config", "core.hooksPath", str(outside)], cwd=repo,
                   check=True, capture_output=True)

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "update-index" not in res.stdout
