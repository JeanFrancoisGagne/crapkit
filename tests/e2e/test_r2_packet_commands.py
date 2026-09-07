"""Packet commands preserve file identity when a real shell executes them."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


def cli(root, *arguments):
    result = subprocess.run([sys.executable, '-m', 'crapkit', arguments[0], '--repo', str(root),
                             *arguments[1:]],
                            cwd=root, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def git(root, *arguments):
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    *arguments], cwd=root, check=True, capture_output=True)


def project(root, path, monkeypatch, *, with_files=True):
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('CRAPKIT_R2_LITERAL', 'expanded')
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
    source = root / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('def f(x):\n    return x\n', encoding='utf-8')
    (root / 'capture.py').write_text(
        'import json, os, sys\nfrom pathlib import Path\n'
        'Path("captured.json").write_text(json.dumps({"args":sys.argv[1:], '
        '"cwd":str(Path.cwd()), "env":os.environ["CRAPKIT_R2_LITERAL"]}), encoding="utf-8")\n',
        encoding='utf-8')
    suffix = '{files}' if with_files else 'fixed'
    template = f'"{sys.executable}" capture.py {suffix}'
    (root / 'crapkit.toml').write_text(
        '[crapkit]\ntarget=6\n[crapkit.scoped_tests]\n'
        f'src={json.dumps(template)}\n'
        '[[scope]]\nname="src"\npaths=["."]\nlanguages=["python"]\ncoverage_optional=true\n',
        encoding='utf-8')
    (root / '.gitignore').write_text('.crapkit/\ncaptured.json\n', encoding='utf-8')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'packet fixture')
    cli(root, 'coverage', '--json')
    return cli(root, 'brief', '--json', '--', path, 'f')['commands']


def execute(root, command, shell):
    if shell == 'powershell':
        encoded = base64.b64encode((command + '; exit $LASTEXITCODE').encode('utf-16le')).decode('ascii')
        args = ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
    elif shell == 'cmd-delayed':
        args = 'cmd /D /V:ON /S /C "' + command + '"'
    else:
        args = command
    return subprocess.run(args, shell=shell == 'default', cwd=root,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


SHELLS = ['default', 'cmd-delayed', 'powershell'] if os.name == 'nt' else ['default']
PATHS = {'percent': 'src/%CRAPKIT_R2_LITERAL%.py', 'bang': 'src/!CRAPKIT_R2_LITERAL!.py',
         'operators': 'src/a & b^c.py', 'apostrophe': "src/a'b.py",
         'powershell-expansion': 'src/$env`CRAPKIT_R2_LITERAL.py',
         'unicode-separator': 'src/a\u2028b.py', 'leading-hyphen': '-option.py'}
if os.name != 'nt':
    PATHS.update({'backslash': 'src/a\\b.py', 'newline': 'src/a\nb.py',
                  'tab': 'src/a\tb.py', 'substitution': 'src/$(echo unwanted).py'})


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('path', list(PATHS.values()), ids=list(PATHS))
def test_printed_commands_select_the_same_literal_file_as_the_packet(tmp_path, monkeypatch, path, shell):
    commands = project(tmp_path, path, monkeypatch)
    result = execute(tmp_path, commands['scoped_tests'], shell)
    assert result.returncode == 0, result.stdout + result.stderr
    captured = json.loads((tmp_path / 'captured.json').read_text(encoding='utf-8'))
    assert captured == {'args': [path], 'cwd': str(tmp_path), 'env': 'expanded'}
    source = 'def f(x):\n' + ''.join(f'    if x == {i}: return {i}\n' for i in range(6)) + '    return -1\n'
    (tmp_path / path).write_text(source, encoding='utf-8')

    gate = execute(tmp_path, commands['gate'], shell)

    assert gate.returncode == 6, gate.stdout + gate.stderr


def test_a_template_without_files_still_runs_its_configured_arguments(tmp_path, monkeypatch):
    commands = project(tmp_path, 'src/plain.py', monkeypatch, with_files=False)
    result = execute(tmp_path, commands['scoped_tests'], 'default')
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'captured.json').read_text())['args'] == ['fixed']


@pytest.mark.parametrize('shell', SHELLS)
def test_the_printed_command_keeps_the_cli_failure_status(tmp_path, monkeypatch, shell):
    commands = project(tmp_path, 'src/%CRAPKIT_R2_LITERAL%.py', monkeypatch)
    (tmp_path / 'capture.py').write_text('raise SystemExit(23)\n', encoding='utf-8')

    result = execute(tmp_path, commands['scoped_tests'], shell)

    assert result.returncode == 1, result.stdout + result.stderr
    assert 'runner exit 23' in result.stderr


@pytest.mark.parametrize('shell', SHELLS)
def test_gate_command_preserves_the_whole_filename(tmp_path, monkeypatch, shell):
    path = 'src/a & b^c.py'
    commands = project(tmp_path, path, monkeypatch)
    source = 'def f(x):\n' + ''.join(f'    if x == {i}: return {i}\n' for i in range(6)) + '    return -1\n'
    (tmp_path / path).write_text(source, encoding='utf-8')

    result = execute(tmp_path, commands['gate'], shell)

    assert result.returncode == 6, result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows encoded command fallback')
@pytest.mark.parametrize('shell', SHELLS)
def test_a_missing_console_script_cannot_report_success(tmp_path, monkeypatch, shell):
    commands = project(tmp_path, 'src/%CRAPKIT_R2_LITERAL%.py', monkeypatch)
    system = Path(os.environ['SystemRoot']) / 'System32'
    monkeypatch.setenv('PATH', str(system) + os.pathsep + str(system / 'WindowsPowerShell' / 'v1.0'))

    result = execute(tmp_path, commands['scoped_tests'], shell)

    assert result.returncode != 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows native application lookup')
def test_multiple_console_installations_keep_the_first_path_match(tmp_path, monkeypatch):
    commands = project(tmp_path, 'src/%CRAPKIT_R2_LITERAL%.py', monkeypatch)
    alternate = tmp_path / '.alternate-bin'
    alternate.mkdir()
    shutil.copyfile(shutil.which('crapkit'), alternate / 'crapkit.exe')
    monkeypatch.setenv('PATH', os.environ['PATH'] + os.pathsep + str(alternate))

    result = execute(tmp_path, commands['scoped_tests'], 'default')

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / 'captured.json').read_text())['args'] == ['src/%CRAPKIT_R2_LITERAL%.py']
