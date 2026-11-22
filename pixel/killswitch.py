"""Global kill hotkey (default Ctrl+Alt+Q).

A daemon thread registers the hotkey with Win32 RegisterHotKey and runs a
message loop; pressing it sets a threading.Event the loop checks often. The
hotkey only signals this process. It sends nothing and reads no other input.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_HOTKEY_ID = 0x5149  # arbitrary, per thread

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT}
_VKS = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
        **{f"f{i}": 0x6F + i for i in range(1, 13)}, "pause": 0x13, "esc": 0x1B}


def parse_hotkey(text: str) -> tuple[int, int]:
    """'ctrl+alt+q' -> (modifiers, virtual key). Needs at least one modifier."""
    *mods, key = text.strip().lower().split("+")
    if not mods:
        raise ValueError(f"kill hotkey '{text}' needs a modifier (ctrl, alt, shift)")
    flags = 0
    for m in mods:
        if m not in _MODS:
            raise ValueError(f"unknown modifier '{m}' in kill hotkey '{text}'")
        flags |= _MODS[m]
    if key not in _VKS:
        raise ValueError(f"unsupported key '{key}' in kill hotkey '{text}'")
    return flags | MOD_NOREPEAT, _VKS[key]


class KillSwitch:
    def __init__(self, hotkey: str = "ctrl+alt+q"):
        self.hotkey = hotkey
        self.event = threading.Event()
        self.registered = False
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    def is_set(self) -> bool:
        return self.event.is_set()

    def start(self) -> bool:
        """Register the hotkey. Returns False (with .error) if it is unavailable."""
        try:
            self._mods, self._vk = parse_hotkey(self.hotkey)
        except ValueError as exc:
            self.error = str(exc)
            return False
        self._thread = threading.Thread(target=self._run, name="pixel-kill", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self.registered

    def _run(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, _HOTKEY_ID, self._mods, self._vk):
            err = ctypes.get_last_error()
            self.error = (f"could not register {self.hotkey} (Windows error {err}; "
                          "another app may already use it)")
            self._ready.set()
            return
        self.registered = True
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    self.event.set()
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(1)
