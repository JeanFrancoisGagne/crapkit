"""The last publicly-reachable coverage gaps: the watch loop as a real process,
the digest alert path both ways, and verify's regression/override print lines."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOML = (
    '[crapkit]\ntarget = 6\nalert_command = "python sink.py"\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
    'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n'
)

MAKE_COV = (
    "import json, os, sys\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "hit = int(open('hits.txt').read()) if os.path.exists('hits.txt') else 1\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'branchy', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 6}}}},\n"
    "             'f': {'0': 1},\n"
    "             'branchMap': {'0': {'loc': {'start': {'line': 2}},\n"
    "                                 'locations': [{'start': {'line': 2}}, {'start': {'line': 3}}]},\n"
    "                           '1': {'loc': {'start': {'line': 4}},\n"
    "                                 'locations': [{'start': {'line': 4}}, {'start': {'line': 5}}]}},\n"
    "             'b': {'0': [1, hit], '1': [hit, 0]}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n"
)

APP = ("export function branchy(a: number, b: number): number {\n"
       "  if (a > 0) { b += 1; }\n"
       "  else { b -= 1; }\n"
       "  if (b > 0) { a += 1; }\n"
       "  else { a -= 1; }\n"
       "  return a + b;\n}\n")

SINK = "import sys, pathlib\npathlib.Path('alerts.txt').write_text(sys.stdin.read(), encoding='utf-8')\n"


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=180, env=dict(os.environ))


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / "sink.py").write_text(SINK, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\nhits.txt\nalerts.txt\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_watch_rescores_a_changed_file_and_stops_cleanly(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0  # rescore overlays a SCORED run
    log = repo / "watch.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([sys.executable, "-m", "crapkit", "watch", "--interval", "0.3"],
                                cwd=repo, stdout=fh, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 20
        while time.time() < deadline and "watching" not in log.read_text(encoding="utf-8"):
            time.sleep(0.2)
        app = repo / "src" / "app.ts"
        app.write_text(app.read_text(encoding="utf-8") + "// touched\n", encoding="utf-8")
        # the rescore runs as a child process; wait for its table, not just the banner
        while time.time() < deadline and "branchy" not in log.read_text(encoding="utf-8"):
            time.sleep(0.2)
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    text = log.read_text(encoding="utf-8")
    assert "watching" in text
    assert "changed: src/app.ts" in text, text
    assert "branchy" in text, "the rescore table for the touched file reaches the log"


def test_digest_before_any_run_names_the_command_that_fixes_it(repo: Path):
    """digest reads the snapshot store straight, so it is the first command a
    fresh clone can hit with nothing recorded. That has to read as an
    instruction, not as a traceback or as an empty week."""
    res = run_cli(repo, "digest")

    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "crapkit coverage" in res.stderr
    assert "Traceback" not in res.stderr


def test_digest_alert_delivers_on_change_and_fails_loudly(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    (repo / "hits.txt").write_text("0", encoding="utf-8")  # coverage drops -> a digest
    assert run_cli(repo, "coverage", "--json").returncode == 0

    res = run_cli(repo, "digest", "--alert")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "branchy" in (repo / "alerts.txt").read_text(encoding="utf-8")

    (repo / "sink.py").write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    (repo / "hits.txt").write_text("1", encoding="utf-8")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    broken = run_cli(repo, "digest", "--alert")
    assert broken.returncode == 5, "a dead alert channel is a tool error, never silence"


def test_verify_prints_ratchet_regression_and_override_lines(repo: Path):
    assert run_cli(repo, "coverage", "--export", "scored.tsv", "--json").returncode == 0
    # a hand-tightened mark below the real score reads as a regression; the key
    # must be lizard's exact long_name, so take it from the export
    row = next(r for r in (repo / "scored.tsv").read_text(encoding="utf-8").splitlines()[1:]
               if "branchy" in r)
    long_name = row.split("\t")[2]
    (repo / "crapkit-ratchet.tsv").write_text(
        f"path\tlong_name\tcrap\nsrc/app.ts\t{long_name}\t1.0000\n", encoding="utf-8")
    res = run_cli(repo, "verify", "--reuse-artifacts")
    assert res.returncode == 7, res.stdout + res.stderr
    assert "RATCHET" in res.stdout and "branchy" in res.stdout

    (repo / "crapkit-ratchet.tsv").unlink()
    app = repo / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8").replace(
        "return a + b;",
        "if (a > b && b > 1 && a > 2 && b > 3 && a > 4 && b > 5) { return 0; }\n  return a + b;"),
        encoding="utf-8")
    blocked = run_cli(repo, "verify", "--reuse-artifacts")
    assert blocked.returncode == 6
    granted = run_cli(repo, "verify", "--reuse-artifacts", "--override", "test: exercising the print path")
    assert granted.returncode == 0, granted.stdout + granted.stderr
    assert "OVERRIDDEN" in granted.stdout


MAKE_COV_DEAD = MAKE_COV.replace(
    "json.dump(cov, open('cov.json', 'w'))",
    "cov[app]['statementMap'] = {'0': {'start': {'line': 2}, 'end': {'line': 2}},\n"
    "                            '1': {'start': {'line': 5}, 'end': {'line': 5}}}\n"
    "cov[app]['s'] = {'0': 1, '1': 0}\n"
    "json.dump(cov, open('cov.json', 'w'))\n")


def test_diff_uncovered_ceiling_fails_verify_with_exit_9(repo: Path):
    (repo / "make_cov.py").write_text(MAKE_COV_DEAD, encoding="utf-8")  # line 5 never runs
    assert run_cli(repo, "coverage", "--json").returncode == 0
    app = repo / "src" / "app.ts"
    lines = app.read_text(encoding="utf-8").splitlines()
    lines[4] = lines[4] + " // touched the dead line"
    app.write_text("\n".join(lines) + "\n", encoding="utf-8")

    warned = run_cli(repo, "verify", "--reuse-artifacts")
    assert warned.returncode == 0, "without the knob a dead changed line only warns"
    assert "uncovered src/app.ts:5" in warned.stderr

    toml = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        toml.replace("target = 6", "target = 6\ndiff_uncovered_max = 0"), encoding="utf-8")
    hard = run_cli(repo, "verify", "--reuse-artifacts")
    assert hard.returncode == 9, hard.stdout + hard.stderr
    assert "ceiling" in hard.stderr


def test_empty_reasons_counts_no_lane_rows(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "orphan").mkdir()
    (tmp_path / "orphan" / "lonely.py").write_text(
        "def lonely(a, b):\n"
        "    if a and b:\n        return 1\n"
        "    if a or b:\n        return 2\n"
        "    if a > b:\n        return 3\n"
        "    return 0\n", encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(
        TOML + '\n[[scope]]\nname = "orphan"\npaths = ["orphan"]\nlanguages = ["python"]\n',
        encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / "sink.py").write_text(SINK, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True)
    assert run_cli(tmp_path, "coverage", "--json").returncode == 0
    res = run_cli(tmp_path, "next-item", "--exclude", "branchy", "--exclude", "lonely")
    out = json.loads(res.stdout)
    assert out["empty"] is True
    assert out["reasons"]["no_lane"] >= 1, "the orphan scope's row must be counted as a tooling gap"


def test_coupling_tolerates_a_fileless_commit():
    from crapkit.coupling import change_coupling
    log = "\x01alice\x021000\n\n\x01bob\x022000\nsrc/a.ts\nsrc/b.ts\n"
    assert change_coupling(log, min_support=1, min_confidence=0.5) != []
