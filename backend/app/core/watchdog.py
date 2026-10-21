"""Process helpers for server processes: parent watchdog and timer resolution."""

import asyncio
import os
import sys

import psutil


async def watch_parent(pid: int, interval: float = 2.0) -> None:
    """Exit a child server process when the control plane that spawned it disappears."""
    while True:
        await asyncio.sleep(interval)
        if not psutil.pid_exists(pid):
            os._exit(0)


def improve_timer_resolution() -> None:
    """On Windows the default timer tick (~15.6 ms) quantises asyncio.sleep; request 1 ms so
    simulated delays are applied accurately. No-op elsewhere."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _Throttle(ctypes.Structure):
                _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

            # Windows 11 ignores timer requests from windowless background processes unless they opt
            # out of timer-resolution power throttling (ProcessPowerThrottling = 4, IGNORE_TIMER_RESOLUTION = 4).
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            info = _Throttle(1, 0x4, 0)
            k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(info), ctypes.sizeof(info))
            ctypes.WinDLL("winmm").timeBeginPeriod(1)
        except (OSError, AttributeError, ctypes.ArgumentError):
            pass
