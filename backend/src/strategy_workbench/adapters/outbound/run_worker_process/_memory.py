"""운영체제 메모리 읽기: 가용 물리 메모리, 프로세스의 현재·최대 RSS(run-worker spec D8).

부모의 메모리 가드와 worker 의 최대 RSS 보고가 이 모듈 하나를 쓴다(#575 리뷰 P3). 새 라이브러리 없이
Windows 는 `ctypes`(kernel32·psapi), Linux 는 `/proc` 와 `resource` 를 읽는다. 읽지 못하면 None 이고
값을 지어내지 않는다 — 어떻게 다룰지는 호출자가 정한다. RSS 는 Windows 에서 working set 이다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 가용 물리 메모리를 읽는 Linux 파일. `MemAvailable` 은 커널 3.14 부터 있다.
_MEMINFO = Path("/proc/meminfo")


def available_memory_bytes() -> int | None:
    """지금 새로 쓸 수 있는 물리 메모리(바이트). 읽지 못하면 None 이다.

    Windows 는 `GlobalMemoryStatusEx` 의 `ullAvailPhys`, Linux 는 `/proc/meminfo` 의 `MemAvailable`
    이다. 그 밖의 플랫폼은 None 이다.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        read = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
        read.argtypes = (ctypes.POINTER(_MemoryStatus),)
        read.restype = wintypes.BOOL
        if not read(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys)
    try:
        lines = _MEMINFO.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for line in lines:
        name, _, rest = line.partition(":")
        if name == "MemAvailable":
            # 값은 "12345678 kB" 꼴이다.
            amount, _, unit = rest.strip().partition(" ")
            if unit == "kB" and amount.isdigit():
                return int(amount) * 1024
            return None
    return None


def process_rss_bytes(pid: int) -> int | None:
    """프로세스 `pid` 의 지금 RSS(바이트). 끝났거나 읽을 권한이 없으면 None 이다.

    Windows 는 `OpenProcess` + `GetProcessMemoryInfo` 의 `WorkingSetSize`, Linux 는
    `/proc/<pid>/statm` 의 상주 페이지 수 × 페이지 크기다.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        query_limited_information, vm_read = 0x1000, 0x0010
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(query_limited_information | vm_read, False, pid)
        if not handle:
            return None
        try:
            counters = _process_counters(handle)
        finally:
            kernel32.CloseHandle(handle)
        return None if counters is None else counters[1]
    try:
        fields = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
    except OSError:
        return None
    if len(fields) < 2 or not fields[1].isdigit():
        return None
    return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")


def peak_rss_bytes() -> int | None:
    """이 프로세스의 최대 RSS(바이트). 읽지 못하면 None 이다(spec D8 계수 재조정의 입력)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        current = ctypes.WinDLL("kernel32", use_last_error=True).GetCurrentProcess
        current.restype = wintypes.HANDLE
        counters = _process_counters(current())
        return None if counters is None else counters[0]
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux 는 KiB, macOS 는 바이트다.
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def _process_counters(handle: int) -> tuple[int, int] | None:
    """Windows: 프로세스 핸들의 (최대 working set, 지금 working set). 읽지 못하면 None 이다."""
    if sys.platform != "win32":
        raise RuntimeError("GetProcessMemoryInfo is Windows only — read /proc on POSIX")
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    read = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    read.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD)
    read.restype = wintypes.BOOL
    if not read(handle, ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize), int(counters.WorkingSetSize)
