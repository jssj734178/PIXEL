"""Hand-written raw trees for pruning tests. No real user data."""

_rid = [0]


def n(control_type: str, name: str = "", children=None, **kw) -> dict:
    """Build a raw node in the capture.py format."""
    _rid[0] += 1
    node = {
        "control_type": control_type,
        "name": name,
        "automation_id": kw.pop("automation_id", ""),
        "class_name": kw.pop("class_name", ""),
        "enabled": kw.pop("enabled", True),
        "offscreen": kw.pop("offscreen", False),
        "rect": kw.pop("rect", [0, 0, 100, 20]),
        "runtime_id": kw.pop("runtime_id", [42, _rid[0]]),
        "value": kw.pop("value", None),
        "value_readonly": kw.pop("value_readonly", False),
        "patterns": kw.pop("patterns", []),
        "children": children or [],
    }
    assert not kw, f"unknown keys {kw}"
    return node


def editor_window() -> dict:
    """Roughly the shape of a blank Win11 Notepad window."""
    return n("Window", "Untitled - Notepad", [
        n("TitleBar", "", [n("Button", "Minimize"), n("Button", "Close")]),
        n("Pane", "", [
            n("Tab", "", [
                n("TabItem", "Untitled. Unmodified.", [n("Text", "Untitled")]),
                n("Button", "Add New Tab", patterns=["invoke"]),
            ]),
        ]),
        n("MenuBar", "", [
            n("MenuItem", "File", patterns=["expand"]),
            n("MenuItem", "Edit", patterns=["expand"]),
        ]),
        n("Pane", "", [n("Pane", "", [
            n("Document", "Text editor", value="", patterns=["value"]),
        ])]),
        n("ScrollBar", "Vertical", [n("Button", "Line up")]),
        n("StatusBar", "", [
            n("Text", "Ln 1, Col 1"),
            n("Text", "UTF-8"),
        ]),
    ])
