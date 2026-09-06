"""Configured shell commands receive selected paths as literal arguments."""
import json
import sys

import pytest

from conftest import git_commit_all, git_init_repo, run_cli


@pytest.mark.parametrize('placeholder', ['{files}', '"{files}"'])
def test_scoped_file_arguments_do_not_expand_environment_variables(tmp_path, placeholder):
    git_init_repo(tmp_path)
    command = f'"{sys.executable}" record.py {placeholder}'
    (tmp_path / 'crapkit.toml').write_text(
        '[crapkit.scoped_tests]\nsrc = ' + json.dumps(command) + '\n'
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n',
        encoding='utf-8')
    (tmp_path / 'src').mkdir()
    path = 'src/%CRAPKIT_LITERAL_TOKEN%!literal!.py'
    (tmp_path / path).write_text('def work():\n    return 1\n', encoding='utf-8')
    (tmp_path / 'record.py').write_text(
        'import json, sys\nfrom pathlib import Path\n'
        'Path("arguments.json").write_text(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
    git_commit_all(tmp_path, 'fixture')

    result = run_cli(tmp_path, 'test-scoped', path,
                     env_extra={'CRAPKIT_LITERAL_TOKEN': 'expanded', 'literal': 'expanded'})

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'arguments.json').read_text()) == [path]
