"""Configuration and secrets.

Settings live in ``config.toml`` at the project root (human-edited, committed).
Secrets live in environment variables, optionally loaded from a git-ignored
``.env`` file. Nothing secret is ever read from ``config.toml``.
"""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
ENV_PATH = ROOT / ".env"
STATE_DIR = ROOT / "state"

DEFAULTS: dict[str, Any] = {
    "assistant": {
        "name": "Vyron",
        "purpose": "a personal daily-driver assistant",
        "tone": "warm, plain-spoken, and brief",
    },
    "model": {
        "provider": "anthropic",
        "name": "claude-opus-5",
        "max_tokens": 4096,
        "effort": "medium",
        "input_usd_per_mtok": 5.0,
        "output_usd_per_mtok": 25.0,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """A dict-of-dicts view over config.toml with defaults filled in."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, section: str) -> dict[str, Any]:
        return self._data.setdefault(section, {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self._data.get(section, {}).get(key, default)

    @property
    def data(self) -> dict[str, Any]:
        return self._data


def load_config(path: Path = CONFIG_PATH) -> Config:
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as f:
            data = tomllib.load(f)
    return Config(_merge(DEFAULTS, data))


def load_env(path: Path = ENV_PATH) -> None:
    """Load KEY=VALUE lines from .env into os.environ (without overriding)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def secret(name: str) -> str | None:
    """Read a secret from the environment. Returns None if unset or blank."""
    value = os.environ.get(name, "").strip()
    return value or None
