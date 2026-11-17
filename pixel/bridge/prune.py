"""Turns a raw UIA tree (dicts from capture.py or a JSON fixture) into a short,
ID-numbered text list for the model. Pure functions, no UIA access.

Rules (see PLAN M1):
1. Drop offscreen and zero-size nodes with their subtrees, plus title bars and scroll bars.
2. Keep interactive control types and give them IDs. Disabled ones are marked.
   An unnamed interactive leaf with no value and no automation id is dropped,
   except text-entry controls, which matter even without a name.
3. Keep Text/Group/Pane/Window as context (no ID) only when named, not a repeat
   of (or contained in) the parent's name, and either a Text leaf or holding
   kept descendants.
4. Everything else is dropped but its children are promoted.
5. A context or dropped node with exactly one kept child collapses into it.
6. Depth limit: nodes below max_depth are cut and a marker line is added.
7. Line cap: after max_elements lines, one "N more" marker line.
8. Names cut to 60 chars, values to 80; whitespace collapsed; control,
   format, private-use and lone-surrogate characters removed.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

INTERACTIVE = frozenset({
    "Button", "SplitButton", "Hyperlink", "Edit", "Document", "ComboBox",
    "CheckBox", "RadioButton", "MenuItem", "TabItem", "ListItem", "TreeItem",
    "Slider", "Spinner",
})
TEXT_ENTRY = frozenset({"Edit", "Document", "ComboBox"})
CONTEXT = frozenset({"Text", "Group", "Pane", "Window"})
DROP_SUBTREE = frozenset({"TitleBar", "ScrollBar"})

NAME_LIMIT = 60
VALUE_LIMIT = 80
INDENT = "  "


@dataclass
class Element:
    """One ID-bearing element, with what actions need to find it again."""
    id: int
    control_type: str
    name: str            # raw name, used for the staleness check
    automation_id: str
    runtime_id: list
    rect: list | None
    patterns: list
    value_readonly: bool


@dataclass
class PruneResult:
    lines: list[str]
    elements: list[Element]
    raw_nodes: int
    depth_cut: bool = False
    omitted: int = 0


@dataclass
class _Out:
    kind: str            # "el" or "ctx"
    node: dict
    name: str
    value: str | None = None
    children: list = field(default_factory=list)

    def size(self) -> int:
        return 1 + sum(c.size() for c in self.children)


def clean_text(text: str | None, limit: int) -> str:
    """Make UI text safe and short. Never raises on odd characters."""
    if not text:
        return ""
    out = []
    for ch in str(text):
        cat = unicodedata.category(ch)
        if ch.isspace() or cat in ("Cc", "Zl", "Zp"):
            out.append(" ")
        elif cat in ("Cf", "Co", "Cs", "Cn"):
            continue  # zero-width marks, icon-font glyphs, broken surrogates
        else:
            out.append(ch)
    cleaned = " ".join("".join(out).split()).replace('"', "'")
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def estimate_tokens(text: str, chars_per_token: float = 4) -> int:
    return math.ceil(len(text) / chars_per_token) if text else 0


def _count(node: dict) -> int:
    return 1 + sum(_count(c) for c in node.get("children", []))


def _visible(node: dict) -> bool:
    if node.get("offscreen"):
        return False
    rect = node.get("rect")
    if rect and (rect[2] <= 0 or rect[3] <= 0):
        return False
    return True


class _Walker:
    def __init__(self, max_depth: int):
        self.max_depth = max_depth
        self.depth_cut = False

    def walk(self, node: dict, depth: int, parent_name: str) -> list[_Out]:
        if depth > self.max_depth:
            self.depth_cut = True
            return []
        if not _visible(node):
            return []
        ctype = node.get("control_type", "")
        if ctype in DROP_SUBTREE:
            return []

        name = clean_text(node.get("name"), NAME_LIMIT)
        kids: list[_Out] = []
        for child in node.get("children", []):
            kids.extend(self.walk(child, depth + 1, name))

        if ctype in INTERACTIVE:
            value = None
            if ctype in TEXT_ENTRY and node.get("value") is not None:
                value = clean_text(node.get("value"), VALUE_LIMIT)
            if not (name or value or node.get("automation_id") or kids or ctype in TEXT_ENTRY):
                return []
            return [_Out("el", node, name, value, kids)]

        if ctype in CONTEXT and name and name not in parent_name:
            if ctype == "Text" and not kids:
                return [_Out("ctx", node, name)]
            if len(kids) > 1:
                return [_Out("ctx", node, name, children=kids)]
        return kids  # dropped wrapper: children promoted; one child = collapse


def _label(out: _Out) -> str:
    node = out.node
    ctype = node.get("control_type", "")
    if out.name:
        label = f'{ctype} "{out.name}"'
    elif node.get("automation_id"):
        label = f"{ctype} #{clean_text(node['automation_id'], NAME_LIMIT)}"
    else:
        label = ctype
    if out.value is not None:
        label += f' = "{out.value}"'
    if out.kind == "el" and not node.get("enabled", True):
        label += " (disabled)"
    return label


def prune(raw_root: dict, *, max_depth: int = 12, max_elements: int = 120) -> PruneResult:
    """Prune a raw window tree. The root is the window itself and is not listed."""
    walker = _Walker(max_depth)
    outs: list[_Out] = []
    for child in raw_root.get("children", []):
        outs.extend(walker.walk(child, 1, clean_text(raw_root.get("name"), NAME_LIMIT)))

    result = PruneResult(lines=[], elements=[], raw_nodes=_count(raw_root),
                         depth_cut=walker.depth_cut)

    def emit(items: list[_Out], level: int) -> None:
        for out in items:
            if len(result.lines) >= max_elements:
                result.omitted += out.size()
                continue
            prefix = INDENT * level
            if out.kind == "el":
                eid = len(result.elements) + 1
                node = out.node
                result.elements.append(Element(
                    id=eid,
                    control_type=node.get("control_type", ""),
                    name=node.get("name", ""),
                    automation_id=node.get("automation_id", ""),
                    runtime_id=list(node.get("runtime_id") or []),
                    rect=node.get("rect"),
                    patterns=list(node.get("patterns") or []),
                    value_readonly=bool(node.get("value_readonly")),
                ))
                result.lines.append(f"{prefix}[{eid}] {_label(out)}")
            else:
                result.lines.append(f"{prefix}{_label(out)}")
            emit(out.children, level + 1)

    emit(outs, 0)
    return result


def render(result: PruneResult, window_title: str = "", process: str = "") -> str:
    header = f"Window: {clean_text(window_title, 80) or '(untitled)'}"
    if process:
        header += f" ({process})"
    lines = [header, *result.lines]
    if result.depth_cut:
        lines.append("… deeper content not shown")
    if result.omitted:
        lines.append(f"… {result.omitted} more elements not shown")
    return "\n".join(lines)
