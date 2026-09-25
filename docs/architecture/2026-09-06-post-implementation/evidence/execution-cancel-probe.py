"""Interrupt the existing bounded runner while a harmless child owns temporary files."""
import _thread
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import crapkit
from crapkit.procs import run_bounded

ROOT = Path(r'C:\Users\jfgag\crapkit')
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'
print('VERIFIED_IMPORT', crapkit.__file__, flush=True)
with tempfile.TemporaryDirectory(prefix='cancel-fixture-', dir=Path(__file__).parent) as directory:
    root = Path(directory)
    child = root / 'child.py'
    child.write_text("import os,time\nfrom pathlib import Path\nroot=Path(__file__).parent\n"
                     "(root/'pid.txt').write_text(str(os.getpid()))\n"
                     "time.sleep(2)\n(root/'survived.txt').write_text('child continued after cancellation')\n",
                     encoding='utf-8')
    timer = threading.Timer(0.1, _thread.interrupt_main)
    timer.start()
    started = time.monotonic()
    try:
        run_bounded(subprocess.list2cmdline([sys.executable, str(child)]), 0.5, cwd=root)
    except KeyboardInterrupt:
        interrupted = time.monotonic() - started
    else:
        raise AssertionError('interruption did not reach the bounded runner')
    finally:
        timer.join()
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and not (root/'survived.txt').exists():
        time.sleep(0.05)
    output = {'bounded_timeout_seconds': 0.5, 'interrupt_requested_after_seconds': 0.1,
              'keyboard_interrupt_returned_after_seconds': interrupted,
              'child_wrote_after_cancellation': (root/'survived.txt').exists(),
              'child_pid': int((root/'pid.txt').read_text())}
    assert output['child_wrote_after_cancellation'], output
    print(json.dumps(output, indent=2))
(Path(__file__).parent/'execution-cancel-result.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
