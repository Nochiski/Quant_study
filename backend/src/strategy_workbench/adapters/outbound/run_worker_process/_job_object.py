"""Windows Job Object: 부모가 어떻게 죽든 OS 가 worker 를 끝내게 한다(run-worker spec D5 주 경로).

`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 를 건 job 하나를 부모가 쥐고 worker 를 넣는다. 부모 프로세스가
끝나 마지막 핸들이 닫히면 OS 가 job 안의 프로세스를 모두 끝낸다. 핸들은 상속하지 않으므로 worker 가
job 을 붙잡아 두지 않는다. POSIX 에는 같은 장치가 없어 stdin EOF 경로가 맡는다.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class KillOnCloseJob:
    """worker 를 담는 job 하나. 부모가 사는 동안 핸들을 닫지 않는다(닫으면 worker 가 끝난다)."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(f"Job Object is Windows only — platform={sys.platform}")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32 = kernel32
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject(KILL_ON_JOB_CLOSE) failed")
        self._handle = handle

    def assign(self, pid: int) -> None:
        if sys.platform != "win32":
            raise RuntimeError(f"Job Object is Windows only — platform={sys.platform}")
        kernel32 = self._kernel32
        process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            raise OSError(ctypes.get_last_error(), f"OpenProcess failed — pid={pid}")
        try:
            if not kernel32.AssignProcessToJobObject(self._handle, process):
                raise OSError(
                    ctypes.get_last_error(), f"AssignProcessToJobObject failed — pid={pid}"
                )
        finally:
            kernel32.CloseHandle(process)
