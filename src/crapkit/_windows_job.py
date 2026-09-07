"""Own a Windows command tree before its launcher starts the command.

The Job follows descendants after their parent exits. Breakaway is disabled;
closing the owner's last handle also stops the Job if the owner crashes.
"""
import ctypes
from ctypes import wintypes


class _Limits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                ('scheduling', wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _Limits), ('io', ctypes.c_ulonglong * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class _CompletionPort(ctypes.Structure):
    _fields_ = [('key', wintypes.HANDLE), ('port', wintypes.HANDLE)]


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CreateIoCompletionPort.restype = wintypes.HANDLE
    return kernel


def _checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


class Job:
    """A registered launcher and all of its descendants, until stop completes."""

    def __init__(self, pid: int):
        self.kernel = _kernel()
        self.handle = wintypes.HANDLE(_checked(self.kernel.CreateJobObjectW(None, None)))
        self.port = None
        try:
            self._configure()
            self._connect()
            self._assign(pid)
        except BaseException:
            self._close()
            raise

    def _configure(self):
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _checked(self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))

    def _assign(self, pid):
        # AssignProcessToJobObject requires SET_QUOTA and TERMINATE rights.
        process = wintypes.HANDLE(_checked(self.kernel.OpenProcess(0x101, False, pid)))
        try:
            _checked(self.kernel.AssignProcessToJobObject(self.handle, process))
        finally:
            self.kernel.CloseHandle(process)

    def _connect(self):
        self.port = wintypes.HANDLE(_checked(self.kernel.CreateIoCompletionPort(
            wintypes.HANDLE(-1), None, 0, 1)))
        connection = _CompletionPort(self.handle, self.port)
        _checked(self.kernel.SetInformationJobObject(
            self.handle, 7, ctypes.byref(connection), ctypes.sizeof(connection)))

    def _wait_stopped(self):
        # A missing notification keeps ownership held. The accounting count resets
        # before process exit and cannot safely replace the completion notice.
        message, key, process = wintypes.DWORD(), ctypes.c_size_t(), wintypes.HANDLE()
        while True:
            _checked(self.kernel.GetQueuedCompletionStatus(
                self.port, ctypes.byref(message), ctypes.byref(key),
                ctypes.byref(process), wintypes.DWORD(0xffffffff)))
            if message.value == 4 and key.value == self.handle.value:
                return  # JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO

    def _close(self):
        if self.port is not None:
            self.kernel.CloseHandle(self.port)
        self.kernel.CloseHandle(self.handle)

    def stop(self):
        """Wait for process exit, not the earlier accounting-count reset."""
        _checked(self.kernel.TerminateJobObject(self.handle, 1))
        self._wait_stopped()
        self._close()
