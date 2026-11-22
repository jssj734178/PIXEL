"""Reads config.toml from the project root."""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"

_DEFAULT_BRIDGE = {"max_depth": 12, "max_elements": 120, "chars_per_token": 4}
_DEFAULT_RUN = {
    "step_cap": 15,
    "history_steps": 8,
    "max_rejected": 3,          # rejected calls in a row before the run stops
    "confirm": True,
    "cloud_apps": ["notepad"],  # the only windows whose trees may be sent to a cloud model
    "kill_hotkey": "ctrl+alt+q",
    "settle_initial": 0.2,
    "settle_interval": 0.15,
    "settle_timeout": 3.0,
}
_DEFAULT_LOG = {
    "path": r"%LOCALAPPDATA%\pixel\pixel.db",   # outside OneDrive: it holds screen text
    "store_tree": True,
}


@lru_cache(maxsize=1)
def load() -> dict:
    data: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    data["bridge"] = {**_DEFAULT_BRIDGE, **data.get("bridge", {})}
    data["run"] = {**_DEFAULT_RUN, **data.get("run", {})}
    data["log"] = {**_DEFAULT_LOG, **data.get("log", {})}
    data["log"]["path"] = os.path.expandvars(str(data["log"]["path"]))
    data.setdefault("apps", {})
    models = data.setdefault("models", {})
    models.setdefault("chain", [])
    models.setdefault("providers", {})
    return data


def bridge() -> dict:
    return load()["bridge"]


def apps() -> dict:
    return load()["apps"]


def run() -> dict:
    return load()["run"]


def models() -> dict:
    """{"chain": [provider names in order], "providers": {name: {model, options, ...}}}.

    There is no default model id here on purpose: model names live only in config.toml.
    """
    return load()["models"]


def log() -> dict:
    """{"path": database path with env vars expanded, "store_tree": bool}."""
    return load()["log"]
