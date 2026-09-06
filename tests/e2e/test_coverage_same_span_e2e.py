"""A same-span coverage artifact must not turn an uncalled function green."""
import json
import sys

from conftest import git_commit_all, git_init_repo, run_cli


def test_coverage_refuses_same_span_functions_with_actionable_message(tmp_path):
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.ts").write_text(
        "function live() { return 1; } function dead() { return 2; }\n", encoding="utf-8")
    artifact = {"src/app.ts": {"fnMap": {
        str(n): {"name": name, "decl": {"start": {"line": 1}},
                 "loc": {"start": {"line": 1}, "end": {"line": 1}}}
        for n, name in enumerate(("live", "dead"))}, "f": {"0": 1, "1": 0},
        "branchMap": {}, "b": {}, "statementMap": {}, "s": {}}}
    (tmp_path / "emit.py").write_text(
        "from pathlib import Path\n"
        "Path('.crapkit').mkdir(exist_ok=True)\n"
        f"Path('.crapkit/cov.json').write_text({json.dumps(artifact)!r}, encoding='utf-8')\n",
        encoding="utf-8")
    command = json.dumps('"' + sys.executable.replace("\\", "/") + '" emit.py')
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["typescript"]\n[[lane]]\nname="unit"\nscopes=["src"]\n'
        f'command={command}\nartifact=".crapkit/cov.json"\nparser="istanbul"\n', encoding="utf-8")
    git_commit_all(tmp_path, "same-span functions")
    result = run_cli(tmp_path, "coverage", "--json")
    assert result.returncode == 5, (result.stdout, result.stderr)
    assert "src/app.ts:1" in result.stderr
    assert "separate lines" in result.stderr
