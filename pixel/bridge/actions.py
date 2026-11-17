"""The six M1 tools. Each returns a plain dict; failures return
{"ok": False, "error": "..."} instead of raising.

Keystrokes only go out after the foreground guard confirms the window from the
last tree read is the one in front.
"""

from __future__ import annotations

import os
import time

from pixel import config
from pixel.bridge import state, win
from pixel.bridge.prune import estimate_tokens, prune, render

CHUNK = 20                       # characters typed between foreground checks
_PYWINAUTO_SPECIAL = set("+^%~(){}[]")
_MODIFIERS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}
_NAMED = {
    "enter": "ENTER", "return": "ENTER", "esc": "ESC", "escape": "ESC",
    "tab": "TAB", "space": "SPACE", "backspace": "BACKSPACE",
    "delete": "DELETE", "del": "DELETE", "insert": "INSERT",
    "home": "HOME", "end": "END", "pageup": "PGUP", "pgup": "PGUP",
    "pagedown": "PGDN", "pgdn": "PGDN",
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}
# Single printable keys allowed in a shortcut. The Windows key is deliberately
# not supported: win+r opens the Run dialog.
_SINGLE = set("abcdefghijklmnopqrstuvwxyz0123456789`-=[]\\;',./")


class KeyError_(ValueError):
    pass


def _fail(msg: str, **extra) -> dict:
    return {"ok": False, "error": msg, **extra}


# ---------- key translation ----------

def _escape_char(ch: str) -> str:
    return "{" + ch + "}" if ch in _PYWINAUTO_SPECIAL else ch


def translate_keys(keys: str) -> str:
    """'ctrl+s' -> '^s', 'alt+f4' -> '%{F4}', 'ctrl+a backspace' -> '^a{BACKSPACE}'.

    Space-separated chords run in order. Raises ValueError on unknown names.
    """
    chords = keys.strip().lower().split()
    if not chords:
        raise KeyError_("no keys given")
    out = []
    for chord in chords:
        parts = chord.split("+")
        # "ctrl++" means ctrl and the plus key
        if chord.endswith("++"):
            parts = chord[:-2].split("+") + ["+"]
        *mods, key = parts
        prefix = ""
        for m in mods:
            if m not in _MODIFIERS:
                raise KeyError_(f"unknown modifier '{m}' in '{chord}' (use ctrl, alt, shift)")
            prefix += _MODIFIERS[m]
        if key in _NAMED:
            out.append(prefix + "{" + _NAMED[key] + "}")
        elif len(key) == 1 and (key in _SINGLE or key == "+"):
            out.append(prefix + _escape_char(key))
        else:
            raise KeyError_(f"unknown key '{key}' in '{chord}'")
    return "".join(out)


def escape_text(text: str) -> str:
    """Make text literal for pywinauto: no character acts as a shortcut."""
    return "".join(_escape_char(c) for c in text.replace("\r\n", "\n").replace("\r", "\n"))


# ---------- foreground guard ----------

def check_foreground(target: dict | None) -> str | None:
    """None if the last-read window is in front, else an error message."""
    if not target or not target.get("handle"):
        return "no tree read yet, run `pixel tree` first"
    if not win.is_window(target["handle"]):
        return f"window '{target.get('title', '')}' from the last read is gone; run `pixel tree` again"
    fg = win.foreground_info()
    if fg["root"] != win.root_owner(target["handle"]) or fg["pid"] != target.get("pid"):
        shown = fg["title"] or fg["process"] or "(none)"
        return f"foreground window is '{shown}', expected '{target.get('title', '')}'"
    return None


def _send(seq: str, *, literal: bool) -> None:
    from pywinauto import keyboard
    keyboard.send_keys(seq, pause=0.01, with_spaces=literal, with_tabs=literal,
                       with_newlines=literal, vk_packet=literal)


# ---------- element lookup ----------

