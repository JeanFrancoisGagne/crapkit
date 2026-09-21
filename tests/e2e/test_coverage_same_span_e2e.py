"""A same-span coverage artifact must not turn an uncalled function green,
and must not end the run either."""
import json
import sys

from conftest import git_commit_all, git_init_repo, run_cli


SIMPLE = "function live() { return 1; } function dead() { return 2; }\n"
BRANCHY = ("function live(a, b, c, d) { if (a) { return 1; } if (b) { return 2; } if (c) { return 3; } "
           "if (d) { return 4; } return 5; } function dead() { return 2; }\n")


def same_span_repo(tmp_path, source: str) -> None:
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.ts").write_text(source, encoding="utf-8")
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


def test_coverage_scores_same_span_functions_uncovered_and_names_the_span(tmp_path):
    same_span_repo(tmp_path, SIMPLE)
    result = run_cli(tmp_path, "coverage", "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "1 source line span(s) hold more than one function" in result.stderr
    assert "separate lines" in result.stderr
    run = json.loads(result.stdout)
    assert (run["functions"], run["untested"], run["measured"]) == (2, 2, 0)


def test_the_worklist_ranks_a_shared_line_function_with_advice_it_can_follow(tmp_path):
    same_span_repo(tmp_path, BRANCHY)
    assert run_cli(tmp_path, "coverage").returncode == 0

    (item,) = json.loads(run_cli(tmp_path, "worklist", "--json").stdout)["active"]

    assert (item["handle"], item["ccn"], item["cov"], item["remedy"]) == (
        "live", 5, 0.0, "split-lines")
