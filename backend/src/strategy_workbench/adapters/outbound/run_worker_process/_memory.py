"""운영체제 메모리 읽기: 프로세스의 최대 RSS(run-worker spec D8).

worker 의 최대 RSS 보고가 이 모듈을 쓴다. 메모리 읽기를 한곳에 모은다(#575 리뷰 P3). 새 라이브러리
없이 Windows 는 `ctypes`(kernel32·psapi), POSIX 는 `resource` 를 읽는다. 읽지 못하면 None 이고 값을
지어내지 않는다. RSS 는 Windows 에서 working set 이다.
"""

from __future__ import annotations

import sys


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
