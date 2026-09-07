"""Own a Windows command tree before its launcher starts the command.

The Job follows descendants after their parent exits. Breakaway is disabled;
closing the owner's last handle also stops the Job if the owner crashes.
"""
import ctypes
from ctypes import wintypes
import time


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


class _Accounting(ctypes.Structure):
    _fields_ = [('times', ctypes.c_longlong * 4), ('page_faults', wintypes.DWORD),
                ('total', wintypes.DWORD), ('active', wintypes.DWORD),
                ('terminated', wintypes.DWORD)]


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
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
        try:
            self._configure()
            self._assign(pid)
        except BaseException:
            self.kernel.CloseHandle(self.handle)
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

    def _active(self):
        counts = _Accounting()
        _checked(self.kernel.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(counts), ctypes.sizeof(counts), None))
        return counts.active

    def stop(self):
        """Do not release a resource while a terminated process still owns it."""
        _checked(self.kernel.TerminateJobObject(self.handle, 1))
        while self._active():
            time.sleep(.01)
        self.kernel.CloseHandle(self.handle)
