"""Bridge: accessibility tree reads and actions (M1 tools, plus scroll and read_text in M2)."""

from pixel.bridge.actions import (
    click_element,
    get_focused_window,
    get_ui_tree,
    open_app,
    read_text,
    scroll,
    send_keys,
    type_text,
)

__all__ = [
    "click_element",
    "get_focused_window",
    "get_ui_tree",
    "open_app",
    "read_text",
    "scroll",
    "send_keys",
    "type_text",
]
