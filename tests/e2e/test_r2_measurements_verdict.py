"""A gate override leaves independent diff refusals untrusted."""
import json
from pathlib import Path

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8")


def prepare(repo: Path):
    (repo / "src").mkdir()
    (repo / ".crapkit").mkdir()
    (repo / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (repo / "alert.py").write_text("pass\n", encoding="utf-8")
    import sys
    alert = f'"{sys.executable}" alert.py'
    (repo / "crapkit.toml").write_text('''[crapkit]
target=6
diff_uncovered_max=0
alert_command=''' + json.dumps(alert) + '''
[[scope]]
name="src"
paths=["src"]
languages=["python"]
[[lane]]
name="py"
command="echo unused"
artifact=".crapkit/cov.json"
parser="coveragepy"
scopes=["src"]
''', encoding="utf-8")
    (repo / "src/app.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    git_init_repo(repo)
    git_commit_all(repo, "baseline")


def artifact(repo, lines, branches, path="src/app.py"):
    missing = list(range(1, lines + 1))
    summary = dict(num_branches=branches, covered_branches=0, num_statements=lines, covered_lines=0)
    region = dict(start_line=1, executed_lines=[], missing_lines=missing, summary=summary)
    report = dict(meta=dict(branch_coverage=True), files={path: dict(
        functions={"f": region}, executed_lines=[], missing_lines=missing)})
    (repo / ".crapkit/cov.json").write_text(json.dumps(report), encoding="utf-8")


def test_diff_refusal_survives_override_and_later_baseline_selection(tmp_path):
    prepare(tmp_path)
    artifact(tmp_path, 2, 0)
    base = run_cli(tmp_path, "coverage", "--reuse-artifacts", "--json")
    assert base.returncode == 0, base.stderr
    baseline_id = json.loads(base.stdout)["run_id"]
    body = "def f(x):\n" + "".join(f"    if x == {n}:\n        return {n}\n" for n in range(6)) + "    return -1\n"
    (tmp_path / "src/app.py").write_text(body, encoding="utf-8")
    artifact(tmp_path, 14, 12)
    control = run_cli(tmp_path, "verify", "--reuse-artifacts", "--json")
    assert control.returncode == 6, control.stderr
    overridden = run_cli(tmp_path, "verify", "--reuse-artifacts", "--override", "accepted gate debt", "--json")
    result = json.loads(overridden.stdout)
    assert overridden.returncode == 9, overridden.stderr
    assert result["ok"] is False
    assert len(result["overridden"]) == 1 and result["gate_violations"] == []
    assert result["diff_uncovered_count"] == 13
    assert result["dirty_findings"] == 13 and result["committed_findings"] == 0
    refused_id = result["run_id"]
    marks = (tmp_path / "crapkit-ratchet.tsv").read_bytes()
    following = run_cli(tmp_path, "verify", "--reuse-artifacts", "--baseline", str(refused_id), "--json")
    assert following.returncode == 1, following.stderr
    assert "cannot serve as a baseline" in following.stderr
    ordinary = run_cli(tmp_path, "verify", "--reuse-artifacts", "--json")
    assert ordinary.returncode == 9, ordinary.stderr
    assert json.loads(ordinary.stdout)["baseline_run"] == baseline_id
    assert (tmp_path / "crapkit-ratchet.tsv").read_bytes() == marks
