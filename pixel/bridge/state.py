"""Remembers the last tree read, so `pixel click 3` in a new process knows what 3 was.

Stored in .pixel/last_tree.json (gitignored). IDs are valid for one read only.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from pixel.config import PROJECT_ROOT
from pixel.bridge.prune import Element

STATE_DIR = PROJECT_ROOT / ".pixel"
STATE_FILE = STATE_DIR / "last_tree.json"


def save(window: dict, elements: list[Element]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    data = {
        "window": {k: window.get(k) for k in ("title", "process", "pid", "handle")},
        "elements": {str(e.id): asdict(e) for e in elements},
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
