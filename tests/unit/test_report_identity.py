"""Report commands retain whole-file identity after ranking filters and caps."""
from html import unescape
from contextlib import closing
import json
from pathlib import Path
import os
import re
import shlex
import subprocess
import sys

import pytest

from cli_inproc_repo import commit_all, git
from crapkit.cli import main
from crapkit.cli._shared import _load_repo_config, _open_store
from crapkit.cli.reports import _report_payload
from crapkit.report import render_report
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


def _repo(root):
    git(root, 'init', '-q', '-b', 'main')
    (root / 'src').mkdir()
    body = ' '.join(f'if (x > {n}) x++;' for n in range(7))
    source = (f'values.map((x) => x).filter((x) => {{ {body} return x; }});\n'
              'values.map((x) => { if (x) x++; if (x) x++; if (x) x++; '
              'if (x) x++; if (x) x++; if (x) x++; return x; });\n')
    (root / 'src/app.ts').write_text(source, encoding='utf-8')
    (root / '.gitignore').write_text('.crapkit/\n', encoding='utf-8')
    (root / 'crapkit.toml').write_text(
        '[crapkit]\ntarget=6\nworklist_floor=6\nworklist_top=1\n'
        '[[scope]]\nname="web"\npaths=["src"]\nlanguages=["typescript"]\n'
        'coverage_optional=true\n', encoding='utf-8')
    commit_all(root, 'same-line callbacks with different complexity')


@pytest.mark.parametrize('command', ['coverage', 'inventory'])
def test_report_handle_names_the_second_callback_after_floor_and_cap(tmp_path, capsys, command):
    _repo(tmp_path)
    assert main([command, '--repo', str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(['worklist', '--json', '--batches', '1', '--repo', str(tmp_path)]) == 0
    worklist = json.loads(capsys.readouterr().out)
    assert len(worklist['active']) == 1
    assert worklist['active_total'] == 2
    entry = worklist['active'][0]
    assert (entry['occurrence'], entry['handle']) == (2, '(anonymous)#2')
    assert worklist['batches'][0]['entries'][0]['handle'] == '(anonymous)#2'
    store = _open_store(tmp_path)
    with closing(store._conn):
        payload = _report_payload(tmp_path, _load_repo_config(tmp_path), store)
    assert payload['worklist']['active'][0]['handle'] == entry['handle']
    page = render_report(payload)
    commands = re.findall(r'<code>(crapkit explain.*?)</code>', unescape(page))
    assert len(commands) == 1
    argv = shlex.split(commands[0])
    assert argv == ['crapkit', 'explain', 'src/app.ts', '(anonymous)#2']
    assert main([*argv[1:], '--json', '--repo', str(tmp_path)]) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained['functions'][0]['history'][0]['ccn'] == 8


def test_rendered_handle_is_escaped_and_keeps_spaces_in_one_argument():
    fixture = Path(__file__).parents[1] / 'fixtures/recorded/report_payload.json'
    payload = json.loads(fixture.read_text(encoding='utf-8'))
    entry = payload['worklist']['active'][0]
    entry.update(handle='route( x < y )#2', occurrence=2)
    page = render_report(payload)
    assert 'route( x &lt; y )#2' in page
    command = re.findall(r'<code>(crapkit explain.*?)</code>', unescape(page))[0]
    assert shlex.split(command)[-1] == entry['handle']


@pytest.mark.skipif(os.name != 'nt', reason='Windows report commands target PowerShell')
def test_windows_report_arguments_reach_a_real_child_verbatim(tmp_path):
    fixture = Path(__file__).parents[1] / 'fixtures/recorded/report_payload.json'
    payload = json.loads(fixture.read_text(encoding='utf-8'))
    entry = payload['worklist']['active'][0]
    entry.update(path="src/a '$VALUE %VALUE% !VALUE!.ts", handle="f( x = 'a' )#2")
    page = render_report(payload)
    command = re.findall(r'<code>(crapkit explain.*?)</code>', unescape(page))[0]
    script = tmp_path / 'arguments.py'
    script.write_text('import json,sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
    child = f"& '{sys.executable}' '{script}'" + command.removeprefix('crapkit')
    result = subprocess.run(['powershell', '-NoProfile', '-Command', child],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['explain', entry['path'], entry['handle']]


def test_position_read_keeps_inventory_scope_copies_without_other_paths_or_metrics(tmp_path):
    from crapkit.cli.queue import _Handles

    first = InventoryRow('web', 'app.ts', '(anonymous)', 1, 1, 1, 1, 1, 1, 0, 0, 0, 1)
    second = first._replace(ccn=8, occurrence=2)
    rows = [first, first._replace(scope='copy'), second, first._replace(path='other.ts')]
    store = SnapshotStore(tmp_path / 'state.sqlite')
    with closing(store._conn):
        run = store.write_run(commit='fixture', tool_versions={}, rows=rows)
        queries = []
        store._conn.set_trace_callback(queries.append)
        handles = _Handles(store, run)
        assert handles.of(second) == '(anonymous)#2'
        assert handles.of(first) == '(anonymous)#1'
        store._conn.set_trace_callback(None)
    selects = [q for q in queries if q.startswith('SELECT')]
    assert len(selects) == 1
    assert selects[0].split(' FROM ')[0] == 'SELECT i.scope, i.path, i.long_name, f.start, f.occurrence'
