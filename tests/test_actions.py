"""Foreground guard and key translation. Win32 calls are stubbed; nothing is sent."""

import pytest

from pixel.bridge import actions, win

TARGET = {"title": "Untitled - Notepad", "process": "notepad.exe", "pid": 100, "handle": 1}


@pytest.fixture
def desktop(monkeypatch):
    """Fake desktop: the test sets which window is in front."""
    fake = {"fg": {"title": "Untitled - Notepad", "process": "notepad.exe",
                   "pid": 100, "handle": 1, "root": 1}}
    monkeypatch.setattr(win, "is_window", lambda h: h in (1, 2))
    monkeypatch.setattr(win, "root_owner", lambda h: h)
    monkeypatch.setattr(win, "foreground_info", lambda: dict(fake["fg"]))
    sent = []
    monkeypatch.setattr(actions, "_send", lambda seq, literal: sent.append(seq))
    fake["sent"] = sent
    return fake


def _terminal_in_front(desktop):
    desktop["fg"] = {"title": "PowerShell", "process": "WindowsTerminal.exe",
                     "pid": 200, "handle": 2, "root": 2}


def test_guard_passes_when_target_in_front(desktop):
    assert actions.check_foreground(TARGET) is None


def test_guard_fails_when_other_window_in_front(desktop):
    _terminal_in_front(desktop)
    err = actions.check_foreground(TARGET)
    assert "foreground window is 'PowerShell'" in err


def test_guard_fails_without_a_read(desktop):
    assert "no tree read yet" in actions.check_foreground(None)


def test_guard_fails_when_window_closed(desktop):
    assert "is gone" in actions.check_foreground({**TARGET, "handle": 99})


def test_guard_fails_on_pid_mismatch(desktop):
    desktop["fg"]["pid"] = 999
    assert actions.check_foreground(TARGET) is not None


def test_send_keys_aborts_and_sends_nothing(desktop, monkeypatch):
    monkeypatch.setattr(actions.state, "load", lambda: {"window": TARGET, "elements": {}})
    _terminal_in_front(desktop)
    res = actions.send_keys("ctrl+a")
    assert not res["ok"] and "nothing was sent" in res["error"]
    assert desktop["sent"] == []


def test_send_keys_sends_when_target_in_front(desktop, monkeypatch):
    monkeypatch.setattr(actions.state, "load", lambda: {"window": TARGET, "elements": {}})
    res = actions.send_keys("ctrl+s")
    assert res == {"ok": True, "sent": "ctrl+s"}
    assert desktop["sent"] == ["^s"]


def test_send_keys_without_read(desktop, monkeypatch):
    monkeypatch.setattr(actions.state, "load", lambda: None)
    res = actions.send_keys("enter")
    assert not res["ok"] and desktop["sent"] == []


class _FakeEl:
    def SetFocus(self):
        pass


def test_typing_stops_when_focus_changes_midway(desktop, monkeypatch):
    loc = {"control_type": "Edit", "name": "Search", "automation_id": ""}
    monkeypatch.setattr(actions, "_resolve", lambda eid: (TARGET, loc, _FakeEl()))
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)

    # No Value pattern: force the keystroke path.
    from pywinauto import uia_defines

    def no_pattern(el, name):
        raise uia_defines.NoPatternInterfaceError()
    monkeypatch.setattr(uia_defines, "get_elem_interface", no_pattern)

    def send_then_popup(seq, literal):
        desktop["sent"].append(seq)
        _terminal_in_front(desktop)  # a different window takes focus after chunk 1
    monkeypatch.setattr(actions, "_send", send_then_popup)

    text = "a" * (actions.CHUNK * 3)
    res = actions.type_text(5, text)
    assert not res["ok"]
    assert res["chars"] == actions.CHUNK
    assert len(desktop["sent"]) == 1
    assert f"stopped after {actions.CHUNK} of {len(text)}" in res["error"]


def test_typing_aborts_before_first_chunk(desktop, monkeypatch):
    loc = {"control_type": "Edit", "name": "Search", "automation_id": ""}
    monkeypatch.setattr(actions, "_resolve", lambda eid: (TARGET, loc, _FakeEl()))
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    from pywinauto import uia_defines
    monkeypatch.setattr(uia_defines, "get_elem_interface",
                        lambda el, name: (_ for _ in ()).throw(uia_defines.NoPatternInterfaceError()))
    _terminal_in_front(desktop)
    res = actions.type_text(5, "hello")
    assert not res["ok"] and res["chars"] == 0 and "nothing was sent" in res["error"]
    assert desktop["sent"] == []


@pytest.mark.parametrize("keys,expected", [
    ("ctrl+s", "^s"),
    ("CTRL+S", "^s"),
    ("alt+f4", "%{F4}"),
    ("enter", "{ENTER}"),
    ("esc", "{ESC}"),
    ("ctrl+shift+t", "^+t"),
    ("ctrl+a backspace", "^a{BACKSPACE}"),
    ("ctrl++", "^{+}"),
    ("shift+9", "+9"),
    ("ctrl+[", "^{[}"),
])
def test_translate_keys(keys, expected):
    assert actions.translate_keys(keys) == expected


@pytest.mark.parametrize("keys", ["", "win+r", "ctrl+banana", "hyper+a", "ctrl+é"])
def test_translate_keys_rejects(keys):
    with pytest.raises(ValueError):
        actions.translate_keys(keys)


def test_escape_text_is_literal():
    assert actions.escape_text("a+b^c%d~(e){f}[g]") == "a{+}b{^}c{%}d{~}{(}e{)}{{}f{}}{[}g{]}"
    assert actions.escape_text("x\r\ny") == "x\ny"
