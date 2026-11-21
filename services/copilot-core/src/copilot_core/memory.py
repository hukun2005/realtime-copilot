"""进程级内存指标（Windows WorkingSet / PrivateBytes）。

PCM 预算只描述音频缓冲占用，不能替代整个进程的内存；这里提供独立的进程指标。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def process_memory() -> dict[str, Any] | None:
    """返回 Windows 进程内存；其他平台或失败时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel32.GetCurrentProcess()
        function = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        if function is None:
            function = ctypes.windll.psapi.GetProcessMemoryInfo  # type: ignore[attr-defined]
        function.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        function.restype = ctypes.c_int
        if not function(handle, ctypes.byref(counters), counters.cb):
            return None
        return {
            "workingSetBytes": int(counters.WorkingSetSize),
            "privateBytes": int(counters.PagefileUsage),
            "peakWorkingSetBytes": int(counters.PeakWorkingSetSize),
        }
    except Exception:
        return None
