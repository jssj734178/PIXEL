"""Reads config.toml from the project root."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"

_DEFAULT_BRIDGE = {"max_depth": 12, "max_elements": 120, "chars_per_token": 4}


@lru_cache(maxsize=1)
def load() -> dict:
    data: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    data["bridge"] = {**_DEFAULT_BRIDGE, **data.get("bridge", {})}
    data.setdefault("apps", {})
    return data


def bridge() -> dict:
    return load()["bridge"]


def apps() -> dict:
    return load()["apps"]
