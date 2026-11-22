"""Turns a raw UIA tree (dicts from capture.py or a JSON fixture) into a short,
ID-numbered text list for the model. Pure functions, no UIA access.

Why prune: a raw tree is hundreds of nodes, most of them invisible layout
wrappers. The model only needs what a person would see and could act on, and
every line costs tokens (and free-tier quota). Pruning decides exactly what
the model sees, so these rules matter more than almost anything else.

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

Example output (render()):
    Window: Untitled - Notepad (notepad.exe)
    [1] MenuItem "File"
    [2] Document "Text editor" = "hello"

How it works: _Walker.walk() makes a first pass that decides what to keep and
builds a small tree of _Out records; prune() then numbers the kept elements
in reading order and emits one indented line each.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

# Control types the model can act on. These get an [id].
INTERACTIVE = frozenset({
    "Button", "SplitButton", "Hyperlink", "Edit", "Document", "ComboBox",
    "CheckBox", "RadioButton", "MenuItem", "TabItem", "ListItem", "TreeItem",
    "Slider", "Spinner",
})
# Interactive types that hold text: kept even when unnamed, and show their value.
TEXT_ENTRY = frozenset({"Edit", "Document", "ComboBox"})
# Types that may be shown as labels (no ID) to give the model context.
CONTEXT = frozenset({"Text", "Group", "Pane", "Window"})
# Types dropped together with everything inside them.
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
    runtime_id: list     # UIA's id for the live element (stable while it exists)
    rect: list | None    # [left, top, width, height] on screen
    patterns: list       # supported UIA patterns, e.g. ["invoke"], ["value"]
    value_readonly: bool


@dataclass
class PruneResult:
    """What prune() returns: the text lines plus the elements behind each [id]."""
    lines: list[str]
    elements: list[Element]   # elements[i].id == i + 1
    raw_nodes: int            # nodes before pruning, for --verbose
    depth_cut: bool = False   # True if some nodes were below max_depth
    omitted: int = 0          # lines dropped by the max_elements cap


@dataclass
class _Out:
    """A kept node in the pruned tree, before it is numbered and printed."""
    kind: str            # "el" or "ctx"
    node: dict
    name: str
    value: str | None = None
    children: list = field(default_factory=list)

    def size(self) -> int:
        """Lines this node would print, itself plus all kept descendants."""
        return 1 + sum(c.size() for c in self.children)


def clean_text(text: str | None, limit: int) -> str:
    """Make UI text safe and short. Never raises on odd characters.

    Whitespace and line breaks become single spaces; invisible format
    characters, icon-font glyphs (private use) and broken surrogates are
    removed; double quotes become single quotes so labels stay parseable;
    anything over `limit` is cut with "…".
    """
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
    """Rough token count: characters divided by chars_per_token (from config), rounded up."""
    return math.ceil(len(text) / chars_per_token) if text else 0


def _count(node: dict) -> int:
    """Total nodes in a raw tree, including the root."""
    return 1 + sum(_count(c) for c in node.get("children", []))


def _visible(node: dict) -> bool:
    """Rule 1: False for offscreen nodes and ones with zero width or height."""
    if node.get("offscreen"):
        return False
    rect = node.get("rect")
    if rect and (rect[2] <= 0 or rect[3] <= 0):
        return False
    return True


class _Walker:
    """First pass: decides which raw nodes to keep. Records whether the depth limit cut anything."""

    def __init__(self, max_depth: int):
        self.max_depth = max_depth
        self.depth_cut = False

    def walk(self, node: dict, depth: int, parent_name: str) -> list[_Out]:
        """Return the kept nodes for this raw node: one _Out, its promoted children, or nothing.

        Returning a list (rather than one node or None) is what makes rule 4
        work: a dropped wrapper simply returns its children's lists, so they
        move up a level. Rule 5 (collapse a wrapper with one kept child)
        follows from the same thing.
        """
        if depth > self.max_depth:            # rule 6
            self.depth_cut = True
            return []
        if not _visible(node):                # rule 1
            return []
        ctype = node.get("control_type", "")
        if ctype in DROP_SUBTREE:             # rule 1
            return []

        name = clean_text(node.get("name"), NAME_LIMIT)
        # Children first (depth-first), so we know whether anything below was kept.
        kids: list[_Out] = []
        for child in node.get("children", []):
            kids.extend(self.walk(child, depth + 1, name))

        if ctype in INTERACTIVE:              # rule 2
            value = None
            if ctype in TEXT_ENTRY and node.get("value") is not None:
                value = clean_text(node.get("value"), VALUE_LIMIT)
            # An unnamed, empty, childless button etc. tells the model nothing.
            if not (name or value or node.get("automation_id") or kids or ctype in TEXT_ENTRY):
                return []
            return [_Out("el", node, name, value, kids)]

        # Rule 3: a named label that adds information beyond its parent's name.
        if ctype in CONTEXT and name and name not in parent_name:
            if ctype == "Text" and not kids:
                return [_Out("ctx", node, name)]
            if len(kids) > 1:
                return [_Out("ctx", node, name, children=kids)]
        return kids  # dropped wrapper: children promoted; one child = collapse


def _label(out: _Out) -> str:
    """One line's text without the [id]: 'Button "Save"', 'Edit #searchBox = "hi"', ..."""
    node = out.node
    ctype = node.get("control_type", "")
    if out.name:
        label = f'{ctype} "{out.name}"'
    elif node.get("automation_id"):
        label = f"{ctype} #{clean_text(node['automation_id'], NAME_LIMIT)}"   # unnamed: use the dev id
    else:
        label = ctype
    if out.value is not None:
        label += f' = "{out.value}"'
    if out.kind == "el" and not node.get("enabled", True):
        label += " (disabled)"
    return label


def prune(raw_root: dict, *, max_depth: int = 12, max_elements: int = 120) -> PruneResult:
    """Prune a raw window tree. The root is the window itself and is not listed.

    IDs are assigned 1, 2, 3, ... in reading order (depth-first), so they are
    only valid for this one read. The model must use IDs from the latest tree.
    """
    walker = _Walker(max_depth)
    outs: list[_Out] = []
    for child in raw_root.get("children", []):
        outs.extend(walker.walk(child, 1, clean_text(raw_root.get("name"), NAME_LIMIT)))

    result = PruneResult(lines=[], elements=[], raw_nodes=_count(raw_root),
                         depth_cut=walker.depth_cut)

    # Second pass: number and print the kept nodes, indenting by nesting level.
    def emit(items: list[_Out], level: int) -> None:
        for out in items:
            if len(result.lines) >= max_elements:     # rule 7: count what we drop
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
    """The final text the model sees: a "Window:" header, the lines, and any cut markers."""
    header = f"Window: {clean_text(window_title, 80) or '(untitled)'}"
    if process:
        header += f" ({process})"
    lines = [header, *result.lines]
    if result.depth_cut:
        lines.append("… deeper content not shown")
    if result.omitted:
        lines.append(f"… {result.omitted} more elements not shown")
    return "\n".join(lines)