def _resolve(eid: int):
    """Find the live UIA element for an ID from the last read, or explain why not."""
    from pywinauto.uia_defines import IUIA
    from pixel.bridge.capture import capture_window

    st = state.load()
    if not st:
        raise LookupError("no tree read yet, run `pixel tree` first")
    loc = st["elements"].get(str(eid))
    if not loc:
        n = len(st["elements"])
        raise LookupError(f"no element {eid} in the last read (IDs 1 to {n})")
    target = st["window"]
    if not win.is_window(target["handle"]):
        raise LookupError(f"window '{target['title']}' is gone; run `pixel tree` again")

    cap = capture_window(target["handle"], config.bridge()["max_depth"])
    el = cap.elements.get(tuple(loc["runtime_id"]))
    stale = f"stale ID {eid} ({loc['control_type']} '{loc['name']}'), run `pixel tree` again"
    if el is None:
        raise LookupError(stale)
    ctype = IUIA().known_control_type_ids.get(el.CurrentControlType, "Unknown")
    if ctype != loc["control_type"] or (el.CurrentName or "") != loc["name"]:
        raise LookupError(stale)
    return target, loc, el


def _describe(loc: dict) -> str:
    return f"{loc['control_type']} '{loc['name'] or loc['automation_id']}'"


# ---------- tools ----------

def get_focused_window() -> dict:
    try:
        fg = win.foreground_info()
        info = win.window_info(fg["root"]) if fg["root"] else fg
        info.pop("root", None)
        return {"ok": True, **info}
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not read the focused window: {exc}")


def get_ui_tree(window: str | None = None, *, max_depth: int | None = None,
                max_elements: int | None = None, include_raw: bool = False) -> dict:
    from pixel.bridge.capture import capture_window

    cfg = config.bridge()
    max_depth = max_depth or cfg["max_depth"]
    max_elements = max_elements or cfg["max_elements"]
    try:
        if window:
            target = win.find_window(window)
            if not target:
                return _fail(f"no visible window matches '{window}'")
        else:
            root = win.foreground_info()["root"]
            if not root:
                return _fail("no foreground window")
            target = win.window_info(root)

        t0 = time.perf_counter()
        cap = capture_window(target["handle"], max_depth)
        seconds = time.perf_counter() - t0
        res = prune(cap.root, max_depth=max_depth, max_elements=max_elements)
        text = render(res, target["title"], target["process"])
        state.save(target, res.elements)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not read the tree: {exc}")

    out = {
        "ok": True,
        "window": target,
        "text": text,
        "elements": len(res.elements),
        "lines": len(res.lines),
        "raw_nodes": cap.raw_nodes,
        "tokens": estimate_tokens(text, cfg["chars_per_token"]),
        "seconds": round(seconds, 2),
    }
    if include_raw:
        out["raw"] = cap.root
    return out


def click_element(id: int) -> dict:  # noqa: A002 - name fixed by the tool menu
    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    try:
        target, loc, el = _resolve(id)
    except LookupError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not find element {id}: {exc}")

    def expand(p):
        if p.CurrentExpandCollapseState == 0:  # collapsed
            p.Expand()
        else:
            p.Collapse()

    attempts = [
        ("invoke", "Invoke", lambda p: p.Invoke()),
        ("toggle", "Toggle", lambda p: p.Toggle()),
        ("select", "SelectionItem", lambda p: p.Select()),
        ("expand", "ExpandCollapse", expand),
    ]
    errors = []
    for method, pattern, act in attempts:
        try:
            iface = get_elem_interface(el, pattern)
        except NoPatternInterfaceError:
            continue
        try:
            act(iface)
            return {"ok": True, "method": method, "target": _describe(loc)}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{method}: {exc}")

    # Fallback: a real mouse click at the element centre, only if the target
    # window is the one actually under that point.
    try:
        r = el.CurrentBoundingRectangle
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        if r.right <= r.left or r.bottom <= r.top:
            return _fail(f"{_describe(loc)} has no pattern to activate and no size to click",
                         tried=errors)
        if win.top_window_at(cx, cy) != win.root_owner(target["handle"]):
            return _fail(f"{_describe(loc)} is covered by another window at ({cx}, {cy}); "
                         "nothing was clicked", tried=errors)
        from pywinauto import mouse
        mouse.click(button="left", coords=(cx, cy))
        return {"ok": True, "method": "coords", "target": _describe(loc), "at": [cx, cy]}
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not click {_describe(loc)}: {exc}", tried=errors)


