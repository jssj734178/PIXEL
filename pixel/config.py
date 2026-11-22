"""Reads config.toml from the project root.

Config layer. Every other module gets settings through the small accessor
functions at the bottom (bridge(), apps(), run(), models(), log()), never by
opening config.toml itself.

Each section is merged over a dict of defaults, so a missing key in
config.toml falls back to a sensible value instead of crashing. The exception
is model names: there is deliberately no default model, so they live only in
config.toml (a CLAUDE.md rule).
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

# The repo folder (pixel/config.py -> pixel/ -> project root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"

# Defaults for each [section]. A key set in config.toml overrides the one here.
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
    """Read config.toml once per process and fill in defaults.

    Cached: later calls return the same dict, so editing config.toml takes
    effect on the next `pixel` command, not mid-run. If the file is missing,
    every section is just its defaults (and the model chain is empty).
    """
    data: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:   # tomllib needs a binary file
            data = tomllib.load(f)
    # Merge each section over its defaults (config.toml wins).
    data["bridge"] = {**_DEFAULT_BRIDGE, **data.get("bridge", {})}
    data["run"] = {**_DEFAULT_RUN, **data.get("run", {})}
    data["log"] = {**_DEFAULT_LOG, **data.get("log", {})}
    # Turn %LOCALAPPDATA% etc. into a real path.
    data["log"]["path"] = os.path.expandvars(str(data["log"]["path"]))
    data.setdefault("apps", {})
    models = data.setdefault("models", {})
    models.setdefault("chain", [])
    models.setdefault("providers", {})
    return data


def bridge() -> dict:
    """[bridge]: tree depth and size limits, and the token estimate divisor."""
    return load()["bridge"]


def apps() -> dict:
    """[apps.*]: {name: {"exe": ..., "process": ...}}. open_app may only use these."""
    return load()["apps"]


def run() -> dict:
    """[run]: step cap, confirm mode, cloud_apps allowlist, kill hotkey, settle timing."""
    return load()["run"]


def models() -> dict:
    """{"chain": [provider names in order], "providers": {name: {model, options, ...}}}.

    There is no default model id here on purpose: model names live only in config.toml.
    """
    return load()["models"]


def log() -> dict:
    """{"path": database path with env vars expanded, "store_tree": bool}."""
    return load()["log"]
