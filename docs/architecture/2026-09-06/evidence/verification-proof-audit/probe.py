"""Exercise report proof helpers on disposable Git/SQLite/artifact fixtures only."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import zlib
import crapkit

ROOT = Path(r'<repo>')
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'
print('VERIFIED_IMPORT', crapkit.__file__)

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'docs/architecture/2026-09-06' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

capture = load('audit_capture', 'capture-verification.py')
proof = load('audit_proof', 'verify-committed-inputs.py')
checks = []

def rejected(label, fn, needle):
    try:
        fn()
    except (RuntimeError, FileNotFoundError) as exc:
        assert needle in str(exc), (label, str(exc), needle)
        checks.append({'case': label, 'result': 'refused', 'reason': str(exc)})
    else:
        raise AssertionError(label + ' was accepted')

def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                    *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()

with tempfile.TemporaryDirectory(prefix='proof-fixture-', dir=Path(__file__).parent) as directory:
    root = Path(directory) / 'repo'
    root.mkdir()
    out = Path(directory) / 'capture'
    out.mkdir()
    git(root, 'init', '--quiet')
    (root / '.gitignore').write_text('.crapkit/\n', encoding='utf-8')
    (root / '.gitattributes').write_text('*.json text eol=lf\n', encoding='utf-8')
    (root / 'src').mkdir()
    (root / 'src/app.py').write_text('value = 1\n', encoding='utf-8')
    fixture_dir = root / 'tests/fixtures/recorded'
    fixture_dir.mkdir(parents=True)
    for name in ('raw_mod.json', 'raw_std.json'):
        (fixture_dir / name).write_bytes(b'{\r\n  "value": 1\r\n}\r\n')
    (root / 'crapkit.toml').write_text('[[lane]]\nname="py"\nartifact=".crapkit/cov/py.json"\n'
                                     'results_artifact=".crapkit/cov/junit.xml"\n', encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '--quiet', '-m', 'fixture')
    commit = git(root, 'rev-parse', 'HEAD')
    artifact_dir = root / '.crapkit/cov'
    artifact_dir.mkdir(parents=True)
    cov_path = artifact_dir / 'py.json'
    cov_bytes = b'{"files":{}}'
    cov_path.write_bytes(cov_bytes)
    junit = b'<testsuites><testsuite tests="2"><testcase name="a"/><testcase name="b"><skipped/></testcase></testsuite></testsuites>'
    junit_path = artifact_dir / 'junit.xml'
    junit_path.write_bytes(junit)
    lanes = {'py': {'artifact_sha256': capture.sha256(cov_bytes), 'exit_code': 0,
                    'failures': [], 'tests_total': 2, 'tests_skipped': 1}}
    with sqlite3.connect(root / '.crapkit/crap.sqlite') as db:
        db.execute('CREATE TABLE runs (id INTEGER PRIMARY KEY, commit_sha TEXT, kind TEXT, verdict_ok INTEGER, findings INTEGER, lanes BLOB)')
        db.execute('INSERT INTO runs VALUES (1,?,\'verify\',1,0,?)', (commit, zlib.compress(json.dumps(lanes).encode())))
    db.close()
    expected = capture.input_hashes(root)
    capture.write(out, 'inputs-before.json', expected)
    capture.write(out, 'inputs-after.json', expected)
    capture.write(out, 'stdout.json', {'ok': True, 'overridden': [], 'run_id': 1, 'commit': commit})
    result = {'returncode': 0, 'changed_inputs': [], 'finished_at': datetime.now(timezone.utc).isoformat()}
    capture.write(out, 'result.json', result)
    capture.write(out, 'command.json', {'argv': ['python', '-m', 'crapkit', 'verify']})
    capture.write(out, 'ledger-before.json', [])
    capture.write(out, 'ledger-after.json', [{'id': 1, 'commit_sha': commit, 'kind': 'verify', 'verdict_ok': 1, 'findings': 0}])
    binding = capture.finalize(out, root)
    assert binding['run_id'] == 1 and binding['mode'] == 'late-finalization'
    assert len(proof.captured_inputs(out, root)[1]) == 2
    normalized = proof.committed_inputs(expected, commit, root)
    assert {row['path'] for row in normalized} == proof.JSON_FIXTURES
    checks.append({'case': 'valid capture and exact two CRLF exceptions', 'result': 'accepted'})

    cov_path.write_bytes(b'{"different":true}')
    rejected('changed coverage', lambda: capture.finalize(out, root), 'coverage differs')
    cov_path.write_bytes(cov_bytes)

    junit_path.write_bytes(junit.replace(b'<testcase name="a"/>', b'<testcase name="a"><failure/></testcase>'))
    rejected('failing JUnit', lambda: capture.finalize(out, root), 'JUnit contains failures')
    junit_path.write_bytes(b'<testsuites><testsuite tests="1"><testcase name="a"/></testsuite></testsuites>')
    rejected('wrong JUnit counts', lambda: capture.finalize(out, root), 'JUnit counts differ')
    junit_path.write_bytes(junit.replace(b'tests="2"', b'tests="3"'))
    rejected('partial JUnit', lambda: capture.finalize(out, root), 'declared count differs')
    junit_path.unlink()
    rejected('missing required JUnit', lambda: capture.finalize(out, root), 'junit.xml')
    junit_path.write_bytes(junit)

    (root / 'src/new.py').write_text('untested = True\n', encoding='utf-8')
    rejected('added working input', lambda: capture.finalize(out, root), 'input paths or bytes differ')
    rejected('added input at post-commit check', lambda: proof.committed_inputs(expected, commit, root), 'working input path set')
    git(root, 'add', 'src/new.py')
    git(root, 'commit', '--quiet', '-m', 'extra committed input')
    extra_commit = git(root, 'rev-parse', 'HEAD')
    git(root, 'rm', '--quiet', 'src/new.py')
    rejected('extra committed file despite matching working set', lambda: proof.committed_inputs(expected, extra_commit, root), 'committed input path set')
    git(root, 'restore', '--staged', 'src/new.py')
    git(root, 'restore', 'src/new.py')
    git(root, 'rm', '--quiet', 'src/new.py')
    git(root, 'commit', '--quiet', '-m', 'remove extra')
    clean_commit = git(root, 'rev-parse', 'HEAD')
    assert proof.committed_inputs(expected, clean_commit, root) == normalized

    original_app = (root / 'src/app.py').read_bytes()
    (root / 'src/app.py').write_bytes(original_app.replace(b'\n', b'\r\n'))
    changed_expected = capture.input_hashes(root)
    rejected('undeclared CRLF source exception', lambda: proof.committed_inputs(changed_expected, clean_commit, root), 'committed bytes differ: src/app.py')
    (root / 'src/app.py').write_bytes(original_app)

    capture.write(out, 'artifacts-after.json', [])
    rejected('empty stale artifact manifest', lambda: proof.captured_inputs(out, root), 'capture binding differs: artifacts-after.json')
    stale_binding = dict(binding, artifacts_sha256=capture.sha256((out / 'artifacts-after.json').read_bytes()))
    capture.write(out, 'artifact-binding.json', stale_binding)
    rejected('empty manifest even with matching manifest hash', lambda: proof.captured_inputs(out, root), 'configured artifact paths or bytes differ')
    capture.finalize(out, root)

    capture.write(out, 'result.json', dict(result, returncode=6))
    rejected('failed capture', lambda: capture.finalize(out, root), 'did not finish cleanly')
    capture.write(out, 'result.json', dict(result, changed_inputs=['src/app.py']))
    rejected('changed capture inputs', lambda: capture.finalize(out, root), 'did not finish cleanly')
    capture.write(out, 'result.json', result)
    capture.write(out, 'ledger-before.json', [{'id': 1}])
    rejected('stale prior run receipt', lambda: capture.finalize(out, root), 'does not belong to this capture attempt')
    capture.write(out, 'ledger-before.json', [])
    capture.write(out, 'ledger-after.json', [])
    rejected('run absent from captured ledger', lambda: capture.finalize(out, root), 'does not belong to this capture attempt')
    capture.write(out, 'ledger-after.json', [{'id': 1, 'commit_sha': commit, 'kind': 'verify', 'verdict_ok': 1, 'findings': 0}])
    capture.finalize(out, root)
    assert len(proof.captured_inputs(out, root)[1]) == 2

output = {'checks': checks, 'passed': len(checks), 'root_tests_or_verification_run': False}
(Path(__file__).parent / 'probe-result.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
print(json.dumps(output, indent=2))
