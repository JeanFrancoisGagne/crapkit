"""R144: verify counted a test that failed and then passed its flake retry as a
forgiven failure, so the OK line said an unchanged failure was forgiven for a
test the baseline never failed.

    <retro venv python> R144.py WORKTREE

verdict_model's check pins a later rule (a baseline verify's own retried pass is
left out of the baseline's failures), which the fix commit does not hold yet,
and it reads retried_passes, which the commit before the fix does not write.
This probe asks this bug's question through forgiven_failures, which both
commits write. It builds a committed repo with one JavaScript function and a
lane whose JUnit reports tests.test_a::flaky failed when tests/state.txt says
so, and whose retest_command reports it passed. `crapkit coverage` measures a
clean baseline (flaky passes); then tests/state.txt makes flaky fail and
`crapkit verify --json` retries it: flaky is not a failure the baseline had,
so it must not be among the forgiven failures.
"""
# source: docs/agent-json.md, verify's table: `forgiven_failures` is the "array of test ids the fresh run and the baseline both failed", and the baseline run below failed no test; docs/lanes.md, the flake retry: "A test that passed its rerun is stored under the lane's `retried_passes`"
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

JUNIT = ("from pathlib import Path\nimport sys\n"
         "fails = Path('tests/state.txt').read_text().strip() == 'flaky' and sys.argv[1:] != ['retest']\n"
         "case = '<testcase classname=\"tests.test_a\" name=\"{name}\">{body}</testcase>'\n"
         "body = '<failure message=\"flaky\"/>' if fails else ''\n"
         "cases = case.format(name='steady', body='') + case.format(name='flaky', body=body)\n"
         "Path('.crapkit').mkdir(exist_ok=True)\n"
         "Path('.crapkit/junit.xml').write_text('<testsuites><testsuite tests=\"2\">' + cases"
         " + '</testsuite></testsuites>', encoding='utf-8')\n")
COVERAGE = ("import json\nfrom pathlib import Path\n"
            "loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}\n"
            "data = {'src/app.js': {'path': 'src/app.js', 'fnMap': {'0': {'name': 'f', 'decl': loc,"
            " 'loc': loc}}, 'f': {'0': 1}, 'statementMap': {'0': loc}, 's': {'0': 1},"
            " 'branchMap': {}, 'b': {}}}\n"
            "Path('.crapkit/cov.json').write_text(json.dumps(data), encoding='utf-8')\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _config() -> str:
    run = json.dumps(f'"{sys.executable}" junit.py && "{sys.executable}" cov.py')
    retest = json.dumps(f'"{sys.executable}" junit.py retest')
    return ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n\n'
            f'[[lane]]\nname = "js"\nparser = "istanbul"\nscopes = ["src"]\ncommand = {run}\n'
            'artifact = ".crapkit/cov.json"\nresults_artifact = ".crapkit/junit.xml"\n'
            f'retest_command = {retest}\n')


def _repo(base: Path) -> Path:
    repo = base / "repo"
    files = {"src/app.js": "function f() {\n  return 1;\n}\n", "tests/state.txt": "pass\n",
             "junit.py": JUNIT, "cov.py": COVERAGE, ".gitignore": ".crapkit/\n__pycache__/\n",
             "crapkit.toml": _config()}
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _crapkit(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=env,
                          capture_output=True)


def _told(done: subprocess.CompletedProcess) -> str:
    return (done.stdout + done.stderr).decode(errors="replace")


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r144-") as scratch:
        repo = _repo(Path(scratch))
        baseline = _crapkit(repo, "coverage")
        if baseline.returncode != 0:
            raise RuntimeError(f"crapkit coverage exited {baseline.returncode}: {_told(baseline)[-400:]}")
        (repo / "tests" / "state.txt").write_bytes(b"flaky\n")
        verified = _crapkit(repo, "verify", "--json")
    if verified.returncode != 0:
        raise RuntimeError(f"verify exited {verified.returncode}, so the retry did not pass: "
                           f"{_told(verified)[-400:]}")
    forgiven = json.loads(verified.stdout)["forgiven_failures"]
    assert "tests.test_a::flaky" not in forgiven, f"verify forgave {forgiven}; the baseline failed no test"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
