"""Small Win32 helpers: foreground window, process names, top-level window lookup.

Kept separate so tests can stub them without touching real windows.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import PureWindowsPath

import win32gui
import win32process

GA_ROOTOWNER = 3
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def process_name(pid: int) -> str:
    """Executable name for a PID, e.g. 'notepad.exe'. Empty if access is denied."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return PureWindowsPath(buf.value).name
    finally:
        _kernel32.CloseHandle(handle)


def root_owner(hwnd: int) -> int:
    """Top-level owner of a window, so popups and dialogs map to their main window."""
    if not hwnd:
        return 0
    return win32gui.GetAncestor(hwnd, GA_ROOTOWNER) or hwnd


def window_info(hwnd: int) -> dict:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return {
        "title": win32gui.GetWindowText(hwnd),
        "process": process_name(pid),
        "pid": pid,
        "handle": hwnd,
    }


def foreground_info() -> dict:
    """Info about the window that would receive keystrokes right now."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"title": "", "process": "", "pid": 0, "handle": 0, "root": 0}
    info = window_info(hwnd)
    info["root"] = root_owner(hwnd)
    return info


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(win32gui.IsWindow(hwnd))


def top_level_windows() -> list[dict]:
    """Visible, titled top-level windows in z-order (topmost first)."""
    found: list[int] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(_cb, None)
    return [window_info(h) for h in found]


def find_window(query: str) -> dict | None:
    """Best top-level window for a title or process substring.

    Order: exact process name (with or without .exe), then process substring,
    then title substring. Within each tier, the topmost window wins.
    """
    q = query.lower().strip()
    if not q:
        return None
    wins = top_level_windows()

    def stem(p: str) -> str:
        return p.lower().removesuffix(".exe")

    tiers = (
        lambda w: stem(w["process"]) == q.removesuffix(".exe"),
        lambda w: q in w["process"].lower(),
        lambda w: q in w["title"].lower(),
    )
    for test in tiers:
        for w in wins:
            if test(w):
                return w
    return None


def find_by_process(process: str) -> dict | None:
    """Topmost visible window whose executable name matches exactly."""
    p = process.lower()
    for w in top_level_windows():
        if w["process"].lower() == p:
            return w
    return None


def top_window_at(x: int, y: int) -> int:
    """Root owner of the window under a screen point."""
    return root_owner(win32gui.WindowFromPoint((x, y)))
