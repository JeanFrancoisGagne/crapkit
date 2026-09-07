"""Exercise foreign OS decisions beside the real native lifecycle regressions."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from threading import Event
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from crapkit import _process_owner as owner
from crapkit import _windows_job as windows
from crapkit import procs


@pytest.mark.parametrize(('group', 'state', 'expected'), [
    ('71', 'S', True), ('71', 'R', True), ('72', 'S', False),
    ('71', 'Z', False), ('71', 'X', False),
])
def test_proc_stat_uses_group_and_live_state(tmp_path, group, state, expected):
    record = tmp_path / 'stat'
    record.write_text(f'100 (a command (with) parentheses) {state} 1 {group} 4 5\n')
    assert owner._proc_group_member(record, '71') is expected


@pytest.mark.parametrize('text', [None, ''])
def test_a_disappearing_proc_record_does_not_keep_a_group_alive(tmp_path, text):
    record = tmp_path / 'stat'
    if text is not None:
        record.write_text(text)
    assert not owner._proc_group_member(record, '71')


@pytest.mark.parametrize(('records', 'expected'), [
    ([], False), (['100 (done) Z 1 71'], False),
    (['100 (peer) S 1 72', '101 (writer) R 1 71'], True),
])
def test_linux_group_scan_waits_only_for_active_members(tmp_path, monkeypatch, records, expected):
    files = []
    for index, record in enumerate(records):
        path = tmp_path / str(index)
        path.write_text(record)
        files.append(path)
    monkeypatch.setattr(owner, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(owner, 'Path', lambda root: SimpleNamespace(glob=lambda pattern: files))
    assert owner._group_active(71) is expected


@pytest.mark.parametrize(('listing', 'expected'), [
    ('', False), (' 71 Z+\n 72 S\n', False), (' 72 R\n 71 S+\n', True),
])
def test_other_posix_uses_group_ids_and_ps_states(monkeypatch, listing, expected):
    run = Mock(return_value=SimpleNamespace(stdout=listing))
    monkeypatch.setattr(owner, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(owner, 'subprocess', SimpleNamespace(run=run))
    assert owner._group_active(71) is expected
    run.assert_called_once_with(['ps', '-A', '-o', 'pgid=', '-o', 'stat='],
                                capture_output=True, text=True, check=True)


def test_ps_failure_does_not_confirm_group_cleanup(monkeypatch):
    error = subprocess.CalledProcessError(1, ['ps'])
    monkeypatch.setattr(owner, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(owner, 'subprocess', SimpleNamespace(run=Mock(side_effect=error)))
    with pytest.raises(subprocess.CalledProcessError):
        owner._group_active(71)


def test_process_group_signals_before_waiting_for_descriptor_closure(monkeypatch):
    events = Mock()
    events.active.side_effect = [True, False]
    monkeypatch.setattr(owner, '_kill_pid', events.kill)
    monkeypatch.setattr(owner, '_group_active', events.active)
    monkeypatch.setattr(owner, 'time', SimpleNamespace(sleep=events.sleep))
    owner._ProcessGroup(71).stop()
    assert events.mock_calls == [call.kill(71), call.active(71), call.sleep(.01), call.active(71)]


def posix_wait(monkeypatch, statuses, ticks=()):
    waitid = Mock(side_effect=statuses)
    clock = SimpleNamespace(monotonic=Mock(side_effect=ticks), sleep=Mock())
    monkeypatch.setattr(procs, 'os', SimpleNamespace(name='posix', waitid=waitid,
                        P_PID=1, WEXITED=4, WNOWAIT=8, WNOHANG=16, CLD_EXITED=1))
    monkeypatch.setattr(procs, 'time', clock)
    return waitid, clock, SimpleNamespace(pid=71, args=['command'])


@pytest.mark.parametrize(('code', 'status', 'expected'), [(1, 7, 7), (2, 9, -9)])
def test_untimed_posix_wait_preserves_exit_and_signal_status(monkeypatch, code, status, expected):
    waitid, clock, proc = posix_wait(monkeypatch, [SimpleNamespace(si_code=code, si_status=status)])
    assert procs._wait_command(proc, None) == expected
    waitid.assert_called_once_with(1, 71, 12)
    clock.sleep.assert_not_called()


def test_bounded_posix_wait_leaves_exited_leader_unreaped(monkeypatch):
    waitid, clock, proc = posix_wait(monkeypatch, [None, SimpleNamespace(si_code=1, si_status=0)],
                                   [10, 10.2, 10.2])
    assert procs._wait_command(proc, 1) == 0
    assert waitid.call_args_list == [call(1, 71, 28), call(1, 71, 28)]
    clock.sleep.assert_called_once_with(.01)


def test_posix_deadline_reports_the_original_command(monkeypatch):
    waitid, clock, proc = posix_wait(monkeypatch, [None], [10, 11])
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        procs._wait_command(proc, 1)
    assert caught.value.cmd == ['command']
    assert caught.value.timeout == 1
    clock.sleep.assert_not_called()


def test_posix_wait_error_cannot_become_success(monkeypatch):
    _, _, proc = posix_wait(monkeypatch, [ChildProcessError('no child')])
    with pytest.raises(ChildProcessError, match='no child'):
        procs._wait_command(proc, None)


def test_windows_wait_preserves_native_wait_result(monkeypatch):
    monkeypatch.setattr(procs, 'os', SimpleNamespace(name='nt'))
    proc = SimpleNamespace(wait=Mock(return_value=23))
    assert procs._wait_command(proc, 2) == 23
    proc.wait.assert_called_once_with(timeout=2)


def job_kernel(monkeypatch):
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 100
    kernel.OpenProcess.return_value = 200
    kernel.CreateIoCompletionPort.return_value = 300
    kernel.SetInformationJobObject.return_value = 1
    kernel.AssignProcessToJobObject.return_value = 1
    kernel.TerminateJobObject.return_value = 1
    monkeypatch.setattr(windows.ctypes, 'WinDLL', Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(windows.ctypes, 'get_last_error', lambda: 5, raising=False)
    monkeypatch.setattr(windows.ctypes, 'WinError', lambda number: OSError(number, 'denied'), raising=False)
    return kernel


def test_job_owns_launcher_before_it_can_create_children(monkeypatch):
    kernel = job_kernel(monkeypatch)
    job = windows.Job(71)
    windows.ctypes.WinDLL.assert_called_once_with('kernel32', use_last_error=True)
    assert kernel.CreateJobObjectW.restype is windows.wintypes.HANDLE
    assert kernel.OpenProcess.restype is windows.wintypes.HANDLE
    configured = kernel.SetInformationJobObject.call_args_list[0].args
    assert configured[0].value == 100
    assert configured[1] == 9
    assert configured[2]._obj.basic.flags == 0x2000
    assert configured[3] == ctypes.sizeof(windows._ExtendedLimits)
    kernel.OpenProcess.assert_called_once_with(0x101, False, 71)
    assigned = kernel.AssignProcessToJobObject.call_args.args
    assert [handle.value for handle in assigned] == [100, 200]
    assert [entry.args[0].value for entry in kernel.CloseHandle.call_args_list] == [200]
    connection = kernel.SetInformationJobObject.call_args_list[1].args
    assert connection[1] == 7
    assert connection[2]._obj.key == 100
    assert connection[2]._obj.port == 300
    assert [entry[0] for entry in kernel.mock_calls] == [
        'CreateJobObjectW', 'SetInformationJobObject', 'CreateIoCompletionPort',
        'SetInformationJobObject', 'OpenProcess', 'AssignProcessToJobObject', 'CloseHandle']
    assert job.handle.value == 100


@pytest.mark.parametrize(('failure', 'closed'), [
    ('CreateJobObjectW', []), ('SetInformationJobObject', [100]),
    ('CreateIoCompletionPort', [100]), ('OpenProcess', [300, 100]),
    ('AssignProcessToJobObject', [200, 300, 100]),
])
def test_job_setup_failure_closes_handles_and_refuses_ownership(monkeypatch, failure, closed):
    kernel = job_kernel(monkeypatch)
    getattr(kernel, failure).return_value = 0
    with pytest.raises(OSError, match='denied'):
        windows.Job(71)
    assert [entry.args[0].value for entry in kernel.CloseHandle.call_args_list] == closed
    kernel.GetQueuedCompletionStatus.assert_not_called()


def test_job_connection_failure_closes_handles_before_refusing(monkeypatch):
    kernel = job_kernel(monkeypatch)
    kernel.SetInformationJobObject.side_effect = [1, 0]
    with pytest.raises(OSError, match='denied'):
        windows.Job(71)
    kernel.AssignProcessToJobObject.assert_not_called()
    assert [entry.args[0].value for entry in kernel.CloseHandle.call_args_list] == [300, 100]
    kernel.GetQueuedCompletionStatus.assert_not_called()


def test_job_waits_for_its_completion_notice_before_closing(monkeypatch):
    kernel = job_kernel(monkeypatch)
    notices = iter([(6, 100), (4, 999), (4, 100)])

    def completion(port, message, key, process, timeout):
        assert port.value == 300
        assert timeout.value == 0xffffffff
        message._obj.value, key._obj.value = next(notices)
        return 1

    kernel.GetQueuedCompletionStatus.side_effect = completion
    job = windows.Job(71)
    kernel.reset_mock()
    job.stop()
    assert [entry[0] for entry in kernel.mock_calls] == [
        'TerminateJobObject', 'GetQueuedCompletionStatus', 'GetQueuedCompletionStatus',
        'GetQueuedCompletionStatus', 'CloseHandle', 'CloseHandle']
    assert [entry.args[0].value for entry in kernel.CloseHandle.call_args_list] == [300, 100]


@pytest.mark.parametrize('failure', ['TerminateJobObject', 'GetQueuedCompletionStatus'])
def test_job_cleanup_failure_does_not_confirm_release(monkeypatch, failure):
    kernel = job_kernel(monkeypatch)
    job = windows.Job(71)
    kernel.reset_mock()
    getattr(kernel, failure).return_value = 0
    with pytest.raises(OSError, match='denied'):
        job.stop()
    kernel.CloseHandle.assert_not_called()


def test_a_missing_completion_keeps_ownership_held(monkeypatch):
    kernel = job_kernel(monkeypatch)
    waiting, release = Event(), Event()

    def completion(port, message, key, process, timeout):
        waiting.set()
        assert release.wait(5), "the test must release the native completion stub"
        message._obj.value, key._obj.value = 4, 100
        return 1

    kernel.GetQueuedCompletionStatus.side_effect = completion
    job = windows.Job(71)
    kernel.reset_mock()
    with ThreadPoolExecutor(max_workers=1) as worker:
        stopped = worker.submit(job.stop)
        try:
            assert waiting.wait(5), "cleanup must reach the native completion wait"
            assert not stopped.done()
            kernel.CloseHandle.assert_not_called()
            kernel.QueryInformationJobObject.assert_not_called()
        finally:
            release.set()
        stopped.result(timeout=5)
