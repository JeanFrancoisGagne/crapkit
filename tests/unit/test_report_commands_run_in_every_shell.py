"""A report's explain command runs as printed wherever the page is read.

report.html is made on one machine and read on another: a Linux CI job
publishes it, and a Windows developer pastes a row's command into cmd.exe.
The command used to be quoted for the OS that wrote it, so the Linux page
printed `crapkit explain 'pkg/cé.py' accent`, and cmd.exe, which does not treat
`'` as a quote, handed crapkit `'pkg/cé.py'` with its quotes: `no function
matching 'accent'`. Every argument here reads literally inside double quotes in
sh, bash, PowerShell and cmd.exe, so the page now prints that one form on every
OS, and each case pastes it into every shell this machine has.

An argument one of those shells rewrites inside double quotes (`$`, `%`, `!`,
a backtick, a backslash, a quote of its own, a line break) has no single
spelling all four read, and keeps its writing OS's form:
tests/unit/test_report_console_commands.py pastes those on the OS that made
them.
"""
from html import unescape
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crapkit import packet
from crapkit.report import render_report
from hang_guard import HANG_SECONDS

FIXTURE = Path(__file__).parents[1] / 'fixtures/recorded/report_payload.json'
GIT_BASH = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git' / 'bin' / 'bash.exe'
STUB = ('import json, sys\n'
        'def main():\n'
        '    print(json.dumps(sys.argv[1:]))\n'
        '    return 0\n')

CASES = {
    'accent': ('pkg/cé.py', 'accent'),
    'anonymous-handle': ('src/a b.js', '(anonymous)#2'),
    'shell-operators': ('src/a & b^c.py', 'g( x < y )#2'),
    'single-quote': ("src/it's.py", "f( x = 'a' )"),
    'hyphen-path': ('-a.js', '(anonymous)#2'),
}


def _shells() -> list[str]:
    """Every shell on this machine a reader could paste into. Git Bash is named
    by its path: on Windows a bare `bash` can reach WSL's instead."""
    if os.name == 'nt':
        found = ['cmd', 'cmd-delayed', 'powershell']
        return found + (['git-bash'] if GIT_BASH.is_file() else [])
    return ['sh', 'bash'] + (['pwsh'] if shutil.which('pwsh') else [])


def _command(path: str, handle: str) -> str:
    payload = json.loads(FIXTURE.read_text(encoding='utf-8'))
    payload['worklist']['active'][0].update(path=path, handle=handle, occurrence=2)
    page = unescape(render_report(payload))
    return re.search(r'<td class="cmd"><code>(.*?)</code>', page, re.DOTALL).group(1)


def _posix_launcher(bin_dir: Path, monkeypatch) -> None:
    """A console script the way pip writes one, first on PATH."""
    bin_dir.mkdir()
    launcher = bin_dir / 'crapkit'
    launcher.write_text(f'#!{sys.executable}\nimport sys\nfrom crapkit.cli import main\n'
                        'sys.exit(main())\n', encoding='utf-8')
    launcher.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])


@pytest.fixture
def argv_crapkit(tmp_path, monkeypatch):
    """A `crapkit` launcher importing a stub whose main prints its argv. On
    Windows it is the installed console script, since cmd.exe and PowerShell
    each hand an .exe its command line their own way."""
    if os.name != 'nt':
        _posix_launcher(tmp_path / 'bin', monkeypatch)
    elif shutil.which('crapkit') is None:
        pytest.skip('no crapkit console script on PATH to launch the stub')
    package = tmp_path / 'stub' / 'crapkit' / 'cli'
    package.mkdir(parents=True)
    (package.parent / '__init__.py').write_text('', encoding='utf-8')
    (package / '__init__.py').write_text(STUB, encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path / 'stub'))
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
    return tmp_path


def _encoded(command: str) -> str:
    return base64.b64encode((command + '; exit $LASTEXITCODE').encode('utf-16le')).decode('ascii')


def _git_bash(command: str, cwd: Path) -> list[str]:
    script = cwd / 'pasted.sh'
    script.write_bytes(command.encode('utf-8') + b'\n')
    return [str(GIT_BASH), '--noprofile', '--norc', script.as_posix()]


def _argv(command: str, shell: str, cwd: Path):
    """How a reader in `shell` runs the pasted line."""
    if shell in ('powershell', 'pwsh'):
        return [shell, '-NoProfile', '-NonInteractive', '-EncodedCommand', _encoded(command)]
    if shell == 'cmd-delayed':
        return 'cmd /D /V:ON /S /C "' + command + '"'
    if shell == 'git-bash':
        return _git_bash(command, cwd)
    if shell in ('sh', 'bash'):
        return [shell, '-c', command]
    return command  # cmd, through subprocess's own `cmd /c`


def _pasted(command: str, shell: str, cwd: Path) -> list[str]:
    result = subprocess.run(_argv(command, shell, cwd), shell=shell == 'cmd', cwd=cwd,
                            capture_output=True, timeout=HANG_SECONDS)
    out, err = (stream.decode('utf-8', 'replace') for stream in (result.stdout, result.stderr))
    assert result.returncode == 0, f'{shell}: {command}\n{out}{err}'
    return json.loads(out)


@pytest.mark.parametrize('shell', _shells())
@pytest.mark.parametrize('made_on', ['posix', 'nt'])
@pytest.mark.parametrize('path, handle', list(CASES.values()), ids=list(CASES))
def test_a_report_made_on_either_os_runs_in_every_shell(path, handle, made_on, shell,
                                                         argv_crapkit, monkeypatch):
    monkeypatch.setattr(packet, 'os', SimpleNamespace(name=made_on))
    command = _command(path, handle)

    argv = _pasted(command, shell, argv_crapkit)

    marker = ['--'] if path.startswith('-') else []
    assert argv == ['explain', *marker, path, handle], command


@pytest.mark.parametrize('path, handle', list(CASES.values()), ids=list(CASES))
def test_both_oses_print_the_same_line(path, handle, monkeypatch):
    printed = {}
    for made_on in ('posix', 'nt'):
        monkeypatch.setattr(packet, 'os', SimpleNamespace(name=made_on))
        printed[made_on] = _command(path, handle)

    assert printed['posix'] == printed['nt'], printed
