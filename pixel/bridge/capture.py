"""Reads one window's UI Automation tree into plain dicts.

This is the only module that walks UIA. It fetches the whole control-view
subtree in a single cached call, so a read is one cross-process round trip
instead of thousands of per-property COM calls.

Raw node shape (also the fixture format):
    {"control_type": str, "name": str, "automation_id": str, "class_name": str,
     "enabled": bool, "offscreen": bool, "rect": [left, top, width, height] | None,
     "runtime_id": [int, ...], "value": str | None, "value_readonly": bool,
     "patterns": [str, ...], "children": [node, ...]}

Background: UI Automation (UIA) is Windows' accessibility API. Each app exposes
a tree of elements (buttons, text fields, ...) with properties (name, type,
position) and "patterns" (things you can do to them: invoke, toggle, set a
value, ...). pywinauto gives us the raw COM interface; this module uses it
directly for speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# property key -> UIA property id name (without the UIA_ prefix / PropertyId suffix)
_PROPS = {
    "name": "Name",
    "control_type": "ControlType",
    "automation_id": "AutomationId",
    "class_name": "ClassName",
    "enabled": "IsEnabled",
    "offscreen": "IsOffscreen",
    "rect": "BoundingRectangle",
    "runtime_id": "RuntimeId",
    "value": "ValueValue",
    "value_readonly": "ValueIsReadOnly",
}
# pattern name used in raw nodes -> availability property
_PATTERNS = {
    "invoke": "IsInvokePatternAvailable",
    "toggle": "IsTogglePatternAvailable",
    "select": "IsSelectionItemPatternAvailable",
    "expand": "IsExpandCollapsePatternAvailable",
    "value": "IsValuePatternAvailable",
    "scroll": "IsScrollPatternAvailable",
    "text": "IsTextPatternAvailable",
}


@dataclass
class Capture:
    """The result of one read: the raw tree plus live handles to act on later."""
    root: dict                       # raw tree, the window node at the top
    raw_nodes: int                   # how many nodes were read (before pruning)
    elements: dict = field(default_factory=dict)  # runtime_id tuple -> live IUIAutomationElement


def _uia():
    """pywinauto's shared UIA COM object (imported lazily so tests need no UIA)."""
    from pywinauto.uia_defines import IUIA
    return IUIA()


def _pid(name: str) -> int:
    """UIA numeric property id for a name, e.g. "Name" -> UIA_NamePropertyId."""
    return getattr(_uia().UIA_dll, f"UIA_{name}PropertyId")


def _clean(v):
    """Map COM values to JSON-friendly ones. 'Not supported' sentinels become None."""
    if isinstance(v, (str, bool, int, float)) or v is None:
        return v
    if isinstance(v, (tuple, list)):
        return [x for x in v if isinstance(x, (int, float))]
    return None


def _cache_request():
    """A UIA cache request: every property and pattern flag we need, for the whole subtree.

    ControlViewCondition skips "raw view" nodes that only exist for layout,
    which roughly halves the tree.
    """
    u = _uia()
    cr = u.iuia.CreateCacheRequest()
    for prop in list(_PROPS.values()) + list(_PATTERNS.values()):
        cr.AddProperty(_pid(prop))
    cr.TreeScope = u.UIA_dll.TreeScope_Subtree
    cr.TreeFilter = u.iuia.ControlViewCondition
    return cr


def _to_dict(el, depth: int, max_depth: int, index: dict, counter: list) -> dict:
    """Convert one cached UIA element (and, recursively, its children) to a raw node dict.

    index:   filled with runtime_id -> live element, so actions can use it later.
    counter: a one-item list used as a mutable counter of nodes visited.
    Every value is read from the cache, so no extra cross-process calls happen.
    """
    u = _uia()
    counter[0] += 1
    node: dict = {}
    for key, prop in _PROPS.items():
        try:
            node[key] = _clean(el.GetCachedPropertyValue(_pid(prop)))
        except Exception:  # noqa: BLE001 - a broken provider must not kill the read
            node[key] = None
    # Normalize: numeric control type -> name, None -> "" / False defaults.
    node["control_type"] = u.known_control_type_ids.get(node["control_type"], "Unknown")
    for key in ("name", "automation_id", "class_name"):
        node[key] = node[key] or ""
    node["enabled"] = bool(node["enabled"]) if node["enabled"] is not None else True
    node["offscreen"] = bool(node["offscreen"])
    node["value_readonly"] = bool(node["value_readonly"])
    rect = node["rect"]
    node["rect"] = [int(x) for x in rect] if rect and len(rect) == 4 else None
    node["runtime_id"] = [int(x) for x in (node["runtime_id"] or [])]

    # Which actions the element supports.
    patterns = []
    for pname, prop in _PATTERNS.items():
        try:
            if el.GetCachedPropertyValue(_pid(prop)) is True:
                patterns.append(pname)
        except Exception:  # noqa: BLE001
            pass
    node["patterns"] = patterns
    if "value" not in patterns:
        node["value"] = None   # a value without ValuePattern is meaningless

    if node["runtime_id"]:
        index[tuple(node["runtime_id"])] = el

    children = []
    # Walk one level past max_depth so prune can tell the tree was cut there.
    if depth <= max_depth:
        try:
            arr = el.GetCachedChildren()
        except Exception:  # noqa: BLE001
            arr = None
        if arr:
            for i in range(arr.Length):
                children.append(_to_dict(arr.GetElement(i), depth + 1, max_depth, index, counter))
    node["children"] = children
    return node


def capture_window(hwnd: int, max_depth: int = 12) -> Capture:
    """Read the full UIA tree of one window (one cached COM call) into a Capture."""
    u = _uia()
    root_el = u.iuia.ElementFromHandle(hwnd).BuildUpdatedCache(_cache_request())
    index: dict = {}
    counter = [0]
    root = _to_dict(root_el, 0, max_depth, index, counter)
    return Capture(root=root, raw_nodes=counter[0], elements=index)
