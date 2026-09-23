"""Private CLI callers and suite handshakes for mutation lifecycle tests."""
from contextlib import contextmanager
import os
import re
import shlex
from pathlib import Path
import signal
import subprocess
import sys

from hang_guard import CHILD_HOLD, HOLD_SECONDS, exited, wait_for


def holding_suite(root, events, phase='mutant'):
    """The suite holds its writer lock until the test releases it. The fixture's
    mutation deadline rises to the hold, so the product cannot end a hold the
    test is still counting on."""
    script = (root / 'suite.py').read_text(encoding='utf-8')
    script = script.replace('time.sleep(.1)',
        f'if phase == {phase!r}:\n'
        '    from crapkit.locks import exclusive_lock\n'
        '    with exclusive_lock(events / "writer.lock", label="suite writer"):\n'
        '        (events / "started").with_suffix(".part").write_text(str(Path.cwd()))\n'
        '        (events / "started").with_suffix(".part").replace(events / "started")\n'
        f'        deadline = time.monotonic() + {CHILD_HOLD}\n'
        '        while not (events / "release").exists() and time.monotonic() < deadline:\n'
        '            time.sleep(.02)\n'
        '        (events / "finished").touch()\n')
    (root / 'suite.py').write_text(script, encoding='utf-8')
    _raise_deadline_to_the_hold(root / 'crapkit.toml')


def _raise_deadline_to_the_hold(config):
    held, count = re.subn(r'mutation_timeout_seconds\s*=\s*\d+',
                          f'mutation_timeout_seconds={HOLD_SECONDS}',
                          config.read_text(encoding='utf-8'))
    assert count == 1, f'{config} spells no single mutation_timeout_seconds to raise to the hold'
    config.write_text(held, encoding='utf-8')


def holding_checkout_hook(root, events):
    hooks = events / 'hooks'
    hooks.mkdir()
    writer = events / 'checkout.py'
    writer.write_text('from pathlib import Path\nimport os, time\n'
                      'from crapkit.locks import exclusive_lock\n'
                      f'events = Path({str(events)!r})\n'
                      'with exclusive_lock(events / "writer.lock", label="Git hook"):\n'
                      '    (events / "started").with_suffix(".part").write_text(str(Path.cwd()))\n'
                      '    (events / "started").with_suffix(".part").replace(events / "started")\n'
                      '    deadline = time.monotonic() + ' + CHILD_HOLD + '\n'
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
    log = events / 'caller.log'
    with log.open('w') as stream:
        caller = subprocess.Popen([sys.executable, '-B', str(script)], stdout=stream, stderr=stream)
        try:
            wait_for(events / 'started', caller, log=log)
            yield caller
        finally:
            (events / 'release').touch()
            if caller.poll() is None:
                stop_caller(caller, events)
            exited(caller, log=log)
