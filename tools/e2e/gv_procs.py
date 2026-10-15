# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows process helpers for the Game View e2e tests (stdlib + ctypes only).

Used inside the editor (tools/e2e/blender_game_view_play.py) and by the driver
(tools/e2e/game_view_play_e2e.py). A process is identified by (pid, creation time), so a
reused pid never counts as a leaked host.
"""

import ctypes
import json
import subprocess
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("gv_procs is Windows-only")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]


def processes() -> list[tuple[int, int, str]]:
    """(pid, parent pid, exe name) of every process."""
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot")
    found = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return found


def _open(pid: int, access: int = PROCESS_QUERY_LIMITED_INFORMATION):
    return _k32.OpenProcess(access, False, pid)


def created(pid: int) -> int | None:
    """Creation time (100 ns ticks) of a running process, or None if it is gone."""
    handle = _open(pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        code = wintypes.DWORD()
        if _k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != STILL_ACTIVE:
            return None  # exited, but someone still holds a handle to it
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        _k32.CloseHandle(handle)


def descendants(root_pid: int) -> list[dict]:
    """Every live process below `root_pid`: [{pid, ppid, name, created}]."""
    procs = processes()
    children = {}
    for pid, ppid, name in procs:
        children.setdefault(ppid, []).append((pid, name))
    found, todo = [], [root_pid]
    while todo:
        parent = todo.pop()
        for pid, name in children.get(parent, ()):
            if pid == root_pid or pid == parent:
                continue
            stamp = created(pid)
            parent_stamp = created(parent)
            # A recycled parent pid would adopt unrelated processes: children start after parents.
            if stamp is None or (parent_stamp is not None and stamp < parent_stamp):
                continue
            found.append({"pid": pid, "ppid": parent, "name": name, "created": stamp})
            todo.append(pid)
    return found


def still_alive(procs: list[dict]) -> list[dict]:
    """The entries of `procs` that are still running (same pid and creation time)."""
    return [p for p in procs if created(p["pid"]) == p["created"]]


def kill(pid: int) -> bool:
    handle = _open(pid, PROCESS_TERMINATE)
    if not handle:
        return False
    try:
        return bool(_k32.TerminateProcess(handle, 1))
    finally:
        _k32.CloseHandle(handle)


def usage(pid: int) -> dict:
    """Private bytes, working set and handle count of a process."""
    handle = _open(pid)
    if not handle:
        return {}
    try:
        counters = _PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        handles = wintypes.DWORD()
        _k32.GetProcessHandleCount(handle, ctypes.byref(handles))
        return {"private_mb": counters.PrivateUsage / 2**20, "working_set_mb": counters.WorkingSetSize / 2**20,
                "handles": handles.value}
    finally:
        _k32.CloseHandle(handle)


_u32 = ctypes.WinDLL("user32", use_last_error=True)
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.IsIconic.argtypes = [wintypes.HWND]
SW_SHOWNOACTIVATE = 4
SW_SHOWMINNOACTIVE = 7


def top_windows(pid: int) -> list[int]:
    """Visible top-level windows of a process."""
    found = []

    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _u32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    _u32.EnumWindows(_EnumWindowsProc(visit), 0)
    return found


def minimize(hwnd: int, minimized: bool) -> None:
    """Minimizes or restores a window without activating it (or anything else)."""
    _u32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE if minimized else SW_SHOWNOACTIVATE)


def is_minimized(hwnd: int) -> bool:
    return bool(_u32.IsIconic(hwnd))


_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
WM_CLOSE = 0x0010


def close_window(hwnd: int) -> bool:
    """Asks a window to close, as its title bar's close button does."""
    return bool(_u32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def gpu_memory_mb(pid: int) -> dict:
    """Dedicated and shared GPU memory of a process from the 'GPU Process Memory' counters (slow, ~1 s)."""
    script = ("$s = (Get-Counter -ErrorAction SilentlyContinue "
              f"'\\GPU Process Memory(pid_{pid}_*)\\Dedicated Usage','\\GPU Process Memory(pid_{pid}_*)\\Shared Usage')"
              ".CounterSamples; $d = ($s | ? Path -like '*dedicated*' | measure CookedValue -Sum).Sum; "
              "$h = ($s | ? Path -like '*shared*' | measure CookedValue -Sum).Sum; "
              "@{dedicated=$d; shared=$h} | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        data = json.loads(out or "{}")
        return {k: (data.get(k) or 0) / 2**20 for k in ("dedicated", "shared")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def command_lines(pids: list[int]) -> dict[int, str]:
    """Command lines of the given processes (through CIM; slow, ~1 s)."""
    if not pids:
        return {}
    filt = " or ".join(f"ProcessId={int(p)}" for p in pids)
    script = (f"Get-CimInstance Win32_Process -Filter '{filt}' | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        data = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if isinstance(data, dict):
        data = [data]
    return {int(d["ProcessId"]): d.get("CommandLine") or "" for d in data}
