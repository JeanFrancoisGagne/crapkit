"""`crapkit mutate`'s command layer, run in-process with the suite runs replaced.

test_mutate_results.py spawns crapkit and git for every verdict, so the mutation
killer suite (which leaves out tests that spawn a process) never reaches
cli/analyses.py:cmd_mutate. These tests call crapkit.cli.main in this process
on a plain directory (no git: `--files` needs none) and replace
crapkit.mutate_pool.run_mutants with a runner that returns fixed verdicts, so
every expected value below follows from the fixture and these rules:

- README.md:809 and docs/agent-json.md:1177: `--files` targets whole files; a
  file outside the scored corpus (a test file) grows no mutants and is listed
  under `outside_corpus`; `--json` prints {mutants, killed, survived,
  survivors [{path, line, op, original, mutated}], outside_corpus}, and a
  survivor is a mutant its suite run did not kill.
- crapkit/cli/_shared.py `_stand` (ADR 0002): without `--repo` a relative
  `--files` path is read from where the user stands, below the root the walk
  found.
- docs/configuration.md:80 and :321: mutate refuses to run without
  mutation_command, and a file over `[exclude] max_file_bytes` is outside the
  corpus; README.md "Exit codes": a refused config exits 3.
- ADR 0002 again: with `--repo` a relative `--files` path is read against that
  root, wherever the user stands.
- mutate_pool.reporter: one progress line per finished mutant, on stderr,
  numbered against the run's mutant count.

src/a.py's one comparison `a < b` grows two mutants, `<=` then `>=` (the
operator table, README.md:809); the fake runner kills the first and lets the
second survive.
"""
from __future__ import annotations

import json

import pytest

from crapkit.cli import main
import crapkit.mutate_pool

CONFIG = ('[crapkit]\ntarget = 6\nmutation_command = "python -c pass"\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
          '[exclude]\nmax_file_bytes = 200\n')
SOURCE = "def f(a, b):\n    return a < b\n"
BIG = "def g(a, b):\n    return a > b\n" + "# padding past max_file_bytes\n" * 10


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "src" / "big.py").write_text(BIG, encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def runs(monkeypatch):
    """The fake runner: kills the first mutant, lets the second survive, and
    reports each through the progress callback cmd_mutate hands it."""
    seen = []

    def run_mutants(root, cfg, mutants, report):
        seen.append((root, [(m.path, m.line, m.op) for m in mutants]))
        verdicts = [index == 0 for index in range(len(mutants))]
        for index, (mutant, killed) in enumerate(zip(mutants, verdicts)):
            report(index, mutant, killed)
        return verdicts

    monkeypatch.setattr(crapkit.mutate_pool, "run_mutants", run_mutants)
    return seen


def _main(capsys, *argv: str) -> tuple[int, str, str]:
    try:
        code = main(list(argv))
    except SystemExit as stop:
        code = stop.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_run_from_below_the_root_lists_the_survivor_and_the_test_file(tree, runs, monkeypatch, capsys):
    monkeypatch.chdir(tree / "src")
    code, out, err = _main(capsys, "mutate", "--files", "a.py", "big.py", "../tests/test_a.py", "--json")
    assert code == 0, err
    assert json.loads(out) == {
        "schema": 1, "mutants": 2, "killed": 1, "survived": 1,
        "survivors": [{"path": "src/a.py", "line": 2, "op": "< -> >=",
                       "original": "    return a < b", "mutated": "    return a >= b"}],
        "outside_corpus": ["src/big.py", "tests/test_a.py"]}
    assert runs == [(tree.resolve(), [("src/a.py", 2, "< -> <="), ("src/a.py", 2, "< -> >=")])]


def test_progress_goes_to_stderr_numbered_against_the_mutant_count(tree, runs, monkeypatch, capsys):
    monkeypatch.chdir(tree)
    code, out, err = _main(capsys, "mutate", "--files", "src/a.py", "--json")
    assert code == 0, err
    progress = [line.split()[:2] for line in err.splitlines() if line.strip().startswith("mutant ")]
    assert progress == [["mutant", "1/2"], ["mutant", "2/2"]]
    assert json.loads(out)["survived"] == 1


def test_the_text_form_names_the_survivor(tree, runs, capsys):
    code, out, err = _main(capsys, "mutate", "--repo", str(tree), "--files", "src/a.py")
    assert code == 0, err
    assert "1/2 killed" in out
    assert "src/a.py:2" in out and "return a >= b" in out
    assert "return a <= b" not in out


def test_mutate_without_mutation_command_refuses_before_any_run(tree, runs, capsys):
    (tree / "crapkit.toml").write_text(CONFIG.replace('mutation_command = "python -c pass"\n', ""),
                                       encoding="utf-8")
    code, out, err = _main(capsys, "mutate", "--repo", str(tree), "--files", "src/a.py")
    assert code == 3
    refusal = "crapkit: mutate needs [crapkit] mutation_command — the suite run once per mutant"
    assert refusal in err.splitlines()
    assert runs == []


def test_with_repo_named_a_relative_file_is_read_against_that_root(tree, runs, monkeypatch, capsys):
    monkeypatch.chdir(tree / "src")
    code, out, err = _main(capsys, "mutate", "--repo", str(tree), "--files", "src/a.py", "--json")
    assert code == 0, err
    assert json.loads(out)["mutants"] == 2
    assert runs[0][1] == [("src/a.py", 2, "< -> <="), ("src/a.py", 2, "< -> >=")]
