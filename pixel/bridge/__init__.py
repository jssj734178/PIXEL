"""Bridge: accessibility tree reads and actions. These are the M1 tools."""

from pixel.bridge.actions import (
    click_element,
    get_focused_window,
    get_ui_tree,
    open_app,
    send_keys,
    type_text,
)

__all__ = [
    "click_element",
    "get_focused_window",
    "get_ui_tree",
    "open_app",
    "send_keys",
    "type_text",
]
