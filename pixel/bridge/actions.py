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


def get_ui_tree(window: str | None = None, *, handle: int | None = None,
                max_depth: int | None = None, max_elements: int | None = None,
                include_raw: bool = False) -> dict:
    """Read one window: by handle, by title/process substring, or the focused one."""
    from pixel.bridge.capture import capture_window

    cfg = config.bridge()
    max_depth = max_depth or cfg["max_depth"]
    max_elements = max_elements or cfg["max_elements"]
    try:
        if handle:
            if not win.is_window(handle):
                return _fail(f"window {handle} is gone")
            target = win.window_info(handle)
        elif window:
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
        # short descriptions by ID, for confirm prompts and validation
        "labels": {e.id: f"{e.control_type} '{e.name or e.automation_id}'"
                   for e in res.elements},
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


def type_text(id: int, text: str, should_stop=None) -> dict:  # noqa: A002
    """should_stop: optional callable, checked between keystroke chunks (kill hotkey)."""
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
        if should_stop and should_stop():
            return _fail(f"stopped by the kill hotkey after {sent} of {len(text)} characters",
                         method="keys", chars=sent)
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


_SCROLL_DIRS = {"up": ("v", -1), "down": ("v", 1), "left": ("h", -1), "right": ("h", 1)}
SCROLL_WALK_UP = 8          # levels above the element searched for a ScrollPattern
READ_LIMIT = 2000           # characters returned by read_text


def scroll(id: int, direction: str) -> dict:  # noqa: A002
    """Scroll the nearest scrollable container of an element by one page."""
    from pywinauto import uia_defines as ud

    direction = (direction or "").strip().lower()
    if direction not in _SCROLL_DIRS:
        return _fail(f"unknown direction '{direction}' (use up, down, left, right)")
    axis, sign = _SCROLL_DIRS[direction]
    try:
        target, loc, el = _resolve(id)
    except LookupError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not find element {id}: {exc}")

    # 1. ScrollPattern on the element or an ancestor, never above the window.
    sp = None
    try:
        iuia = ud.IUIA().iuia
        window_el = iuia.ElementFromHandle(target["handle"])
        node = el
        for _ in range(SCROLL_WALK_UP + 1):
            try:
                sp = ud.get_elem_interface(node, "Scroll")
                break
            except ud.NoPatternInterfaceError:
                pass
            if iuia.CompareElements(node, window_el):
                break
            node = iuia.ControlViewWalker.GetParentElement(node)
            if not node:
                break
    except Exception:  # noqa: BLE001 - fall back to the wheel
        sp = None

    if sp is not None:
        try:
            if axis == "v":
                can = sp.CurrentVerticallyScrollable
                pct = lambda: float(sp.CurrentVerticalScrollPercent)  # noqa: E731
            else:
                can = sp.CurrentHorizontallyScrollable
                pct = lambda: float(sp.CurrentHorizontalScrollPercent)  # noqa: E731
            if not can:
                return {"ok": True, "method": "pattern", "direction": direction,
                        "at_edge": True, "note": f"nothing to scroll {direction}"}
            before = pct()
            if (sign > 0 and before >= 100) or (sign < 0 and before <= 0):
                return {"ok": True, "method": "pattern", "direction": direction,
                        "before": round(before), "after": round(before), "at_edge": True}
            amount = ud.scroll_large_increment if sign > 0 else ud.scroll_large_decrement
            if axis == "v":
                sp.Scroll(ud.scroll_no_amount, amount)
            else:
                sp.Scroll(amount, ud.scroll_no_amount)
            time.sleep(0.1)
            after = pct()
            return {"ok": True, "method": "pattern", "direction": direction,
                    "before": round(before), "after": round(after),
                    "at_edge": round(before) == round(after)}
        except Exception as exc:  # noqa: BLE001
            return _fail(f"scrolling {_describe(loc)} failed: {exc}")

    # 2. Mouse wheel at the element centre (vertical only), behind the same
    #    covered-window check as click.
    if axis != "v":
        return _fail(f"{_describe(loc)} has no scrollable container for {direction}")
    try:
        r = el.CurrentBoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            return _fail(f"{_describe(loc)} has no scrollable container and no size")
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        if win.top_window_at(cx, cy) != win.root_owner(target["handle"]):
            return _fail(f"{_describe(loc)} is covered by another window; nothing was scrolled")
        from pywinauto import mouse
        mouse.scroll(coords=(cx, cy), wheel_dist=-3 if sign > 0 else 3)
        return {"ok": True, "method": "wheel", "direction": direction}
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not scroll {_describe(loc)}: {exc}")


def read_text(id: int) -> dict:  # noqa: A002
    """Text of an element: ValuePattern, then TextPattern, then its name."""
    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    try:
        _target, loc, el = _resolve(id)
    except LookupError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not find element {id}: {exc}")
    try:
        try:
            text, source = get_elem_interface(el, "Value").CurrentValue or "", "value"
        except NoPatternInterfaceError:
            try:
                rng = get_elem_interface(el, "Text").DocumentRange
                text, source = rng.GetText(READ_LIMIT + 1) or "", "text"
            except NoPatternInterfaceError:
                text, source = el.CurrentName or "", "name"
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not read {_describe(loc)}: {exc}")
    return {"ok": True, "text": text[:READ_LIMIT], "source": source,
            "truncated": len(text) > READ_LIMIT, "target": _describe(loc)}


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
