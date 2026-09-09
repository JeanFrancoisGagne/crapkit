"""Private CLI callers and suite handshakes for mutation lifecycle tests."""
from contextlib import contextmanager
import os
import shlex
from pathlib import Path
import signal
import subprocess
import sys
import time


def wait_for(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f'caller did not reach {path}')
        time.sleep(.02)


def holding_suite(root, events, phase='mutant'):
    script = (root / 'suite.py').read_text()
    script = script.replace('time.sleep(.1)',
        f'if phase == {phase!r}:\n'
        '    from crapkit.locks import exclusive_lock\n'
        '    with exclusive_lock(events / "writer.lock", label="suite writer"):\n'
        '        (events / "started.part").write_text(str(Path.cwd()))\n'
        '        (events / "started.part").replace(events / "started")\n'
        '        deadline = time.monotonic() + 45\n'
        '        while not (events / "release").exists() and time.monotonic() < deadline:\n'
        '            time.sleep(.02)\n'
        '        (events / "finished").touch()\n')
    (root / 'suite.py').write_text(script, encoding='utf-8')


def holding_checkout_hook(root, events):
    hooks = events / 'hooks'
    hooks.mkdir()
    writer = events / 'checkout.py'
    writer.write_text('from pathlib import Path\nimport time\n'
                      'from crapkit.locks import exclusive_lock\n'
                      f'events = Path({str(events)!r})\n'
                      'with exclusive_lock(events / "writer.lock", label="Git hook"):\n'
                      '    (events / "started.part").write_text(str(Path.cwd()))\n'
                      '    (events / "started.part").replace(events / "started")\n'
                      '    deadline = time.monotonic() + 45\n'
                      '    while not (events / "release").exists() and time.monotonic() < deadline:\n'
                      '        time.sleep(.02)\n'
                      '    (events / "finished").touch()\n', encoding='utf-8')
    hook = hooks / 'post-checkout'
    command = ' '.join(shlex.quote(str(part).replace('\\', '/')) for part in [sys.executable, '-B', writer])
    hook.write_text('#!/bin/sh\nexec ' + command + '\n', encoding='utf-8', newline='\n')
    hook.chmod(0o755)
    subprocess.run(['git', 'config', 'core.hooksPath', str(hooks)], cwd=root, check=True)


def caller_script(root, events):
    path = events / 'caller.py'
    path.write_text('from pathlib import Path\nimport os\nfrom crapkit.cli import main\n'
                    f'events = Path({str(events)!r})\n'
                    '(events / "caller-pid").write_text(str(os.getpid()))\n'
                    'try:\n'
                    f'    code = main(["mutate", "--repo", {str(root)!r}, '
                    '"--files", "app.py", "--json"])\n'
                    'except KeyboardInterrupt:\n'
                    '    (events / "interrupted").touch()\n'
                    '    code = 130\n'
                    'raise SystemExit(code)\n', encoding='utf-8')
    return path


def stop_caller(caller, events, how=signal.SIGTERM):
    assert caller.poll() is None
    os.kill(int((events / 'caller-pid').read_text()), how)


@contextmanager
def running_mutation(root, events):
    script = caller_script(root, events)
    with (events / 'caller.log').open('w') as log:
        caller = subprocess.Popen([sys.executable, '-B', str(script)], stdout=log, stderr=log)
        try:
            wait_for(events / 'started')
            yield caller
        finally:
            (events / 'release').touch()
            if caller.poll() is None:
                stop_caller(caller, events)
            caller.wait(timeout=30)