def _norm(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def type_text(id: int, text: str) -> dict:  # noqa: A002
    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    try:
        target, loc, el = _resolve(id)
    except LookupError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not find element {id}: {exc}")

    replace = False
    note = None
    # 1. UI Automation ValuePattern: no keystrokes, focus does not matter.
    try:
        vp = get_elem_interface(el, "Value")
        if not vp.CurrentIsReadOnly:
            vp.SetValue(text)
            time.sleep(0.05)
            if _norm(vp.CurrentValue) == _norm(text):
                return {"ok": True, "method": "value", "chars": len(text),
                        "target": _describe(loc)}
            replace = True  # SetValue changed the field but not to what we wanted
            note = "value read-back did not match; retyped with keystrokes"
    except NoPatternInterfaceError:
        pass
    except Exception as exc:  # noqa: BLE001
        note = f"SetValue failed ({exc}); used keystrokes"

    # 2. Keystrokes, behind the foreground guard.
    try:
        el.SetFocus()
        time.sleep(0.1)
    except Exception:  # noqa: BLE001 - the guard below decides
        pass

    err = check_foreground(target)
    if err:
        return _fail(f"{err}; nothing was sent", method="keys", chars=0)
    if replace:
        _send("^a", literal=False)

    sent = 0
    for start in range(0, len(text), CHUNK):
        err = check_foreground(target)
        if err:
            return _fail(f"{err}; stopped after {sent} of {len(text)} characters",
                         method="keys", chars=sent)
        chunk = text[start:start + CHUNK]
        _send(escape_text(chunk), literal=True)
        sent += len(chunk)

    out = {"ok": True, "method": "keys", "chars": sent, "target": _describe(loc)}
    if note:
        out["note"] = note
    return out


def send_keys(keys: str) -> dict:
    try:
        seq = translate_keys(keys)
    except ValueError as exc:
        return _fail(str(exc))
    st = state.load()
    err = check_foreground(st["window"] if st else None)
    if err:
        return _fail(f"{err}; nothing was sent")
    try:
        _send(seq, literal=False)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"sending keys failed: {exc}")
    return {"ok": True, "sent": keys.strip().lower()}


def _bring_to_front(hwnd: int) -> bool:
    import win32con
    import win32gui

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - Windows may refuse; try UIA next
        pass
    if win.foreground_info()["root"] == win.root_owner(hwnd):
        return True
    try:
        from pywinauto.uia_defines import IUIA
        IUIA().iuia.ElementFromHandle(hwnd).SetFocus()
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.2)
    return win.foreground_info()["root"] == win.root_owner(hwnd)


def open_app(name: str) -> dict:
    apps = config.apps()
    key = name.strip().lower()
    spec = apps.get(key)
    if not spec:
        return _fail(f"'{name}' is not in the app allowlist ({', '.join(sorted(apps)) or 'empty'})")

    existing = win.find_by_process(spec["process"])
    if existing:
        front = _bring_to_front(existing["handle"])
        return {"ok": True, "action": "focused", "window": existing["title"], "foreground": front}

    exe = os.path.expandvars(spec["exe"])
    try:
        os.startfile(exe)  # noqa: S606 - allowlisted path from config.toml, no shell command line
    except OSError as exc:
        return _fail(f"could not launch {exe}: {exc}")

    deadline = time.time() + 15
    while time.time() < deadline:
        found = win.find_by_process(spec["process"])
        if found:
            time.sleep(0.5)  # let the first frame paint before a tree read
            front = _bring_to_front(found["handle"])
            return {"ok": True, "action": "launched", "window": found["title"], "foreground": front}
        time.sleep(0.25)
    return _fail(f"launched {exe} but no {spec['process']} window appeared within 15 s")
