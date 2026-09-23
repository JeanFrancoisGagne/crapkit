"""A report row's explain command reaches crapkit intact from every shell it is pasted into.

The page quoted a selector for PowerShell only. cmd.exe hands single quotes on to
the program, so `'(anonymous)#2'` arrived with its quotes and matched no function.
Each case pastes the printed line into a real shell, unchanged, and a stand-in
`crapkit` reports the argv it received.
"""
from html import unescape
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crapkit import packet
from crapkit.cli.parser import build_parser
from crapkit.report import render_report

FIXTURE = Path(__file__).parents[1] / 'fixtures/recorded/report_payload.json'
SHELLS = ['cmd', 'cmd-delayed', 'powershell'] if os.name == 'nt' else ['sh']
STUB = ('import json, sys\n'
        'def main():\n'
        '    print(json.dumps(sys.argv[1:]))\n'
        '    return 0\n')


def _cell(path: str, handle: str) -> re.Match:
    """The drill-down cell of a one-row page: its command, then anything after it."""
    payload = json.loads(FIXTURE.read_text(encoding='utf-8'))
    payload['worklist']['active'][0].update(path=path, handle=handle, occurrence=2)
    page = unescape(render_report(payload))
    return re.search(r'<td class="cmd"><code>(.*?)</code>(.*?)</td>', page, re.DOTALL)


def _command(path: str, handle: str) -> str:
    return _cell(path, handle).group(1)


@pytest.fixture
def argv_crapkit(tmp_path, monkeypatch):
    """The installed `crapkit` launcher, importing a stub whose main prints its argv."""
    package = tmp_path / 'stub' / 'crapkit' / 'cli'
    package.mkdir(parents=True)
    (package.parent / '__init__.py').write_text('', encoding='utf-8')
    (package / '__init__.py').write_text(STUB, encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path / 'stub'))
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('CRAPKIT_REPORT_LITERAL', 'expanded')
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
    return tmp_path


def _pasted(command: str, shell: str, cwd: Path) -> list[str]:
    if shell == 'powershell':
        script = (command + '; exit $LASTEXITCODE').encode('utf-16le')
        args = ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                base64.b64encode(script).decode('ascii')]
    elif shell == 'cmd-delayed':
        args = 'cmd /D /V:ON /S /C "' + command + '"'
    else:
        args = command
    result = subprocess.run(args, shell=shell in ('cmd', 'sh'), cwd=cwd, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


CASES = {
    'anonymous-handle': ('src/a b.js', '(anonymous)#2'),
    'expansion-text': ("src/a '$CRAPKIT_REPORT_LITERAL %CRAPKIT_REPORT_LITERAL% "
                       "!CRAPKIT_REPORT_LITERAL!.ts", "f( x = 'a' )#2"),
    'shell-operators': ('src/a & b^c.py', 'g( x < y )#2'),
    'double-quote': ('src/cls.py', 'run( self , mode = "fast" )'),
    'backslash-quote': ('src/cls.py', 'g( s = "a\\\\" )'),
}


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('path, handle', list(CASES.values()), ids=list(CASES))
def test_the_pasted_explain_command_hands_crapkit_the_path_and_the_handle(
        path, handle, shell, argv_crapkit):
    command = _command(path, handle)

    assert _pasted(command, shell, argv_crapkit) == ['explain', path, handle]


@pytest.mark.parametrize('shell', SHELLS)
def test_a_path_that_starts_with_a_hyphen_is_read_as_the_path(shell, argv_crapkit):
    command = _command('-a.js', '(anonymous)#2')

    argv = _pasted(command, shell, argv_crapkit)

    parsed = build_parser().parse_args(argv)
    assert (parsed.path, parsed.name) == ('-a.js', '(anonymous)#2')


def test_a_plain_path_and_line_selector_print_bare():
    assert _command('src/app.py', '12') == 'crapkit explain src/app.py 12'


def test_a_plain_command_fills_its_cell_alone():
    cell = _cell('src/app.py', '12')

    assert (cell.group(1), cell.group(2)) == ('crapkit explain src/app.py 12', '')


def test_an_encoded_command_wraps_in_its_cell_and_says_what_it_opens(monkeypatch):
    """A Remix route such as users.$id.tsx takes the encoded form on Windows: one
    unbroken base64 run that no reader can match to a function."""
    monkeypatch.setattr(packet, 'os', SimpleNamespace(name='nt'))

    cell = _cell('app/routes/users.$id.tsx', 'loader( )')

    assert cell.group(1).startswith('powershell -NoProfile -NonInteractive -EncodedCommand ')
    assert cell.group(2) == '<div class="loc">encoded form of: explain app/routes/users.$id.tsx loader( )</div>'


def test_the_command_cell_wraps_instead_of_widening_the_table():
    page = render_report(json.loads(FIXTURE.read_text(encoding='utf-8')))

    assert 'td.cmd code { white-space: normal; overflow-wrap: anywhere; }' in page
