"""The tool menu the model may call, and validation of what it returns.

Loop layer: nothing here touches the desktop. A call that fails validation never
reaches the bridge; the error text goes back to the model on the next step.

Two parts:
- menu(): the list of tools (name, description, JSON schema) sent to the model.
  This list is the main safety boundary: there is simply no tool for deleting
  files, buying things or launching arbitrary programs.
- validate(): checks one call the model made against that menu and the current
  tree (right tool name, right arguments, types, lengths, and an element ID
  that actually exists), and cleans up harmless type slips like "3" for 3.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Tool kinds. The loop treats each kind differently.
ACTION = "action"   # changes the UI; asked about in confirm mode
READ = "read"       # reads only
STOP = "stop"       # ends the run, handled by the loop

TEXT_LIMIT = 500    # max characters type_text may send in one call
DIRECTIONS = ["up", "down", "left", "right"]


@dataclass
class ToolSpec:
    """One tool on the menu: what the model sees, plus its kind for the loop."""
    name: str
    description: str   # shown to the model; tells it when to use the tool
    schema: dict       # JSON schema of the arguments
    kind: str = ACTION


def _obj(props: dict, required: list[str]) -> dict:
    """Shorthand for a JSON-schema object with these properties."""
    return {"type": "object", "properties": props, "required": required}


# The element-ID argument shared by most tools.
_ID = {"type": "integer", "description": "element [id] from the current tree"}


def menu(apps: list[str]) -> list[ToolSpec]:
    """The M2 menu. `apps` is the enum for open_app (the allowlisted cloud apps)."""
    return [
        ToolSpec("open_app",
                 "Launch an allowed app, or bring it to the front if it is already open.",
                 _obj({"name": {"type": "string", "enum": list(apps)}}, ["name"])),
        ToolSpec("click_element",
                 "Activate an element (button, menu item, tab, list item, checkbox). "
                 "Use the [id] from the current tree.",
                 _obj({"id": _ID}, ["id"])),
        ToolSpec("type_text",
                 "Type text into an editable element. Replaces the field's whole content "
                 "when the app allows it. Use the [id] from the current tree.",
                 _obj({"id": _ID, "text": {"type": "string", "maxLength": TEXT_LIMIT}},
                      ["id", "text"])),
        ToolSpec("send_keys",
                 "Send a keyboard shortcut to the app window, e.g. \"ctrl+s\", \"enter\", "
                 "\"alt+f\". Separate several with spaces. The Windows key is not available.",
                 _obj({"keys": {"type": "string", "maxLength": 60}}, ["keys"])),
        ToolSpec("scroll",
                 "Scroll the area containing an element by one page to reveal more content.",
                 _obj({"id": _ID, "direction": {"type": "string", "enum": DIRECTIONS}},
                      ["id", "direction"])),
        ToolSpec("read_text",
                 "Read the full text of an element (the tree shows at most 80 characters "
                 "of a field).",
                 _obj({"id": _ID}, ["id"]), kind=READ),
        ToolSpec("finish",
                 "End the task. Call this only after the current tree shows the task is done.",
                 _obj({"summary": {"type": "string", "maxLength": 300}}, ["summary"]),
                 kind=STOP),
        ToolSpec("ask_user",
                 "Stop and ask the user a question: the task is unclear, needs a login, or "
                 "needs something you are not allowed to do.",
                 _obj({"question": {"type": "string", "maxLength": 300}}, ["question"]),
                 kind=STOP),
    ]


def _type_name(prop: dict) -> str:
    """The schema type of one argument, for error messages."""
    return prop.get("type", "any")


def _usage(spec: ToolSpec) -> str:
    """"'id' (integer), 'text' (string)": the required arguments, for error messages."""
    props = spec.schema["properties"]
    req = spec.schema.get("required", [])
    return ", ".join(f"'{k}' ({_type_name(props[k])})" for k in req)


def _short(args) -> str:
    """The model's arguments as JSON, cut to 120 characters, for error messages."""
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(args)
    return s if len(s) <= 120 else s[:119] + "…"


def _coerce(name: str, key: str, prop: dict, value):
    """Return (ok, value_or_error) for one argument.

    Integers: accepts 3, 3.0 and "3" (models often send numbers as strings) but
    rejects True/False, since bool is a subclass of int in Python.
    Strings: must be str and within maxLength. Either type: must be in the enum
    if the schema has one.
    """
    t = prop.get("type")
    if t == "integer":
        if isinstance(value, bool):
            return False, f"{name}: '{key}' must be an integer, got {value!r}"
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if not isinstance(value, int):
            return False, f"{name}: '{key}' must be an integer, got {value!r}"
    elif t == "string":
        if not isinstance(value, str):
            return False, f"{name}: '{key}' must be a string, got {value!r}"
        limit = prop.get("maxLength")
        if limit and len(value) > limit:
            return False, f"{name}: '{key}' is {len(value)} characters, the limit is {limit}"
    if "enum" in prop and value not in prop["enum"]:
        return False, f"{name}: '{key}' must be one of {', '.join(map(str, prop['enum']))}; got {value!r}"
    return True, value


def validate(name, args, specs: list[ToolSpec], element_ids) -> tuple[bool, dict | str]:
    """Check one call from the model against the menu and the current tree.

    Returns (True, clean_args) or (False, error text for the model).

    Checks, in order: the tool exists; args is an object; no required argument
    is missing; no unknown argument is present; each value has the right type
    (see _coerce); and any "id" is an element in the tree the model just saw.
    The error texts are written for the model, so it can correct itself.
    """
    by_name = {s.name: s for s in specs}
    spec = by_name.get(name)
    if spec is None:
        return False, f"ERROR: unknown tool '{name}'. Available tools: {', '.join(by_name)}"
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return False, f"ERROR: {name} arguments must be an object. Got: {_short(args)}"

    props = spec.schema["properties"]
    required = spec.schema.get("required", [])
    missing = [k for k in required if k not in args]
    if missing:
        return False, f"ERROR: {name} needs {_usage(spec)}. Got: {_short(args)}"
    extra = [k for k in args if k not in props]
    if extra:
        return False, (f"ERROR: {name} does not take {', '.join(repr(k) for k in extra)}. "
                       f"It needs {_usage(spec)}")

    # Type-check and normalize every argument.
    clean = {}
    for key, value in args.items():
        ok, out = _coerce(name, key, props[key], value)
        if not ok:
            return False, f"ERROR: {out}"
        clean[key] = out

    # The ID must come from the tree read this step (IDs are renumbered every read).
    if "id" in clean:
        ids = sorted(element_ids)
        if clean["id"] not in element_ids:
            span = f"IDs {ids[0]} to {ids[-1]}" if ids else "no IDs: there is no app window yet"
            return False, f"ERROR: no element {clean['id']} in the current tree ({span})"
    return True, clean
